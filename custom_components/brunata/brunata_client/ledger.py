"""Pure per-meter consumption ledger encoding/parsing — no `homeassistant`
import.

The ledger is an integration-owned, append-only record of every reading
Brunata has ever actually returned for a meter (one line per poll, or per
historical backfill point), independent of Home Assistant's own recorder
"sum" statistics column — built specifically so the dashboard card's numbers
stop depending on that column's reliability (see README's "Kendte
begrænsninger" on the 2026-07-13 recorder-sum discontinuity). Kept
HA-independent (same split as aggregation.py/scheduling.py) so the file
format itself is testable offline; custom_components/brunata/ledger.py wraps
this with the actual file I/O (which must run off the event loop).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta


def encode_line(ts: datetime, value: int) -> str:
    """One ledger entry -> one JSONL line. `ts` must be timezone-aware
    (stored as its own ISO 8601 string, not a Danish dd-mm-yyyy format, to
    avoid any day/month parsing ambiguity later) and `value` is always an
    integer in the meter's own smallest raw unit (liters for water, raw
    pulse count for heat) — never a float, so repeated read/write cycles
    can never introduce floating-point drift into the stored history.
    """
    return json.dumps({"ts": ts.isoformat(), "value": value})


def decode_line(line: str) -> tuple[datetime, int] | None:
    """One JSONL line -> (timestamp, value), or None if the line is empty or
    corrupt (e.g. a partially-written line from an interrupted append) —
    callers should skip a None rather than fail the whole ledger read, since
    one bad line must never take down every other, valid line in the file.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
        return datetime.fromisoformat(obj["ts"]), int(obj["value"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def parse_lines(lines: list[str]) -> list[tuple[datetime, int]]:
    """A ledger file's raw lines -> parsed (timestamp, value) entries,
    silently dropping any that don't decode. Order is NOT guaranteed to be
    chronological (though in practice each write only ever appends, so it
    normally already is) — callers that need chronological order should
    sort explicitly.
    """
    parsed = []
    for line in lines:
        decoded = decode_line(line)
        if decoded is not None:
            parsed.append(decoded)
    return parsed


def trim_to_max_age(
    entries: list[tuple[datetime, int]], as_of: datetime, max_age_days: int
) -> list[tuple[datetime, int]]:
    """Drop entries older than `max_age_days` before `as_of` — same
    retention convention as statistics.py's mounting-date-aware 3-year cap
    on the monthly dashboard view. Keeps the ledger file from growing
    unbounded across years of polling.
    """
    cutoff = as_of - timedelta(days=max_age_days)
    return [(ts, value) for ts, value in entries if ts >= cutoff]
