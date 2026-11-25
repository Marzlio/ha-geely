"""Geely Home Assistant integration. Sign-in and status reads only.

Home Assistant is imported inside the setup functions so the status parser
can be tested without Home Assistant installed.
"""

from __future__ import annotations

from .const import DOMAIN, PLATFORMS as _PLATFORM_NAMES

__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry", "async_reload_entry"]


async def async_setup_entry(hass, entry) -> bool:
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
    from homeassistant.const import Platform

    from .coordinator import GeelyCoordinator

    platforms = [Platform(name) for name in _PLATFORM_NAMES]
    coordinator = GeelyCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise ConfigEntryNotReady(f"{DOMAIN}: {type(err).__name__}") from err
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})["trips"] = coordinator.trips
    _ensure_trip_command(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


def _ensure_trip_command(hass) -> None:
    """One websocket read of the saved routes. Registered once per process."""
    slot = hass.data.setdefault(DOMAIN, {})
    if slot.get("trip_command"):
        return
    import voluptuous as vol
    from homeassistant.components import websocket_api

    @websocket_api.websocket_command({vol.Required("type"): "geely/trip_tracks"})
    @websocket_api.async_response
    async def ws_trip_tracks(hass, connection, msg):
        store = hass.data.get(DOMAIN, {}).get("trips")
        if store is None:
            connection.send_result(msg["id"], {"trips": []})
            return
        connection.send_result(msg["id"], store.tracks())

    websocket_api.async_register_command(hass, ws_trip_tracks)
    slot["trip_command"] = True


async def async_unload_entry(hass, entry) -> bool:
    from homeassistant.const import Platform

    platforms = [Platform(name) for name in _PLATFORM_NAMES]
    return await hass.config_entries.async_unload_platforms(entry, platforms)


async def async_reload_entry(hass, entry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass, entry) -> bool:
    return entry.version == 1
