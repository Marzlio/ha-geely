"""Regional backends and the rule that picks one for a car.

Derived from const.py of the published Home Assistant integration
YossiKon/geely-connect (MIT, see NOTICE.txt). The three signing identities
below are the ones that integration already publishes. Nothing here was taken
from the Android package.
"""
from __future__ import annotations

from typing import Any

# The area is a property of the car's telematics registration, read from the
# vehicle-list record. Sign-in, the email code and the vehicle list are not
# regional; certificate provisioning and the session exchange are.
REGIONS: dict[str, dict[str, str]] = {
    "EU": {
        "app_id": "GEELYE245",
        "app_secret": "48d6fff3ea19447bbf6f3ed76a608ff9",
        "cert_host": "api.ecloudeu.com",
        "control_host": "apis.ecloudeu.com",
        "mint_host": "m-lcmsam-eu.geely.com",
    },
    "NA": {
        "app_id": "GEELYUS",
        "app_secret": "cd3a278dc4e844ca8a1c22f7b2447a0e",
        "cert_host": "api.ecloudus.com",
        "control_host": "apis.ecloudus.com",
        "mint_host": "m-lcmsam-us.geely.com",
    },
    "APAC": {
        "app_id": "GEELYE245",
        "app_secret": "5b2e7f2f569e4173a9aea65e0c9133e3",
        "cert_host": "api.ecloudkr.com",
        "control_host": "apis.ecloudkr.com",
        "mint_host": "m-lcmsam-kr.geely.com",
    },
}

DEFAULT_REGION = "EU"

# Areas the published integration cannot sign for. Their hosts are never
# contacted from here; the names are kept only so the page can say which area
# a car belongs to. SA is the dedicated South Africa backend, AE the UAE one,
# which the integration reads only with keys an owner supplies in a file.
UNAVAILABLE_REGIONS: dict[str, str] = {
    "SA": "South Africa (dedicated backend)",
    "AE": "United Arab Emirates",
}

REGION_LABELS: dict[str, str] = {
    "EU": "Europe / International",
    "NA": "North America",
    "APAC": "Asia-Pacific",
}

# Market codes on a vehicle record that differ from the backend names.
MARKET_TO_REGION: dict[str, str] = {"AP": "APAC"}

# Header values the published client sends with every read.
CLIENT_ID = "OOGLE0000APPE64ARM64264T31485278"
DEFAULT_VEHICLE_SERIES = "E245-J1"

# Countries the published setup form offers, with its default.
DEFAULT_COUNTRY = "GB"
COUNTRIES: dict[str, str] = {
    "AT": "Austria", "AU": "Australia", "BE": "Belgium", "BG": "Bulgaria",
    "BR": "Brazil", "CA": "Canada", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "DE": "Germany", "GR": "Greece", "HK": "Hong Kong",
    "HU": "Hungary", "IS": "Iceland", "ID": "Indonesia", "IE": "Ireland",
    "IL": "Israel", "IT": "Italy", "JP": "Japan", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MY": "Malaysia", "MT": "Malta",
    "MX": "Mexico", "NL": "Netherlands", "NZ": "New Zealand", "NO": "Norway",
    "PH": "Philippines", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SG": "Singapore", "SK": "Slovakia", "SI": "Slovenia", "KR": "South Korea",
    "ES": "Spain", "SE": "Sweden", "CH": "Switzerland", "TH": "Thailand",
    "GB": "United Kingdom", "US": "United States", "VN": "Vietnam",
}


def resolve_vehicle_region(vehicle: dict[str, Any]) -> str | None:
    """The telematics area a vehicle-list record names, or None.

    Same order as the published client: tspInfo[].serviceRegion, then
    edgeInfo.code, then a top-level serviceRegion, then the market codes.
    """
    tsp = vehicle.get("tspInfo")
    if isinstance(tsp, list):
        for entry in tsp:
            if isinstance(entry, dict) and entry.get("serviceRegion"):
                return str(entry["serviceRegion"]).upper()
    edge = vehicle.get("edgeInfo")
    if isinstance(edge, dict) and edge.get("code"):
        return str(edge["code"]).upper()
    top = vehicle.get("serviceRegion")
    if top:
        return str(top).upper()
    for key in ("saleMarket", "tcamMarket"):
        market = vehicle.get(key)
        if market:
            code = str(market).upper()
            return MARKET_TO_REGION.get(code, code)
    return None


class RegionUnavailable(Exception):
    """The car belongs to an area this page has no published identity for."""

    def __init__(self, region: str, reason: str) -> None:
        super().__init__(reason)
        self.region = region
        self.reason = reason


def backend_for(vehicle: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """(area, backend) for a vehicle record, as the published client picks it.

    A record that names no area, or one no backend here matches, is signed
    for EU, which is what the published client does. An area in
    UNAVAILABLE_REGIONS stops here and nothing is sent anywhere.
    """
    region = resolve_vehicle_region(vehicle) or DEFAULT_REGION
    if region in UNAVAILABLE_REGIONS:
        raise RegionUnavailable(
            region,
            f"This car is registered in the {region} area "
            f"({UNAVAILABLE_REGIONS[region]}). The published client has no "
            "app credentials for that area, so this page cannot read the car.",
        )
    return region, REGIONS.get(region, REGIONS[DEFAULT_REGION])
