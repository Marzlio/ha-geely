"""Turn one vehicle-status snapshot into the rows the page shows.

The field paths, value codes and validity rules follow sensor.py,
binary_sensor.py, device_tracker.py and helpers.py of the published
integration YossiKon/geely-connect, itself derived from
nitaybz/geely-global-ha (both MIT, see NOTICE.txt). A reading the car does
not report produces no row.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

_BASIC = ("vehicleStatus", "basicVehicleStatus")
_ADD = ("vehicleStatus", "additionalVehicleStatus")
_MAINT = (*_ADD, "maintenanceStatus")
_EV = (*_ADD, "electricVehicleStatus")
_CLIM = (*_ADD, "climateStatus")
_SAFE = (*_ADD, "drivingSafetyStatus")
_RUN = (*_ADD, "runningStatus")

_CHARGER_CONNECTION = {"0": "Disconnected", "1": "Plugged in",
                       "2": "Plugged in", "3": "Charging"}
# 3 and 9 were measured on real cars; 0 and 1 come with the original mapping.
_PARK_BRAKE = {"0": "Released", "1": "Engaged", "9": "Released", "3": "Engaged"}
_ENGINE = {"engine_off": "Off", "engine_running": "Running", "running": "Running",
           "off": "Off", "1": "Running", "0": "Off"}

# The car's "no estimate" for a minute countdown: every bit of an 11-bit field.
_NO_ESTIMATE_MINUTES = 2047
_ARC_MS_PER_DEGREE = 3_600_000.0


def walk(data: Any, path: tuple[str, ...]) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _minutes(value: Any) -> float | None:
    m = _number(value)
    if m is None or m <= 0 or m >= _NO_ESTIMATE_MINUTES:
        return None
    return m


def _flag_is_false(block: Any, name: str) -> bool:
    """True only when the car explicitly disowns the reading beside a flag."""
    if not isinstance(block, dict):
        return False
    flag = block.get(name)
    return flag is not None and str(flag).strip().lower() in ("false", "0")


def _trim(n: float, digits: int = 1) -> str:
    text = f"{n:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _duration(minutes: float) -> str:
    total = int(round(minutes))
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    return f"{hours} h" if hours else f"{mins} min"


def is_charging(data: dict, *, code_3_is_plugged: bool = False) -> bool:
    """Whether the car is taking charge, from the signals it gives.

    Connection code 3 is the official word and AC sessions say it. A DC fast
    charge can hold the code at 1, so the DC contactor with current flowing
    into the pack also counts. On a car whose catalogue declares the 1.0
    charge scheme, 3 only means the cable is in, and `isCharging` or a
    running countdown decides.
    """
    if str(walk(data, (*_EV, "statusOfChargerConnection"))) == "3":
        if not code_3_is_plugged:
            return True
        flag = str(walk(data, (*_EV, "isCharging"))).strip().lower()
        if flag == "true" or _minutes(walk(data, (*_EV, "timeToFullyCharged"))) is not None:
            return True
        if flag != "false":
            return True
    if str(walk(data, (*_EV, "dcDcConnectStatus"))) == "3":
        amps = _number(walk(data, (*_EV, "dcChargeIAct")))
        if amps is not None:
            return amps < -0.5
    return False


def _charger_connection(data: dict, code_3_is_plugged: bool) -> str | None:
    raw = walk(data, (*_EV, "statusOfChargerConnection"))
    if raw is None or raw == "":
        return None
    label = _CHARGER_CONNECTION.get(str(raw), f"Code {raw}")
    charging = is_charging(data, code_3_is_plugged=code_3_is_plugged)
    if label == "Plugged in" and charging:
        return "Charging"
    if label == "Charging" and not charging:
        return "Plugged in"
    return label


def _mapped(data: dict, path: tuple[str, ...], mapping: dict[str, str]) -> str | None:
    raw = walk(data, path)
    if raw is None or raw == "":
        return None
    key = str(raw).strip()
    found = mapping.get(key) or mapping.get(key.lower().replace("-", "_"))
    # An unknown code is shown as it came, rather than given an invented name.
    return found or f"Code {key[:24]}"


def _seatbelt(data: dict) -> str | None:
    """The car sends true while the driver belt is released."""
    raw = walk(data, (*_SAFE, "seatBeltStatusDriver"))
    if raw is None or raw == "":
        return None
    released = str(raw).strip().lower() in ("true", "1")
    return "Unbuckled" if released else "Buckled"


def _open_closed(data: dict, field: str) -> str | None:
    raw = walk(data, (*_SAFE, field))
    if raw is None or raw == "":
        return None
    return "Open" if str(raw) == "1" else "Closed"


def _pressure(data: dict, field: str) -> str | None:
    kpa = _number(walk(data, (*_MAINT, field)))
    if kpa is None or kpa <= 0:          # a sleeping sensor reports 0
        return None
    return (f"{_trim(kpa, 0)} kPa ({kpa * 0.01:.2f} bar, "
            f"{kpa * 0.1450377:.1f} psi)")


def decode_coordinate(raw: Any, *, max_abs: float) -> float | None:
    """A position field in degrees. The cloud sends arc-milliseconds
    (degrees x 3,600,000); a value already inside the degree range is degrees."""
    v = _number(raw)
    if v is None:
        return None
    if abs(v) <= max_abs:
        return v
    candidate = v / _ARC_MS_PER_DEGREE
    return candidate if abs(candidate) <= max_abs else None


def reported_at(data: dict) -> datetime | None:
    """The car's own time stamp on this snapshot, or None."""
    ms = _number(walk(data, ("vehicleStatus", "updateTime")))
    if ms is None or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class TripBook:
    """Distance of the drive this page is watching, kept in memory.

    The car does not send a last-trip record. The distance is the odometer
    when the engine switches off, minus the odometer when it switched on.
    A drive that finished before this book was watching stays unknown.
    """

    def __init__(self) -> None:
        self.start_km: float | None = None
        self.last_km: float | None = None
        self.was_running: bool | None = None
        self.km: float | None = None

    def update(self, data: dict) -> None:
        running = _engine_running(data)
        km = _odometer_km(data)
        if running is None or km is None:
            return
        if running and not self.was_running:
            self.start_km = km
        elif (not running and self.was_running and self.start_km is not None):
            covered = km - self.start_km
            if 0 <= covered < 2000:
                self.last_km = round(covered, 1)
            self.start_km = None
        self.was_running = running
        self.km = km

    def rows(self) -> list[tuple[str, str]]:
        if self.was_running is None and self.last_km is None:
            return []
        if self.last_km is None:
            last = ("Last trip",
                    "Not seen yet. Refresh across a drive, from engine on "
                    "until engine off.")
        else:
            last = ("Last trip", f"{_trim(self.last_km, 1)} km")
        if self.was_running and self.start_km is not None and self.km is not None:
            covered = max(0.0, self.km - self.start_km)
            progress = ("Trip in progress", f"{_trim(round(covered, 1), 1)} km")
        else:
            progress = ("Trip in progress", "0 km")
        return [last, progress]


def _engine_running(data: dict) -> bool | None:
    raw = walk(data, (*_BASIC, "engineStatus"))
    if raw is None or raw == "":
        return None
    label = _ENGINE.get(str(raw).strip().lower().replace("-", "_"))
    if label == "Running":
        return True
    if label == "Off":
        return False
    return None


def _odometer_km(data: dict) -> float | None:
    km = _number(walk(data, (*_MAINT, "odometer")))
    return km if km is not None and km > 0 else None


def _per_100(data: dict, field: str) -> float | None:
    value = _number(walk(data, (*_EV, field)))
    return value if value is not None and value > 0 else None


def _km_per_kwh(per_100: float | None) -> str | None:
    if per_100 is None:
        return None
    return f"{_trim(100.0 / per_100, 2)} km/kWh"


def _wh_per_km(per_100: float | None) -> str | None:
    if per_100 is None:
        return None
    return f"{_trim(per_100 * 10.0, 0)} Wh/km"


def _efficiency(data: dict) -> str | None:
    return _km_per_kwh(_per_100(data, "averPowerConsumption"))


def _rpm(data: dict) -> str | None:
    raw = _number(walk(data, (*_ADD, "drivingBehaviourStatus", "engineSpeed")))
    if raw is None or raw < 0:
        return None
    return f"{_trim(raw, 0)} rpm"


def _hours(data: dict) -> str | None:
    raw = _number(walk(data, (*_MAINT, "engineHrsToService")))
    if raw is None or raw < 0:
        return None
    return f"{_trim(raw, 0)} h"


def _tenths_km(path: tuple[str, ...], data: dict) -> str | None:
    """The fuel and battery odometers arrive in tenths of a kilometre."""
    raw = _number(walk(data, path))
    if raw is None or raw < 0:
        return None
    return f"{_trim(raw / 10.0, 1)} km"


def _range_at_full(data: dict) -> str | None:
    """The car's remaining range scaled to a full battery. Blank below 10%."""
    charge = _number(walk(data, (*_EV, "chargeLevel")))
    remaining = _number(walk(data, (*_EV, "distanceToEmptyOnBatteryOnly")))
    if charge is None or remaining is None or charge < 10 or remaining <= 0:
        return None
    return f"{_trim(round(remaining * 100.0 / charge), 0)} km"


def _charge_leg(data: dict, code_3_is_plugged: bool) -> tuple[float, float, bool] | None:
    """Volts, amps and whether the live leg is AC, only while charging."""
    def pair(u_key: str, i_key: str) -> tuple[float, float] | None:
        volts = _number(walk(data, (*_EV, u_key)))
        amps = _number(walk(data, (*_EV, i_key)))
        if volts is None or amps is None:
            return None
        return volts, amps

    ac = pair("chargeUAct", "chargeIAct")
    dc = pair("dcChargeUAct", "dcChargeIAct")
    if ac is None and dc is None:
        return None
    if not is_charging(data, code_3_is_plugged=code_3_is_plugged):
        return None
    contactor = str(walk(data, (*_EV, "dcDcConnectStatus"))) == "3"
    if contactor and dc is not None and dc[1] < -0.5:
        return dc[0], abs(dc[1]), False
    if ac is not None and ac[0] >= 100 and ac[1] > 0.5:
        return ac[0], ac[1], True
    return None


def _charge_power(data: dict, code_3_is_plugged: bool) -> str | None:
    leg = _charge_leg(data, code_3_is_plugged)
    if leg is None:
        return None
    volts, amps, is_ac = leg
    if volts <= 0 or amps <= 0:
        return None
    watts = volts * amps
    if is_ac and volts >= 300:
        watts *= 3.0 ** 0.5
    kind = "AC" if is_ac else "DC"
    return f"{_trim(watts / 1000.0, 2)} kW ({kind}, {_trim(volts, 0)} V, {_trim(amps, 1)} A)"


def _pack_power(data: dict) -> str | None:
    volts = _number(walk(data, (*_EV, "dcChargeUAct")))
    amps = _number(walk(data, (*_EV, "dcChargeIAct")))
    if volts is None or amps is None or not 100 <= volts <= 900:
        return None
    kw = volts * amps / 1000.0
    direction = "into the pack" if kw < 0 else "out of the pack"
    return f"{_trim(abs(kw), 2)} kW {direction}"


def _fuel_range_km(data: dict) -> float | None:
    reported = _number(walk(data, (*_BASIC, "distanceToEmpty")))
    if reported is not None and reported >= 0:
        return reported
    litres = _number(walk(data, (*_RUN, "fuelLevel")))
    per_100 = _number(walk(data, (*_RUN, "aveFuelConsumption")))
    if litres is None or per_100 is None or litres < 0 or per_100 <= 0:
        return None
    return litres / per_100 * 100.0


def build_groups(data: dict, *, code_3_is_plugged: bool = False,
                 trip: TripBook | None = None) -> list[dict]:
    """[{title, rows: [(label, text)]}], with absent readings left out and
    empty groups dropped."""

    def unit(path: tuple[str, ...], suffix: str, digits: int = 1) -> str | None:
        n = _number(walk(data, path))
        return None if n is None else f"{_trim(n, digits)} {suffix}"

    time_to_full = _minutes(walk(data, (*_EV, "timeToFullyCharged")))

    speed = None
    if not _flag_is_false(walk(data, _BASIC), "speedValidity"):
        speed = unit((*_BASIC, "speed"), "km/h", 0)

    outside = None
    if not _flag_is_false(walk(data, _CLIM), "exteriorTempValidity"):
        outside = unit((*_CLIM, "exteriorTemp"), "°C")

    days = _number(walk(data, (*_MAINT, "daysToService")))
    # A car on the 1.0 charge scheme sends a constant 1 here in place of a
    # reading, so on those cars a 1 is treated as not reported.
    if days is not None and code_3_is_plugged and int(days) == 1:
        days = None

    position = walk(data, (*_BASIC, "position"))
    location = altitude = None
    if isinstance(position, dict):
        lat = decode_coordinate(position.get("latitude"), max_abs=90.0)
        lon = decode_coordinate(position.get("longitude"), max_abs=180.0)
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            location = f"{lat:.5f}, {lon:.5f}"
            if str(position.get("marsCoordinates")).strip().lower() in ("true", "1"):
                location += " (GCJ-02 grid, offset from a normal map)"
        alt = _number(position.get("altitude"))
        if alt is not None and location is not None:
            altitude = f"{_trim(alt, 0)} m"

    groups = [
        ("Battery and charging", [
            ("Battery", unit((*_EV, "chargeLevel"), "%", 0)),
            ("Electric range", unit((*_EV, "distanceToEmptyOnBatteryOnly"), "km", 0)),
            ("Charger connection", _charger_connection(data, code_3_is_plugged)),
            ("Time to full charge",
             None if time_to_full is None else _duration(time_to_full)),
            ("Average consumption", unit((*_EV, "averPowerConsumption"), "kWh/100 km")),
            ("Trip consumption", unit((*_EV, "averTraPowerConsumption"), "kWh/100 km")),
            ("Charging power", _charge_power(data, code_3_is_plugged)),
            ("Pack power", _pack_power(data)),
        ]),
        ("Driving", [
            ("Speed", speed),
            ("Average speed", unit((*_RUN, "avgSpeed"), "km/h", 1)),
            ("Odometer", unit((*_MAINT, "odometer"), "km")),
            ("Distance on fuel", _tenths_km((*_ADD, "fuelStatus", "odometerOnFuelOnly"), data)),
            ("Distance on battery", _tenths_km((*_EV, "odometerOnBatteryOnly"), data)),
            ("Trip meter", unit((*_RUN, "tripMeter1"), "km")),
            ("Trip meter 2", unit((*_RUN, "tripMeter2"), "km")),
            ("Engine", _mapped(data, (*_BASIC, "engineStatus"), _ENGINE)),
            ("Park brake", _mapped(data, (*_SAFE, "electricParkBrakeStatus"), _PARK_BRAKE)),
            ("Fuel level", unit((*_RUN, "fuelLevel"), "L")),
            ("Fuel level percent", unit((*_RUN, "fuelLevelPct"), "%", 0)),
            ("Fuel consumption", unit((*_RUN, "aveFuelConsumption"), "L/100 km")),
            ("Trip fuel consumption", unit((*_RUN, "aveTraFuelConsumption"), "L/100 km")),
            ("Engine speed", _rpm(data)),
            ("Coolant temperature", unit((*_RUN, "engineCoolantTemperature"), "°C")),
            ("Engine oil health", unit((*_MAINT, "engineOilHealthLevel"), "%", 0)),
            ("Engine hours to service", _hours(data)),
        ]),
        ("Temperature", [
            ("Cabin temperature", unit((*_CLIM, "interiorTemp"), "°C")),
            ("Cabin humidity", unit((*_ADD, "pollutionStatus", "relHumSts"), "%", 0)),
            ("Outside temperature", outside),
        ]),
        ("Tyre pressures", [
            ("Tyre pressure, driver", _pressure(data, "tyreStatusDriver")),
            ("Tyre pressure, passenger", _pressure(data, "tyreStatusPassenger")),
            ("Tyre pressure, rear driver side", _pressure(data, "tyreStatusDriverRear")),
            ("Tyre pressure, rear passenger side",
             _pressure(data, "tyreStatusPassengerRear")),
            ("Tyre temperature, driver", unit((*_MAINT, "tyreTempDriver"), "°C")),
            ("Tyre temperature, passenger", unit((*_MAINT, "tyreTempPassenger"), "°C")),
            ("Tyre temperature, rear driver side",
             unit((*_MAINT, "tyreTempDriverRear"), "°C")),
            ("Tyre temperature, rear passenger side",
             unit((*_MAINT, "tyreTempPassengerRear"), "°C")),
        ]),
        ("Doors, boot and bonnet", [
            ("Driver door", _open_closed(data, "doorOpenStatusDriver")),
            ("Passenger door", _open_closed(data, "doorOpenStatusPassenger")),
            ("Rear door, driver side", _open_closed(data, "doorOpenStatusDriverRear")),
            ("Rear door, passenger side",
             _open_closed(data, "doorOpenStatusPassengerRear")),
            ("Boot", _open_closed(data, "trunkOpenStatus")),
            ("Bonnet", _open_closed(data, "engineHoodOpenStatus")),
            ("Driver seatbelt", _seatbelt(data)),
        ]),
        ("12 V battery and service", [
            ("12 V battery", unit((*_MAINT, "mainBatteryStatus", "chargeLevel"), "%", 0)),
            ("12 V battery voltage", unit((*_MAINT, "mainBatteryStatus", "voltage"), "V")),
            ("Distance to service", unit((*_MAINT, "distanceToService"), "km", 0)),
            ("Days to service", None if days is None else f"{int(days)} days"),
        ]),
        ("Location", [
            ("Location", location),
            ("Altitude", altitude),
        ]),
    ]
    book = trip
    if book is None and _odometer_km(data) is not None and _engine_running(data) is not None:
        book = TripBook()
        book.update(data)
    fuel_km = _fuel_range_km(data)
    electric_km = _number(walk(data, (*_EV, "distanceToEmptyOnBatteryOnly")))
    combined = None
    if fuel_km is not None and electric_km is not None and electric_km >= 0:
        combined = f"{_trim(round(fuel_km + electric_km), 0)} km"
    analytics = []
    if book is not None:
        analytics.extend(book.rows())
    analytics.extend([
        ("Efficiency", _efficiency(data)),
        ("Energy per km", _wh_per_km(_per_100(data, "averPowerConsumption"))),
        ("Trip efficiency", _km_per_kwh(_per_100(data, "averTraPowerConsumption"))),
        ("Trip energy per km", _wh_per_km(_per_100(data, "averTraPowerConsumption"))),
        ("Range at full charge", _range_at_full(data)),
        ("Fuel range", None if fuel_km is None else f"{_trim(round(fuel_km), 0)} km"),
        ("Combined range", combined),
    ])
    groups.insert(0, ("Trips and analytics", analytics))
    out = []
    for title, rows in groups:
        present = [(label, text) for label, text in rows if text is not None]
        if present:
            out.append({"title": title, "rows": present})
    return out


# Every row label build_groups can produce. The tests check a full snapshot
# against it.
ALL_LABELS: tuple[str, ...] = (
    "Last trip", "Trip in progress", "Efficiency", "Energy per km",
    "Trip efficiency", "Trip energy per km", "Range at full charge",
    "Battery", "Electric range", "Charger connection", "Time to full charge",
    "Average consumption", "Trip consumption", "Speed", "Odometer",
    "Trip meter", "Trip meter 2", "Engine", "Park brake", "Cabin temperature",
    "Outside temperature", "Tyre pressure, driver", "Tyre pressure, passenger",
    "Tyre pressure, rear driver side", "Tyre pressure, rear passenger side",
    "Driver door", "Passenger door", "Rear door, driver side",
    "Rear door, passenger side", "Boot", "Bonnet", "12 V battery",
    "12 V battery voltage", "Distance to service", "Days to service",
    "Location", "Altitude",
)
