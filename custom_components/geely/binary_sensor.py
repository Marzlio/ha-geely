"""On and off readings: engine, charge, doors, locks."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BINARY_SENSORS, CONF_VEHICLES
from .coordinator import GeelyCoordinator
from .entity import GeelyEntity

_DEVICE = {
    "running": BinarySensorDeviceClass.RUNNING,
    "battery_charging": BinarySensorDeviceClass.BATTERY_CHARGING,
    "plug": BinarySensorDeviceClass.PLUG,
    "door": BinarySensorDeviceClass.DOOR,
}

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GeelyCoordinator = entry.runtime_data
    async_add_entities(
        GeelyBinary(coordinator, car["vin"], spec)
        for car in entry.data.get(CONF_VEHICLES, [])
        for spec in BINARY_SENSORS
    )


class GeelyBinary(GeelyEntity, BinarySensorEntity):
    def __init__(self, coordinator: GeelyCoordinator, vin: str, spec) -> None:
        super().__init__(coordinator, vin, spec.key, spec.name)
        if spec.device_class:
            self._attr_device_class = _DEVICE[spec.device_class]

    @property
    def is_on(self) -> bool | None:
        value = self.car.get(self._key)
        return None if value is None else bool(value)
