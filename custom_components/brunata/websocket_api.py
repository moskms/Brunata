"""WebSocket API for the Del 3b monthly/daily consumption card.

Pure read layer — no new Brunata API calls. Reads come from HA's recorder
statistics via statistics.py, already populated by the one-time history
backfill (coordinator.py) plus each sensor's own ongoing state_class=
total_increasing auto-compilation.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from . import statistics
from .const import DOMAIN, build_meter_naming


def _all_active_meters(hass: HomeAssistant) -> list[dict]:
    """Every active meter dict across all loaded config entries, deduplicated
    by meterId. Belt-and-suspenders alongside coordinator.py's own dedup —
    this one also protects against the (unusual, but possible if the
    integration was ever set up twice) case of the same physical meter
    showing up under two different coordinators.
    """
    deduped: dict[int, dict] = {}
    for coordinator in hass.data.get(DOMAIN, {}).values():
        for meter in coordinator.active_meters:
            deduped[meter["meterId"]] = meter
    return list(deduped.values())


def _resolve_meter(hass: HomeAssistant, meter_id: int) -> tuple[str, dict] | None:
    """meter_id -> (entity_id, meter dict), or None if not found/not active."""
    meters = _all_active_meters(hass)
    naming = build_meter_naming(meters)
    naming_entry = naming.get(meter_id)
    if naming_entry is None:
        return None
    meter = next(m for m in meters if m["meterId"] == meter_id)
    object_id, _name = naming_entry
    return f"sensor.brunata_{object_id}", meter


def _scale_for_meter(hass: HomeAssistant, meter_id: int) -> float | None:
    """Current cached scale factor for a meter — only set for heat (O)
    meters (see BrunataClient._fetch_scale), None for water meters.
    """
    for coordinator in hass.data.get(DOMAIN, {}).values():
        reading = coordinator.data.get(meter_id) if coordinator.data else None
        if reading is not None:
            return reading.scale
    return None


@websocket_api.websocket_command({vol.Required("type"): "brunata/list_meters"})
@websocket_api.async_response
async def ws_list_meters(hass: HomeAssistant, connection, msg) -> None:
    meters = _all_active_meters(hass)
    naming = build_meter_naming(meters)
    result = [
        {
            "meter_id": meter["meterId"],
            "entity_id": f"sensor.brunata_{naming[meter['meterId']][0]}",
            "name": naming[meter["meterId"]][1],
            "allocation_unit": meter["allocationUnit"],
        }
        for meter in meters
    ]
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "brunata/monthly_summary",
        vol.Required("meter_id"): int,
        vol.Optional("year"): int,
    }
)
@websocket_api.async_response
async def ws_monthly_summary(hass: HomeAssistant, connection, msg) -> None:
    meter_id = msg["meter_id"]
    resolved = _resolve_meter(hass, meter_id)
    if resolved is None:
        connection.send_error(msg["id"], "not_found", f"Unknown meter_id {meter_id}")
        return
    entity_id, meter = resolved
    result = await statistics.async_get_monthly_summary(
        hass,
        entity_id,
        msg.get("year"),
        mounting_date=meter.get("mountingDate"),
        allocation_unit=meter.get("allocationUnit"),
    )
    # Included so the frontend card can show heat consumption in raw
    # "enheder" instead of kWh in the monthly table only (None for water
    # meters, which have no scale factor).
    result["scale"] = _scale_for_meter(hass, meter_id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "brunata/daily_breakdown",
        vol.Required("meter_id"): int,
        vol.Required("year"): int,
        vol.Required("month"): int,
    }
)
@websocket_api.async_response
async def ws_daily_breakdown(hass: HomeAssistant, connection, msg) -> None:
    resolved = _resolve_meter(hass, msg["meter_id"])
    if resolved is None:
        connection.send_error(msg["id"], "not_found", f"Unknown meter_id {msg['meter_id']}")
        return
    entity_id, meter = resolved
    result = await statistics.async_get_daily_breakdown(
        hass, entity_id, msg["year"], msg["month"], allocation_unit=meter.get("allocationUnit")
    )
    connection.send_result(msg["id"], result)


def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list_meters)
    websocket_api.async_register_command(hass, ws_monthly_summary)
    websocket_api.async_register_command(hass, ws_daily_breakdown)
