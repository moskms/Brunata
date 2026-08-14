"""HA-side wrapper around brunata_client.ledger — the integration-owned,
recorder-independent per-meter consumption history (one JSONL file per
meter, under <config>/brunata_ledger/<meterNo>.jsonl).

Built so the dashboard card's monthly/rolling numbers stop depending on
HA's own recorder "sum" statistics column, whose reliability this project
already found wanting once (see README's "Kendte begrænsninger" on the
2026-07-13 discontinuity). All file I/O here is blocking and MUST run via
hass.async_add_executor_job — never directly on the event loop.

Files are named by `meterNo` (the physical meter's own serial number, e.g.
"60886237" — the same number shown on Brunata's own "Aflæsninger og
målere" page and printed on the meter itself), NOT by `meterId` (Brunata's
internal API identifier, e.g. 8260593). Confirmed real case this matters
for (2026-08-14): a user trying to cross-check a ledger file against
Brunata's own site has no way to connect "8260593.jsonl" to anything they
can actually see — `meterNo` is directly recognizable. `meterId` is still
what's passed to Brunata's own history endpoint (see
async_backfill_gap_from_brunata/async_find_and_heal_all_gaps below, which
take both) — only the ON-DISK FILENAME changed, not the API contract.
`meterNo` is confirmed present on every MeterReading (see models.py) and
every active_meters entry (metersforconsumer's own `meterNo` field), so no
extra Brunata API call is needed to resolve it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from homeassistant.core import HomeAssistant

from datetime import timedelta

from .brunata_client.history import parse_brunata_datetime
from .brunata_client.ledger import encode_line, find_gaps, parse_lines, trim_to_max_age

_LOGGER = logging.getLogger(__name__)

_LEDGER_DIR = "brunata_ledger"
# Same 3-year retention convention as statistics.py's _MAX_MONTHLY_HISTORY.
_MAX_AGE_DAYS = 3 * 365
# Same threshold as coordinator.py's per-poll trailing-edge check — kept as
# one shared default so "what counts as a gap" means the same thing
# whether it's caught automatically at the tail or found retroactively
# anywhere in the history by async_find_and_heal_all_gaps below. MUST stay
# well above the longest gap normal meter reporting produces (water meters
# report 2-4x/day, so up to ~24h between two real readings is routine, not
# a fault) — confirmed the hard way (2026-08-14) when 6h caused a full
# history scan to misread ~3 years of ordinary polling gaps as damage and
# re-fetch nearly all of it from Brunata in one button press.
DEFAULT_GAP_THRESHOLD = timedelta(hours=36)


def _ledger_path(hass: HomeAssistant, meter_no: str) -> str:
    return os.path.join(hass.config.config_dir, _LEDGER_DIR, f"{meter_no}.jsonl")


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
    """Atomic overwrite: write to a sibling `.tmp` file first, then
    os.replace() it into place. Every call rewrites the WHOLE ledger file
    (see async_append_entry's docstring), so a crash/power loss mid-write
    without this would risk corrupting/truncating the meter's entire
    history, not just the newest entry. os.replace() is documented as
    atomic on both POSIX and Windows, so a crash can only ever leave a
    stray, harmless .tmp file behind — never a half-written real file.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    os.replace(tmp_path, path)


def _dedupe(entries: list[tuple[datetime, int]]) -> list[tuple[datetime, int]]:
    """Drop exact (timestamp, value) duplicates, keeping first-seen order.

    Confirmed real case this exists for (2026-08-14): the pre-36h gap
    threshold caused async_find_and_heal_all_gaps to re-fetch overlapping
    windows for what turned out to be ordinary polling gaps, and neither
    async_append_entry nor async_backfill_from_points deduplicated before
    writing — so the same real (ts, value) reading ended up written 3-6x in
    a row in the actual on-disk file. Harmless to computation (
    _resample_to_last_per_period already collapses same-period rows down to
    the last one), but wasteful and confusing to anyone reading the file
    directly. `dict.fromkeys` over hashable (ts, value) tuples preserves
    order (guaranteed since Python 3.7) while dropping repeats.
    """
    return list(dict.fromkeys(entries))


async def async_read_ledger(hass: HomeAssistant, meter_no: str) -> list[tuple[datetime, int]]:
    """All (timestamp, value) entries currently stored for this meter,
    chronologically sorted. Empty if the ledger file doesn't exist yet
    (e.g. before this meter's first poll/backfill has ever completed).
    """
    path = _ledger_path(hass, meter_no)
    lines = await hass.async_add_executor_job(_read_lines, path)
    entries = parse_lines(lines)
    entries.sort(key=lambda entry: entry[0])
    return entries


async def async_append_entry(
    hass: HomeAssistant, meter_no: str, allocation_unit: str, ts: datetime, raw_value: float
) -> None:
    """Append one new poll's reading, then re-enforce the 3-year retention
    cap. One line per actual poll — deliberately NOT deduplicated/bucketed
    to one per day, since water meters report several times a day and each
    is a real, independent data point worth keeping at full resolution.
    (Exact repeat (ts, value) pairs ARE still collapsed via _dedupe — see
    its docstring — that's cleanup of accidental repeats, not the
    once-per-day bucketing this docstring is disclaiming.)

    Reads, trims, and rewrites the whole file on every call rather than a
    pure O(1) append: deliberately simple, since even 3 years of hourly-ish
    entries is a small file (low hundreds of KB) — cheap enough to redo on
    every poll instead of needing a separate periodic sweep, and this way
    the retention cap is enforced unconditionally on every single write.
    """
    path = _ledger_path(hass, meter_no)
    existing_lines = await hass.async_add_executor_job(_read_lines, path)
    entries = parse_lines(existing_lines)
    entries.append((ts, _to_raw_int(raw_value, allocation_unit)))
    entries.sort(key=lambda entry: entry[0])
    entries = _dedupe(entries)
    entries = trim_to_max_age(entries, ts, _MAX_AGE_DAYS)
    new_lines = [encode_line(entry_ts, entry_value) for entry_ts, entry_value in entries]
    await hass.async_add_executor_job(_write_lines, path, new_lines)


async def async_backfill_gap_from_brunata(
    hass: HomeAssistant,
    client,
    meter_id: int,
    meter_no: str,
    allocation_unit: str,
    start: datetime,
    end: datetime,
) -> int:
    """Fetch this meter's real Brunata history for [start, end] and merge it
    into the ledger — shared by both coordinator.py's automatic gap-healing
    (a new poll's reading landing suspiciously far after the last known
    ledger entry, confirmed real case: an unattended ConfigEntryAuthFailed
    period, 2026-08-12/13) and the manual brunata.backfill_ledger_gap
    service, so the same tested logic backs both the "notice it yourself"
    and "the integration notices for you" paths.

    Takes both `meter_id` (Brunata's own history endpoint is keyed by it —
    see fetch_meter_history) and `meter_no` (the ledger file on disk is
    named by it instead — see module docstring) since the two numbers are
    used for two different purposes here.

    Deliberately scoped to [start, end] rather than the whole meter history:
    the point isn't to re-run the one-time full backfill, just to patch a
    specific hole — reusing history.py's fetch_meter_history, which already
    has the confirmed-safe 600-item-limit chunking built in regardless of
    how wide the gap turns out to be.

    Returns the number of history points fetched (0 if Brunata's history has
    nothing for this window — e.g. the meter genuinely wasn't transmitting,
    as opposed to us just not asking).
    """
    from .brunata_client.history import fetch_meter_history

    result = await fetch_meter_history(client, meter_id, start, until=end)
    await async_backfill_from_points(hass, meter_no, allocation_unit, result.points)
    return len(result.points)


async def async_find_and_heal_all_gaps(
    hass: HomeAssistant,
    client,
    meter_id: int,
    meter_no: str,
    allocation_unit: str,
    threshold: timedelta | None = None,
) -> list[tuple[datetime, datetime, int]]:
    """Scan this meter's ENTIRE ledger history for gaps — not just the tail
    — and backfill every one found from Brunata's own history endpoint.

    Confirmed real limitation this fixes (2026-08-12/13): coordinator.py's
    per-poll check (async_append_entry's caller) only ever compares a fresh
    reading against the ledger's own last entry, so it can only catch a gap
    while it's still the newest thing in the file. Once even one later poll
    succeeds, that gap is buried mid-history and permanently invisible to
    that check — this function has no such blind spot, since it uses
    brunata_client.ledger.find_gaps() over the whole, already-sorted
    history instead of just the last entry.

    Each gap found is backfilled via async_backfill_gap_from_brunata before
    moving on to the next, so a later gap's fetch always sees the
    now-more-complete ledger from healing the earlier one first.

    Returns [(gap_start, gap_end, points_backfilled), ...] for every gap
    found — an empty list means "scanned, found nothing", a real, distinct,
    reassuring outcome callers (the manual button/service) should report
    just as clearly as finding something.
    """
    threshold = threshold or DEFAULT_GAP_THRESHOLD
    entries = await async_read_ledger(hass, meter_no)
    gaps = find_gaps(entries, threshold)

    healed: list[tuple[datetime, datetime, int]] = []
    for gap_start, gap_end in gaps:
        count = await async_backfill_gap_from_brunata(
            hass, client, meter_id, meter_no, allocation_unit, gap_start, gap_end
        )
        healed.append((gap_start, gap_end, count))
    return healed


async def async_backfill_from_points(
    hass: HomeAssistant, meter_no: str, allocation_unit: str, points: list[dict]
) -> None:
    """One-time seed of a meter's ledger from Brunata's own raw metervalues
    history — the SAME `points` already fetched for the one-time recorder
    backfill (see coordinator.py's async_import_history_if_needed), reused
    here rather than triggering a second, redundant full history re-fetch
    from Brunata. Deliberately sourced from Brunata's raw history endpoint,
    not HA's recorder, so the ledger's own history never inherits whatever
    the recorder's own "sum" column might have gotten wrong.

    Safe to call even if the ledger already has some entries (e.g. a few
    live polls happened before this ran) — merges, re-sorts, and
    deduplicates exact (ts, value) repeats (see _dedupe) rather than
    assuming an empty file, though callers should still gate this behind a
    one-time completion flag to avoid needlessly re-running it every setup.
    """
    if not points:
        return

    new_entries = [
        (parse_brunata_datetime(point["readingDate"]), _to_raw_int(point["value"], allocation_unit))
        for point in points
    ]

    path = _ledger_path(hass, meter_no)
    existing_lines = await hass.async_add_executor_job(_read_lines, path)
    combined = parse_lines(existing_lines) + new_entries
    combined.sort(key=lambda entry: entry[0])
    combined = _dedupe(combined)
    if combined:
        combined = trim_to_max_age(combined, combined[-1][0], _MAX_AGE_DAYS)
    new_lines = [encode_line(ts, value) for ts, value in combined]
    await hass.async_add_executor_job(_write_lines, path, new_lines)


def _rename_if_needed(old_path: str, new_path: str) -> bool:
    """Blocking rename of one ledger file from its old (meterId-named) path
    to its new (meterNo-named) path. Returns True if a rename actually
    happened. Never overwrites an existing new_path (defensive: if both
    somehow already exist, leave the old one alone rather than risk
    silently discarding either file's history) and is a no-op if old_path
    doesn't exist (nothing to migrate for this meter, e.g. a fresh setup
    that never had the old naming).
    """
    if not os.path.exists(old_path):
        return False
    if os.path.exists(new_path):
        _LOGGER.warning(
            "[BRUNATA LEDGER MIGRATION] both %s and %s exist — leaving both in "
            "place rather than guessing which to keep; investigate manually",
            old_path, new_path,
        )
        return False
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    os.rename(old_path, new_path)
    return True


async def async_migrate_ledger_filenames(hass: HomeAssistant, active_meters: list[dict]) -> int:
    """One-time migration: rename every existing <meterId>.jsonl to
    <meterNo>.jsonl (see module docstring for why). Meant to be called once
    per config entry, gated by CONF_LEDGER_FILENAMES_MIGRATED in
    coordinator.py — same one-time-flag pattern as
    CONF_HISTORY_IMPORTED/CONF_LEDGER_BACKFILLED, so it doesn't re-check the
    filesystem on every single poll.

    Returns the number of files actually renamed (0 is a normal, common
    outcome — e.g. a fresh install that was never on the old naming scheme
    to begin with).
    """
    migrated = 0
    for meter in active_meters:
        meter_id = meter.get("meterId")
        meter_no = meter.get("meterNo")
        if meter_id is None or meter_no is None:
            continue
        old_path = _ledger_path(hass, str(meter_id))
        new_path = _ledger_path(hass, str(meter_no))
        if old_path == new_path:
            continue
        renamed = await hass.async_add_executor_job(_rename_if_needed, old_path, new_path)
        if renamed:
            migrated += 1
            _LOGGER.info(
                "[BRUNATA LEDGER MIGRATION] renamed ledger file for meter %s: "
                "%s.jsonl -> %s.jsonl", meter_id, meter_id, meter_no,
            )
    return migrated
