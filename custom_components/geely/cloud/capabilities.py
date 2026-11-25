"""Read the car's capability catalogue into a list of names.

The catalogue is a list of entries, each with a `functionId` and a
`valueEnable` flag, that says which features the car has. The entry names
follow capabilities.py of the published integration YossiKon/geely-connect
(MIT, see NOTICE.txt). This module only names what the car lists. It builds
no request and nothing here can be sent to a car.
"""
from __future__ import annotations

from typing import Any

# The names shown on the page, in order.
LABELS: tuple[str, ...] = (
    "Lock", "Boot", "Frunk", "Climate", "Seats", "Defrost", "Windows",
    "Sunroof", "Charge schedule", "Find car", "Cabin purge", "Parking comfort",
)

_CLIMATE_ROWS = ("remote_climate_control_2", "combined_climate_control")


def _split(raw: Any) -> set[str]:
    if raw is None:
        return set()
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return {str(p).strip() for p in parts if str(p).strip()}


def _params(entry: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    params = entry.get("paramsJson")
    for p in params if isinstance(params, list) else []:
        if isinstance(p, dict) and p.get("nameKey") and p.get("config") is not None:
            out[p["nameKey"]] = p["config"]
    return out


def code_3_is_plugged(items: list | None) -> bool:
    """True for a car whose catalogue declares the 1.0 charge scheme, where
    connection code 3 means the cable is in rather than a charge running."""
    by_id = {x.get("functionId"): x for x in items or [] if isinstance(x, dict)}
    row_2 = by_id.get("remote_charge_2") or {}
    if row_2.get("valueEnable") in (True, "true", "True", 1, "1"):
        return False
    return "remote_charge_1" in by_id


def listed(items: list | None) -> list[tuple[str, bool]] | None:
    """[(name, whether the car's catalogue lists it)], or None when there is
    no catalogue to read."""
    if items is None:
        return None
    by_id = {x.get("functionId"): x for x in items
             if isinstance(x, dict) and x.get("functionId")}

    def enabled(function_id: str) -> bool:
        entry = by_id.get(function_id)
        return bool(entry and entry.get("valueEnable") in (True, "true", "True", 1, "1"))

    # The car splits its climate declaration across two entries, so when
    # either is enabled the parameters of both are read.
    climate_params: dict[str, Any] = {}
    if any(enabled(row) for row in _CLIMATE_ROWS):
        for row in reversed(_CLIMATE_ROWS):
            climate_params.update(_params(by_id.get(row) or {}))
    climate_devices = str(climate_params.get("climate_devices") or "")

    unlock = by_id.get("remote_control_unlock_2") or {}
    unlock_targets = _split(unlock.get("valueEnum")) | _split(_params(unlock).get("door"))
    lock_1_0 = by_id.get("remote_lock_unlock") or {}
    devices_1_0 = {d.lower() for d in (
        _split(lock_1_0.get("valueEnum"))
        | _split(_params(lock_1_0).get("door"))
        | _split(_params(lock_1_0).get("control_devices")))}

    found = {
        "Lock": any(enabled(f) for f in (
            "door_lock_switch_control", "remote_control_lock_2",
            "remote_control_unlock_2", "remote_lock_unlock")),
        "Boot": (enabled("remote_control_open_2") or enabled("remote_trunk_unlock")
                 or (enabled("remote_control_unlock_2") and "trunk" in unlock_targets)),
        "Frunk": enabled("remote_lock_unlock") and "front_trunk" in devices_1_0,
        "Climate": (any(enabled(r) for r in _CLIMATE_ROWS)
                    or enabled("remote_air_condition_switch")),
        "Seats": any(climate_params.get(k) for k in (
            "dpt_heat_loc", "seatheat_area", "dpt_vent_loc", "seatvent_area")),
        "Defrost": "defrost" in climate_devices,
        "Windows": any(enabled(f) for f in (
            "remote_control_window_2", "remote_control_ventilate_2",
            "remote_window_open", "remote_window_close", "remote_window_ventilate")),
        "Sunroof": enabled("remote_control_skylight_2"),
        "Charge schedule": (enabled("apt_charging_single_cycle_G2")
                            or enabled("remote_appointment_charging")),
        "Find car": enabled("honk_flash"),
        "Cabin purge": enabled("remote_purification"),
        "Parking comfort": enabled("parking_comfortable_2"),
    }
    return [(name, bool(found[name])) for name in LABELS]
