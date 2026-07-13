"""Pure monthly/daily consumption aggregation math — no `homeassistant` import.

Turns chronologically-ordered, cumulative recorder-statistics rows into
period consumption deltas, year-based monthly summaries, and year-over-year
comparisons. Kept HA-independent (same split as history.py) so it's testable
offline and usable from custom_components/brunata/statistics.py, which
supplies the raw rows from
homeassistant.components.recorder.statistics.statistics_during_period().
"""


_RESET_THRESHOLD = 0.9  # matches HA's own total_increasing reset detection


def compute_reset_compensated_sums(values: list[float]) -> list[float]:
    """Chronological raw (possibly physically-resetting) cumulative readings
    -> a monotonically increasing running total, one output per input value.

    Mirrors HA's own state_class=total_increasing statistics compiler
    (homeassistant/components/sensor/recorder.py's reset_detected(): a value
    below 90% of the previous one is treated as a physical counter reset, not
    negative consumption — the new cycle's own reading is added as-is rather
    than subtracted from). This only matters for the one-time historical
    backfill (statistics.py's _bucket_by_hour) — HA's live recorder already
    does this automatically for ongoing polling, since that goes through the
    sensor's own state_class-aware compiler; the bulk import bypasses it.
    """
    running_sum = 0.0
    previous: float | None = None
    result = []
    for value in values:
        if previous is None:
            pass  # first ever point: nothing to diff against yet
        elif value < previous * _RESET_THRESHOLD:
            running_sum += value  # reset: new cycle contributes from its own 0
        else:
            running_sum += value - previous
        result.append(running_sum)
        previous = value
    return result


def compute_period_deltas(rows: list[dict]) -> list[dict]:
    """rows: chronological [{"start": datetime, "sum": float}, ...].

    Returns [{"start": datetime, "consumption": float | None}, ...] for
    rows[1:], each computed as this period's cumulative sum minus the
    previous period's — rows[0] is only consumed as the baseline for
    rows[1]'s delta and never appears in the output. Caller must include one
    extra row before the first period they actually want.

    `consumption` is None when the delta is negative — this happens when the
    physical meter's cumulative counter reset between the two snapshots
    (confirmed on a real account: a heat meter's cumulative value fell to
    ~0 around June/July 2026), NOT falling consumption. There is no way to
    reconstruct the true consumption for that period from these two coarse
    snapshots alone: `current.sum` only reflects what accumulated AFTER the
    reset, so neither `current - previous` (negative/nonsensical) nor
    `current - 0` (ignores whatever was consumed before the reset, still
    within the same period) is a correct number — so it's reported as
    unknown rather than guessed.
    """
    deltas = []
    for previous, current in zip(rows, rows[1:]):
        raw_delta = current["sum"] - previous["sum"]
        consumption = raw_delta if raw_delta >= 0 else None
        deltas.append({"start": current["start"], "consumption": consumption})
    return deltas


def _by_year_month(rows: list[dict]) -> dict[tuple[int, int], float | None]:
    """[{"start", "sum"}, ...] -> {(year, month): consumption | None}.

    A dict comprehension inherently collapses duplicate (year, month) keys
    (keeping the last), which also protects against duplicate/overlapping
    raw rows for the same period ever producing duplicate months downstream.
    """
    deltas = compute_period_deltas(rows)
    return {(d["start"].year, d["start"].month): d["consumption"] for d in deltas}


def compute_available_years(rows: list[dict]) -> list[int]:
    """Distinct years for which at least one month has a usable (non-reset)
    consumption value, ascending — used to build the frontend's year dropdown.
    """
    by_year_month = _by_year_month(rows)
    return sorted({year for (year, _month), consumption in by_year_month.items() if consumption is not None})


def compute_monthly_summary_for_year(rows: list[dict], year: int) -> dict:
    """January-December breakdown for one calendar year, plus a yearly total.

    Returns {"year": int, "months": [{"month", "consumption", "yoy_percent"}, ...
    12 entries, Jan-Dec], "total_consumption": float | None}. A month with no
    data (meter not yet installed, future month, or a reset — see
    compute_period_deltas) gets `consumption: None` and `yoy_percent: None`,
    never a guessed value. `total_consumption` sums whatever months ARE
    available (e.g. a year still in progress, or a meter installed mid-year)
    rather than requiring a complete year — None only if no month has data.
    """
    by_year_month = _by_year_month(rows)

    months = []
    for month in range(1, 13):
        consumption = by_year_month.get((year, month))
        last_year_consumption = by_year_month.get((year - 1, month))
        if consumption is not None and last_year_consumption is not None and last_year_consumption != 0:
            yoy_percent = (consumption - last_year_consumption) / last_year_consumption * 100
        else:
            yoy_percent = None
        months.append({"month": month, "consumption": consumption, "yoy_percent": yoy_percent})

    known = [m["consumption"] for m in months if m["consumption"] is not None]
    total_consumption = sum(known) if known else None

    return {"year": year, "months": months, "total_consumption": total_consumption}


def compute_daily_breakdown(rows: list[dict]) -> list[dict]:
    """rows: chronological [{"start": datetime, "sum": float}, ...], one per
    day, spanning the target month plus one baseline day before it.

    Returns [{"day": int, "consumption": float | None}, ...] for every day
    present in the deltas, in date order. `consumption` is None on a reset
    day — see compute_period_deltas.
    """
    deltas = compute_period_deltas(rows)
    return [{"day": d["start"].day, "consumption": d["consumption"]} for d in deltas]
