"""One-time historical backfill into HA long-term statistics.

Ongoing statistics are compiled automatically by the recorder from the
sensors' regular state updates (state_class=total_increasing) — this module
only ever runs once per config entry, to backfill the gap between a meter's
mountingDate and the moment the integration was first set up. See
coordinator.py's async_import_history_if_needed().
"""

import logging
from collections import OrderedDict
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .brunata_client.aggregation import (
    compute_available_years,
    compute_daily_breakdown,
    compute_monthly_summary_for_year,
    compute_reset_compensated_sums,
)
from .brunata_client.history import parse_brunata_datetime
from .const import ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET

_LOGGER = logging.getLogger(__name__)


def _bucket_by_hour(
    points: list[dict],
    scale: float | None,
    allow_physical_reset: bool,
    entity_id: str,
) -> list[StatisticData]:
    """Collapse raw readings into hour-aligned buckets.

    Points are cumulative meter totals, so the latest reading within an hour
    is that hour's correct end-of-hour reading — no averaging needed.

    `scale`, when given, is applied the same way fetch_consumption_data()
    applies it to the live reading: /consumer/meters/{meterId}/metervalues
    returns raw, unscaled pulse values for heat (O) meters (confirmed from
    HAR — same units as meteroverview's meterValue), so without this the
    heat sensor's imported history would be in the wrong unit compared to
    its live value.

    A physical meter's cumulative counter can reset to ~0 (confirmed on a
    real heat meter around June/July 2026) — `sum` runs the hour-bucketed
    readings through compute_reset_compensated_sums() so the imported
    long-term statistic stays monotonically increasing across such a reset,
    the same way HA's own live total_increasing statistics compiler already
    does for ongoing polling (that compiler only sees state changes as they
    happen; this bulk one-time import bypasses it entirely, so it has to
    replicate the same compensation itself). `state` stays the raw reading
    (matching what the live sensor actually shows, reset included) — only
    `sum`, the value period deltas are computed from, is compensated.

    `allow_physical_reset` gates whether a large drop is treated as a real
    meter reset (heat/"O" meters only) or as invalid data to be
    clamped/rejected (water meters — see compute_reset_compensated_sums and
    README's "Kendte begrænsninger"). Any rejected reading is logged here,
    since this is the one place with both an entity_id and logging access.
    """
    by_hour: "OrderedDict[datetime, dict]" = OrderedDict()
    for point in sorted(points, key=lambda p: p["readingDate"]):
        reading_date = parse_brunata_datetime(point["readingDate"])
        hour = reading_date.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = point  # later (sorted) points overwrite earlier ones

    hours = list(by_hour.keys())
    raw_values = [
        point["value"] * scale if scale is not None else point["value"]
        for point in by_hour.values()
    ]
    invalid_indices: list[int] = []
    compensated_sums = compute_reset_compensated_sums(
        raw_values, allow_physical_reset=allow_physical_reset, invalid_indices=invalid_indices
    )
    for index in invalid_indices:
        _LOGGER.warning(
            "[BRUNATA DATA VALIDATION] %s: rejected invalid reading at %s "
            "(value=%.4f is a physically-impossible drop from the last known-"
            "valid reading, and this meter type cannot physically reset) — "
            "treated as zero consumption for this hour, not interpolated or "
            "guessed.",
            entity_id, hours[index], raw_values[index],
        )

    return [
        StatisticData(start=hour, state=raw_value, sum=compensated_sum)
        for hour, raw_value, compensated_sum in zip(hours, raw_values, compensated_sums)
    ]


async def async_import_meter_history(
    hass: HomeAssistant,
    entity_id: str,
    unit_of_measurement: str,
    name: str,
    points: list[dict],
    scale: float | None = None,
    allow_physical_reset: bool = True,
) -> None:
    """Import a meter's raw metervalues history as long-term statistics for entity_id."""
    if not points:
        return

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=name,
        source="recorder",
        statistic_id=entity_id,
        unit_of_measurement=unit_of_measurement,
    )
    async_import_statistics(
        hass, metadata, _bucket_by_hour(points, scale, allow_physical_reset, entity_id)
    )


# ----------------------------------------------------------------------
# Monthly/daily consumption view (Del 3b) — pure read layer over the
# recorder statistics already populated above / by each sensor's own
# state_class=total_increasing auto-compilation. No new Brunata API calls.
# ----------------------------------------------------------------------


def _shift_months(reference: datetime, delta: int) -> datetime:
    """First-of-month `delta` months away from `reference` (delta may be negative)."""
    total = reference.year * 12 + (reference.month - 1) + delta
    year, month = divmod(total, 12)
    return reference.replace(
        year=year, month=month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )


async def _async_statistics_during_period(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime | None,
    statistic_ids: set[str],
    period: str,
    types: set[str],
) -> dict[str, list[dict]]:
    """statistics_during_period() is a plain sync function that hits the
    recorder DB directly — it must run in the recorder's executor, not be
    awaited directly. Confirmed against homeassistant/components/recorder/
    websocket_api.py's own `_ws_get_statistics_during_period`, which calls it
    the same way. `units=None` = raw/unconverted values in each statistic's
    native unit (confirmed against the recorder source), which is what we
    want since Brunata's own units are already what we store.
    """
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start_time,
        end_time,
        statistic_ids,
        period,
        None,  # units
        types,
    )


def _normalize_state_rows(
    raw_rows: list[dict], allow_physical_reset: bool, entity_id: str
) -> list[dict]:
    """statistics_during_period() rows -> a reset-compensated, monotonically
    increasing [{"start": datetime, "sum": float}, ...] series, computed from
    each row's "state" field — NOT the recorder's own "sum" column.

    Confirmed unreliable via a real, isolated incident (2026-07-13): on that
    day, "state" climbed smoothly and correctly (matching the live sensor
    exactly, day-over-day deltas all a normal ~0.02-0.07), while HA's own
    compiled "sum" for the same day collapsed from 135.99 to 0.006 — with
    last_reset staying null throughout, meaning HA's own recorder didn't even
    flag it as a detected reset. Water/heat meters don't physically reset
    like that on their own (confirmed project knowledge for water; heat's
    own real reset is a separate, already-handled case) — this was an
    internal recorder-compiler artifact, not real consumption data.

    "state" itself showed no anomaly at any point during the same incident,
    so period consumption is now computed from it instead, run through the
    SAME reset-compensation logic already used for the one-time historical
    import (compute_reset_compensated_sums) — which still correctly handles
    a REAL physical meter reset (e.g. the heat meter's own, confirmed reset),
    just no longer trusts HA's own "sum" tracking for the ongoing/live view.

    `allow_physical_reset` gates that same behavior here too — a general,
    independent validation layer that also runs on every live/ongoing fetch
    (not just the one-time import): for meter types that cannot physically
    reset (water), any drop is rejected as invalid data rather than treated
    as a reset, so a future recurrence of the July 13 class of bug (whatever
    its cause) is caught automatically, regardless of whether it shows up in
    "sum" or even in "state" itself. See README's "Kendte begrænsninger".

    NOTE: the exact shape of `row["start"]` (epoch seconds vs. datetime) has
    varied across HA versions — this normalizes both so aggregation.py never
    has to care. Worth re-checking against your actual HA version if this
    ever raises.
    """
    parsed: list[dict] = []
    for row in raw_rows:
        state = row.get("state")
        if state is None:
            continue  # no reading recorded for this period at all — skip
        start = row["start"]
        if isinstance(start, (int, float)):
            start = dt_util.utc_from_timestamp(start)
        parsed.append({"start": dt_util.as_local(start), "state": state})

    invalid_indices: list[int] = []
    compensated_sums = compute_reset_compensated_sums(
        [r["state"] for r in parsed],
        allow_physical_reset=allow_physical_reset,
        invalid_indices=invalid_indices,
    )
    for index in invalid_indices:
        _LOGGER.warning(
            "[BRUNATA DATA VALIDATION] %s: rejected invalid reading at %s "
            "(value=%.4f is a physically-impossible drop from the last known-"
            "valid reading, and this meter type cannot physically reset) — "
            "treated as zero consumption for this period, not interpolated "
            "or guessed. Investigate if this happens often.",
            entity_id, parsed[index]["start"], parsed[index]["state"],
        )

    return [
        {"start": r["start"], "sum": value}
        for r, value in zip(parsed, compensated_sums)
    ]


# Bulk-query date range for the monthly view is capped, regardless of how far
# back a meter's own history goes — a 7-year-old meter (e.g. water, mounted
# 2019) confirmed to make statistics_during_period() hang indefinitely when
# queried from year 2000 onward, on real hardware. The full history remains
# available via HA's own History/Statistics UI, which isn't limited by this
# cap — this only bounds what the monthly dashboard card queries.
_MAX_MONTHLY_HISTORY = timedelta(days=3 * 365)


def _monthly_summary_start_time(now: datetime, mounting_date: str | None) -> datetime:
    """The later (narrower) of `now - _MAX_MONTHLY_HISTORY` and the meter's own
    mountingDate — never queries further back than necessary, and never
    further back than the hard cap above, whichever is closer to `now`.
    """
    capped_start = now - _MAX_MONTHLY_HISTORY
    if not mounting_date:
        return capped_start
    try:
        mounted = parse_brunata_datetime(mounting_date)
    except (ValueError, TypeError):
        return capped_start
    return max(mounted, capped_start)


async def async_get_monthly_summary(
    hass: HomeAssistant,
    statistic_id: str,
    year: int | None = None,
    mounting_date: str | None = None,
    allocation_unit: str | None = None,
) -> dict:
    """Year-based monthly breakdown (Jan-Dec) plus which years have data.

    If `year` is omitted (or has no data), defaults to the most recent year
    that does have data. `mounting_date` (the meter's own mountingDate from
    /consumer/metersforconsumer, when available) narrows the query further
    than the _MAX_MONTHLY_HISTORY cap if the meter is younger than that.
    `allocation_unit` ("O"/"W"/"K") selects whether a data drop is treated as
    a real physical reset or rejected as invalid — see _normalize_state_rows.
    Returns {"available_years": [...], "year": int | None, "months": [...],
    "total_consumption": float | None} — see compute_monthly_summary_for_year
    for the "months"/"total_consumption" shape. `available_years` is empty
    and `year`/`total_consumption` are None if the meter has no usable
    history at all yet.
    """
    start_time = _monthly_summary_start_time(dt_util.now(), mounting_date)
    raw = await _async_statistics_during_period(
        hass, start_time, None, {statistic_id}, "month", {"state"}
    )
    raw_rows_for_id = raw.get(statistic_id, [])
    _LOGGER.debug(
        "async_get_monthly_summary(%s): %d raw month row(s) from the recorder "
        "(start_time=%s)",
        statistic_id, len(raw_rows_for_id), start_time,
    )
    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    rows = _normalize_state_rows(raw_rows_for_id, allow_physical_reset, statistic_id)

    available_years = compute_available_years(rows)
    if not available_years:
        return {"available_years": [], "year": None, "months": [], "total_consumption": None}

    resolved_year = year if year in available_years else available_years[-1]
    return {"available_years": available_years, **compute_monthly_summary_for_year(rows, resolved_year)}


async def async_get_daily_breakdown(
    hass: HomeAssistant,
    statistic_id: str,
    year: int,
    month: int,
    allocation_unit: str | None = None,
) -> list[dict]:
    """{day, consumption} for every day in the given (year, month)."""
    month_start = _shift_months(datetime(year, month, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE), 0)
    baseline_start = month_start - timedelta(days=1)
    next_month_start = _shift_months(month_start, 1)
    raw = await _async_statistics_during_period(
        hass, baseline_start, next_month_start, {statistic_id}, "day", {"state"}
    )
    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    rows = _normalize_state_rows(raw.get(statistic_id, []), allow_physical_reset, statistic_id)
    return compute_daily_breakdown(rows)
