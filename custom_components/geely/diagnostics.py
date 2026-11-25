"""A diagnostics download with the account, the certificate and the position removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACTIVE_SECONDS,
    CONF_IDFA,
    CONF_IDFV,
    CONF_PARKED_SECONDS,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_VEHICLES,
    DEFAULT_ACTIVE_SECONDS,
    DEFAULT_PARKED_SECONDS,
)

_REDACT = {CONF_TOKEN, CONF_USER_ID, CONF_IDFA, CONF_IDFV, "cert_pem", "key_pem", "vin"}

TO_REDACT_IN_CAR = {"latitude", "longitude", "speed_reported", "outside_reported"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    cars = []
    for car in (coordinator.data or {}).values():
        if not isinstance(car, dict):
            continue
        cars.append({
            "name": car.get("name"),
            "series": car.get("series"),
            "battery_pct": car.get("battery_pct"),
            "range_km": car.get("range_km"),
            "odometer_km": car.get("odometer_km"),
            "engine_on": car.get("engine_on"),
            "charging": car.get("charging"),
        })
    return async_redact_data(
        {
            "poll_active_seconds": entry.options.get(
                CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS),
            "poll_parked_seconds": entry.options.get(
                CONF_PARKED_SECONDS, DEFAULT_PARKED_SECONDS),
            "vehicle_count": len(entry.data.get(CONF_VEHICLES, [])),
            "cars": cars,
        },
        _REDACT | TO_REDACT_IN_CAR,
    )
