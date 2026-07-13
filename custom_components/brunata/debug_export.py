"""Debug export of raw metervalues history, for manual verification.

Writes the exact, unconverted points Brunata returned from
/consumer/meters/{meterId}/metervalues — before hour-bucketing, scale
conversion, or reset compensation (see statistics.py) — so they can be
compared directly against Brunata's own "Aflæsninger og målere" page and
against what HA ends up showing, without trusting this integration's own
math. Only ever triggered from coordinator.py's async_import_history_if_needed()
(the one-time backfill), never during regular polling.

Written to config/brunata_debug/ — NOT www/, which is served publicly over
HTTP by the frontend static path registered in __init__.py. These files
contain personal consumption data.
"""

from __future__ import annotations

import json
import os

from homeassistant.core import HomeAssistant

from .brunata_client.history import parse_brunata_datetime


def _write_meter_debug_file(
    config_dir: str, meter: dict, scale: float | None, points: list[dict]
) -> None:
    debug_dir = os.path.join(config_dir, "brunata_debug")
    os.makedirs(debug_dir, exist_ok=True)

    readings = sorted(
        (
            {"reading_date": point["readingDate"], "raw_value": point["value"]}
            for point in points
        ),
        key=lambda r: parse_brunata_datetime(r["reading_date"]),
    )

    payload = {
        "meter_id": meter["meterId"],
        "meter_no": meter["meterNo"],
        "placement": meter.get("placement"),
        "allocation_unit": meter["allocationUnit"],
        "scale": scale,
        "readings": readings,
    }

    path = os.path.join(debug_dir, f"{meter['meterId']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def async_export_meter_debug_json(
    hass: HomeAssistant, meter: dict, scale: float | None, points: list[dict]
) -> None:
    """Write config/brunata_debug/{meter_id}.json for one meter.

    `points` must be the raw metervalues points as returned by
    fetch_all_meter_history() — before any conversion — and `scale` the same
    value (or None for non-heat meters) coordinator.py passes to
    statistics.async_import_meter_history() for this meter, so the file
    reflects exactly what was used to build the imported statistics.
    """
    await hass.async_add_executor_job(
        _write_meter_debug_file, hass.config.config_dir, meter, scale, points
    )
