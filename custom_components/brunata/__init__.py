"""The Brunata Online integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataLoginError

from .const import DOMAIN, PLATFORMS
from .coordinator import BrunataDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = BrunataClient(
        username=entry.data[CONF_USERNAME], password=entry.data[CONF_PASSWORD]
    )
    try:
        await client.login()
    except BrunataLoginError as err:
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err

    coordinator = BrunataDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_import_history_if_needed()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()
    return unloaded
