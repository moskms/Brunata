"""Sensor platform for Brunata Online: heat, hot water, cold water."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfEnergy, UnitOfVolume

from .const import ALLOCATION_UNIT_NAMES, ALLOCATION_UNIT_SLUGS, DOMAIN
from .coordinator import BrunataDataUpdateCoordinator

# allocationUnit -> (device_class, native_unit, attribute on ConsumptionData)
_SENSOR_SPEC = {
    "O": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, "heat_kwh"),
    "W": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS, "hot_water_m3"),
    "K": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS, "cold_water_m3"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BrunataSensor(coordinator, entry, allocation_unit)
        for allocation_unit in _SENSOR_SPEC
    )


class BrunataSensor(CoordinatorEntity[BrunataDataUpdateCoordinator], SensorEntity):
    """One sensor for one Brunata meter type (heat/hot water/cold water)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BrunataDataUpdateCoordinator,
        entry: ConfigEntry,
        allocation_unit: str,
    ) -> None:
        super().__init__(coordinator)
        self._allocation_unit = allocation_unit
        self._attr_device_class, self._attr_native_unit_of_measurement, self._value_attr = (
            _SENSOR_SPEC[allocation_unit]
        )
        self._attr_name = ALLOCATION_UNIT_NAMES[allocation_unit]

        # Explicit, predictable entity_id — required so the one-time history
        # backfill (coordinator.async_import_history_if_needed ->
        # statistics.py) targets the exact same statistic_id this entity's
        # own long-term statistics will be compiled under. Also matches the
        # entity IDs required by copilot-instructions-del2.md.
        slug = ALLOCATION_UNIT_SLUGS[allocation_unit]
        self.entity_id = f"sensor.brunata_{slug}"

        # unique_id is the real Brunata meter_id for this allocation unit, not
        # the entity name, so it survives renames/restarts (del2 requirement).
        # Falls back to a per-entry id if no matching meter has been seen yet
        # (e.g. very first update failed) so entity creation never crashes.
        meter = self._find_meter()
        self._attr_unique_id = (
            f"{DOMAIN}_{meter.meter_id}" if meter else f"{DOMAIN}_{entry.entry_id}_{allocation_unit}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brunata",
            manufacturer="Brunata",
        )

    def _find_meter(self):
        if not self.coordinator.data:
            return None
        for meter in self.coordinator.data.raw_meters:
            if meter.allocation_unit == self._allocation_unit:
                return meter
        return None

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return getattr(self.coordinator.data, self._value_attr)
