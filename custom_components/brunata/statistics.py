"""One-time historical backfill into HA long-term statistics.

Ongoing statistics are compiled automatically by the recorder from the
sensors' regular state updates (state_class=total_increasing) — this module
only ever runs once per config entry, to backfill the gap between a meter's
mountingDate and the moment the integration was first set up. See
coordinator.py's async_import_history_if_needed().
"""

from collections import OrderedDict
from datetime import datetime

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
)
from homeassistant.core import HomeAssistant

from brunata_client.history import parse_brunata_datetime


def _bucket_by_hour(points: list[dict]) -> list[StatisticData]:
    """Collapse raw readings into hour-aligned buckets.

    Points are cumulative meter totals (never reset), so the latest reading
    within an hour is that hour's correct end-of-hour value — no averaging
    or delta math needed.
    """
    by_hour: "OrderedDict[datetime, dict]" = OrderedDict()
    for point in sorted(points, key=lambda p: p["readingDate"]):
        reading_date = parse_brunata_datetime(point["readingDate"])
        hour = reading_date.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = point  # later (sorted) points overwrite earlier ones

    return [
        StatisticData(start=hour, state=point["value"], sum=point["value"])
        for hour, point in by_hour.items()
    ]


async def async_import_meter_history(
    hass: HomeAssistant,
    entity_id: str,
    unit_of_measurement: str,
    name: str,
    points: list[dict],
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
    async_import_statistics(hass, metadata, _bucket_by_hour(points))
