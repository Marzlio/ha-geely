"""Polling coordinator. One read of each car, on a timer. No commands."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud.client import GeelyClient, GeelyError
from .cloud.regions import RegionUnavailable
from .cloud.transport import GuardedTransport, TlsTransport
from .const import (
    CONF_ACTIVE_SECONDS,
    CONF_PARKED_SECONDS,
    CONF_VEHICLES,
    DEFAULT_ACTIVE_SECONDS,
    DEFAULT_PARKED_SECONDS,
    DOMAIN,
)
from .session import account_from_data, link_from_saved
from .snapshot import keep, parse_status

_LOGGER = logging.getLogger(__name__)


class GeelyCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Latest parsed snapshot for every car on the account, keyed by VIN."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self._client = GeelyClient(GuardedTransport(TlsTransport()))
        self._catalogues: dict[str, list] = {}
        active = int(entry.options.get(CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=active),
        )

    def _poll(self) -> dict[str, dict[str, Any]]:
        account = account_from_data(dict(self.entry.data))
        found: dict[str, dict[str, Any]] = {}
        moving = False
        for saved in self.entry.data.get(CONF_VEHICLES, []):
            try:
                link = link_from_saved(saved)
                raw = self._client.read_status(account, link)
                state = self._client.read_vehicle_state(account, link)
                reservation = self._client.read_charging_reservation(account, link)
                vin = str(saved["vin"])
                if vin not in self._catalogues:
                    catalogue = self._client.read_catalogue(account, link)
                    if isinstance(catalogue, list):
                        self._catalogues[vin] = catalogue
            except GeelyError as err:
                if err.kind == "auth":
                    raise ConfigEntryAuthFailed("Sign-in expired") from None
                raise UpdateFailed(err.kind) from None
            except RegionUnavailable:
                raise UpdateFailed("region") from None
            parsed = parse_status(raw)
            parsed["name"] = saved.get("name") or "Geely"
            parsed["series"] = saved.get("series") or ""
            parsed["status"] = keep(raw)
            parsed["state"] = keep(state)
            parsed["reservation"] = keep(reservation)
            parsed["catalogue"] = keep(self._catalogues.get(vin, []))
            found[vin] = parsed
            if parsed.get("engine_on") or parsed.get("charging"):
                moving = True
        parked = int(self.entry.options.get(
            CONF_PARKED_SECONDS, DEFAULT_PARKED_SECONDS))
        active = int(self.entry.options.get(
            CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS))
        self.update_interval = timedelta(seconds=active if moving else parked)
        return found

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            return await self.hass.async_add_executor_job(self._poll)
        except (ConfigEntryAuthFailed, UpdateFailed):
            raise
        except Exception as err:
            _LOGGER.debug("status read failed: %s", type(err).__name__)
            raise UpdateFailed(type(err).__name__) from None
