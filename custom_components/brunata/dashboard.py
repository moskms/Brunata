"""Automatic 'Brunata' Lovelace dashboard creation.

Investigated directly against homeassistant/components/lovelace/__init__.py
(HA core source): the live DashboardsCollection instance that owns the
sidebar registration is only ever a local variable inside lovelace's own
async_setup() — hass.data[LOVELACE_DATA] is a LovelaceData dataclass with
fields resource_mode/dashboards/resources/yaml_dashboards only. There is no
dashboards_collection attribute, and no other hass.data key holding a
reference to it. So there is genuinely no accessible API for another
integration to call into the running collection.

Given that, this writes directly to the same two storage files lovelace
itself uses, in the same JSON shape its own DictStorageCollection/
LovelaceStorage classes produce (confirmed by reading dashboard.py and
helpers/collection.py directly):
  - .storage/lovelace_dashboards -> {"items": [{"id", "url_path", "title",
    "icon", "show_in_sidebar", "require_admin", "mode"}, ...]}
  - .storage/lovelace.<id> -> {"config": {"views": [...]}}

This is undocumented internal storage format, not a public API — confirmed
correct against HA's current source at the time of writing, but it could
change on any HA release. Confirmed with the user before implementing this
way (the alternative — no accessible live API at all — leaves no other
option for "automatic" dashboard creation).

Known limitation: because the live DashboardsCollection object can't be
reached, the sidebar panel only picks up this new entry starting from the
user's NEXT Home Assistant restart after first setup — not instantly. This
is logged clearly below.

Only ever creates the dashboard if `url_path == "brunata"` doesn't already
exist — never updates or overwrites it afterwards, including if the user has
since edited it by hand. No other dashboard (e.g. "Overblik") is ever read
or touched, since this only ever appends one new item to the dashboards list
and writes one new, dedicated config file for its own id.

Removal (async_remove_dashboard) is the mirror operation, called from
__init__.py's async_remove_entry — confirmed against HA's config_entries.py
that this hook (not async_unload_entry) is the correct one: it only fires on
permanent config entry deletion, never on a plain unload/reload/disable, and
by the time it runs the entry being removed has already been dropped from
hass.config_entries' own list, so checking for other remaining Brunata
entries there is safe. Unlike creation, removal is unconditional once
called: even a manually-edited dashboard is deleted (confirmed desired
behavior with the user — see README's "Dashboard" section).
"""

from __future__ import annotations

import logging

from homeassistant.components.persistent_notification import async_create as async_create_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_URL_PATH = "brunata"
_DASHBOARDS_STORAGE_KEY = "lovelace_dashboards"
_DASHBOARDS_STORAGE_VERSION = 1
_DASHBOARD_CONFIG_STORAGE_KEY = f"lovelace.{_DASHBOARD_URL_PATH}"
_DASHBOARD_CONFIG_STORAGE_VERSION = 1

# allocationUnit -> brunata-monthly-card.js's `meter_type` config value.
_METER_TYPES = {"O": "heat", "W": "hot_water", "K": "cold_water"}


def _build_views(active_meters: list[dict]) -> list[dict]:
    """One view: a full-width "Forbrug" header, and one same-height section
    per meter type actually present — never a hardcoded assumption of
    exactly three.

    Uses the "sections" view strategy (HA 2024.9+) rather than the classic
    masonry view: masonry auto-balances top-level cards into columns by
    height, which put the header markdown cards in the same column as
    whichever meter card happened to land there first — making that one
    column visibly taller than the other two, with no way in masonry to
    make an arbitrary card span the full width instead. "sections" fixes
    this directly: the header lives in its own section with
    column_span = number of meter sections (a real, dedicated full-width
    row), and each meter type gets its own single-column section below it —
    so all meter sections end up the same height (each renders the same
    fixed Jan-Dec + Total row count regardless of data), and their bottoms
    line up naturally.
    """
    present_units = {m["allocationUnit"] for m in active_meters}

    meter_sections: list[dict] = []
    for allocation_unit in ("O", "W", "K"):
        if allocation_unit not in present_units:
            continue
        meter_sections.append(
            {
                "type": "grid",
                "cards": [
                    {
                        "type": "custom:brunata-monthly-card",
                        "meter_type": _METER_TYPES[allocation_unit],
                        "show_title": False,
                    }
                ],
            }
        )

    column_count = len(meter_sections) or 1
    header_section = {
        "type": "grid",
        "column_span": column_count,
        "cards": [
            {"type": "heading", "heading": "Forbrug"},
            {
                "type": "markdown",
                "content": "**Varme** måles i enheder · **Varmt/Koldt vand** måles i m³",
            },
        ],
    }

    return [
        {
            "title": "Brunata",
            "path": _DASHBOARD_URL_PATH,
            "type": "sections",
            "max_columns": column_count,
            "sections": [header_section, *meter_sections],
        }
    ]


async def async_setup_dashboard(hass: HomeAssistant, active_meters: list[dict]) -> None:
    """Create the 'Brunata' sidebar dashboard once, if it doesn't already exist."""
    try:
        dashboards_store = Store(hass, _DASHBOARDS_STORAGE_VERSION, _DASHBOARDS_STORAGE_KEY)
        dashboards_data = await dashboards_store.async_load()
        items = list((dashboards_data or {}).get("items", []))

        if any(item.get("url_path") == _DASHBOARD_URL_PATH for item in items):
            _LOGGER.debug("Brunata dashboard already registered — leaving it untouched")
            return

        if not active_meters:
            # Nothing to build sections from yet — leave it for a future
            # setup/reload to try again rather than create an empty shell.
            _LOGGER.debug("No active meters yet — skipping dashboard creation for now")
            return

        items.append(
            {
                "id": _DASHBOARD_URL_PATH,
                "url_path": _DASHBOARD_URL_PATH,
                "title": "Brunata",
                "icon": "mdi:water-thermometer",
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
            }
        )
        await dashboards_store.async_save({"items": items})

        config_store = Store(
            hass, _DASHBOARD_CONFIG_STORAGE_VERSION, _DASHBOARD_CONFIG_STORAGE_KEY
        )
        await config_store.async_save({"config": {"views": _build_views(active_meters)}})

        _LOGGER.warning(
            "Created the 'Brunata' dashboard. It will appear in the sidebar "
            "after your next Home Assistant restart — this integration has "
            "no way to register it in the live sidebar without one."
        )
    except Exception as err:  # noqa: BLE001 — deliberately broad, see module docstring
        _LOGGER.warning(
            "Could not automatically create the 'Brunata' dashboard (%s: %s). "
            "Add it manually instead — see the 'Dashboard' section in README.md.",
            type(err).__name__,
            err,
        )
        async_create_notification(
            hass,
            (
                "Brunata kunne ikke oprette sit dashboard automatisk. Tilføj det "
                "manuelt i stedet — se afsnittet \"Dashboard\" i integrationens "
                "README.md for den fulde YAML."
            ),
            title="Brunata",
            notification_id="brunata_dashboard_setup_failed",
        )


async def async_remove_dashboard(hass: HomeAssistant) -> None:
    """Delete the 'Brunata' dashboard entirely — registry entry + content.

    Only call this once the last Brunata config entry is being permanently
    removed (see __init__.py's async_remove_entry, which checks for other
    remaining entries first). Deletes unconditionally, even if the user has
    since edited the dashboard by hand — confirmed desired behavior. Never
    touches any other dashboard's registry entry or content file.
    """
    try:
        dashboards_store = Store(hass, _DASHBOARDS_STORAGE_VERSION, _DASHBOARDS_STORAGE_KEY)
        dashboards_data = await dashboards_store.async_load()
        items = list((dashboards_data or {}).get("items", []))

        remaining = [item for item in items if item.get("url_path") != _DASHBOARD_URL_PATH]
        if len(remaining) != len(items):
            await dashboards_store.async_save({"items": remaining})

        config_store = Store(
            hass, _DASHBOARD_CONFIG_STORAGE_VERSION, _DASHBOARD_CONFIG_STORAGE_KEY
        )
        await config_store.async_remove()  # safe even if it was never created

        _LOGGER.debug("Removed the 'Brunata' dashboard (last config entry removed)")
    except Exception as err:  # noqa: BLE001 — deliberately broad, see module docstring
        _LOGGER.warning(
            "Could not automatically remove the 'Brunata' dashboard (%s: %s). "
            "Remove it manually via Indstillinger → Dashboards if you no "
            "longer want it.",
            type(err).__name__,
            err,
        )
