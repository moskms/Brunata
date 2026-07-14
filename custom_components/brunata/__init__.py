"""The Brunata Online integration."""

from __future__ import annotations

import os

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataLoginError

from . import dashboard, services, websocket_api  # services: permanent debug tool, see services.py
from .const import DOMAIN, PLATFORMS
from .coordinator import BrunataDataUpdateCoordinator

# Del 3b frontend card. Registered once in async_setup (domain-wide, not
# per-entry) — same reasoning as the WebSocket commands below.
_FRONTEND_JS_URL = "/brunata_static/brunata-monthly-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Called once per HA startup, regardless of how many config entries exist."""
    websocket_api.async_register_commands(hass)
    services.async_register_services(hass)  # permanent debug tool, see services.py

    js_path = os.path.join(os.path.dirname(__file__), "www", "brunata-monthly-card.js")
    # NOTE: StaticPathConfig / async_register_static_paths is the current
    # (HA 2023.9+) async API for this — worth checking against your actual
    # HA version if this raises, same caveat as the recorder statistics API.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_FRONTEND_JS_URL, js_path, cache_headers=False)]
    )
    frontend.add_extra_js_url(hass, _FRONTEND_JS_URL)
    return True


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
    await dashboard.async_setup_dashboard(hass, coordinator.active_meters)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called only on permanent deletion of a config entry — never on a plain
    unload/reload/disable (confirmed against HA's config_entries.py: this is
    a separate hook from async_unload_entry, invoked by async_remove()).

    By the time this runs, `entry` has already been removed from
    hass.config_entries' own list (also confirmed against config_entries.py:
    `del self._entries[entry.entry_id]` happens before this hook is called),
    so an empty async_entries(DOMAIN) here correctly means "no other Brunata
    accounts remain" without needing to filter `entry` out ourselves.
    """
    if hass.config_entries.async_entries(DOMAIN):
        return  # other Brunata account(s) still configured — keep the dashboard
    await dashboard.async_remove_dashboard(hass)
