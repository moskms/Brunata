"""Historical backfill into HA long-term statistics (feeds the Energy
dashboard/HA's own recorder-based views only), and the dashboard card's own
monthly/daily/rolling consumption reads (fed from the integration-owned
ledger — see ledger.py — NOT the recorder, since HA's own "sum" statistics
column was found unreliable once already; see README's "Kendte
begrænsninger").

The recorder-based historical import only ever runs once per config entry,
to backfill the gap between a meter's mountingDate and the moment the
integration was first set up; ongoing recorder statistics are otherwise
compiled automatically from the sensors' regular state updates
(state_class=total_increasing). See coordinator.py's
async_import_history_if_needed().
"""

import logging
from collections import OrderedDict
from datetime import datetime

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import ledger
from .brunata_client.aggregation import (
    compute_available_years,
    compute_daily_breakdown,
    compute_monthly_summary_for_year,
    compute_reset_compensated_sums,
    compute_rolling_window_total,
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
# Monthly/daily/rolling consumption view (Del 3b onwards) — pure read layer
# over the integration-owned ledger (ledger.py), NOT the recorder. No new
# Brunata API calls: the ledger is populated by coordinator.py's regular
# polling and one-time history backfill, same as before — only the SOURCE
# read from here changed, from statistics_during_period() to ledger.py.
# ----------------------------------------------------------------------


def _shift_months(reference: datetime, delta: int) -> datetime:
    """First-of-month `delta` months away from `reference` (delta may be negative)."""
    total = reference.year * 12 + (reference.month - 1) + delta
    year, month = divmod(total, 12)
    return reference.replace(
        year=year, month=month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _normalize_ledger_rows(
    entries: list[tuple[datetime, int]],
    allocation_unit: str,
    scale: float | None,
    allow_physical_reset: bool,
    entity_id: str,
) -> list[dict]:
    """Ledger (timestamp, raw_int) entries -> a reset-compensated,
    monotonically increasing [{"start": datetime, "sum": float}, ...]
    series — the ledger-based replacement for the old recorder-based
    _normalize_state_rows, feeding the exact same aggregation.py functions
    as before.

    Converts each raw integer back to a float in the meter's real unit (m3
    for water, kWh for heat via `scale`) before running the SAME
    reset-compensation/validation layer used everywhere else in this
    project (compute_reset_compensated_sums) — still correctly handles a
    REAL physical heat-meter reset, and still rejects an impossible drop
    for water as invalid data rather than a guess. See README's "Kendte
    begrænsninger" for the full reasoning (originally written for the
    recorder-based path, equally true here).
    """
    if not entries:
        return []

    def _to_float(raw_value: int) -> float:
        if allocation_unit in ("W", "K"):
            return raw_value / 1000.0
        return raw_value * scale if scale is not None else float(raw_value)

    starts = [dt_util.as_local(ts) for ts, _ in entries]
    raw_values = [_to_float(value) for _, value in entries]

    invalid_indices: list[int] = []
    compensated_sums = compute_reset_compensated_sums(
        raw_values, allow_physical_reset=allow_physical_reset, invalid_indices=invalid_indices
    )
    for index in invalid_indices:
        _LOGGER.warning(
            "[BRUNATA DATA VALIDATION] %s: rejected invalid ledger reading at "
            "%s (value=%.4f is a physically-impossible drop from the last "
            "known-valid reading, and this meter type cannot physically "
            "reset) — treated as zero consumption for this period, not "
            "interpolated or guessed. Investigate if this happens often.",
            entity_id, starts[index], raw_values[index],
        )

    return [{"start": start, "sum": value} for start, value in zip(starts, compensated_sums)]


async def async_get_monthly_summary(
    hass: HomeAssistant,
    meter_id: int,
    year: int | None = None,
    allocation_unit: str | None = None,
    scale: float | None = None,
    entity_id: str = "",
) -> dict:
    """Year-based monthly breakdown (Jan-Dec) plus which years have data.

    If `year` is omitted (or has no data), defaults to the most recent year
    that does have data. `allocation_unit` ("O"/"W"/"K") selects whether a
    data drop is treated as a real physical reset or rejected as invalid —
    see _normalize_ledger_rows. `scale` converts a heat meter's raw ledger
    pulses to kWh (ignored for water). Returns {"available_years": [...],
    "year": int | None, "months": [...], "total_consumption": float | None}
    — see compute_monthly_summary_for_year for the "months"/
    "total_consumption" shape. `available_years` is empty and
    `year`/`total_consumption` are None if the meter has no ledger history
    at all yet (e.g. right after a fresh setup, before the one-time ledger
    backfill has completed).
    """
    entries = await ledger.async_read_ledger(hass, meter_id)
    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    rows = _normalize_ledger_rows(entries, allocation_unit, scale, allow_physical_reset, entity_id)

    available_years = compute_available_years(rows)
    if not available_years:
        return {"available_years": [], "year": None, "months": [], "total_consumption": None}

    resolved_year = year if year in available_years else available_years[-1]
    return {"available_years": available_years, **compute_monthly_summary_for_year(rows, resolved_year)}


async def async_get_daily_breakdown(
    hass: HomeAssistant,
    meter_id: int,
    year: int,
    month: int,
    allocation_unit: str | None = None,
    scale: float | None = None,
    entity_id: str = "",
) -> list[dict]:
    """{day, consumption} for every day in the given (year, month)."""
    entries = await ledger.async_read_ledger(hass, meter_id)
    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    rows = _normalize_ledger_rows(entries, allocation_unit, scale, allow_physical_reset, entity_id)

    # compute_daily_breakdown returns one entry per day PRESENT IN ITS INPUT
    # (it doesn't know about calendar months) — so unlike the monthly
    # summary above, this needs the ledger's (potentially years of) rows
    # narrowed down to just this one month plus a single baseline row
    # before it, mirroring what the old recorder query's date range used to
    # do automatically.
    month_start = _shift_months(datetime(year, month, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE), 0)
    next_month_start = _shift_months(month_start, 1)
    baseline_rows = [row for row in rows if row["start"] < month_start]
    month_rows = [row for row in rows if month_start <= row["start"] < next_month_start]
    if not month_rows:
        return []
    windowed_rows = ([baseline_rows[-1]] if baseline_rows else []) + month_rows
    return compute_daily_breakdown(windowed_rows)


async def async_get_rolling_summary(
    hass: HomeAssistant,
    meter_id: int,
    allocation_unit: str | None = None,
    scale: float | None = None,
    entity_id: str = "",
    window_days: int = 30,
) -> dict:
    """Rolling `window_days`-day total consumption, plus the absolute
    difference from the same window exactly one year earlier — mirrors
    Brunata's own portal's "Sidste 30 dage" summary cards, shown above the
    individual meters in the dashboard card. Reuses the same ledger-based,
    reset-/invalid-data-validated computation as the monthly/daily views.
    """
    entries = await ledger.async_read_ledger(hass, meter_id)
    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    rows = _normalize_ledger_rows(entries, allocation_unit, scale, allow_physical_reset, entity_id)
    return compute_rolling_window_total(rows, dt_util.now().date(), window_days)
