"""Where the car last reported it was. The read does not wake the car."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VEHICLES
from .coordinator import GeelyCoordinator
from .entity import GeelyEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GeelyCoordinator = entry.runtime_data
    async_add_entities(
        GeelyTracker(coordinator, car["vin"])
        for car in entry.data.get(CONF_VEHICLES, [])
    )


class GeelyTracker(GeelyEntity, TrackerEntity):
    def __init__(self, coordinator: GeelyCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "location", "Location")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self.car.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self.car.get("longitude")

    @property
    def extra_state_attributes(self) -> dict:
        return {"position_trusted": self.car.get("position_trusted")}
