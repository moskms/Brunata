"""DataUpdateCoordinator for Brunata Online, incl. historical meter import.

See docs/api-reference.md for the confirmed 600-item limit on
GET /consumer/meters/{meterId}/metervalues and the reasoning behind the
adaptive chunking strategy below.

NOTE: only the historical-import piece is implemented here so far. Regular
periodic updates (async_update_data / meteroverview polling) and writing
imported points into HA long-term statistics are follow-up work — see
copilot-instructions-del2.md, Trin 2.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from brunata_client import BrunataClient
from brunata_client.exceptions import BrunataDataError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# /consumer/meters/{meterId}/metervalues returns at most 600 points per call
# and silently drops everything older than the 600 newest when the window
# holds more (limited=True) — see docs/api-reference.md. Older periods are far
# sparser than recent ones, so we start wide and only shrink when the server
# actually reports limited=True, instead of guessing a fixed chunk size.
_INITIAL_HISTORY_INTERVAL = timedelta(days=365)
_MIN_HISTORY_INTERVAL = timedelta(hours=1)


def _parse_brunata_datetime(value: str) -> datetime:
    """Parse a Brunata timestamp/date string (e.g. "2026-07-12T19:42:00+02:00"
    or a bare "2025-03-25" mountingDate) into an aware datetime.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


class BrunataDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinates fetching data from the Brunata Online API."""

    def __init__(self, hass: HomeAssistant, client: BrunataClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=1),
        )
        self.client = client

    # ------------------------------------------------------------------
    # Historical import
    # ------------------------------------------------------------------

    async def async_fetch_meter_history(
        self,
        meter_id: int,
        start: datetime,
        until: datetime | None = None,
    ) -> list[dict]:
        """Fetch a meter's full readings from `start` to `until` (default: now).

        Implements the adaptive chunking strategy confirmed in
        docs/api-reference.md:

        1. Start with a large window (`_INITIAL_HISTORY_INTERVAL`).
        2. Call metervalues for [cursor, cursor + window).
        3. If the response is `limited`, halve the window and retry the SAME
           cursor (not the whole period from scratch).
        4. Once a call comes back unlimited, keep its points, advance the
           cursor to just after the last point actually returned, and repeat
           (trying to grow the window back up) until `until` is reached.
        """
        now = until or datetime.now().astimezone()
        cursor = start
        window = _INITIAL_HISTORY_INTERVAL
        points: list[dict] = []

        while cursor < now:
            end = min(cursor + window, now)
            payload = await self.client.fetch_meter_values(meter_id, cursor, end)

            if payload.get("limited"):
                window /= 2
                if window < _MIN_HISTORY_INTERVAL:
                    raise BrunataDataError(
                        f"meter {meter_id}: still limited at a {window} window "
                        f"between {cursor} and {end} — more than 600 readings "
                        "in under an hour is unexpected per docs/api-reference.md"
                    )
                continue  # retry the same cursor with the halved window

            values = payload.get("meterValues", [])
            points.extend(values)

            if values:
                last_date = max(_parse_brunata_datetime(v["readingDate"]) for v in values)
                cursor = last_date + timedelta(seconds=1)
            else:
                cursor = end  # nothing in this window — move on regardless

            # Data density varies a lot over the years (docs/api-reference.md);
            # try growing the window back up for the next chunk instead of
            # staying stuck at a small size picked for a denser period.
            window = min(window * 2, _INITIAL_HISTORY_INTERVAL)

        return points

    async def async_fetch_all_meter_history(self) -> dict[int, list[dict]]:
        """Fetch full history for every meter from its mountingDate to now
        (or to dismountedDate for meters no longer in use).
        """
        meters = await self.client.fetch_meters_for_consumer()
        now = datetime.now().astimezone()
        history: dict[int, list[dict]] = {}

        for meter in meters:
            meter_id = meter["meterId"]
            start = _parse_brunata_datetime(meter["mountingDate"])
            dismounted = meter.get("dismountedDate")
            until = _parse_brunata_datetime(dismounted) if dismounted else now
            history[meter_id] = await self.async_fetch_meter_history(meter_id, start, until)

        return history
