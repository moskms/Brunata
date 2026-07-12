"""Adaptive historical-import chunking for /consumer/meters/{meterId}/metervalues.

Pulled out of custom_components/brunata/coordinator.py so it has no dependency
on `homeassistant` — this makes it usable both from the HA coordinator and
from standalone scripts (see scripts/history_smoke_test.py).

See docs/api-reference.md for the confirmed 600-item limit and the reasoning
behind starting wide and only shrinking on `limited: true`.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .client import BrunataClient
from .exceptions import BrunataDataError

# /consumer/meters/{meterId}/metervalues returns at most 600 points per call
# and silently drops everything older than the 600 newest when the window
# holds more (limited=True). Older periods are far sparser than recent ones,
# so we start wide and only shrink when the server actually reports
# limited=True, instead of guessing a fixed chunk size.
INITIAL_HISTORY_INTERVAL = timedelta(days=365)
MIN_HISTORY_INTERVAL = timedelta(hours=1)

# The three confirmed, real consumption meter types (see docs/api-reference.md
# and copilot-instructions-del2.md). "P" (puls/pulse channel) meters share a
# meterNo with one of these but aren't a reportable consumption type on their
# own, so history import is scoped to O/W/K only.
_HISTORY_ALLOCATION_UNITS = {"O", "W", "K"}


def parse_brunata_datetime(value: str) -> datetime:
    """Parse a Brunata timestamp/date string (e.g. "2026-07-12T19:42:00+02:00"
    or a bare "2025-03-25" mountingDate) into an aware datetime.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


@dataclass
class FetchAttempt:
    """One HTTP call to metervalues, whether it succeeded or failed.

    Passed to the optional `on_attempt` callback so callers (e.g. a smoke
    test) can report progress/anomalies as it happens, instead of only
    seeing a summary once everything is done.
    """

    timestamp: datetime
    meter_id: int
    start: datetime
    end: datetime
    elapsed_seconds: float
    status_code: int | None  # None if the request raised before getting a response
    num_points: int | None  # None on error
    limited: bool | None  # None on error
    error: str | None  # None on success


OnAttempt = Callable[[FetchAttempt], None]


class MeterHistoryResult:
    """Points fetched for one meter, plus how many API calls it took."""

    def __init__(self, meter_id: int, points: list[dict], api_calls: int) -> None:
        self.meter_id = meter_id
        self.points = points
        self.api_calls = api_calls


async def fetch_meter_history(
    client: BrunataClient,
    meter_id: int,
    start: datetime,
    until: datetime | None = None,
    on_attempt: OnAttempt | None = None,
) -> MeterHistoryResult:
    """Fetch a meter's full readings from `start` to `until` (default: now).

    1. Start with a large window (INITIAL_HISTORY_INTERVAL).
    2. Call metervalues for [cursor, cursor + window).
    3. If the response is `limited`, halve the window and retry the SAME
       cursor (not the whole period from scratch).
    4. Once a call comes back unlimited, keep its points, advance the cursor
       to just after the last point actually returned, and repeat (trying to
       grow the window back up) until `until` is reached.

    `on_attempt`, if given, is called after every single HTTP call (success
    or failure) with a `FetchAttempt` describing it.
    """
    now = until or datetime.now().astimezone()
    cursor = start
    window = INITIAL_HISTORY_INTERVAL
    points: list[dict] = []
    api_calls = 0

    while cursor < now:
        end = min(cursor + window, now)
        timestamp = datetime.now()
        call_start = time.monotonic()
        try:
            payload = await client.fetch_meter_values(meter_id, cursor, end)
        except BrunataDataError as exc:
            elapsed = time.monotonic() - call_start
            api_calls += 1
            if on_attempt:
                on_attempt(
                    FetchAttempt(
                        timestamp=timestamp,
                        meter_id=meter_id,
                        start=cursor,
                        end=end,
                        elapsed_seconds=elapsed,
                        status_code=getattr(exc, "status_code", None),
                        num_points=None,
                        limited=None,
                        error=str(exc),
                    )
                )
            raise

        elapsed = time.monotonic() - call_start
        api_calls += 1
        limited = bool(payload.get("limited"))
        values = payload.get("meterValues", [])

        if on_attempt:
            on_attempt(
                FetchAttempt(
                    timestamp=timestamp,
                    meter_id=meter_id,
                    start=cursor,
                    end=end,
                    elapsed_seconds=elapsed,
                    status_code=200,
                    num_points=len(values),
                    limited=limited,
                    error=None,
                )
            )

        if limited:
            window /= 2
            if window < MIN_HISTORY_INTERVAL:
                raise BrunataDataError(
                    f"meter {meter_id}: still limited at a {window} window "
                    f"between {cursor} and {end} — more than 600 readings "
                    "in under an hour is unexpected per docs/api-reference.md"
                )
            continue  # retry the same cursor with the halved window

        points.extend(values)

        # limited=False is the server's guarantee that [cursor, end) is fully
        # covered (docs/api-reference.md) — so `end` is always safe to advance
        # to, regardless of the dates on the returned points themselves. The
        # previous version derived the new cursor from max(readingDate) in the
        # response instead: if the window had no data near its own tail (e.g.
        # a real gap, or "now" not reached yet), that computed cursor could
        # land at or before the old cursor, causing the exact same window to
        # be requested forever — this is the infinite loop seen on meter
        # 8260593. `end` is guaranteed > cursor here (window > 0, cursor < now).
        cursor = end

        # Data density varies a lot over the years (docs/api-reference.md);
        # try growing the window back up for the next chunk instead of
        # staying stuck at a small size picked for a denser period.
        window = min(window * 2, INITIAL_HISTORY_INTERVAL)

    return MeterHistoryResult(meter_id=meter_id, points=points, api_calls=api_calls)


async def fetch_all_meter_history(
    client: BrunataClient,
    on_attempt: OnAttempt | None = None,
) -> list[MeterHistoryResult]:
    """Fetch full history for the three real consumption meters (heat, hot
    water, cold water) from each meter's mountingDate to now (or to
    dismountedDate for a meter no longer in use).
    """
    meters = await client.fetch_meters_for_consumer()
    now = datetime.now().astimezone()
    results: list[MeterHistoryResult] = []

    for meter in meters:
        if meter.get("allocationUnit") not in _HISTORY_ALLOCATION_UNITS:
            continue
        start = parse_brunata_datetime(meter["mountingDate"])
        dismounted = meter.get("dismountedDate")
        until = parse_brunata_datetime(dismounted) if dismounted else now
        results.append(
            await fetch_meter_history(
                client, meter["meterId"], start, until, on_attempt=on_attempt
            )
        )

    return results
