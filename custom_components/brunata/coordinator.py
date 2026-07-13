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

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataLoginError, BrunataSessionError
from .brunata_client.history import fetch_all_meter_history
from .brunata_client.models import MeterReading

from . import debug_export, statistics
from .const import (
    ALLOCATION_UNIT_SLUGS,
    CONF_HISTORY_IMPORTED,
    DOMAIN,
    UPDATE_INTERVAL,
    build_meter_naming,
)

_LOGGER = logging.getLogger(__name__)

# unit code -> HA unit of measurement, matching sensor.py's native units.
_UNIT_OF_MEASUREMENT = {"O": "kWh", "W": "m³", "K": "m³"}


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

        return result

    async def async_import_history_if_needed(self) -> None:
        """One-time backfill from each meter's mountingDate to first setup.

        Never runs again once CONF_HISTORY_IMPORTED is set on the config
        entry — ongoing data comes from the regular hourly meteroverview
        polling above, via the recorder's automatic statistics compilation.
        """
        if self.entry.data.get(CONF_HISTORY_IMPORTED):
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

            # Raw, pre-conversion export for manual cross-checking against
            # Brunata's own portal — see debug_export.py. Only ever runs here
            # (the one-time backfill), never on regular polling updates.
            await debug_export.async_export_meter_debug_json(
                self.hass, meter, scale, result.points
            )

            await statistics.async_import_meter_history(
                self.hass,
                entity_id=f"sensor.brunata_{object_id}",
                unit_of_measurement=_UNIT_OF_MEASUREMENT[allocation_unit],
                name=f"Brunata {name}",
                points=result.points,
                scale=scale,
            )

        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_HISTORY_IMPORTED: True}
        )
