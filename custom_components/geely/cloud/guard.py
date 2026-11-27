"""The complete list of requests this service is able to send.

Every outbound request passes check() before a socket is opened. A request
that is not on the list raises OutboundRefused and goes nowhere. The list
holds sign-in, the vehicle list, the device certificate, the session exchange
the reads of the car, and one request that asks the car to upload a fresh
status. That is the same request the Geely app sends when its map is opened.
It wakes the car. The tests pin the list.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# A vehicle identifier as it may appear in a request path.
_VEHICLE = r"[A-Za-z0-9]{8,20}"

_ACCOUNT_HOST = r"access-app-global\.geely\.com"
_LIST_HOSTS = r"m-lcmsam-(?:eu|us|kr)\.geely\.com"
_CERT_HOSTS = r"api\.ecloud(?:eu|us|kr)\.com"
_READ_HOSTS = r"apis\.ecloud(?:eu|us|kr)\.com"
# The captcha picture servers named by the captcha itself. Only bare host
# names under these two suffixes, as in the published client, and never the
# account or vehicle-list hosts.
_CAPTCHA_STATIC = (r"(?!access-|m-lcmsam-)"
                   r"[A-Za-z0-9][A-Za-z0-9.\-]{0,252}\.(?:geely|geetest)\.com")

# (name, method, host pattern, path pattern). The path excludes the query.
ALLOWED: tuple[tuple[str, str, str, str], ...] = (
    ("captcha load", "GET", r"captcha4\.geely\.com", r"/load"),
    ("captcha verify", "GET", r"captcha4\.geely\.com", r"/verify"),
    ("captcha picture", "GET", _CAPTCHA_STATIC, r"/[A-Za-z0-9_\-./]{1,300}"),
    ("send email code", "POST", _ACCOUNT_HOST, r"/cidpsso/captcha/v3/getCaptcha"),
    ("exchange email code", "POST", _ACCOUNT_HOST, r"/cidpsso/user/v3/login"),
    ("vehicle list", "GET", r"m-lcmsam-eu\.geely\.com",
     r"/cidpcar/vehicleOwner/v2/controlCars"),
    ("vehicle list (APAC)", "GET", r"m-lcmsam-kr\.geely\.com",
     r"/cidpcar/vehicleOwner/v1/listControlCars"),
    ("one-time session code", "POST", _LIST_HOSTS, r"/cidpsso/oauth2/v1/getCode"),
    ("certificate challenge", "POST", _CERT_HOSTS, r"/auth/cert/info"),
    ("certificate issue", "POST", _CERT_HOSTS, r"/auth/cert/file"),
    ("session exchange", "POST", r"apis\.ecloud(?:eu|us)\.com",
     r"/auth/account/session/secure"),
    ("session exchange (APAC)", "POST", r"api\.ecloudkr\.com",
     r"/auth-center/account/session"),
    ("read vehicle status", "GET", _READ_HOSTS,
     rf"/remote-control/vehicle/status/{_VEHICLE}"),
    ("read vehicle state", "GET", _READ_HOSTS,
     rf"/remote-control/vehicle/status/state/{_VEHICLE}"),
    ("read charging reservation", "GET", _READ_HOSTS,
     rf"/remote-control/charging/reservation/{_VEHICLE}"),
    ("read capability catalogue", "GET", _READ_HOSTS,
     rf"/geelyTCAccess/tcservices/capability/{_VEHICLE}"),
    ("ask the car to upload its status", "PUT", _READ_HOSTS,
     rf"/remote-control/vehicle/telematics/{_VEHICLE}"),
)

_COMPILED = tuple(
    (name, method, re.compile(rf"\A{host}\Z"), re.compile(rf"\A{path}\Z"))
    for name, method, host, path in ALLOWED
)


class OutboundRefused(Exception):
    """A request that is not on the list was about to be sent."""


def check(method: str, host: str, target: str) -> str:
    """Return the list entry's name for a request, or raise OutboundRefused.

    `target` is the request target: a path with an optional query. The error
    text never repeats the target, which can carry a vehicle identifier.
    """
    if not isinstance(method, str) or not isinstance(host, str) \
            or not isinstance(target, str):
        raise OutboundRefused("malformed outbound request refused")
    if any(ch in target or ch in host for ch in ("\r", "\n", " ", "\x00")):
        raise OutboundRefused("malformed outbound request refused")
    if not target.startswith("/") or target.startswith("//"):
        raise OutboundRefused("malformed outbound request refused")
    path = urlsplit(target).path
    if ".." in path or "//" in path:
        raise OutboundRefused("malformed outbound request refused")
    for name, allowed_method, host_re, path_re in _COMPILED:
        if method == allowed_method and host_re.match(host.lower()) \
                and path_re.match(path):
            return name
    raise OutboundRefused(f"outbound {method} to {host} is not on the allow list")
