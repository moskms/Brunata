from datetime import date, datetime, timedelta, timezone

import pytest

from brunata_client.aggregation import (
    compute_available_years,
    compute_daily_breakdown,
    compute_monthly_summary_for_year,
    compute_period_deltas,
    compute_reset_compensated_sums,
    compute_rolling_window_total,
)


def _dt(year: int, month: int, day: int = 1) -> datetime:
    return datetime(year, month, day)


def test_compute_period_deltas_basic():
    rows = [
        {"start": _dt(2026, 5), "sum": 100.0},
        {"start": _dt(2026, 6), "sum": 130.0},
        {"start": _dt(2026, 7), "sum": 145.0},
    ]
    deltas = compute_period_deltas(rows)
    assert deltas == [
        {"start": _dt(2026, 6), "consumption": 30.0},
        {"start": _dt(2026, 7), "consumption": 15.0},
    ]


def test_compute_period_deltas_empty_and_single_row():
    assert compute_period_deltas([]) == []
    assert compute_period_deltas([{"start": _dt(2026, 1), "sum": 5.0}]) == []


def test_compute_period_deltas_negative_delta_is_none_not_negative():
    # Meter counter reset (confirmed real case: heat meter cumulative value
    # fell to ~0 around June/July 2026) — must never surface as a negative
    # consumption number.
    rows = [
        {"start": _dt(2026, 6), "sum": 6200.0},
        {"start": _dt(2026, 7), "sum": 51.0},  # reset mid-period
    ]
    deltas = compute_period_deltas(rows)
    assert deltas == [{"start": _dt(2026, 7), "consumption": None}]


def test_compute_period_deltas_zero_delta_is_zero_not_none():
    rows = [
        {"start": _dt(2026, 6), "sum": 100.0},
        {"start": _dt(2026, 7), "sum": 100.0},
    ]
    deltas = compute_period_deltas(rows)
    assert deltas == [{"start": _dt(2026, 7), "consumption": 0.0}]


def test_compute_monthly_summary_for_year_yoy_percent():
    # July last year = 145 - 130 = 15; July this year = 200 - 170 = 30 -> +100%
    rows = [
        {"start": _dt(2025, 6), "sum": 100.0},
        {"start": _dt(2025, 7), "sum": 115.0},  # 2025-07 consumption = 15
        {"start": _dt(2026, 6), "sum": 170.0},
        {"start": _dt(2026, 7), "sum": 200.0},  # 2026-07 consumption = 30
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    july = next(m for m in summary["months"] if m["month"] == 7)
    assert july == {"month": 7, "consumption": 30.0, "yoy_percent": 100.0}


def test_compute_monthly_summary_for_year_no_prior_year_data_is_none_not_zero():
    # Meter too new — no 2025 data at all, must not guess/extrapolate a %.
    rows = [
        {"start": _dt(2026, 6), "sum": 170.0},
        {"start": _dt(2026, 7), "sum": 200.0},
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    july = next(m for m in summary["months"] if m["month"] == 7)
    assert july == {"month": 7, "consumption": 30.0, "yoy_percent": None}


def test_compute_monthly_summary_for_year_has_all_12_months():
    rows = [
        {"start": _dt(2026, 5), "sum": 100.0},
        {"start": _dt(2026, 6), "sum": 130.0},
        {"start": _dt(2026, 7), "sum": 145.0},
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    assert [m["month"] for m in summary["months"]] == list(range(1, 13))
    # months with no data at all (e.g. January) are None, not omitted/guessed
    january = next(m for m in summary["months"] if m["month"] == 1)
    assert january == {"month": 1, "consumption": None, "yoy_percent": None}


def test_compute_monthly_summary_for_year_total_sums_available_months_only():
    rows = [
        {"start": _dt(2026, 5), "sum": 100.0},
        {"start": _dt(2026, 6), "sum": 130.0},  # June consumption = 30
        {"start": _dt(2026, 7), "sum": 145.0},  # July consumption = 15
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    assert summary["total_consumption"] == pytest.approx(45.0)


def test_compute_monthly_summary_for_year_reset_month_excluded_from_total():
    rows = [
        {"start": _dt(2026, 5), "sum": 100.0},
        {"start": _dt(2026, 6), "sum": 6200.0},  # June consumption = 6100
        {"start": _dt(2026, 7), "sum": 51.0},  # reset — July consumption unknown
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    july = next(m for m in summary["months"] if m["month"] == 7)
    assert july["consumption"] is None
    assert summary["total_consumption"] == pytest.approx(6100.0)


def test_compute_monthly_summary_for_year_no_data_at_all_gives_none_total():
    summary = compute_monthly_summary_for_year([], 2026)
    assert summary["total_consumption"] is None
    assert all(m["consumption"] is None for m in summary["months"])


def test_compute_available_years():
    rows = [
        {"start": _dt(2024, 12), "sum": 50.0},
        {"start": _dt(2025, 6), "sum": 100.0},
        {"start": _dt(2026, 6), "sum": 170.0},
        {"start": _dt(2026, 7), "sum": 200.0},
    ]
    assert compute_available_years(rows) == [2025, 2026]


def test_compute_available_years_excludes_reset_only_year():
    # A year whose only delta is a reset (None) shouldn't appear as "available".
    rows = [
        {"start": _dt(2025, 12), "sum": 6200.0},
        {"start": _dt(2026, 1), "sum": 51.0},  # reset, only 2026 datapoint
    ]
    assert compute_available_years(rows) == []


def test_compute_daily_breakdown():
    rows = [
        {"start": _dt(2026, 7, 11), "sum": 100.0},
        {"start": _dt(2026, 7, 12), "sum": 100.45},
        {"start": _dt(2026, 7, 13), "sum": 101.2},
    ]
    breakdown = compute_daily_breakdown(rows)
    assert breakdown[0]["day"] == 12
    assert breakdown[0]["consumption"] == pytest.approx(0.45)
    assert breakdown[1]["day"] == 13
    assert breakdown[1]["consumption"] == pytest.approx(0.75)


def test_compute_daily_breakdown_reset_is_none():
    rows = [
        {"start": _dt(2026, 7, 11), "sum": 6200.0},
        {"start": _dt(2026, 7, 12), "sum": 51.0},  # reset
    ]
    breakdown = compute_daily_breakdown(rows)
    assert breakdown == [{"day": 12, "consumption": None}]


# ---------------------------------------------------------------------------
# compute_reset_compensated_sums — the _bucket_by_hour reset-compensation fix
# ---------------------------------------------------------------------------


def test_compute_reset_compensated_sums_no_reset_matches_raw_deltas():
    # No reset: compensated sums are just the cumulative deltas, as before.
    values = [100.0, 130.0, 145.0]
    assert compute_reset_compensated_sums(values) == [0.0, 30.0, 45.0]


def test_compute_reset_compensated_sums_across_a_reset_stays_monotonic():
    # Mirrors the real heat meter: climbs to 6200, resets to ~0, climbs again.
    values = [6100.0, 6200.0, 51.0, 75.0]
    sums = compute_reset_compensated_sums(values)
    # Monotonically increasing throughout, unlike the raw values themselves.
    assert all(later >= earlier for earlier, later in zip(sums, sums[1:]))
    # Pre-reset accumulation preserved (100 consumed before the reset)...
    assert sums[1] == pytest.approx(100.0)
    # ...and the post-reset cycle's own consumption (75 - 51 = 24) is added
    # on top, not lost or double-counted.
    assert sums[3] - sums[2] == pytest.approx(24.0)


def test_compute_reset_compensated_sums_small_dip_within_threshold_still_diffs():
    # A small dip (e.g. rounding/telegram noise, not a real reset — within
    # HA's own 90% reset-detection threshold) is still treated as a (small
    # negative) diff contribution, exactly like HA's own live compiler would.
    values = [100.0, 99.5]
    sums = compute_reset_compensated_sums(values)
    assert sums[1] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# compute_monthly_summary_for_year — duplicate-month investigation
#
# By construction (a dict keyed by (year, month), and a fixed range(1, 13)
# output loop) this function cannot produce two entries for the same month
# in its output, no matter what the raw input rows look like. These tests
# feed it exactly the edge cases suspected in practice — rows landing in the
# same month at different times of day, and rows straddling a month/year
# boundary — to demonstrate that directly rather than just asserting it.
# ---------------------------------------------------------------------------


def test_compute_monthly_summary_for_year_same_month_multiple_row_times():
    # Two raw rows both fall in July, at different times of day/month.
    rows = [
        {"start": _dt(2026, 6, 1), "sum": 100.0},
        {"start": datetime(2026, 7, 1, 0, 0), "sum": 130.0},
        {"start": datetime(2026, 7, 15, 12, 0), "sum": 999.0},  # same month, later
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    julys = [m for m in summary["months"] if m["month"] == 7]
    assert len(julys) == 1  # exactly one July entry, never two
    # Uses the chronologically LAST July row for July's own consumption.
    assert julys[0]["consumption"] == pytest.approx(999.0 - 130.0)


def test_compute_monthly_summary_for_year_tz_aware_rows_same_calendar_month():
    # Two tz-aware timestamps that are hours apart but land in the same
    # local calendar month once normalized (as statistics.py's
    # _normalize_rows already does before calling this function) — simulates
    # a suspected timezone-duplicate scenario. Both rows tag as July, so the
    # dict keeps only the chronologically LAST one (30 = 160-130), not the
    # sum of both — exactly one July entry, never two.
    tz = timezone(timedelta(hours=2))
    rows = [
        {"start": datetime(2026, 6, 30, 23, 0, tzinfo=tz), "sum": 100.0},
        {"start": datetime(2026, 7, 1, 0, 0, tzinfo=tz), "sum": 130.0},
        {"start": datetime(2026, 7, 31, 22, 0, tzinfo=tz), "sum": 160.0},
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    julys = [m for m in summary["months"] if m["month"] == 7]
    assert len(julys) == 1
    assert julys[0]["consumption"] == pytest.approx(30.0)  # 160 - 130, last July row wins


# ---------------------------------------------------------------------------
# 2026-07-13 HA recorder "sum"-column discontinuity — state-based fix
#
# Confirmed real incident: HA's own compiled "sum" for
# sensor.brunata_varmt_vand_entre collapsed from 135.99 to 0.006 on
# 2026-07-13, with last_reset staying null (HA itself never flagged a
# reset) — while the raw "state" field kept climbing smoothly across the
# same day, matching the live sensor exactly. statistics.py's
# _normalize_state_rows() now computes period sums from "state" via
# compute_reset_compensated_sums() instead of trusting the recorder's own
# "sum" column, specifically to be immune to this class of bug. These
# tests reproduce the incident's actual state values (day-level, 1-14 July)
# to prove the fix, entirely offline.
# ---------------------------------------------------------------------------


def test_compute_reset_compensated_sums_immune_to_july13_sum_column_glitch():
    # Real "state" values from the 14 daily rows (1-14 July 2026), smoothly
    # climbing throughout — this is the ground truth the live sensor showed,
    # unaffected by whatever the recorder's own "sum" column did.
    states = [
        153.68, 153.71, 153.75, 153.79, 153.82, 153.86, 153.90,
        153.93, 153.95, 153.98, 154.02, 154.05, 154.19, 154.212,
    ]
    sums = compute_reset_compensated_sums(states)

    # Monotonically increasing throughout, including across day 13 — no
    # dip/collapse of the kind HA's own "sum" column exhibited that day.
    assert all(later >= earlier for earlier, later in zip(sums, sums[1:]))

    # Day 13's own consumption (state 154.05 -> 154.19) is a normal, small
    # positive delta — NOT the near-total collapse HA's own "sum" column
    # showed for the same day (135.99 -> 0.006).
    day13_consumption = sums[12] - sums[11]
    assert day13_consumption == pytest.approx(154.19 - 154.05)
    assert day13_consumption > 0

    # Total consumption across the full 14 days matches the simple
    # first-to-last state delta — i.e. nothing was lost or double-counted
    # by (wrongly) treating day 13 as a physical reset.
    assert sums[-1] - sums[0] == pytest.approx(states[-1] - states[0])


def test_compute_daily_breakdown_immune_to_july13_sum_column_glitch():
    # Same incident, run through the actual daily_breakdown row shape
    # (_normalize_state_rows' output: {"start": datetime, "sum": <state,
    # already reset-compensated>}) to prove the full call path — not just
    # the low-level compensation function — produces a correct, continuous
    # July, with no "—" gap and no bogus near-100% single-day spike.
    states = [
        153.68, 153.71, 153.75, 153.79, 153.82, 153.86, 153.90,
        153.93, 153.95, 153.98, 154.02, 154.05, 154.19, 154.212,
    ]
    compensated = compute_reset_compensated_sums(states)
    rows = [
        {"start": _dt(2026, 7, day) if day > 1 else _dt(2026, 6, 30), "sum": value}
        for day, value in zip(range(1, 15), compensated)
    ]
    breakdown = compute_daily_breakdown(rows)

    by_day = {row["day"]: row["consumption"] for row in breakdown}
    # Every day has a real (non-None, non-huge) consumption figure — in
    # particular day 13, the day HA's own "sum" column collapsed on.
    assert all(c is not None for c in by_day.values())
    assert by_day[13] == pytest.approx(154.19 - 154.05)
    assert by_day[13] < 1.0  # nowhere near the ~136 "reset" HA's sum column implied


# ---------------------------------------------------------------------------
# allow_physical_reset / invalid_indices — the general data-validation layer
#
# Independent of, and layered on top of, the physical-reset compensation
# above: for meter types that cannot physically reset (water), any drop is
# rejected as invalid data (frozen at the last known-valid value, zero
# consumption for that period) rather than treated as a reset. This protects
# against ANY future unexplained negative discontinuity in the source data,
# not just the specific 2026-07-13 HA "sum"-column incident — including the
# (worse) hypothetical case where a similar glitch shows up directly in
# "state" itself, which the state-based switch alone wouldn't catch.
# ---------------------------------------------------------------------------


def test_allow_physical_reset_false_rejects_drop_instead_of_compensating():
    # Same shape as the real heat-meter reset case, but for a meter type that
    # is NOT allowed to reset (water): the drop must be rejected, not added
    # as a new cycle.
    values = [6100.0, 6200.0, 51.0, 75.0]
    invalid_indices: list[int] = []
    sums = compute_reset_compensated_sums(
        values, allow_physical_reset=False, invalid_indices=invalid_indices
    )
    # 75.0 is still below 6200*0.9, so it's also rejected as invalid — a
    # meter that's actually reset would need several genuinely climbing
    # readings before one lands back above the 90% threshold again.
    assert invalid_indices == [2, 3]
    # Frozen at the last known-valid value for both rejected periods: zero
    # consumption, not a huge negative delta nor a bogus "reset" addition.
    assert sums[2] == sums[1]
    assert sums[3] == sums[1]


def test_allow_physical_reset_false_true_july13_style_glitch_self_heals():
    # Reproduces the July 13 incident's magnitude directly (135.99 -> 0.006,
    # ~99.99% drop) as if it had occurred in "state" itself, not just HA's
    # own "sum" column — the worst case the previous round's fix (switching
    # to "state") would NOT have caught on its own. With
    # allow_physical_reset=False (water meters), this layer must catch and
    # self-heal it with no manual intervention.
    values = [135.94, 135.99, 0.006, 136.02, 136.07]
    invalid_indices: list[int] = []
    sums = compute_reset_compensated_sums(
        values, allow_physical_reset=False, invalid_indices=invalid_indices
    )
    assert invalid_indices == [2]  # only the glitched reading is rejected
    # No collapse in the compensated running total across the glitch.
    assert all(later >= earlier for earlier, later in zip(sums, sums[1:]))
    # The glitched period itself contributes zero consumption...
    assert sums[2] == sums[1]
    # ...and the very next, genuinely-valid reading resumes a normal, small
    # delta from the correct pre-glitch baseline (136.02 - 135.99 = 0.03),
    # not a ~136 spike from being diffed against the rejected 0.006 value.
    assert sums[3] - sums[2] == pytest.approx(136.02 - 135.99)


def test_allow_physical_reset_true_default_unaffected_by_new_parameter():
    # Default behavior (heat meters, and every existing caller/test in this
    # file) must stay byte-for-byte identical to before this parameter was
    # introduced.
    values = [100.0, 130.0, 145.0]
    assert compute_reset_compensated_sums(values) == [0.0, 30.0, 45.0]
    assert compute_reset_compensated_sums(values, allow_physical_reset=True) == [0.0, 30.0, 45.0]


def test_compute_monthly_summary_for_year_month_boundary_rows_no_off_by_one():
    # Rows exactly on month-start boundaries (as HA's own "month" period rows
    # are, per the recorder source) must attribute each delta to exactly one
    # month, with no duplication or skipped month. Each row's own "sum" is
    # the cumulative total as of the end of ITS OWN month (row_June1.sum is
    # June's ending total, not May's) — so delta(row_M, row_M+1), tagged
    # with row_M+1's month, correctly represents month M+1's own consumption.
    rows = [
        {"start": _dt(2026, 5, 1), "sum": 100.0},
        {"start": _dt(2026, 6, 1), "sum": 130.0},
        {"start": _dt(2026, 7, 1), "sum": 145.0},
        {"start": _dt(2026, 8, 1), "sum": 200.0},
    ]
    summary = compute_monthly_summary_for_year(rows, 2026)
    by_month = {m["month"]: m["consumption"] for m in summary["months"]}
    assert by_month[6] == pytest.approx(30.0)
    assert by_month[7] == pytest.approx(15.0)
    assert by_month[8] == pytest.approx(55.0)
    # May has no data of its own here (its row only serves as June's baseline).
    assert by_month[5] is None


# ---------------------------------------------------------------------------
# compute_rolling_window_total — "Sidste 30 dage" summary (mirrors Brunata's
# own portal cards), shown above the individual meters in the dashboard card.
# ---------------------------------------------------------------------------


def _daily_rows(start_day: date, num_days: int, daily_consumption: float) -> list[dict]:
    """[{"start": datetime, "sum": float}, ...] for num_days consecutive days
    starting at start_day, each contributing exactly daily_consumption, plus
    one baseline day before start_day (so the first real day's delta is
    computable) — the same shape statistics.py's _normalize_state_rows hands
    to compute_daily_breakdown/compute_rolling_window_total in practice.
    """
    return [
        {
            "start": datetime(start_day.year, start_day.month, start_day.day) + timedelta(days=i - 1),
            "sum": i * daily_consumption,
        }
        for i in range(num_days + 1)
    ]


def test_compute_rolling_window_total_basic_30_days():
    as_of = date(2026, 7, 15)
    # Window ends the day BEFORE as_of (2026-07-14, since the 15th itself is
    # still in progress) — 30 real days (plus one baseline day) ending there.
    rows = _daily_rows(as_of - timedelta(days=30), 30, 1.0)
    result = compute_rolling_window_total(rows, as_of, window_days=30)
    assert result["total"] == pytest.approx(30.0)
    assert result["diff_from_last_year"] is None  # no data a year earlier at all


def test_compute_rolling_window_total_excludes_as_of_day_itself():
    # A reading dated as_of itself (today, still in progress) must NOT be
    # counted in the window — only through as_of - 1 day.
    as_of = date(2026, 7, 15)
    rows = _daily_rows(as_of - timedelta(days=30), 30, 1.0)
    # Add one more day landing on as_of itself, with a large jump that would
    # be very obviously wrong if it were included.
    rows.append({"start": datetime(as_of.year, as_of.month, as_of.day), "sum": rows[-1]["sum"] + 1000.0})
    result = compute_rolling_window_total(rows, as_of, window_days=30)
    assert result["total"] == pytest.approx(30.0)  # unaffected by the as_of-dated row


def test_compute_rolling_window_total_diff_from_last_year():
    as_of = date(2026, 7, 15)
    window_end = as_of - timedelta(days=1)
    window_start = window_end - timedelta(days=29)
    last_year_window_end = window_end - timedelta(days=365)
    last_year_window_start = last_year_window_end - timedelta(days=29)

    # This year's window: 30 days at 2.0/day = 60. Last year's window: 30
    # days at 1.5/day = 45. diff_from_last_year should be 60 - 45 = 15.
    rows = _daily_rows(last_year_window_start, 30, 1.5) + _daily_rows(window_start, 30, 2.0)
    result = compute_rolling_window_total(rows, as_of, window_days=30)
    assert result["total"] == pytest.approx(60.0)
    assert result["diff_from_last_year"] == pytest.approx(15.0)


def test_compute_rolling_window_total_no_data_at_all_is_none():
    result = compute_rolling_window_total([], date(2026, 7, 15), window_days=30)
    assert result == {"total": None, "diff_from_last_year": None}


def test_compute_rolling_window_total_partial_window_still_sums_available_days():
    as_of = date(2026, 7, 15)
    # Meter only has 10 days of history within the 30-day window (e.g. newly
    # mounted) — must still report a real total from what IS available,
    # not None.
    rows = _daily_rows(as_of - timedelta(days=10), 10, 1.0)
    result = compute_rolling_window_total(rows, as_of, window_days=30)
    assert result["total"] == pytest.approx(10.0)


def test_compute_rolling_window_total_immune_to_invalid_reading_in_window():
    # A rejected/invalid reading (already clamped to the last known-valid
    # value by compute_reset_compensated_sums' allow_physical_reset=False
    # layer, see the July 13 incident) inside the window must not corrupt
    # the 30-day total: the frozen day itself shows zero consumption, but
    # since the day after it reflects the meter's true, unaffected
    # cumulative reading, its delta (computed against the correct last-
    # valid baseline) naturally absorbs whatever the frozen day "lost" — no
    # consumption is permanently dropped from the window, only reattributed
    # to a different day within it.
    as_of = date(2026, 7, 15)
    rows = _daily_rows(as_of - timedelta(days=30), 30, 1.0)
    # Simulate day 13 being flagged invalid and frozen (sum stays flat for
    # one day instead of advancing by 1.0) — day 14 onward keep their real,
    # unaffected values.
    rows[13]["sum"] = rows[12]["sum"]
    result = compute_rolling_window_total(rows, as_of, window_days=30)
    assert result["total"] == pytest.approx(30.0)  # nothing lost over the full window
