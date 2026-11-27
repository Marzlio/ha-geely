"""A button that asks the car to upload, then reads that upload."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
        GeelyFetch(coordinator, car["vin"])
        for car in entry.data.get(CONF_VEHICLES, [])
    )


class GeelyFetch(GeelyEntity, ButtonEntity):
    """One press wakes the car and reads the status it uploads."""

    def __init__(self, coordinator: GeelyCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "fetch", "Fetch status")

    async def async_press(self) -> None:
        await self.coordinator.async_fetch(self._vin)
