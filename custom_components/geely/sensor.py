"""Numeric and text readings."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VEHICLES, SENSORS
from .coordinator import GeelyCoordinator
from .entity import GeelyEntity

_DEVICE = {
    "battery": SensorDeviceClass.BATTERY,
    "distance": SensorDeviceClass.DISTANCE,
    "speed": SensorDeviceClass.SPEED,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "voltage": SensorDeviceClass.VOLTAGE,
    "pressure": SensorDeviceClass.PRESSURE,
}
_STATE = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GeelyCoordinator = entry.runtime_data
    async_add_entities(
        GeelySensor(coordinator, car["vin"], spec)
        for car in entry.data.get(CONF_VEHICLES, [])
        for spec in SENSORS
    )


class GeelySensor(GeelyEntity, SensorEntity):
    def __init__(self, coordinator: GeelyCoordinator, vin: str, spec) -> None:
        super().__init__(coordinator, vin, spec.key, spec.name)
        self._spec = spec
        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit
        if spec.device_class:
            self._attr_device_class = _DEVICE[spec.device_class]
        if spec.state_class:
            self._attr_state_class = _STATE[spec.state_class]

    @property
    def native_value(self):
        return self.car.get(self._key)

    @property
    def extra_state_attributes(self) -> dict:
        if self._key == "speed_kmh":
            return {
                "reported": self.car.get("speed_reported"),
                "valid": self.car.get("speed_valid"),
            }
        if self._key == "outside_c":
            return {
                "reported": self.car.get("outside_reported"),
                "valid": self.car.get("outside_valid"),
            }
        return {}
