"""Rebuild the in-memory sign-in from what Home Assistant stored.

The account token and the car certificate have to be kept or the next start
would need a new email code. They are not written to the log.
"""

from __future__ import annotations

from typing import Any

from .cloud.client import Account, Device, GeelyClient, GeelyError, VehicleLink
from .cloud.regions import REGIONS, RegionUnavailable
from .cloud.transport import GuardedTransport, TlsTransport, make_identity
from .const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_IDFA,
    CONF_IDFV,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_VEHICLES,
)


def account_from_data(data: dict[str, Any]) -> Account:
    return Account(
        token=str(data[CONF_TOKEN]),
        user_id=str(data[CONF_USER_ID]),
        email=str(data[CONF_EMAIL]),
        country=str(data[CONF_COUNTRY]),
        device=Device(idfa=str(data[CONF_IDFA]), idfv=str(data[CONF_IDFV])),
    )


def remember_vehicle(link: VehicleLink, name: str) -> dict[str, str]:
    """The fields needed to open the same car again after a restart."""
    return {
        "vin": link.vin,
        "name": name[:60],
        "series": link.series,
        "region": link.region,
        "device_id": link.device_id,
        "cert_pem": link.cert_pem,
        "key_pem": link.key_pem,
    }


def link_from_saved(saved: dict[str, Any]) -> VehicleLink:
    identity = make_identity(
        str(saved["cert_pem"]).encode(), str(saved["key_pem"]).encode())
    return VehicleLink(
        region=str(saved["region"]),
        backend=dict(REGIONS[str(saved["region"])]),
        vin=str(saved["vin"]),
        series=str(saved["series"]),
        device_id=str(saved["device_id"]),
        identity=identity,
        cert_pem=str(saved["cert_pem"]),
        key_pem=str(saved["key_pem"]),
    )


def car_name(vehicle: dict[str, Any]) -> str:
    name = (vehicle.get("nickname") or vehicle.get("nickName")
            or vehicle.get("model") or vehicle.get("modelName") or "Geely")
    vin = str(vehicle.get("vin") or "")
    label = str(name)[:60]
    return f"{label} (…{vin[-4:]})" if len(vin) >= 8 else label


def send_code(email: str, country: str, device: Device) -> None:
    client = GeelyClient(GuardedTransport(TlsTransport()))
    client.send_email_code(email, country, device)


def complete_sign_in(email: str, code: str, country: str,
                     device: Device) -> dict[str, Any]:
    """Exchange the emailed code and open every car the account can read.

    The returned dict is what the config entry stores. It holds the account
    token and each car's certificate.
    """
    client = GeelyClient(GuardedTransport(TlsTransport()))
    account = client.exchange_code(email, code, country, device)
    vehicles = client.list_vehicles(account)
    if not vehicles:
        raise GeelyError("no_vehicles")
    saved = []
    for vehicle in vehicles:
        try:
            link = client.open_vehicle(account, vehicle)
        except RegionUnavailable:
            continue
        saved.append(remember_vehicle(link, car_name(vehicle)))
    if not saved:
        raise RegionUnavailable("unavailable", "no published identity")
    return {
        CONF_EMAIL: email,
        CONF_COUNTRY: country,
        CONF_TOKEN: account.token,
        CONF_USER_ID: account.user_id,
        CONF_IDFA: device.idfa,
        CONF_IDFV: device.idfv,
        CONF_VEHICLES: saved,
    }
