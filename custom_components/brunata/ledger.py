"""HA-side wrapper around brunata_client.ledger — the integration-owned,
recorder-independent per-meter consumption history (one JSONL file per
meter, under <config>/brunata_ledger/<meter_id>.jsonl).

Built so the dashboard card's monthly/rolling numbers stop depending on
HA's own recorder "sum" statistics column, whose reliability this project
already found wanting once (see README's "Kendte begrænsninger" on the
2026-07-13 discontinuity). All file I/O here is blocking and MUST run via
hass.async_add_executor_job — never directly on the event loop.
"""

from __future__ import annotations

import os
from datetime import datetime

from homeassistant.core import HomeAssistant

from .brunata_client.history import parse_brunata_datetime
from .brunata_client.ledger import encode_line, parse_lines, trim_to_max_age

_LEDGER_DIR = "brunata_ledger"
# Same 3-year retention convention as statistics.py's _MAX_MONTHLY_HISTORY.
_MAX_AGE_DAYS = 3 * 365


def _ledger_path(hass: HomeAssistant, meter_id: int) -> str:
    return os.path.join(hass.config.config_dir, _LEDGER_DIR, f"{meter_id}.jsonl")


def _to_raw_int(value: float, allocation_unit: str) -> int:
    """Brunata's own raw meter value -> the integer unit the ledger stores:
    liters (m3 x 1000) for water (W/K), raw pulse count for heat (O).

    Deliberately never scaled to kWh here, even for heat — `reading_value`/
    metervalues' `point["value"]` are already the raw, unscaled figure
    (confirmed in sensor.py/statistics.py's own `_bucket_by_hour`: scale is
    applied AFTER reading the raw value, never before). The ledger stores
    the rawest, most stable representation Brunata gives us; statistics.py
    applies the (separately cached, possibly-refreshed) scale factor at
    READ time instead — so a future change to how scale is looked up never
    requires touching already-stored ledger history.
    """
    if allocation_unit in ("W", "K"):
        return round(value * 1000)
    return round(value)


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.readlines()


def _write_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


async def async_read_ledger(hass: HomeAssistant, meter_id: int) -> list[tuple[datetime, int]]:
    """All (timestamp, value) entries currently stored for this meter,
    chronologically sorted. Empty if the ledger file doesn't exist yet
    (e.g. before this meter's first poll/backfill has ever completed).
    """
    path = _ledger_path(hass, meter_id)
    lines = await hass.async_add_executor_job(_read_lines, path)
    entries = parse_lines(lines)
    entries.sort(key=lambda entry: entry[0])
    return entries


async def async_append_entry(
    hass: HomeAssistant, meter_id: int, allocation_unit: str, ts: datetime, raw_value: float
) -> None:
    """Append one new poll's reading, then re-enforce the 3-year retention
    cap. One line per actual poll — deliberately NOT deduplicated/bucketed
    to one per day, since water meters report several times a day and each
    is a real, independent data point worth keeping at full resolution.

    Reads, trims, and rewrites the whole file on every call rather than a
    pure O(1) append: deliberately simple, since even 3 years of hourly-ish
    entries is a small file (low hundreds of KB) — cheap enough to redo on
    every poll instead of needing a separate periodic sweep, and this way
    the retention cap is enforced unconditionally on every single write.
    """
    path = _ledger_path(hass, meter_id)
    existing_lines = await hass.async_add_executor_job(_read_lines, path)
    entries = parse_lines(existing_lines)
    entries.append((ts, _to_raw_int(raw_value, allocation_unit)))
    entries.sort(key=lambda entry: entry[0])
    entries = trim_to_max_age(entries, ts, _MAX_AGE_DAYS)
    new_lines = [encode_line(entry_ts, entry_value) for entry_ts, entry_value in entries]
    await hass.async_add_executor_job(_write_lines, path, new_lines)


async def async_backfill_from_points(
    hass: HomeAssistant, meter_id: int, allocation_unit: str, points: list[dict]
) -> None:
    """One-time seed of a meter's ledger from Brunata's own raw metervalues
    history — the SAME `points` already fetched for the one-time recorder
    backfill (see coordinator.py's async_import_history_if_needed), reused
    here rather than triggering a second, redundant full history re-fetch
    from Brunata. Deliberately sourced from Brunata's raw history endpoint,
    not HA's recorder, so the ledger's own history never inherits whatever
    the recorder's own "sum" column might have gotten wrong.

    Safe to call even if the ledger already has some entries (e.g. a few
    live polls happened before this ran) — merges and re-sorts rather than
    assuming an empty file, though callers should still gate this behind a
    one-time completion flag to avoid needlessly re-running it every setup.
    """
    if not points:
        return

    new_entries = [
        (parse_brunata_datetime(point["readingDate"]), _to_raw_int(point["value"], allocation_unit))
        for point in points
    ]

    path = _ledger_path(hass, meter_id)
    existing_lines = await hass.async_add_executor_job(_read_lines, path)
    combined = parse_lines(existing_lines) + new_entries
    combined.sort(key=lambda entry: entry[0])
    if combined:
        combined = trim_to_max_age(combined, combined[-1][0], _MAX_AGE_DAYS)
    new_lines = [encode_line(ts, value) for ts, value in combined]
    await hass.async_add_executor_job(_write_lines, path, new_lines)
