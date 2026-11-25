"""Shared device and availability for one car."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeelyCoordinator


class GeelyEntity(CoordinatorEntity[GeelyCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: GeelyCoordinator, vin: str, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._vin = vin
        self._key = key
        self._attr_unique_id = f"{vin}_{key}"
        self._attr_name = name

    @property
    def car(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        car = data.get(self._vin)
        return car if isinstance(car, dict) else {}

    @property
    def available(self) -> bool:
        return super().available and self._vin in (self.coordinator.data or {})

    @property
    def device_info(self) -> DeviceInfo:
        car = self.car
        return DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            name=str(car.get("name") or "Geely"),
            manufacturer="Geely",
            model=str(car.get("series") or "") or None,
        )
