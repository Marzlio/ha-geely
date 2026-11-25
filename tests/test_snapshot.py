"""The snapshot parser and the request allow-list. No network, no Home Assistant."""

from __future__ import annotations

import unittest

from custom_components.geely.cloud.guard import ALLOWED
from custom_components.geely.snapshot import parse_status


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

    def test_a_short_reply_does_not_invent_values(self):
        parsed = parse_status({"vehicleStatus": {}})
        self.assertIsNone(parsed["battery_pct"])
        self.assertIsNone(parsed["latitude"])
        self.assertIsNone(parsed["engine_on"])


class AllowList(unittest.TestCase):
    def test_nothing_on_the_list_asks_the_car_to_act(self):
        blob = " ".join(" ".join(row) for row in ALLOWED)
        for banned in ("telematics", "charge-server", "serviceId", "operationScheduling"):
            self.assertNotIn(banned, blob)
        methods = {row[1] for row in ALLOWED}
        self.assertTrue(methods <= {"GET", "POST"})


if __name__ == "__main__":
    unittest.main()
