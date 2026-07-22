"""Phase 2 (manual, not wired into automatic polling): correct HA's recorder
long-term statistics for a meter using the integration-owned ledger as the
source of truth, via async_import_statistics().

Verified against HA's own recorder/statistics.py source before building
this (not guessed):
- `_import_statistics_with_session` upserts by (metadata_id, start): if a
  row already exists for that hour it's UPDATED in place, otherwise
  inserted — so calling async_import_statistics() again for an
  already-imported period corrects it, it never creates a duplicate.
- The import job is enqueued on the recorder's own single serialized
  worker queue — the same queue the recorder's own live statistics
  compiler uses — so there is no race between this and the recorder's
  regular background compilation.
- The long-term `Statistics` table (as opposed to `StatisticsShortTerm`) is
  exempt from HA's periodic purge, so correcting old periods here is safe
  indefinitely.
- HA is deprecating the legacy has_mean/has_sum metadata fields in favor of
  mean_type/unit_class (breaks_in_ha_version: 2026.11) — deliberately NOT
  migrated yet (see git history): the compatibility shim in HA's own source
  confirms has_mean/has_sum still works correctly today, and switching
  requires a separate minimum-HA-version decision to be made first.

Never call this automatically — only ever manually, via the
brunata.reconcile_recorder_statistics service (services.py), and only for
FULLY-ELAPSED past hours: correcting the current, still-accumulating hour
would race the recorder's own live compiler for that exact same period.
"""

from __future__ import annotations

import logging

from homeassistant.components.recorder.statistics import StatisticMetaData, async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import ledger
from .const import ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
from .statistics import _bucket_by_hour

_LOGGER = logging.getLogger(__name__)


async def async_reconcile_from_ledger(
    hass: HomeAssistant,
    meter_id: int,
    entity_id: str,
    name: str,
    unit_of_measurement: str,
    allocation_unit: str,
    scale: float | None,
) -> int:
    """Recompute this meter's long-term recorder statistics from its ledger
    history, and upsert-correct them via async_import_statistics(). Only
    fully-elapsed past hours are ever written — the current, in-progress
    hour is always excluded, so this can never race the recorder's own live
    compilation of that same period.

    Returns the number of hourly periods reconciled (0 if the ledger has no
    entries for this meter yet).
    """
    entries = await ledger.async_read_ledger(hass, meter_id)
    if not entries:
        return 0

    # _bucket_by_hour expects the same {"readingDate", "value"} shape
    # Brunata's own raw metervalues history uses — raw, PRE-scale value
    # (scale is applied inside _bucket_by_hour itself, same as the original
    # one-time backfill), so ledger's stored liters/pulses are converted
    # back to the meter's real raw unit (m3 for water) here first.
    points = [
        {
            "readingDate": ts.isoformat(),
            "value": raw_value / 1000.0 if allocation_unit in ("W", "K") else float(raw_value),
        }
        for ts, raw_value in entries
    ]

    allow_physical_reset = allocation_unit in ALLOCATION_UNITS_ALLOWING_PHYSICAL_RESET
    all_stats = _bucket_by_hour(points, scale, allow_physical_reset, entity_id)

    # Never touch the current, still-accumulating hour — only fully-elapsed
    # past periods, so this can't race the recorder's own live compiler for
    # that exact same hour.
    current_hour_start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    past_stats = [stat for stat in all_stats if stat["start"] < current_hour_start]
    if not past_stats:
        return 0

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=name,
        source="recorder",
        statistic_id=entity_id,
        unit_of_measurement=unit_of_measurement,
    )
    async_import_statistics(hass, metadata, past_stats)

    _LOGGER.warning(
        "[BRUNATA RECONCILE] %s: corrected %d hourly period(s) in the recorder "
        "from the ledger (up to %s)",
        entity_id, len(past_stats), past_stats[-1]["start"],
    )
    return len(past_stats)
