"""DataUpdateCoordinator for Brunata Online, incl. historical meter import.

The chunking algorithm for historical import lives in brunata_client.history
(no `homeassistant` dependency, so it's also usable from standalone scripts —
see scripts/history_smoke_test.py). This module wraps it for the one-time
backfill, and drives the regular hourly live-data polling.

Generalized (Del 3a) to an arbitrary number of meters per allocationUnit —
nothing here assumes exactly one heat/hot-water/cold-water meter.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

from homeassistant.components.logbook import async_log_entry
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataLoginError, BrunataSessionError
from .brunata_client.history import fetch_all_meter_history, parse_brunata_datetime
from .brunata_client.models import MeterReading
from .brunata_client.scheduling import compute_next_poll_target

from . import debug_export, ledger, statistics
from .const import (
    ALLOCATION_UNIT_OF_MEASUREMENT,
    ALLOCATION_UNIT_SLUGS,
    ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET,
    CONF_HISTORY_IMPORTED,
    CONF_LEDGER_BACKFILLED,
    DOMAIN,
    UPDATE_INTERVAL,
    build_meter_naming,
)

_LOGGER = logging.getLogger(__name__)

# Adaptive polling (see brunata_client/scheduling.py for the actual median/
# rounding math, unit-tested offline). Water meters (W/K) update several
# times a day — a longer rolling window smooths out day-to-day jitter; heat
# (O) updates once a day, so its window is sized in "days of history"
# instead. Both are "a couple of weeks" of real observations either way.
_MIN_OBSERVATIONS = 5
_WATER_HISTORY_LEN = 30
_HEAT_HISTORY_LEN = 14
_ROUND_TO_MINUTES = 5


class BrunataDataUpdateCoordinator(DataUpdateCoordinator[dict[int, MeterReading]]):
    """Coordinates fetching data from the Brunata Online API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BrunataClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.client = client
        # Refreshed every _async_update_data() call — an account's meter list
        # can change over time (meters added/dismounted) without a reload.
        self.active_meters: list[dict] = []
        # Precomputed once per poll (see _async_compute_summaries) so the
        # dashboard card can read an already-known-good monthly/rolling
        # summary instead of depending on a fresh WS round-trip succeeding
        # at the exact moment the dashboard happens to load. In-memory only
        # (plain instance attributes, not coordinator.data or an entity
        # attribute) — never written to the recorder database.
        self.monthly_summaries: dict[int, dict] = {}
        self.rolling_summaries: dict[int, dict] = {}
        # Adaptive polling state — in-memory only, resets to the fixed
        # UPDATE_INTERVAL fallback on every restart/reload (same precedent
        # as the summaries above), and re-learns within _MIN_OBSERVATIONS
        # polls. Keyed by meter_id so per-meter drift is possible even
        # though the scheduling DECISION itself is made per meter-type
        # (see _async_maybe_reschedule) — there's only one shared
        # meteroverview API call per account, so only one schedule exists.
        self._reading_time_history: dict[int, deque[datetime]] = {}
        self._current_schedule: tuple[int | None, int] | None = None
        self._unsub_schedule = None

    def _log_activity(self, message: str) -> None:
        """Write one line to this device's Activity tab, so it's visible at a
        glance whether the integration is actually still polling Brunata —
        rather than a silent "no new data" that could just as easily be a
        dead polling loop (see this project's 2026-07-13 recorder
        investigation, where confirming polling was even happening was a
        real, non-obvious debugging step).

        logbook.async_log_entry is a synchronous @callback despite its
        "async_" prefix (confirmed against HA's own source) — must be
        called directly, not awaited.

        The entity_id these entries attach to MUST be sensor.py's
        BrunataStatusSensor, not one of the three real meter sensors —
        confirmed against homeassistant/components/logbook/helpers.py's
        is_sensor_continuous(): any sensor with a unit_of_measurement, a
        state_class, or a numeric device_class (which all three meter
        sensors correctly have, for the Energy dashboard) is treated as a
        "continuous" data source and its logbook entries are silently
        excluded from the Activity tab — pointing at a meter sensor here
        would run correctly every hour and never once show up. The status
        entity has none of those three attributes, so its entries aren't
        filtered.

        Resolved via the entity registry by unique_id (async_get_entity_id —
        a cheap, synchronous, @callback lookup, confirmed safe to call here
        against HA's own entity_registry.py) rather than a hardcoded entity_id
        string, since sensor.py deliberately leaves this entity's entity_id
        to HA's own registry to assign/disambiguate (relevant if more than
        one Brunata account is ever configured). Returns None — and is
        skipped here — if the entity hasn't been registered yet (e.g. the
        very first poll, before sensor.py's async_setup_entry has run).
        """
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.entry.entry_id}_status"
        )
        if entity_id is None:
            return
        async_log_entry(
            self.hass,
            name="Brunata",
            message=message,
            domain=DOMAIN,
            entity_id=entity_id,
        )

    async def _fetch_active_meters(self) -> list[dict]:
        """GET /consumer/metersforconsumer, filtered to active O/W/K meters.

        Deduplicated by meterId — defensive against metersforconsumer ever
        returning the same physical meter twice, which would otherwise
        surface as the same meter/months appearing twice in the Del 3b
        monthly view (each duplicate producing its own identical WS entry).
        """
        meters = await self.client.fetch_meters_for_consumer()
        active = [
            m
            for m in meters
            if not m.get("dismountedDate") and m["allocationUnit"] in ALLOCATION_UNIT_SLUGS
        ]
        deduped: dict[int, dict] = {}
        for meter in active:
            deduped[meter["meterId"]] = meter
        return list(deduped.values())

    async def _fetch_consumption_with_retry(self):
        try:
            return await self.client.fetch_consumption_data()
        except (BrunataLoginError, BrunataSessionError):
            # Session/token likely expired between polls — one re-login retry
            # before giving up (see docs/login-flow.md on session lifetime).
            await self.client.login()
            return await self.client.fetch_consumption_data()

    async def _async_update_data(self) -> dict[int, MeterReading]:
        try:
            consumption = await self._fetch_consumption_with_retry()
            self.active_meters = await self._fetch_active_meters()
        except Exception as err:
            self._log_activity(f"Kunne ikke hente data fra Brunata Online: {err}")
            raise UpdateFailed(str(err)) from err

        readings_by_id = {m.meter_id: m for m in consumption.raw_meters}
        result: dict[int, MeterReading] = {}
        for meter in self.active_meters:
            meter_id = meter["meterId"]
            reading = readings_by_id.get(meter_id)
            if reading is None:
                # Active per metersforconsumer but absent from this cycle's
                # meteroverview (e.g. hasn't reported yet) — keep the entity
                # alive with no fresh value rather than dropping it.
                reading = MeterReading(
                    meter_id=meter_id,
                    meter_no=meter["meterNo"],
                    placement=meter.get("placement") or "",
                    allocation_unit=meter["allocationUnit"],
                    unit=meter["unit"],
                    unit_label=str(meter["unit"]),
                    scale=None,
                    reading_value=None,
                    reading_date=None,
                    transmitting=meter.get("transmitting", False),
                )
            result[meter_id] = reading

        try:
            await self._async_append_ledger_entries(result)
        except Exception as err:  # noqa: BLE001 — best-effort, must not break the live poll
            _LOGGER.warning(
                "Could not append this poll's readings to the ledger (%s: %s)",
                type(err).__name__, err,
            )

        try:
            await self._async_compute_summaries(result)
        except Exception as err:  # noqa: BLE001 — best-effort precompute, must not break the live poll
            _LOGGER.warning(
                "Could not precompute monthly/rolling summaries (%s: %s) — the "
                "dashboard card will fall back to its on-demand WS calls instead",
                type(err).__name__, err,
            )

        self._record_reading_observations(result)
        try:
            await self._async_maybe_reschedule()
        except Exception as err:  # noqa: BLE001 — best-effort, must not break the live poll
            _LOGGER.warning(
                "Could not update adaptive polling schedule (%s: %s) — keeping "
                "the current schedule",
                type(err).__name__, err,
            )

        self._log_activity(f"Opdaterede data for {len(result)} måler(e) fra Brunata Online")
        return result

    async def _async_append_ledger_entries(self, readings: dict[int, MeterReading]) -> None:
        """One ledger line per meter per actual poll (see ledger.py) — NOT
        deduplicated by whether the value changed, unlike
        _record_reading_observations' scheduling history: every successful
        poll is a real, independent Brunata data point worth keeping at full
        resolution, per Fase 1's own requirement.

        Skips a meter entirely if this poll didn't return a real reading
        for it (reading_value/reading_date both None — e.g. active per
        metersforconsumer but absent from this cycle's meteroverview), since
        there's nothing real to record in that case.
        """
        for meter_id, reading in readings.items():
            if reading.reading_value is None or reading.reading_date is None:
                continue
            try:
                ts = parse_brunata_datetime(reading.reading_date)
            except (ValueError, TypeError):
                continue
            await ledger.async_append_entry(
                self.hass, meter_id, reading.allocation_unit, ts, reading.reading_value
            )

    def _record_reading_observations(self, readings: dict[int, MeterReading]) -> None:
        """Append a new observation to a meter's rolling history whenever its
        `reading_date` (Brunata's own per-meter telegramDate) genuinely
        changed since the last poll — not just because a poll happened.

        This is what lets scheduling.py learn the true intra-hour delivery
        pattern even though we currently only poll once an hour: Brunata's
        own timestamp already encodes the precise moment a reading was
        taken, independent of when we happened to check for it.
        """
        for meter_id, reading in readings.items():
            if reading.reading_date is None:
                continue
            try:
                observed = parse_brunata_datetime(reading.reading_date)
            except (ValueError, TypeError):
                continue

            history = self._reading_time_history.get(meter_id)
            if history is None:
                max_len = _HEAT_HISTORY_LEN if reading.allocation_unit == "O" else _WATER_HISTORY_LEN
                history = deque(maxlen=max_len)
                self._reading_time_history[meter_id] = history

            if history and history[-1] == observed:
                continue  # same telegram as last poll — not a new reading
            history.append(observed)

    async def _async_maybe_reschedule(self) -> None:
        """Recompute the adaptive poll target from the (possibly
        just-updated) observation history, and switch HA's own scheduling
        mechanism if the target has changed since last time.

        Which meter type governs the schedule: a mixed or water-only
        account is always governed by the water-driven, several-times-daily
        cadence — one shared meteroverview call covers every meter
        regardless, so heat can't independently slow the account's overall
        polling down without starving the water meters. Only an account
        with NO water meters at all (heat-only) switches to the genuinely
        rare, once-daily cadence — see the module docstring in
        scheduling.py for the actual median/rounding math (pure, unit-tested
        offline).
        """
        water_ids = [m["meterId"] for m in self.active_meters if m["allocationUnit"] in ("W", "K")]
        heat_ids = [m["meterId"] for m in self.active_meters if m["allocationUnit"] == "O"]

        if water_ids:
            observations = [
                ts for meter_id in water_ids for ts in self._reading_time_history.get(meter_id, ())
            ]
            target = compute_next_poll_target(
                observations, min_observations=_MIN_OBSERVATIONS,
                round_to_minutes=_ROUND_TO_MINUTES, daily=False,
            )
        elif heat_ids:
            observations = [
                ts for meter_id in heat_ids for ts in self._reading_time_history.get(meter_id, ())
            ]
            target = compute_next_poll_target(
                observations, min_observations=_MIN_OBSERVATIONS,
                round_to_minutes=_ROUND_TO_MINUTES, daily=True,
            )
        else:
            target = None  # no meters at all yet

        if target == self._current_schedule:
            return

        self._current_schedule = target
        if self._unsub_schedule is not None:
            self._unsub_schedule()
            self._unsub_schedule = None

        if target is None:
            # Not enough observations yet (or no meters) — fall back to
            # HA's own normal built-in fixed-interval polling.
            self.update_interval = UPDATE_INTERVAL
            return

        hour, minute = target
        self.update_interval = None  # disable the built-in interval loop — we drive refreshes ourselves now
        self._unsub_schedule = async_track_time_change(
            self.hass, self._async_scheduled_refresh, hour=hour, minute=minute, second=0
        )
        _LOGGER.info(
            "Brunata adaptive polling: switched to %s at %s based on %d observed reading(s)",
            "once daily" if hour is not None else "every hour",
            f"{hour:02d}:{minute:02d}" if hour is not None else f"xx:{minute:02d}",
            len(observations),
        )

    async def _async_scheduled_refresh(self, _now: datetime) -> None:
        """async_track_time_change's callback — confirmed against HA's own
        helpers/event.py that `action` is dispatched via
        hass.async_run_hass_job, which correctly awaits an async def
        callback (unlike hass.services.async_register's lambda pitfall
        found earlier in this project), so this can be a real coroutine.
        """
        await self.async_request_refresh()

    def async_shutdown_adaptive_schedule(self) -> None:
        """Cancel the adaptive polling listener, if one is registered —
        called from __init__.py's async_unload_entry so a removed/reloaded
        config entry doesn't leave a dangling time-change callback pointing
        at a coordinator that's no longer in use.
        """
        if self._unsub_schedule is not None:
            self._unsub_schedule()
            self._unsub_schedule = None

    async def _async_compute_summaries(self, readings: dict[int, MeterReading]) -> None:
        """Precompute each active meter's monthly + rolling-30-day summary,
        the same calculation websocket_api.py's ws_monthly_summary/
        ws_rolling_summary already do on-demand — reusing the exact same
        statistics.py functions, just run once per hourly poll instead of
        once per dashboard load. Reads from the ledger (see ledger.py), not
        the recorder — no extra Brunata API calls either way.

        Deliberately best-effort per meter (a comparison to `readings`, this
        cycle's fresh data, not `self.data`, which the base
        DataUpdateCoordinator hasn't updated yet at this point in the call).
        """
        naming = build_meter_naming(self.active_meters)
        monthly_summaries: dict[int, dict] = {}
        rolling_summaries: dict[int, dict] = {}

        for meter in self.active_meters:
            meter_id = meter["meterId"]
            naming_entry = naming.get(meter_id)
            if naming_entry is None:
                continue
            object_id, _name = naming_entry
            entity_id = f"sensor.brunata_{object_id}"
            allocation_unit = meter["allocationUnit"]
            reading = readings.get(meter_id)
            scale = reading.scale if reading else None

            monthly = await statistics.async_get_monthly_summary(
                self.hass, meter_id, allocation_unit=allocation_unit,
                scale=scale, entity_id=entity_id,
            )
            monthly["scale"] = scale
            monthly_summaries[meter_id] = monthly

            rolling = await statistics.async_get_rolling_summary(
                self.hass, meter_id, allocation_unit=allocation_unit,
                scale=scale, entity_id=entity_id,
            )
            rolling["scale"] = scale
            rolling_summaries[meter_id] = rolling

        self.monthly_summaries = monthly_summaries
        self.rolling_summaries = rolling_summaries

    async def async_import_history_if_needed(self) -> None:
        """One-time backfill from each meter's mountingDate to first setup —
        both into HA's recorder (CONF_HISTORY_IMPORTED, feeds the Energy
        dashboard/native HA views) AND into the integration-owned ledger
        (CONF_LEDGER_BACKFILLED, feeds the dashboard card — see ledger.py).

        The two flags are tracked separately and checked independently:
        an existing install upgrading to include the ledger feature already
        has CONF_HISTORY_IMPORTED set (so its recorder backfill must NOT
        re-run), but still needs CONF_LEDGER_BACKFILLED done — so this
        fetches Brunata's raw history ONCE and feeds whichever of the two
        backfills still needs it, rather than triggering a second,
        redundant full history re-fetch just for the ledger. Never runs
        either again once both flags are set — ongoing data comes from the
        regular hourly meteroverview polling above.
        """
        needs_recorder_backfill = not self.entry.data.get(CONF_HISTORY_IMPORTED)
        needs_ledger_backfill = not self.entry.data.get(CONF_LEDGER_BACKFILLED)
        if not needs_recorder_backfill and not needs_ledger_backfill:
            return

        naming = build_meter_naming(self.active_meters)
        meters_by_id = {m["meterId"]: m for m in self.active_meters}
        results = await fetch_all_meter_history(self.client)

        for result in results:
            meter = meters_by_id.get(result.meter_id)
            naming_entry = naming.get(result.meter_id)
            if meter is None or naming_entry is None:
                _LOGGER.warning(
                    "History fetched for meter %s but it's not in the current "
                    "active meter list — skipping statistics import",
                    result.meter_id,
                )
                continue

            allocation_unit = meter["allocationUnit"]
            object_id, name = naming_entry
            reading = self.data.get(result.meter_id) if self.data else None
            scale = reading.scale if reading else None

            if needs_recorder_backfill:
                # Raw, pre-conversion export for manual cross-checking
                # against Brunata's own portal — see debug_export.py. Only
                # ever runs here (the one-time backfill), never on regular
                # polling updates.
                await debug_export.async_export_meter_debug_json(
                    self.hass, meter, scale, result.points
                )
                await statistics.async_import_meter_history(
                    self.hass,
                    entity_id=f"sensor.brunata_{object_id}",
                    unit_of_measurement=ALLOCATION_UNIT_OF_MEASUREMENT[allocation_unit],
                    name=f"Brunata {name}",
                    points=result.points,
                    scale=scale,
                    allow_physical_reset=allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET,
                )

            if needs_ledger_backfill:
                await ledger.async_backfill_from_points(
                    self.hass, result.meter_id, allocation_unit, result.points
                )

        entry_updates: dict = {}
        if needs_recorder_backfill:
            entry_updates[CONF_HISTORY_IMPORTED] = True
        if needs_ledger_backfill:
            entry_updates[CONF_LEDGER_BACKFILLED] = True
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, **entry_updates}
        )
