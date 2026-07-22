"""WebSocket API for the Del 3b monthly/daily consumption card.

Pure read layer — no new Brunata API calls. Reads come from HA's recorder
statistics via statistics.py, already populated by the one-time history
backfill (coordinator.py) plus each sensor's own ongoing state_class=
total_increasing auto-compilation.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

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


def _build_summaries_snapshot(hass: HomeAssistant) -> dict:
    """{meter_id (str) -> {"monthly_summary": ..., "rolling_summary": ...}}
    for every active meter that already has a precomputed summary —
    coordinator.py's _async_compute_summaries populates these once per
    hourly poll. A meter absent from this snapshot (e.g. right after a
    reload, before the coordinator's first refresh) is simply omitted; the
    card falls back to its existing on-demand ws_monthly_summary/
    ws_rolling_summary calls for that meter in that case.

    Keys are strings (not int meter_id) because this dict is sent to the
    frontend as a WS message payload, which round-trips through JSON —
    keeping this explicit rather than relying on int-keyed dict -> JSON
    stringification happening implicitly.
    """
    meters = _all_active_meters(hass)
    naming = build_meter_naming(meters)
    coordinators = list(hass.data.get(DOMAIN, {}).values())

    result: dict[str, dict] = {}
    for meter in meters:
        meter_id = meter["meterId"]
        if naming.get(meter_id) is None:
            continue
        for coordinator in coordinators:
            monthly = coordinator.monthly_summaries.get(meter_id)
            if monthly is None:
                continue
            result[str(meter_id)] = {
                "monthly_summary": monthly,
                "rolling_summary": coordinator.rolling_summaries.get(meter_id),
            }
            break
    return result


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
    scale = _scale_for_meter(hass, meter_id)
    result = await statistics.async_get_monthly_summary(
        hass,
        meter_id,
        msg.get("year"),
        allocation_unit=meter.get("allocationUnit"),
        scale=scale,
        entity_id=entity_id,
    )
    # Included so the frontend card can show heat consumption in raw
    # "enheder" instead of kWh in the monthly table only (None for water
    # meters, which have no scale factor).
    result["scale"] = scale
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
    meter_id = msg["meter_id"]
    resolved = _resolve_meter(hass, meter_id)
    if resolved is None:
        connection.send_error(msg["id"], "not_found", f"Unknown meter_id {meter_id}")
        return
    entity_id, meter = resolved
    result = await statistics.async_get_daily_breakdown(
        hass,
        meter_id,
        msg["year"],
        msg["month"],
        allocation_unit=meter.get("allocationUnit"),
        scale=_scale_for_meter(hass, meter_id),
        entity_id=entity_id,
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "brunata/rolling_summary",
        vol.Required("meter_id"): int,
        vol.Optional("window_days"): int,
    }
)
@websocket_api.async_response
async def ws_rolling_summary(hass: HomeAssistant, connection, msg) -> None:
    meter_id = msg["meter_id"]
    resolved = _resolve_meter(hass, meter_id)
    if resolved is None:
        connection.send_error(msg["id"], "not_found", f"Unknown meter_id {meter_id}")
        return
    entity_id, meter = resolved
    scale = _scale_for_meter(hass, meter_id)
    result = await statistics.async_get_rolling_summary(
        hass,
        meter_id,
        allocation_unit=meter.get("allocationUnit"),
        scale=scale,
        entity_id=entity_id,
        window_days=msg.get("window_days", 30),
    )
    # Same reasoning as ws_monthly_summary: lets the frontend show heat's
    # rolling total in raw "enheder" instead of kWh.
    result["scale"] = scale
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "brunata/subscribe_summaries"})
@websocket_api.async_response
async def ws_subscribe_summaries(hass: HomeAssistant, connection, msg) -> None:
    """Push every active meter's precomputed monthly/rolling summary to the
    frontend immediately on subscribe, and again every time any Brunata
    coordinator finishes a poll — reusing the same long-lived,
    auto-reconnecting WS connection the frontend already uses for its own
    entity-state sync (hass.connection.subscribeMessage on the JS side),
    instead of a one-shot RPC that fails outright if the connection happens
    to be mid-reconnect at the exact moment a dashboard loads. That
    connection-timing failure — not slow computation — was the actual bug
    this was built to fix; the numbers were always at most an hour stale
    either way.
    """
    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], _build_summaries_snapshot(hass))
        )

    unsubs = [
        coordinator.async_add_listener(_push)
        for coordinator in hass.data.get(DOMAIN, {}).values()
    ]

    @callback
    def _unsubscribe_all() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = _unsubscribe_all
    connection.send_result(msg["id"])
    _push()  # current snapshot immediately, don't wait for the next poll


def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_list_meters)
    websocket_api.async_register_command(hass, ws_monthly_summary)
    websocket_api.async_register_command(hass, ws_daily_breakdown)
    websocket_api.async_register_command(hass, ws_rolling_summary)
    websocket_api.async_register_command(hass, ws_subscribe_summaries)
