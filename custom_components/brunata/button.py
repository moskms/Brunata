"""Button platform for Brunata Online: a manual "Opdatér nu" refresh trigger.

One button per config entry (account), not per meter — coordinator.py's
async_request_refresh() always fetches every active meter in a single
GET /consumer/meteroverview call (confirmed in the adaptive-polling work),
so there is no meaningful "refresh just this one meter" action to expose.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import BrunataDataUpdateCoordinator

# Guards against a double-click or repeated impatient taps hammering
# Brunata's login/API endpoints — same underlying concern as the hourly
# polling interval, just enforced on the manual trigger instead. Short
# enough to still feel responsive for the button's actual use case
# (comparing a fresh value against Brunata's own website right now).
_COOLDOWN = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BrunataRefreshButton(coordinator, entry)])


class BrunataRefreshButton(CoordinatorEntity[BrunataDataUpdateCoordinator], ButtonEntity):
    """Forces an immediate coordinator refresh, bypassing the adaptive/
    hourly poll schedule — for comparing a fresh value against Brunata's own
    website right now, without waiting for the next scheduled poll.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = ButtonDeviceClass.UPDATE
    _attr_name = "Opdatér nu"

    def __init__(self, coordinator: BrunataDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brunata",
            manufacturer="Brunata",
        )
        self._last_pressed: datetime | None = None  # dt_util.now(), set on each accepted press

    async def async_press(self) -> None:
        now = dt_util.now()
        if self._last_pressed is not None and now - self._last_pressed < _COOLDOWN:
            wait_seconds = int((_COOLDOWN - (now - self._last_pressed)).total_seconds()) + 1
            raise HomeAssistantError(
                f"Vent {wait_seconds} sekund(er) mere før du opdaterer igen — "
                "for at undgå at overbelaste Brunatas login/API."
            )
        self._last_pressed = now
        await self.coordinator.async_request_refresh()
