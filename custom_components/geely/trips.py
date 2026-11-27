"""Keep each drive from the status reads already being made.

The car sends one position at a time, not a route. A drive starts when the
engine is seen running and ends when it is seen off. The distance and the
average speed come from the odometer and the time between those two reads.
Nothing here asks the car to do anything.
"""

from __future__ import annotations

import json
import os
import threading
from math import asin, cos, radians, sin, sqrt
from typing import Any

from .const import PACK_KWH

_MIN_SAVE_KM = 0.2
_MIN_SHOW_KM = 0.05
_MIN_AVG_SECONDS = 45
_MAX_AVG_KMH = 180.0
_MAX_TRIPS = 40
_MAX_POINTS = 500


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return float(value)


def _path_km(points: list[dict]) -> float:
    total = 0.0
    for earlier, later in zip(points, points[1:]):
        total += _between_km(earlier, later)
    return total


def _between_km(earlier: dict, later: dict) -> float:
    lat1 = _num(earlier.get("lat"))
    lon1 = _num(earlier.get("lon"))
    lat2 = _num(later.get("lat"))
    lon2 = _num(later.get("lon"))
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    step = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(min(1.0, sqrt(step)))


def _distance_km(trip: dict) -> float:
    start = _num(trip.get("start_km"))
    end = _num(trip.get("end_km"))
    if start is not None and end is not None and end + 0.05 >= start:
        return max(0.0, end - start)
    return _path_km(trip.get("points") or [])


def _duration(trip: dict) -> float | None:
    started = _num(trip.get("started"))
    ended = _num(trip.get("ended"))
    if started is None or ended is None or ended < started:
        return None
    return ended - started


def _average(distance_km: float, duration_s: float | None) -> float | None:
    if duration_s is None or duration_s < _MIN_AVG_SECONDS or distance_km < _MIN_SHOW_KM:
        return None
    speed = distance_km / (duration_s / 3600.0)
    if speed > _MAX_AVG_KMH:
        return None
    return round(speed, 1)


def _kwh_per_100(trip: dict | None) -> float | None:
    """Percent dropped times the pack size, over the kilometres of one drive."""
    if not isinstance(trip, dict):
        return None
    start = _num(trip.get("start_pct"))
    end = _num(trip.get("end_pct"))
    km = _distance_km(trip)
    if start is None or end is None or km < 5:
        return None
    drop = start - end
    if drop < 1:
        return None
    per_100 = drop / 100.0 * PACK_KWH / km * 100.0
    if not 4 <= per_100 <= 60:
        return None
    return round(per_100, 2)


def _point(sample: dict) -> dict | None:
    lat = _num(sample.get("latitude"))
    lon = _num(sample.get("longitude"))
    if lat is None or lon is None:
        return None
    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None
    alt = _num(sample.get("altitude_m"))
    point = {"t": round(float(sample["t"]), 1), "lat": round(lat, 5), "lon": round(lon, 5)}
    if alt is not None:
        point["alt"] = round(alt, 1)
    return point


def _same(earlier: dict, later: dict) -> bool:
    return (
        earlier.get("t") == later.get("t")
        or (
            earlier.get("lat") == later.get("lat")
            and earlier.get("lon") == later.get("lon")
            and earlier.get("alt") == later.get("alt")
        )
    )


def _thin(points: list[dict]) -> list[dict]:
    if len(points) <= _MAX_POINTS:
        return points
    step = max(2, len(points) // _MAX_POINTS)
    kept = points[::step]
    if kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


def _view(trip: dict, *, open_trip: bool) -> dict | None:
    points = list(trip.get("points") or [])
    if not points:
        return None
    distance = round(_distance_km(trip), 1)
    if open_trip and distance < _MIN_SHOW_KM:
        return None
    duration = _duration(trip)
    return {
        "id": trip["id"],
        "open": open_trip,
        "started": trip["started"],
        "ended": trip.get("ended"),
        "distance_km": distance,
        "duration_s": None if duration is None else int(duration),
        "avg_speed_kmh": _average(distance, duration),
        "points": points,
    }


class TripLog:
    """Trips for every car on the account, stored beside the Home Assistant config."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._books: dict[str, dict] | None = None

    def _ready(self) -> dict[str, dict]:
        """Load the file on the poll thread, not while Home Assistant is starting."""
        if self._books is None:
            self._books = self._load()
        return self._books

    def observe(self, vin: str, sample: dict) -> None:
        when = _num(sample.get("t"))
        if not vin or when is None:
            return
        with self._lock:
            books = self._ready()
            book = books.setdefault(vin, {"open": None, "trips": []})
            engine = sample.get("engine_on")
            odometer = _num(sample.get("odometer_km"))
            if engine is True:
                self._running(book, sample, when, odometer)
            elif engine is False:
                self._stopped(book, sample, when, odometer)
            self._save()

    def summary(self, vin: str) -> dict[str, Any]:
        """Readings for an entity. Positions stay out of this copy."""
        with self._lock:
            book = self._ready().get(vin) or {"open": None, "trips": []}
            rows = []
            open_view = _view(book["open"], open_trip=True) if book.get("open") else None
            if open_view:
                rows.append(_brief(open_view))
            for trip in reversed(book.get("trips") or []):
                view = _view(trip, open_trip=False)
                if view:
                    rows.append(_brief(view))
            return {
                "state": "Driving" if open_view else "Parked",
                "saved": len(book.get("trips") or []),
                "trips": rows,
            }

    def kwh_per_100(self, vin: str) -> float | None:
        """kWh/100 km from the battery used on the open drive, or the last one.

        The car does not send a trip consumption, so this is the percent
        dropped times the published pack size, divided by the kilometres.
        """
        with self._lock:
            book = self._ready().get(vin) or {}
            trip = book.get("open")
            rate = _kwh_per_100(trip)
            if rate is None:
                saved = book.get("trips") or []
                rate = _kwh_per_100(saved[-1] if saved else None)
            return rate

    def tracks(self) -> dict[str, Any]:
        """Every saved drive, newest first, for the trips page."""
        with self._lock:
            rows = []
            for book in self._ready().values():
                if book.get("open"):
                    view = _view(book["open"], open_trip=True)
                    if view:
                        rows.append(view)
                for trip in book.get("trips") or []:
                    view = _view(trip, open_trip=False)
                    if view:
                        rows.append(view)
        rows.sort(key=lambda item: item["started"], reverse=True)
        rows.sort(key=lambda item: not item["open"])
        return {"trips": rows}

    def _running(self, book: dict, sample: dict, when: float, odometer: float | None) -> None:
        trip = book.get("open")
        if trip is None:
            trip = {
                "id": str(int(when)),
                "started": when,
                "ended": when,
                "start_km": odometer,
                "end_km": odometer,
                "points": [],
            }
            book["open"] = trip
            self._note_charge(trip, sample, opening=True)
        else:
            self._note_charge(trip, sample, opening=False)
        trip["ended"] = when
        if odometer is not None:
            trip["end_km"] = odometer
        self._add_point(trip, sample)

    def _stopped(self, book: dict, sample: dict, when: float, odometer: float | None) -> None:
        trip = book.get("open")
        if trip is None:
            return
        trip["ended"] = when
        if odometer is not None:
            trip["end_km"] = odometer
        self._note_charge(trip, sample, opening=False)
        self._add_point(trip, sample)
        trip["points"] = _thin(trip["points"])
        distance = _distance_km(trip)
        if distance >= _MIN_SAVE_KM and trip["points"]:
            duration = _duration(trip)
            trip["distance_km"] = round(distance, 1)
            trip["duration_s"] = None if duration is None else int(duration)
            trip["avg_speed_kmh"] = _average(distance, duration)
            book["trips"].append(trip)
            if len(book["trips"]) > _MAX_TRIPS:
                book["trips"] = book["trips"][-_MAX_TRIPS:]
        book["open"] = None

    def _note_charge(self, trip: dict, sample: dict, *, opening: bool) -> None:
        pct = _num(sample.get("battery_pct"))
        if pct is None or not 0 <= pct <= 100:
            return
        if opening or trip.get("start_pct") is None:
            trip["start_pct"] = pct
        trip["end_pct"] = pct

    def _add_point(self, trip: dict, sample: dict) -> None:
        point = _point(sample)
        if point is None:
            return
        points = trip["points"]
        if points and _same(points[-1], point):
            return
        points.append(point)
        if len(points) > _MAX_POINTS * 2:
            trip["points"] = _thin(points)

    def _load(self) -> dict[str, dict]:
        try:
            with open(self._path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        cars = raw.get("cars") if isinstance(raw, dict) else None
        if not isinstance(cars, dict):
            return {}
        books = {}
        for vin, book in cars.items():
            if isinstance(vin, str) and isinstance(book, dict):
                trips = [item for item in book.get("trips") or [] if isinstance(item, dict)]
                opened = book.get("open") if isinstance(book.get("open"), dict) else None
                books[vin] = {"open": opened, "trips": trips[-_MAX_TRIPS:]}
        return books

    def _save(self) -> None:
        payload = {"version": 1, "cars": self._books or {}}
        temporary = self._path + ".tmp"
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(temporary, self._path)


def _brief(view: dict) -> dict[str, Any]:
    return {
        "id": view["id"],
        "open": view["open"],
        "started": view["started"],
        "ended": view["ended"],
        "distance_km": view["distance_km"],
        "duration_s": view["duration_s"],
        "avg_speed_kmh": view["avg_speed_kmh"],
        "points": len(view["points"]),
    }
