"""One-time historical backfill into HA long-term statistics.

Ongoing statistics are compiled automatically by the recorder from the
sensors' regular state updates (state_class=total_increasing) — this module
only ever runs once per config entry, to backfill the gap between a meter's
mountingDate and the moment the integration was first set up. See
coordinator.py's async_import_history_if_needed().
"""

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


def _bucket_by_hour(points: list[dict], scale: float | None) -> list[StatisticData]:
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
    """
    by_hour: "OrderedDict[datetime, dict]" = OrderedDict()
    for point in sorted(points, key=lambda p: p["readingDate"]):
        reading_date = parse_brunata_datetime(point["readingDate"])
        hour = reading_date.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = point  # later (sorted) points overwrite earlier ones

    raw_values = [
        point["value"] * scale if scale is not None else point["value"]
        for point in by_hour.values()
    ]
    compensated_sums = compute_reset_compensated_sums(raw_values)

    return [
        StatisticData(start=hour, state=raw_value, sum=compensated_sum)
        for hour, raw_value, compensated_sum in zip(by_hour.keys(), raw_values, compensated_sums)
    ]


async def async_import_meter_history(
    hass: HomeAssistant,
    entity_id: str,
    unit_of_measurement: str,
    name: str,
    points: list[dict],
    scale: float | None = None,
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
    async_import_statistics(hass, metadata, _bucket_by_hour(points, scale))


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


def _normalize_rows(raw_rows: list[dict]) -> list[dict]:
    """statistics_during_period() rows -> [{"start": datetime, "sum": float}, ...].

    NOTE: the exact shape of `row["start"]` (epoch seconds vs. datetime) has
    varied across HA versions — this normalizes both so aggregation.py never
    has to care. Worth re-checking against your actual HA version if this
    ever raises.
    """
    rows = []
    for row in raw_rows:
        start = row["start"]
        if isinstance(start, (int, float)):
            start = dt_util.utc_from_timestamp(start)
        rows.append({"start": dt_util.as_local(start), "sum": row["sum"]})
    return rows


# Fixed, safely-early start for the "full history" query below — simpler and
# more robust than trying to know each meter's real mountingDate here; the
# recorder just returns nothing for months before real data exists.
_EARLIEST_POSSIBLE_DATA = datetime(2000, 1, 1)


async def async_get_monthly_summary(
    hass: HomeAssistant, statistic_id: str, year: int | None = None
) -> dict:
    """Year-based monthly breakdown (Jan-Dec) plus which years have data.

    If `year` is omitted (or has no data), defaults to the most recent year
    that does have data. Returns
    {"available_years": [...], "year": int | None, "months": [...],
    "total_consumption": float | None} — see compute_monthly_summary_for_year
    for the "months"/"total_consumption" shape. `available_years` is empty
    and `year`/`total_consumption` are None if the meter has no usable
    history at all yet.
    """
    start_time = _EARLIEST_POSSIBLE_DATA.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    raw = await _async_statistics_during_period(
        hass, start_time, None, {statistic_id}, "month", {"sum"}
    )
    rows = _normalize_rows(raw.get(statistic_id, []))

    available_years = compute_available_years(rows)
    if not available_years:
        return {"available_years": [], "year": None, "months": [], "total_consumption": None}

    resolved_year = year if year in available_years else available_years[-1]
    return {"available_years": available_years, **compute_monthly_summary_for_year(rows, resolved_year)}


async def async_get_daily_breakdown(
    hass: HomeAssistant, statistic_id: str, year: int, month: int
) -> list[dict]:
    """{day, consumption} for every day in the given (year, month)."""
    month_start = _shift_months(datetime(year, month, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE), 0)
    baseline_start = month_start - timedelta(days=1)
    next_month_start = _shift_months(month_start, 1)
    raw = await _async_statistics_during_period(
        hass, baseline_start, next_month_start, {statistic_id}, "day", {"sum"}
    )
    rows = _normalize_rows(raw.get(statistic_id, []))
    return compute_daily_breakdown(rows)
