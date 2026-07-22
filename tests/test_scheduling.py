from datetime import datetime, timedelta, timezone

import pytest

from brunata_client.scheduling import compute_next_poll_target

_TZ = timezone(timedelta(hours=2))  # arbitrary fixed offset, matches aware Brunata timestamps


def _dt(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=_TZ)


def test_too_few_observations_returns_none():
    # Fallback case (requirement 6): fewer than min_observations samples ->
    # caller must keep using its fixed-interval default, never guess.
    observations = [_dt(1, 13, 43), _dt(2, 11, 44), _dt(3, 10, 44)]
    assert compute_next_poll_target(observations, min_observations=5) is None


def test_exactly_min_observations_no_longer_none():
    observations = [_dt(d, 12, 43) for d in range(1, 6)]
    assert compute_next_poll_target(observations, min_observations=5) is not None


def test_water_pattern_hourly_minute_only_ignores_hour():
    # Mirrors the real observed cluster: 13:43, 11:44, 10:44, 09:43, 08:43,
    # 07:45 -- several updates/day, tight minute-of-hour cluster around
    # 43-45, at different hours each time. hour must always be None (=
    # "every hour at this minute"), and the minute must be rounded UP to
    # the next 5-minute mark from the median, never down.
    observations = [
        _dt(1, 13, 43), _dt(1, 11, 44), _dt(1, 10, 44),
        _dt(1, 9, 43), _dt(1, 8, 43), _dt(1, 7, 45),
    ]
    hour, minute = compute_next_poll_target(observations, min_observations=5, daily=False)
    assert hour is None
    # median minute of [43, 44, 44, 43, 43, 45] = 43.5 -> round up -> 45
    assert minute == 45


def test_water_pattern_never_rounds_down():
    # Every single observation already lands exactly on a 5-minute mark
    # (:40) -- the result must still be strictly AFTER it (:45), not :40
    # itself, per requirement 3 ("kort tid EFTER dette mønster").
    observations = [_dt(d, 10, 40) for d in range(1, 6)]
    hour, minute = compute_next_poll_target(observations, min_observations=5, daily=False)
    assert hour is None
    assert minute == 45


def test_heat_pattern_daily_uses_full_time_of_day():
    # Mirrors the real observed heat-meter cluster: 02:41-02:44, once/day.
    observations = [
        _dt(1, 2, 41), _dt(2, 2, 42), _dt(3, 2, 43),
        _dt(4, 2, 44), _dt(5, 2, 41), _dt(6, 2, 43),
    ]
    hour, minute = compute_next_poll_target(observations, min_observations=5, daily=True)
    # median minutes-since-midnight of [161,162,163,164,161,163] = 162.5
    # -> 02:42.5 -> round up -> 02:45
    assert hour == 2
    assert minute == 45


def test_heat_pattern_hour_rollover_handled_by_datetime_arithmetic():
    # Median lands right at the top of the hour (:58-:59) -- rounding up
    # must correctly roll over into the next hour, not wrap the minute back
    # to something nonsensical.
    observations = [_dt(d, 1, 58) for d in range(1, 6)]
    hour, minute = compute_next_poll_target(observations, min_observations=5, daily=True)
    assert (hour, minute) == (2, 0)


def test_mixed_account_water_and_heat_pooled_separately():
    # Simulates coordinator.py's own pooling: water (W+K) observations are
    # combined into one set for the frequent/hourly target, heat (O)
    # observations are computed completely separately for its own daily
    # target -- the two must not influence each other's median at all.
    water_observations = [
        _dt(1, 13, 43), _dt(1, 11, 44), _dt(1, 10, 44),
        _dt(1, 9, 43), _dt(1, 8, 43),
    ]
    heat_observations = [_dt(d, 2, 42) for d in range(1, 6)]

    water_hour, water_minute = compute_next_poll_target(water_observations, min_observations=5, daily=False)
    heat_hour, heat_minute = compute_next_poll_target(heat_observations, min_observations=5, daily=True)

    assert water_hour is None
    assert water_minute == 45  # median 43 -> round up -> 45
    assert heat_hour == 2
    assert heat_minute == 45  # median 42 -> round up -> 45


def test_median_is_robust_to_a_single_outlier():
    # One anomalously late reading shouldn't drag the target far off --
    # median (not mean) keeps it anchored to the real cluster.
    observations = [_dt(1, 10, 43), _dt(2, 10, 44), _dt(3, 10, 43), _dt(4, 10, 44), _dt(5, 10, 59)]
    hour, minute = compute_next_poll_target(observations, min_observations=5, daily=False)
    assert hour is None
    assert minute == 45  # median of [43,44,43,44,59] = 44 -> round up -> 45, not skewed toward 59
