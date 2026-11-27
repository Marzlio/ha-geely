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
    "power": SensorDeviceClass.POWER,
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
    cars = entry.data.get(CONF_VEHICLES, [])
    async_add_entities(
        [GeelySensor(coordinator, car["vin"], spec)
         for car in cars for spec in SENSORS]
        + [GeelyFullStatus(coordinator, car["vin"]) for car in cars]
        + [GeelyTrips(coordinator, car["vin"]) for car in cars]
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
        if self._key == "charge_kw":
            return {
                "volts": self.car.get("charge_volts"),
                "amps": self.car.get("charge_amps"),
                "supply": self.car.get("charge_kind"),
            }
        return {}


class GeelyFullStatus(GeelyEntity, SensorEntity):
    """The whole reply, so a dashboard can use any field later.

    The state is the car's own timestamp. The attributes are the records."""

    def __init__(self, coordinator: GeelyCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "full_status", "Full status")

    @property
    def native_value(self):
        status = self.car.get("status")
        if isinstance(status, dict):
            vehicle = status.get("vehicleStatus")
            if isinstance(vehicle, dict) and vehicle.get("updateTime") is not None:
                return str(vehicle["updateTime"])
        return None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "status": self.car.get("status") or {},
            "state": self.car.get("state") or {},
            "reservation": self.car.get("reservation") or {},
            "catalogue": self.car.get("catalogue") or [],
        }


class GeelyTrips(GeelyEntity, SensorEntity):
    """Whether a drive is being recorded, and the saved drives without their routes."""

    def __init__(self, coordinator: GeelyCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "trips", "Trips")

    @property
    def native_value(self):
        return self.coordinator.trips.summary(self._vin)["state"]

    @property
    def extra_state_attributes(self) -> dict:
        summary = self.coordinator.trips.summary(self._vin)
        return {"saved": summary["saved"], "trips": summary["trips"]}
