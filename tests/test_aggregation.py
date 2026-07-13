from datetime import datetime, timedelta, timezone

import pytest

from brunata_client.aggregation import (
    compute_available_years,
    compute_daily_breakdown,
    compute_monthly_summary_for_year,
    compute_period_deltas,
    compute_reset_compensated_sums,
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
