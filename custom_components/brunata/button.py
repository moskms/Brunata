"""Button platform for Brunata Online: manual "Opdatér nu" and "Tjek for
huller"-triggers.

One of each per config entry (account), not per meter — coordinator.py's
async_request_refresh() always fetches every active meter in a single
GET /consumer/meteroverview call (confirmed in the adaptive-polling work),
so there is no meaningful "refresh just this one meter" action to expose,
and the same reasoning applies to gap-checking (one shared ledger-gap check
loop over every active meter, see coordinator.py's
async_check_and_backfill_all_gaps).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
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

# Gap-checking can trigger a real Brunata history fetch per meter (not just
# a cheap meteroverview snapshot like the refresh button), so it gets a
# longer cooldown — there's also no real reason to press it more than once
# every few minutes, unlike the refresh button's "compare against the
# website right now" use case.
_GAP_CHECK_COOLDOWN = timedelta(minutes=5)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [BrunataRefreshButton(coordinator, entry), BrunataCheckGapsButton(coordinator, entry)]
    )


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


class BrunataCheckGapsButton(CoordinatorEntity[BrunataDataUpdateCoordinator], ButtonEntity):
    """User-facing counterpart to the automatic per-poll ledger gap-healing
    (coordinator.py's _async_backfill_gap_if_needed) — added after the
    2026-08-12/13 gap incident, where the only way to fix it was the
    brunata.backfill_ledger_gap service, which needed the user to know an
    entity_id and a precise date range: too technical for routine use.

    This button needs none of that — it just re-runs the exact same
    check/backfill the coordinator already does after every poll, for every
    active meter, right now, and reports the result via a persistent
    notification (a plain button press has no other way to show its own
    outcome). "Checked and found nothing" is reported just as clearly as
    "found and fixed something" — silence after a press would be
    indistinguishable from the button not having worked at all.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Tjek for huller"

    def __init__(self, coordinator: BrunataDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_check_gaps"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brunata",
            manufacturer="Brunata",
        )
        self._last_pressed: datetime | None = None  # dt_util.now(), set on each accepted press

    async def async_press(self) -> None:
        now = dt_util.now()
        if self._last_pressed is not None and now - self._last_pressed < _GAP_CHECK_COOLDOWN:
            wait_seconds = int((_GAP_CHECK_COOLDOWN - (now - self._last_pressed)).total_seconds()) + 1
            raise HomeAssistantError(
                f"Vent {wait_seconds} sekund(er) mere før du tjekker for huller igen."
            )
        self._last_pressed = now

        results = await self.coordinator.async_check_and_backfill_all_gaps()

        if results:
            naming = {m["meterId"]: m for m in self.coordinator.active_meters}
            lines = []
            for meter_id, gaps in results.items():
                placement = naming.get(meter_id, {}).get("placement", meter_id)
                for gap_start, gap_end, count in gaps:
                    lines.append(
                        f"- {placement}: {gap_start.strftime('%d.%m %H:%M')} – "
                        f"{gap_end.strftime('%d.%m %H:%M')}, {count} punkt(er) hentet"
                    )
            total_gaps = sum(len(gaps) for gaps in results.values())
            message = (
                f"Fandt og udfyldte {total_gaps} hul(ler) på tværs af "
                f"{len(results)} måler(e):\n" + "\n".join(lines)
            )
        else:
            message = "Tjekkede hele historikken for alle målere — ingen huller fundet."

        persistent_notification.async_create(
            self.hass,
            message,
            title="Brunata: hul-tjek",
            notification_id=f"{DOMAIN}_gap_check_{self._entry.entry_id}",
        )
