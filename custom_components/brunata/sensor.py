"""Sensor platform for Brunata Online: one sensor per active physical meter.

Generalized (Del 3a) — no fixed assumption of exactly one heat/hot-water/
cold-water meter. Entities are built from whatever coordinator.active_meters
actually contains.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume

from .const import DOMAIN, build_meter_naming
from .coordinator import BrunataDataUpdateCoordinator

# allocationUnit -> (device_class, native_unit). Still keyed only by
# allocationUnit, per copilot-instructions-del3.md point 3 — unrelated to how
# many meters of that type exist.
_SENSOR_SPEC = {
    "O": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "W": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS),
    "K": (SensorDeviceClass.WATER, UnitOfVolume.CUBIC_METERS),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BrunataDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    meters_by_id = {m["meterId"]: m for m in coordinator.active_meters}
    naming = build_meter_naming(coordinator.active_meters)

    entities = [
        BrunataSensor(
            coordinator,
            entry,
            meter_id=meter_id,
            allocation_unit=meters_by_id[meter_id]["allocationUnit"],
            object_id=object_id,
            name=name,
        )
        for meter_id, (object_id, name) in naming.items()
    ]
    entities.append(BrunataStatusSensor(coordinator, entry))
    async_add_entities(entities)


class BrunataSensor(CoordinatorEntity[BrunataDataUpdateCoordinator], SensorEntity):
    """One sensor for one physical Brunata meter."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BrunataDataUpdateCoordinator,
        entry: ConfigEntry,
        meter_id: int,
        allocation_unit: str,
        object_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_device_class, self._attr_native_unit_of_measurement = _SENSOR_SPEC[
            allocation_unit
        ]
        self._attr_name = name

        # Explicit, predictable entity_id — required so the one-time history
        # backfill (coordinator.async_import_history_if_needed ->
        # statistics.py) targets the exact same statistic_id this entity's
        # own long-term statistics will be compiled under.
        self.entity_id = f"sensor.brunata_{object_id}"

        # unique_id is the real Brunata meter_id, never allocationUnit or
        # placement (both can change or collide) — del3a requirement.
        self._attr_unique_id = f"{entry.entry_id}_{meter_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brunata",
            manufacturer="Brunata",
        )

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        reading = self.coordinator.data.get(self._meter_id)
        if reading is None or reading.reading_value is None:
            return None
        # Mirrors fetch_consumption_data()'s own aggregation formula: heat
        # (O) meter readings are raw pulses and need the scale multiplier;
        # water meters (unit already m³) don't have a scale.
        return (
            reading.reading_value * reading.scale
            if reading.scale is not None
            else reading.reading_value
        )


class BrunataStatusSensor(CoordinatorEntity[BrunataDataUpdateCoordinator], SensorEntity):
    """Diagnostic-only entity with no unit_of_measurement, state_class, or
    device_class — unlike the three real meter sensors above.

    Exists purely so coordinator.py's _log_activity() has an entity_id its
    logbook entries can attach to that Home Assistant's own Activity tab
    won't silently filter out. Confirmed against homeassistant/components/
    logbook/helpers.py's is_sensor_continuous(): ANY sensor entity with a
    unit_of_measurement, a state_class, or a numeric device_class is
    treated as a "continuous" data source and excluded from the Activity/
    logbook view entirely — which the three meter sensors all correctly
    have (required for the Energy dashboard), so they could never show
    activity entries no matter how _log_activity() was pointed at them.
    This entity deliberately has none of those three attributes, so it is
    not "continuous" and its logbook entries DO show up.

    Its own state ("OK"/"Fejl", mirroring the coordinator's last poll
    outcome) is secondary — a small bonus beyond just being a valid logbook
    anchor, per the diagnostic-entity convention (entity_category=
    diagnostic keeps it out of the way on the main device card).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Status"

    def __init__(self, coordinator: BrunataDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        # Deliberately NOT an explicit self.entity_id override (unlike
        # BrunataSensor above) — this entity has no long-term-statistics
        # dependency on a predictable entity_id, so HA's own entity registry
        # is free to pick/disambiguate it normally (matters if more than one
        # Brunata account is ever configured). coordinator.py resolves the
        # actual, current entity_id by unique_id via the entity registry
        # instead of assuming a fixed string — see _log_activity().
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brunata",
            manufacturer="Brunata",
        )

    @property
    def native_value(self) -> str:
        return "OK" if self.coordinator.last_update_success else "Fejl"
