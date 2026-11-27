"""Names shared by the integration. No Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "geely"

CONF_EMAIL = "email"
CONF_COUNTRY = "country"
CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_IDFA = "idfa"
CONF_IDFV = "idfv"
CONF_VEHICLES = "vehicles"
CONF_CODE = "code"
CONF_ACTIVE_SECONDS = "active_seconds"
CONF_PARKED_SECONDS = "parked_seconds"

DEFAULT_ACTIVE_SECONDS = 45
DEFAULT_PARKED_SECONDS = 45
# A quiet car is asked to upload at most this often. The Geely app does not
# use a timer: it sends the same request only when its map is opened.
WAKE_GAP_SECONDS = 600
# The cloud copy is old enough that a new charge or a move would be missed.
STALE_REPORT_SECONDS = 180
# How long to wait after the wake before reading the upload.
WAKE_SETTLE_SECONDS = 8

PLATFORMS = ("sensor", "binary_sensor", "device_tracker", "button")


@dataclass(frozen=True)
class SensorSpec:
    """One numeric or text reading."""

    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"


@dataclass(frozen=True)
class BinarySpec:
    """One on/off reading. `on_means` is only for the entity name."""

    key: str
    name: str
    device_class: str | None = None


SENSORS: tuple[SensorSpec, ...] = (
    SensorSpec("battery_pct", "Battery", "%", "battery"),
    SensorSpec("range_km", "Electric range", "km", "distance"),
    SensorSpec("speed_kmh", "Speed", "km/h", "speed"),
    SensorSpec("avg_speed_kmh", "Average speed", "km/h", "speed"),
    SensorSpec("odometer_km", "Odometer", "km", "distance", "total_increasing"),
    SensorSpec("cabin_c", "Cabin temperature", "°C", "temperature"),
    SensorSpec("outside_c", "Outside temperature", "°C", "temperature"),
    SensorSpec("altitude_m", "Altitude", "m", "distance"),
    SensorSpec("direction_deg", "Direction", "°"),
    SensorSpec("aux_voltage", "12 V battery voltage", "V", "voltage"),
    SensorSpec("distance_to_service_km", "Distance to service", "km", "distance"),
    SensorSpec("days_to_service", "Days to service", "d", None),
    SensorSpec("time_to_full_min", "Time to full charge", "min", None),
    SensorSpec("charge_kw", "Charge power", "kW", "power"),
    SensorSpec("consumption_kwh_100", "Average consumption", "kWh/100km"),
    SensorSpec("trip_consumption_kwh_100", "Trip consumption", "kWh/100km"),
    SensorSpec("tyre_driver_kpa", "Tyre pressure, driver", "kPa", "pressure"),
    SensorSpec("tyre_passenger_kpa", "Tyre pressure, passenger", "kPa", "pressure"),
    SensorSpec("tyre_driver_rear_kpa", "Tyre pressure, rear driver side", "kPa", "pressure"),
    SensorSpec("tyre_passenger_rear_kpa", "Tyre pressure, rear passenger side", "kPa", "pressure"),
    SensorSpec("charger", "Charger", None, None, None),
    SensorSpec("engine", "Engine", None, None, None),
    SensorSpec("park_brake", "Park brake", None, None, None),
    SensorSpec("seatbelt_driver", "Driver seatbelt", None, None, None),
    SensorSpec("seatbelt_passenger", "Passenger seatbelt", None, None, None),
)

BINARY_SENSORS: tuple[BinarySpec, ...] = (
    BinarySpec("engine_on", "Engine running", "running"),
    BinarySpec("charging", "Charging", "battery_charging"),
    BinarySpec("plugged_in", "Plugged in", "plug"),
    BinarySpec("pre_climate", "Pre-climate"),
    BinarySpec("door_driver", "Driver door", "door"),
    BinarySpec("door_passenger", "Passenger door", "door"),
    BinarySpec("door_driver_rear", "Rear door, driver side", "door"),
    BinarySpec("door_passenger_rear", "Rear door, passenger side", "door"),
    BinarySpec("trunk_open", "Boot", "door"),
    BinarySpec("hood_open", "Bonnet", "door"),
    BinarySpec("lock_driver", "Driver door locked"),
    BinarySpec("lock_passenger", "Passenger door locked"),
    BinarySpec("lock_driver_rear", "Rear door locked, driver side"),
    BinarySpec("lock_passenger_rear", "Rear door locked, passenger side"),
    BinarySpec("trunk_locked", "Boot locked"),
)
