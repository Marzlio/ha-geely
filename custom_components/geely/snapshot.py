"""Turn one vehicle-status reply into the values the entities show.

A missing field stays None. A speed, outside temperature or other reading
the car marks invalid stays None, and the raw number is kept beside it so
it is not thrown away. This module does not talk to Geely.
"""

from __future__ import annotations

from typing import Any

from .cloud import readings
from .cloud.capabilities import code_3_is_plugged
from .const import STALE_REPORT_SECONDS, WAKE_GAP_SECONDS

_BASIC = ("vehicleStatus", "basicVehicleStatus")
_ADD = ("vehicleStatus", "additionalVehicleStatus")
_EV = (*_ADD, "electricVehicleStatus")
_RUN = (*_ADD, "runningStatus")
_CLIM = (*_ADD, "climateStatus")
_SAFE = (*_ADD, "drivingSafetyStatus")
_MAINT = (*_ADD, "maintenanceStatus")

_ENGINE = {"engine_off": "Off", "engine_running": "Running", "running": "Running",
           "off": "Off", "1": "Running", "0": "Off"}
_PARK = {"0": "Released", "1": "Engaged", "9": "Released", "3": "Engaged"}
_CHARGER = {"0": "Disconnected", "1": "Plugged in", "2": "Plugged in", "3": "Charging"}


def _num(data: dict, path: tuple[str, ...]) -> float | None:
    return readings._number(readings.walk(data, path))


def _invalid(data: dict, block: tuple[str, ...], flag: str) -> bool:
    return readings._flag_is_false(readings.walk(data, block), flag)


def _text(raw: Any, mapping: dict[str, str]) -> str | None:
    if raw is None or raw == "":
        return None
    key = str(raw).strip()
    return mapping.get(key) or mapping.get(key.lower().replace("-", "_")) or key


def _open(data: dict, field: str) -> bool | None:
    raw = readings.walk(data, (*_SAFE, field))
    if raw is None or raw == "":
        return None
    return str(raw).strip() == "1"


def _locked(data: dict, field: str) -> bool | None:
    """True when the car reports the latch locked. On this car that code is 1."""
    raw = readings.walk(data, (*_SAFE, field))
    if raw is None or raw == "":
        return None
    return str(raw).strip() == "1"


def _belt(data: dict, field: str) -> str | None:
    """The car sends true while that belt is released."""
    raw = readings.walk(data, (*_SAFE, field))
    if raw is None or raw == "":
        return None
    released = str(raw).strip().lower() in ("true", "1")
    return "Unbuckled" if released else "Buckled"


def _pressure(data: dict, field: str) -> float | None:
    value = _num(data, (*_MAINT, field))
    if value is None or value <= 0:
        return None
    return value


def _flag(raw: Any) -> bool | None:
    if raw is None or raw == "":
        return None
    return str(raw).strip().lower() in ("true", "1")


def keep(value: Any) -> Any:
    """A JSON-safe copy of one record. Account credentials are removed.
    Everything the car sent, including empty groups, stays."""
    if isinstance(value, dict):
        return {key: keep(item) for key, item in value.items()
                if isinstance(key, str) and _keep_key(key)}
    if isinstance(value, list):
        return [keep(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _keep_key(key: str) -> bool:
    folded = key.lower().replace("_", "")
    return not any(word in folded for word in (
        "token", "email", "secret", "password", "certificate"))


def report_age_seconds(data: Any, now: float) -> float | None:
    """How old the car's own timestamp is, or None when the reply has none."""
    if not isinstance(data, dict):
        return None
    vehicle = data.get("vehicleStatus")
    stamp = vehicle.get("updateTime") if isinstance(vehicle, dict) else None
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    seconds = float(stamp) / 1000.0 if stamp > 10_000_000_000 else float(stamp)
    return now - seconds


def fresh_upload_due(age: float | None, since_wake: float, *,
                     forced: bool = False) -> bool:
    """Whether to ask the car to upload. A manual fetch always does.
    An automatic one never runs twice inside the gap."""
    if forced:
        return True
    if since_wake < WAKE_GAP_SECONDS:
        return False
    if age is None:
        return True
    return age >= STALE_REPORT_SECONDS


def charge_rate(data: Any, catalogue: Any = None) -> dict[str, Any]:
    """Kilowatts, volts and amps of the live charge, or empty when it is not charging.

    The leg is chosen the same way as the local page: the DC contactor for a
    fast charge, otherwise the AC supply when it is actually delivering current.
    """
    empty = {"charge_kw": None, "charge_volts": None, "charge_amps": None, "charge_kind": None}
    leg = readings._charge_leg(
        data if isinstance(data, dict) else {},
        code_3_is_plugged(catalogue if isinstance(catalogue, list) else None))
    if leg is None:
        return empty
    volts, amps, is_ac = leg
    if volts <= 0 or amps <= 0:
        return empty
    watts = volts * amps
    if is_ac and volts >= 300:
        watts *= 3.0 ** 0.5
    return {
        "charge_kw": round(watts / 1000.0, 2),
        "charge_volts": round(volts, 1),
        "charge_amps": round(amps, 1),
        "charge_kind": "AC" if is_ac else "DC",
    }


def parse_status(data: Any) -> dict[str, Any]:
    """The entity values for one snapshot. Never raises on a short reply."""
    if not isinstance(data, dict):
        data = {}
    speed = _num(data, (*_BASIC, "speed"))
    speed_ok = not _invalid(data, _BASIC, "speedValidity")
    outside = _num(data, (*_CLIM, "exteriorTemp"))
    outside_ok = not _invalid(data, _CLIM, "exteriorTempValidity")
    position = readings.walk(data, (*_BASIC, "position"))
    latitude = longitude = altitude = None
    trusted = None
    if isinstance(position, dict):
        latitude = readings.decode_coordinate(position.get("latitude"), max_abs=90.0)
        longitude = readings.decode_coordinate(position.get("longitude"), max_abs=180.0)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            latitude = longitude = None
        else:
            altitude = readings._number(position.get("altitude"))
        trusted = _flag(position.get("posCanBeTrusted"))
    engine_on = readings._engine_running(data)
    return {
        "battery_pct": _num(data, (*_EV, "chargeLevel")),
        "range_km": _num(data, (*_EV, "distanceToEmptyOnBatteryOnly")),
        "speed_kmh": speed if speed_ok else None,
        "speed_reported": speed,
        "speed_valid": speed_ok,
        "avg_speed_kmh": _num(data, (*_RUN, "avgSpeed")),
        "odometer_km": readings._odometer_km(data),
        "cabin_c": _num(data, (*_CLIM, "interiorTemp")),
        "outside_c": outside if outside_ok else None,
        "outside_reported": outside,
        "outside_valid": outside_ok,
        "altitude_m": altitude,
        "latitude": latitude,
        "longitude": longitude,
        "position_trusted": trusted,
        "direction_deg": _num(data, (*_BASIC, "direction")),
        "aux_voltage": _num(data, (*_MAINT, "mainBatteryStatus", "voltage")),
        "distance_to_service_km": _num(data, (*_MAINT, "distanceToService")),
        "days_to_service": _num(data, (*_MAINT, "daysToService")),
        "time_to_full_min": readings._minutes(
            readings.walk(data, (*_EV, "timeToFullyCharged"))),
        "consumption_kwh_100": _num(data, (*_EV, "averPowerConsumption")),
        "trip_consumption_kwh_100": _num(data, (*_EV, "averTraPowerConsumption")),
        "tyre_driver_kpa": _pressure(data, "tyreStatusDriver"),
        "tyre_passenger_kpa": _pressure(data, "tyreStatusPassenger"),
        "tyre_driver_rear_kpa": _pressure(data, "tyreStatusDriverRear"),
        "tyre_passenger_rear_kpa": _pressure(data, "tyreStatusPassengerRear"),
        "charger": _text(
            readings.walk(data, (*_EV, "statusOfChargerConnection")), _CHARGER),
        "engine": _text(readings.walk(data, (*_BASIC, "engineStatus")), _ENGINE),
        "park_brake": _text(
            readings.walk(data, (*_SAFE, "electricParkBrakeStatus")), _PARK),
        "seatbelt_driver": _belt(data, "seatBeltStatusDriver"),
        "seatbelt_passenger": _belt(data, "seatBeltStatusPassenger"),
        "engine_on": engine_on,
        "charging": readings.is_charging(data),
        "plugged_in": _flag(readings.walk(data, (*_EV, "isPluggedIn"))),
        "pre_climate": _flag(readings.walk(data, (*_CLIM, "preClimateActive"))),
        "door_driver": _open(data, "doorOpenStatusDriver"),
        "door_passenger": _open(data, "doorOpenStatusPassenger"),
        "door_driver_rear": _open(data, "doorOpenStatusDriverRear"),
        "door_passenger_rear": _open(data, "doorOpenStatusPassengerRear"),
        "trunk_open": _open(data, "trunkOpenStatus"),
        "hood_open": _open(data, "engineHoodOpenStatus"),
        "lock_driver": _locked(data, "doorLockStatusDriver"),
        "lock_passenger": _locked(data, "doorLockStatusPassenger"),
        "lock_driver_rear": _locked(data, "doorLockStatusDriverRear"),
        "lock_passenger_rear": _locked(data, "doorLockStatusPassengerRear"),
        "trunk_locked": _locked(data, "trunkLockStatus"),
    }
