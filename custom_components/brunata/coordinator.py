"""DataUpdateCoordinator for Brunata Online, incl. historical meter import.

The chunking algorithm for historical import lives in brunata_client.history
(no `homeassistant` dependency, so it's also usable from standalone scripts —
see scripts/history_smoke_test.py). This module wraps it for the one-time
backfill, and drives the regular hourly live-data polling.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataLoginError, BrunataSessionError
from .brunata_client.history import fetch_all_meter_history
from .brunata_client.models import ConsumptionData

from . import statistics
from .const import (
    ALLOCATION_UNIT_NAMES,
    ALLOCATION_UNIT_SLUGS,
    CONF_HISTORY_IMPORTED,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# unit code -> HA unit of measurement, matching sensor.py's native units.
_UNIT_OF_MEASUREMENT = {"O": "kWh", "W": "m³", "K": "m³"}


class BrunataDataUpdateCoordinator(DataUpdateCoordinator[ConsumptionData]):
    """Coordinates fetching data from the Brunata Online API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BrunataClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> ConsumptionData:
        try:
            return await self.client.fetch_consumption_data()
        except (BrunataLoginError, BrunataSessionError):
            # Session/token likely expired between polls — one re-login retry
            # before giving up (see docs/login-flow.md on session lifetime).
            try:
                await self.client.login()
                return await self.client.fetch_consumption_data()
            except Exception as err:
                raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    async def async_import_history_if_needed(self) -> None:
        """One-time backfill from each meter's mountingDate to first setup.

        Never runs again once CONF_HISTORY_IMPORTED is set on the config
        entry — ongoing data comes from the regular hourly meteroverview
        polling above, via the recorder's automatic statistics compilation.
        """
        if self.entry.data.get(CONF_HISTORY_IMPORTED):
            return

        meters_by_id = {
            m["meterId"]: m for m in await self.client.fetch_meters_for_consumer()
        }
        results = await fetch_all_meter_history(self.client)

        for result in results:
            meter = meters_by_id.get(result.meter_id)
            if meter is None:
                _LOGGER.warning(
                    "History fetched for meter %s but it's missing from "
                    "metersforconsumer — skipping statistics import",
                    result.meter_id,
                )
                continue

            allocation_unit = meter["allocationUnit"]
            slug = ALLOCATION_UNIT_SLUGS.get(allocation_unit)
            if slug is None:
                continue  # not one of O/W/K — shouldn't happen, fetch_all_meter_history already filters

            await statistics.async_import_meter_history(
                self.hass,
                entity_id=f"sensor.brunata_{slug}",
                unit_of_measurement=_UNIT_OF_MEASUREMENT[allocation_unit],
                name=f"Brunata {ALLOCATION_UNIT_NAMES[allocation_unit]}",
                points=result.points,
            )

        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_HISTORY_IMPORTED: True}
        )
