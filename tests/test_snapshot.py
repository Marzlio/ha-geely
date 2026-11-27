"""The snapshot parser and the request allow-list. No network, no Home Assistant."""

from __future__ import annotations

import unittest

from custom_components.geely.cloud.guard import ALLOWED
from custom_components.geely.snapshot import (
    charge_rate,
    consumption,
    fresh_upload_due,
    keep,
    parse_status,
    report_age_seconds,
    reported_days,
)


def _sample() -> dict:
    return {"vehicleStatus": {
        "updateTime": "1795361037799",
        "basicVehicleStatus": {
            "engineStatus": "engine_running",
            "speed": "85.0",
            "speedValidity": "false",
            "direction": "234",
            "distanceToEmpty": "320",
            "position": {
                "latitude": "-90000000",
                "longitude": "100800000",
                "altitude": "1470",
                "posCanBeTrusted": "true",
                "marsCoordinates": "false",
            },
        },
        "additionalVehicleStatus": {
            "electricVehicleStatus": {
                "chargeLevel": "93",
                "distanceToEmptyOnBatteryOnly": "320",
                "isCharging": "false",
                "isPluggedIn": "false",
                "statusOfChargerConnection": "0",
                "timeToFullyCharged": "0",
            },
            "maintenanceStatus": {
                "odometer": "9893.4",
                "distanceToService": "10121",
                "daysToService": "1",
                "mainBatteryStatus": {"voltage": "14.0"},
                "tyreStatusDriver": "0",
            },
            "runningStatus": {"avgSpeed": "41"},
            "climateStatus": {
                "preClimateActive": "true",
                "exteriorTemp": "151",
                "exteriorTempValidity": "false",
            },
            "drivingSafetyStatus": {
                "doorOpenStatusDriver": "0",
                "doorLockStatusDriver": "1",
                "trunkOpenStatus": "0",
                "trunkLockStatus": "1",
                "engineHoodOpenStatus": "0",
                "electricParkBrakeStatus": "0",
                "seatBeltStatusDriver": "false",
                "seatBeltStatusPassenger": "true",
            },
        },
    }}


class Snapshot(unittest.TestCase):
    def test_an_invalid_speed_is_kept_but_not_shown_as_speed(self):
        parsed = parse_status(_sample())
        self.assertIsNone(parsed["speed_kmh"])
        self.assertEqual(parsed["speed_reported"], 85.0)
        self.assertFalse(parsed["speed_valid"])
        self.assertIsNone(parsed["outside_c"])
        self.assertEqual(parsed["outside_reported"], 151.0)

    def test_the_readings_that_were_valid(self):
        parsed = parse_status(_sample())
        self.assertEqual(parsed["battery_pct"], 93.0)
        self.assertEqual(parsed["range_km"], 320.0)
        self.assertEqual(parsed["odometer_km"], 9893.4)
        self.assertEqual(parsed["aux_voltage"], 14.0)
        self.assertEqual(parsed["altitude_m"], 1470.0)
        self.assertEqual(parsed["latitude"], -25.0)
        self.assertEqual(parsed["longitude"], 28.0)
        self.assertTrue(parsed["position_trusted"])
        self.assertTrue(parsed["engine_on"])
        self.assertFalse(parsed["charging"])
        self.assertFalse(parsed["door_driver"])
        self.assertTrue(parsed["lock_driver"])
        self.assertTrue(parsed["trunk_locked"])
        self.assertEqual(parsed["seatbelt_driver"], "Buckled")
        self.assertEqual(parsed["seatbelt_passenger"], "Unbuckled")
        self.assertTrue(parsed["pre_climate"])
        self.assertIsNone(parsed["tyre_driver_kpa"])
        self.assertEqual(parsed["park_brake"], "Released")

    def test_charge_power_is_the_live_supply(self):
        data = _sample()
        electric = data["vehicleStatus"]["additionalVehicleStatus"]["electricVehicleStatus"]
        electric["statusOfChargerConnection"] = "3"
        electric["chargeUAct"] = "230.4"
        electric["chargeIAct"] = "28.0"
        electric["dcDcConnectStatus"] = "0"
        rate = charge_rate(data)
        self.assertEqual(rate["charge_kw"], 6.45)
        self.assertEqual(rate["charge_volts"], 230.4)
        self.assertEqual(rate["charge_amps"], 28.0)
        self.assertEqual(rate["charge_kind"], "AC")
        self.assertEqual(rate["charge_source"], "measured")
        self.assertIsNone(charge_rate(_sample())["charge_kw"])

    def test_charge_power_is_estimated_from_time_and_percent(self):
        data = _sample()
        electric = data["vehicleStatus"]["additionalVehicleStatus"]["electricVehicleStatus"]
        electric["statusOfChargerConnection"] = "3"
        electric["isCharging"] = "true"
        electric["chargeLevel"] = "38"
        electric["timeToFullyCharged"] = "540"
        rate = charge_rate(data)
        self.assertEqual(rate["charge_kw"], 2.71)
        self.assertEqual(rate["charge_source"], "estimated")
        self.assertIsNone(rate["charge_volts"])

    def test_the_whole_reply_is_kept(self):
        raw = _sample()
        raw["result"] = {"sessionId": "TX1", "accessToken": "nope"}
        kept = keep(raw)
        self.assertEqual(kept["result"]["sessionId"], "TX1")
        self.assertNotIn("accessToken", kept["result"])
        self.assertEqual(
            kept["vehicleStatus"]["basicVehicleStatus"]["speed"], "85.0")
        self.assertEqual(keep({"list": []}), {"list": []})

    def test_a_short_reply_does_not_invent_values(self):
        parsed = parse_status({"vehicleStatus": {}})
        self.assertIsNone(parsed["battery_pct"])
        self.assertIsNone(parsed["latitude"])
        self.assertIsNone(parsed["engine_on"])


    def test_a_quiet_car_is_asked_to_upload_once(self):
        self.assertTrue(fresh_upload_due(200, 1000))
        self.assertFalse(fresh_upload_due(200, 30))
        self.assertFalse(fresh_upload_due(20, 1000))
        self.assertTrue(fresh_upload_due(None, 1000))
        self.assertFalse(fresh_upload_due(None, 30))
        self.assertTrue(fresh_upload_due(20, 30, forced=True))
        self.assertEqual(
            report_age_seconds(
                {"vehicleStatus": {"updateTime": 1_700_000_000_000}},
                1_700_000_100),
            100)


class Duration(unittest.TestCase):
    def test_time_to_full_reads_as_hours_and_minutes(self):
        from custom_components.geely.cloud.readings import _duration

        self.assertEqual(_duration(532), "8 h 52 min")
        self.assertEqual(_duration(60), "1 h")
        self.assertEqual(_duration(45), "45 min")
        self.assertEqual(_duration(90), "1 h 30 min")


class Consumption(unittest.TestCase):
    def test_average_is_estimated_from_the_range_when_the_car_omits_it(self):
        rate = consumption(_sample())
        self.assertEqual(rate["consumption_kwh_100"], 11.45)
        self.assertEqual(rate["consumption_source"], "estimated")
        self.assertIsNone(rate["trip_consumption_kwh_100"])

    def test_a_reported_average_is_kept(self):
        data = _sample()
        electric = data["vehicleStatus"]["additionalVehicleStatus"]["electricVehicleStatus"]
        electric["averPowerConsumption"] = "16.4"
        electric["averTraPowerConsumption"] = "12.1"
        rate = consumption(data)
        self.assertEqual(rate["consumption_kwh_100"], 16.4)
        self.assertEqual(rate["consumption_source"], "measured")
        self.assertEqual(rate["trip_consumption_kwh_100"], 12.1)
        self.assertEqual(rate["trip_consumption_source"], "measured")

    def test_the_constant_one_day_to_service_is_not_a_reading(self):
        catalogue = [{"functionId": "remote_charge_1", "valueEnable": True}]
        self.assertEqual(parse_status(_sample())["days_to_service"], 1)
        self.assertIsNone(reported_days(1, catalogue))
        self.assertEqual(reported_days(220, catalogue), 220)
        self.assertEqual(reported_days(1, None), 1)

    def test_a_drive_estimates_trip_consumption_from_the_battery_drop(self):
        import os
        import tempfile

        from custom_components.geely.trips import TripLog

        folder = tempfile.mkdtemp()
        log = TripLog(os.path.join(folder, "trips.json"))
        start = {
            "t": 1_000, "engine_on": True, "odometer_km": 100.0,
            "battery_pct": 80, "latitude": -25.0, "longitude": 28.0,
        }
        end = {
            "t": 2_000, "engine_on": False, "odometer_km": 128.4,
            "battery_pct": 71, "latitude": -25.1, "longitude": 28.1,
        }
        log.observe("CAR", start)
        self.assertIsNone(log.kwh_per_100("CAR"))
        log.observe("CAR", end)
        self.assertEqual(log.kwh_per_100("CAR"), 12.49)


class AllowList(unittest.TestCase):
    def test_the_only_wake_is_the_status_upload(self):
        puts = [row for row in ALLOWED if row[1] == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertIn("telematics", puts[0][3])
        blob = " ".join(" ".join(row) for row in ALLOWED)
        for banned in ("charge-server", "serviceId", "operationScheduling"):
            self.assertNotIn(banned, blob)


if __name__ == "__main__":
    unittest.main()
