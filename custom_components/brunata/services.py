"""DIAGNOSTIC/MANUAL TOOLS — not part of normal integration operation.

Two services, neither ever called by async_setup_entry/the coordinator/the
card — both purely manual, reachable from Developer Tools -> Actions:

`brunata.export_meter_data` — runs statistics_during_period() directly for
one entity_id and date range and writes the full raw recorder result to
/config/brunata_debug_query.json. Deliberately independent of the dashboard
card, the WebSocket API, and our own meter_id -> entity_id resolution logic,
so it can inspect exactly what the recorder returns without any of this
integration's own aggregation logic in the way. Kept permanently (by user's
own choice, 2026-07) because it proved directly useful once already: it's
what surfaced the 2026-07-13 discovery that HA's own compiled "sum" column
can silently diverge from the real "state" value for a statistic, with no
last_reset flag set — the root cause behind statistics.py switching to
state-based, and later ledger-based, period consumption calculation.

`brunata.reconcile_recorder_statistics` — Phase 2 of the ledger work (see
ledger.py/reconcile.py): recomputes and corrects one meter's recorder
long-term statistics from the integration-owned ledger, for fully-elapsed
past hours only. Built and verified, but deliberately NOT wired into any
automatic schedule yet — manual-only until proven safe in practice.
"""

from __future__ import annotations

import json
import logging
import os

import voluptuous as vol
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from . import reconcile
from .const import ALLOCATION_UNIT_OF_MEASUREMENT, DOMAIN, build_meter_naming

_LOGGER = logging.getLogger(__name__)

SERVICE_EXPORT_METER_DATA = "export_meter_data"
SERVICE_RECONCILE_RECORDER_STATISTICS = "reconcile_recorder_statistics"

_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("start_date"): str,
        vol.Required("end_date"): str,
        vol.Optional("period", default="month"): vol.In(
            ["5minute", "hour", "day", "week", "month"]
        ),
    }
)

_RECONCILE_SERVICE_SCHEMA = vol.Schema({vol.Required("entity_id"): str})


def _write_debug_query_file(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


async def _async_handle_export_meter_data(hass: HomeAssistant, call: ServiceCall) -> None:
    entity_id = call.data["entity_id"]
    start_date = call.data["start_date"]
    end_date = call.data["end_date"]
    period = call.data["period"]

    _LOGGER.warning(
        "[BRUNATA EXPORT SERVICE] called with entity_id=%s start_date=%s "
        "end_date=%s period=%s",
        entity_id, start_date, end_date, period,
    )

    path = os.path.join(hass.config.config_dir, "brunata_debug_query.json")

    try:
        start_date_parsed = dt_util.parse_date(start_date)
        end_date_parsed = dt_util.parse_date(end_date)
        if start_date_parsed is None or end_date_parsed is None:
            raise ValueError(
                f"Could not parse start_date/end_date as YYYY-MM-DD: "
                f"start_date={start_date!r} end_date={end_date!r}"
            )
        start_time = dt_util.start_of_local_day(start_date_parsed)
        end_time = dt_util.start_of_local_day(end_date_parsed)

        # Exact same underlying call the card/websocket_api.py uses — just
        # requesting every field type instead of only "sum", and with
        # explicit, user-supplied dates instead of our own derived ones.
        raw = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start_time,
            end_time,
            {entity_id},
            period,
            None,  # units
            {"sum", "state", "min", "max", "mean", "last_reset", "change"},
        )

        rows = raw.get(entity_id, [])
        payload = {
            "called_with": {
                "entity_id": entity_id,
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
            },
            "resolved_start_time": start_time.isoformat(),
            "resolved_end_time": end_time.isoformat(),
            "row_count": len(rows),
            "raw_result": raw,
        }
        await hass.async_add_executor_job(_write_debug_query_file, path, payload)
        _LOGGER.warning(
            "[BRUNATA EXPORT SERVICE] SUCCESS — wrote %d row(s) for %s to %s",
            len(rows), entity_id, path,
        )
    except Exception as err:
        _LOGGER.error(
            "[BRUNATA EXPORT SERVICE] FAILED for entity_id=%s: %s: %s",
            entity_id, type(err).__name__, err, exc_info=True,
        )
        # Still write a file, even on failure, so "the file never appeared"
        # can never again be ambiguous between "service wasn't called" and
        # "service was called but crashed before writing anything".
        try:
            await hass.async_add_executor_job(
                _write_debug_query_file,
                path,
                {
                    "called_with": {
                        "entity_id": entity_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "period": period,
                    },
                    "error": f"{type(err).__name__}: {err}",
                },
            )
        except Exception as write_err:  # pragma: no cover - best-effort only
            _LOGGER.error(
                "[BRUNATA EXPORT SERVICE] Could not even write the error file: %s: %s",
                type(write_err).__name__, write_err,
            )
        raise


def _resolve_meter_by_entity_id(hass: HomeAssistant, entity_id: str) -> dict | None:
    """entity_id -> {"meter_id", "name", "allocation_unit", "scale"}, or None
    if it doesn't match any currently-loaded Brunata meter. Reverse lookup
    of the same build_meter_naming() every other entity_id resolution in
    this integration already relies on.
    """
    for coordinator in hass.data.get(DOMAIN, {}).values():
        naming = build_meter_naming(coordinator.active_meters)
        for meter in coordinator.active_meters:
            meter_id = meter["meterId"]
            naming_entry = naming.get(meter_id)
            if naming_entry is None:
                continue
            object_id, name = naming_entry
            if f"sensor.brunata_{object_id}" != entity_id:
                continue
            reading = coordinator.data.get(meter_id) if coordinator.data else None
            return {
                "meter_id": meter_id,
                "name": name,
                "allocation_unit": meter["allocationUnit"],
                "scale": reading.scale if reading else None,
            }
    return None


async def _async_handle_reconcile_recorder_statistics(hass: HomeAssistant, call: ServiceCall) -> None:
    entity_id = call.data["entity_id"]
    _LOGGER.warning("[BRUNATA RECONCILE SERVICE] called with entity_id=%s", entity_id)

    resolved = _resolve_meter_by_entity_id(hass, entity_id)
    if resolved is None:
        _LOGGER.error(
            "[BRUNATA RECONCILE SERVICE] FAILED: %s is not a currently-loaded "
            "Brunata meter entity",
            entity_id,
        )
        raise HomeAssistantError(f"Unknown Brunata meter entity_id: {entity_id}")

    try:
        count = await reconcile.async_reconcile_from_ledger(
            hass,
            meter_id=resolved["meter_id"],
            entity_id=entity_id,
            name=f"Brunata {resolved['name']}",
            unit_of_measurement=ALLOCATION_UNIT_OF_MEASUREMENT[resolved["allocation_unit"]],
            allocation_unit=resolved["allocation_unit"],
            scale=resolved["scale"],
        )
        _LOGGER.warning(
            "[BRUNATA RECONCILE SERVICE] SUCCESS — corrected %d hourly period(s) "
            "for %s from the ledger",
            count, entity_id,
        )
    except Exception as err:
        _LOGGER.error(
            "[BRUNATA RECONCILE SERVICE] FAILED for entity_id=%s: %s: %s",
            entity_id, type(err).__name__, err, exc_info=True,
        )
        raise


def async_register_services(hass: HomeAssistant) -> None:
    # A plain `lambda call: _async_handle_export_meter_data(hass, call)` here
    # was the confirmed bug: asyncio.iscoroutinefunction() is False for a
    # lambda even though calling it returns a coroutine, so HA's service
    # dispatcher never awaited it — the coroutine was silently created and
    # discarded (RuntimeWarning: coroutine ... was never awaited). An actual
    # `async def` handler is required so HA recognizes and awaits it.
    async def _handle_export_meter_data(call: ServiceCall) -> None:
        await _async_handle_export_meter_data(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_METER_DATA,
        _handle_export_meter_data,
        schema=_SERVICE_SCHEMA,
    )

    # Phase 2, manual-only (see reconcile.py's own module docstring for the
    # full verified-safe-to-call reasoning) — never triggered automatically
    # by the coordinator/polling, only from Developer Tools -> Actions.
    async def _handle_reconcile_recorder_statistics(call: ServiceCall) -> None:
        await _async_handle_reconcile_recorder_statistics(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECONCILE_RECORDER_STATISTICS,
        _handle_reconcile_recorder_statistics,
        schema=_RECONCILE_SERVICE_SCHEMA,
    )
