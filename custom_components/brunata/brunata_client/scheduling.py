"""Pure adaptive-polling scheduling math — no `homeassistant` import.

Turns a rolling window of observed meter-reading timestamps (Brunata's own
per-meter `telegramDate`, not our own poll time) into a suggested next poll
target: a (hour, minute) to align future polls with Brunata's actual
delivery pattern, instead of an arbitrary fixed interval. Kept HA-independent
(same split as aggregation.py) so it's testable offline and usable from
custom_components/brunata/coordinator.py, which supplies the observation
history and drives the actual HA scheduling.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta


def _round_up(reference: datetime, round_to_minutes: int) -> datetime:
    """`reference`, rounded up to the next `round_to_minutes` boundary —
    always strictly after `reference`, even when `reference` already falls
    exactly on a boundary (so a poll scheduled from an observed median never
    lands exactly at, let alone before, Brunata's typical delivery time).

    Uses real datetime arithmetic (not manual minute/hour modulo math) so
    an hour or day rollover near the top of the hour / midnight is handled
    correctly for free.
    """
    bumped = reference + timedelta(minutes=1)
    remainder = bumped.minute % round_to_minutes
    if remainder:
        bumped += timedelta(minutes=round_to_minutes - remainder)
    return bumped.replace(second=0, microsecond=0)


def compute_next_poll_target(
    observations: list[datetime],
    min_observations: int = 5,
    round_to_minutes: int = 5,
    daily: bool = False,
) -> tuple[int | None, int] | None:
    """Suggest the next adaptive poll target from a meter's (or pooled
    meters') observed reading timestamps.

    `observations` should be Brunata's own reading timestamps (telegramDate),
    not our own poll times — this is what lets the true delivery pattern be
    learned even while only polling once an hour: Brunata's timestamp
    already encodes the precise moment a reading was taken, independent of
    when we happened to check for it.

    Returns `None` if there are fewer than `min_observations` samples — the
    caller should keep using its existing fixed-interval fallback in that
    case (never guess from too little data).

    Otherwise returns `(hour, minute)`:
    - `daily=False` (water meters — several updates/day): `hour` is always
      `None`, meaning "every hour at this `minute`" — only the minute-of-hour
      component of the observations is used, since water's pattern repeats
      each hour.
    - `daily=True` (heat meters — one update/day): both `hour` and `minute`
      are a real time-of-day, computed from the observations' full
      hour-of-day + minute-of-day.

    In both cases the target is the observed MEDIAN (robust to the odd
    outlier, unlike a mean) rounded UP to the next `round_to_minutes`
    boundary — never down — so a scheduled poll lands shortly AFTER
    Brunata's typical delivery time, not before or exactly at it. Because
    the caller feeds in a bounded rolling window of observations (not the
    full history), the target naturally drifts if Brunata's own delivery
    pattern permanently shifts, without any separate decay logic needed.
    """
    if len(observations) < min_observations:
        return None

    if daily:
        minutes_since_midnight = [obs.hour * 60 + obs.minute for obs in observations]
        median_minutes = statistics.median(minutes_since_midnight)
        reference = datetime(2000, 1, 1) + timedelta(minutes=median_minutes)
        rounded = _round_up(reference, round_to_minutes)
        return rounded.hour, rounded.minute

    median_minute = statistics.median(obs.minute for obs in observations)
    reference = datetime(2000, 1, 1) + timedelta(minutes=median_minute)
    rounded = _round_up(reference, round_to_minutes)
    return None, rounded.minute
