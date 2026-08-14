"""The Brunata Online integration."""

from __future__ import annotations

import os

from homeassistant.components import persistent_notification
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .brunata_client import BrunataClient
from .brunata_client.exceptions import BrunataInvalidCredentialsError, BrunataLoginError

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
    #
    # cache_headers=True (confirmed fix, 2026-08-15): the original False was
    # convenient during active development (every JS edit visible on a plain
    # reload, no manual cache-clear needed), but in steady-state use it forces
    # the browser to re-fetch the whole file over the network on EVERY single
    # dashboard load. On a slow/flaky connection (confirmed real case: the
    # Android companion app on mobile data) that fetch loses the race against
    # Lovelace trying to render `custom:brunata-monthly-card` before the
    # custom element has actually been defined — surfacing as a red
    # "Konfigurationsfejl" card that only goes away once the browser happens
    # to have the file cached (e.g. after a manual refresh gave it time).
    # cache_headers=True lets the browser cache the file normally (with
    # proper ETag/Last-Modified revalidation, not an unconditional forever-
    # cache) — future JS edits still show up on a normal reload, they're just
    # no longer racing a full re-download on every single page load.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_FRONTEND_JS_URL, js_path, cache_headers=True)]
    )
    # NOT frontend.add_extra_js_url() (confirmed real case, 2026-08-15):
    # that injects a plain deferred <script type="module"> into every page's
    # <head>, with NO guarantee it finishes loading before Lovelace tries to
    # render a `custom:brunata-monthly-card` — cache_headers=True alone
    # didn't fix this on a slow/flaky connection (Android companion app),
    # which still reliably lost that race and showed "Konfigurationsfejl"
    # even after a full redeploy + restart. Registering it as a proper
    # Lovelace *resource* instead (see dashboard.py's
    # async_ensure_lovelace_resource) uses the mechanism ha-panel-lovelace
    # itself explicitly loads and waits for before evaluating any
    # dashboard's cards — the actual guarantee this needs, not just a
    # faster/cached fetch of the same unguaranteed race.
    await dashboard.async_ensure_lovelace_resource(hass, _FRONTEND_JS_URL)
    return True


def _login_retry_notification_id(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_login_retry_{entry.entry_id}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = BrunataClient(
        username=entry.data[CONF_USERNAME], password=entry.data[CONF_PASSWORD]
    )
    try:
        await client.login()
    except BrunataInvalidCredentialsError as err:
        # Confirmed wrong username/password (Keycloak login page itself said
        # so) — genuinely needs the user to fix their credentials.
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except BrunataLoginError as err:
        # Anything else in the login flow (e.g. the confirmed 2026-08-12
        # case: a transient 503 from Brunata's own /rest/authorize) is NOT
        # a credentials problem — ConfigEntryAuthFailed would wrongly tell
        # the user to re-enter a password that was never actually wrong.
        # ConfigEntryNotReady instead makes HA retry setup automatically
        # with backoff, no user action required.
        await client.close()
        persistent_notification.async_create(
            hass,
            f"Brunata-integrationen kunne midlertidigt ikke logge ind hos "
            f"Brunata Online ({err}). Home Assistant prøver automatisk "
            "igen — ingen handling nødvendig, medmindre dette gentager sig "
            "over længere tid.",
            title="Brunata: midlertidig loginfejl",
            notification_id=_login_retry_notification_id(entry),
        )
        raise ConfigEntryNotReady(str(err)) from err
    else:
        # Clears any stale notification from a previous failed attempt that
        # HA has now successfully retried past.
        persistent_notification.async_dismiss(hass, _login_retry_notification_id(entry))

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
        coordinator.async_shutdown_adaptive_schedule()
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
