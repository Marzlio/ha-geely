"""Sign-in and read calls against the Geely cloud. Reads only.

Ported from api.py and config_flow.py of the published integration
YossiKon/geely-connect, itself derived from nitaybz/geely-global-ha (both
MIT, see NOTICE.txt). This port keeps the account sign-in, the vehicle list,
the device certificate, the session exchange and two reads of the car. The
published client's command methods were not ported, and guard.py refuses any
request outside that list.

Nothing in this module logs, and no error it raises carries a value from an
account or a car: errors hold a fixed kind and, at most, the short result
code the server gave.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import re
import secrets
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from . import captcha
from .guard import OutboundRefused
from .regions import (
    CLIENT_ID,
    DEFAULT_VEHICLE_SERIES,
    RegionUnavailable,
    backend_for,
)
from .transport import (
    GuardedTransport,
    Identity,
    TlsRefused,
    Transport,
    make_identity,
)

ACCOUNT_HOST = "access-app-global.geely.com"
LIST_HOST = "m-lcmsam-eu.geely.com"
LIST_HOST_APAC = "m-lcmsam-kr.geely.com"

_OK = 10000000
# Result codes that mean the session is no longer valid.
_AUTH_FAILURE_CODES = {"60000000", "60000001", "60000110", "1402"}
# Certificate-server codes that mean the account belongs to another area.
_REGION_MISMATCH_CODES = {"1501", "1445"}
_STATUS_OK_CODES = {"1000", "10000000"}

_VIN_RE = re.compile(r"\A[A-Za-z0-9]{8,20}\Z")
_USER_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")
_CODE_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,24}\Z")

_SEND_ATTEMPTS = 5


class GeelyError(Exception):
    """A failed step. `kind` is one of a fixed set; `code` is the server's
    short result code when it gave one. Neither holds account or car data."""

    KINDS = frozenset({
        "captcha_unreachable",   # the captcha host could not be reached
        "send_failed",           # the email code could not be requested
        "bad_code",              # the one-time code was not accepted
        "no_vehicles",           # the account lists no car
        "auth",                  # Geely ended the session
        "region",                # the area's servers refused this account
        "network",               # a Geely host could not be reached
        "tls",                   # a Geely host failed the certificate check
        "upstream",              # any other refusal or unreadable answer
    })

    def __init__(self, kind: str, code: Any = None) -> None:
        assert kind in self.KINDS
        self.kind = kind
        self.code = _safe_code(code)
        super().__init__(kind if self.code is None else f"{kind} ({self.code})")


def _safe_code(code: Any) -> str | None:
    """A result code, kept only when it is a short plain token."""
    if code is None or isinstance(code, bool):
        return None
    text = str(code)
    return text if _CODE_RE.match(text) else None


def valid_vin(vin: Any) -> bool:
    return isinstance(vin, str) and bool(_VIN_RE.match(vin))


@dataclass
class Device:
    """What tells Geely this page apart from the owner's phone. Random per
    sign-in, kept in memory with the session."""
    idfa: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    idfv: str = field(default_factory=lambda: str(uuid.uuid4()).upper())


@dataclass
class Account:
    token: str = field(repr=False)
    user_id: str = field(repr=False)
    email: str = field(repr=False)
    country: str = "GB"
    device: Device = field(default_factory=Device, repr=False)


@dataclass
class VehicleLink:
    """An open read channel to one car: the area it lives in, the in-memory
    client certificate, and the rotating session token."""
    region: str
    backend: dict[str, str] = field(repr=False)
    vin: str = field(repr=False)
    series: str
    device_id: str = field(repr=False)
    identity: Identity = field(repr=False)
    cert_pem: str = field(default="", repr=False)
    key_pem: str = field(default="", repr=False)
    jwt: str | None = field(default=None, repr=False)
    jwt_user: str | None = field(default=None, repr=False)
    jwt_expires: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ---------- request signing (HMAC-SHA1 over a canonical string) ----------

def _percent_encode_value(s: str) -> str:
    enc = quote(s, safe="!*'();@&=+$?#[]")
    return enc.replace("/", "%2F").replace(":", "%3A").replace(",", "%2C")


def build_sign_string(*, method: str, path: str, query: str, accept: str,
                      nonce: str, timestamp_ms: int, body: bytes) -> str:
    signed = {"x-api-signature-nonce": nonce, "x-api-signature-version": "1.0"}
    canonical_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
    pairs = sorted(parse_qsl(query, keep_blank_values=True), key=lambda kv: kv[0])
    canonical_query = "&".join(f"{k}={_percent_encode_value(v)}" for k, v in pairs)
    md5_b64 = base64.b64encode(hashlib.md5(body).digest()).decode()
    return "\n".join([accept, canonical_headers, canonical_query, md5_b64,
                      f"{timestamp_ms}", method.upper(), path])


def _nonce() -> str:
    """Shaped like the app's nonce. It only has to be unique per request."""
    prefix = "".join(random.choices("0123456789abcdef", k=3))
    middle = "".join(random.choices("0123456789abcdef", k=12))
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
    return f"{prefix}-{middle}{suffix}{int(time.time() * 1000)}"


def _signature(secret: str, sign_string: str) -> str:
    digest = hmac.new(secret.encode(), sign_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


_ACCEPT = "application/json;responseformat=3"


def _signed_headers(backend: dict[str, str], method: str, target: str,
                    body: bytes, device_id: str | None = None) -> dict[str, str]:
    parts = urlsplit(target)
    nonce, ts_ms = _nonce(), int(time.time() * 1000)
    sign_string = build_sign_string(
        method=method, path=parts.path, query=parts.query, accept=_ACCEPT,
        nonce=nonce, timestamp_ms=ts_ms, body=body)
    headers = {
        "X-APP-ID": backend["app_id"],
        "Accept": _ACCEPT,
        "X-AGENT-TYPE": "android",
        "X-DEVICE-TYPE": "mobile",
        "X-OPERATOR-CODE": "geely",
    }
    if device_id is not None:
        headers["X-DEVICE-IDENTIFIER"] = device_id
    headers.update({
        "X-ENV-TYPE": "production",
        "X-VERSION": "geelyNew",
    })
    if device_id is not None:
        headers.update({"X-TIMEZONE": "UTC", "Accept-Language": "en_US"})
    headers.update({
        "Content-Type": "application/json; charset=utf-8",
        "X-api-signature-version": "1.0",
        "X-api-signature-nonce": nonce,
        "X-timestamp": str(ts_ms),
        "X-signature": _signature(backend["app_secret"], sign_string),
        "user-agent": "okhttp/4.11.0",
    })
    return headers


def _app_headers(account: Account | None = None, *, country: str,
                 device: Device) -> dict[str, str]:
    """The headers Geely's account and vehicle-list gateways expect."""
    now_ms = int(time.time() * 1000)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "geely/1.9.8 (iPhone; iOS 26.3.1; Scale/3.00)",
        "version": "1.9.8",
        "devicename": "Local web page",
        "model": "iPhone",
        "system-flag": "1",
        "systemversion": "26.3.1",
        "countrycode": country,
        "accept-language": "en-GB",
        "lang": "en-GB",
        "accept": "*/*",
        "devicehardwareidfa": device.idfa,
        "devicehardwareidfv": device.idfv,
        "requesttime": str(now_ms),
        "requestid": f"{now_ms}{secrets.token_hex(6)}",
    }
    if account is not None:
        headers["token"] = account.token
        headers["userid"] = account.user_id
    return headers


def _lifetime(value: Any, default: int = 7200) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return seconds if seconds > 60 else default


def _code_of(reply: dict) -> str | None:
    code = reply.get("code")
    return None if code is None else str(code)


# ---------- the client ----------

class GeelyClient:
    def __init__(self, transport: Transport) -> None:
        # Whatever transport is supplied, the allow list sits in front of it.
        self._transport = GuardedTransport(transport)

    # -- plumbing --

    def _send(self, method: str, host: str, target: str, headers: dict,
              body: bytes = b"", identity: Identity | None = None,
              timeout: float = 20.0) -> dict:
        """Send one request and return its JSON object. Every failure is
        turned into a GeelyError that carries no request or reply text."""
        try:
            reply = self._transport.request(method, host, target, headers, body,
                                            identity, timeout)
        except (TlsRefused, OutboundRefused):
            raise GeelyError("tls") from None
        except OSError:
            raise GeelyError("network") from None
        try:
            parsed = json.loads(reply.body)
        except ValueError:
            raise GeelyError("upstream", reply.status) from None
        if not isinstance(parsed, dict):
            raise GeelyError("upstream", reply.status)
        return parsed

    # -- sign-in --

    def send_email_code(self, email: str, country: str, device: Device) -> None:
        """Pass the captcha and ask Geely to email a one-time code."""
        headers = _app_headers(country=country, device=device)
        last: dict | None = None
        for _ in range(_SEND_ATTEMPTS):
            try:
                solved = captcha.solve(self._transport)
            except (TlsRefused, OutboundRefused):
                raise GeelyError("tls") from None
            except OSError:
                # Unreachable now is unreachable in a minute; do not retry.
                raise GeelyError("captcha_unreachable") from None
            except Exception:  # noqa: BLE001 - a bad answer; try a new puzzle
                continue
            if solved is None:
                continue
            try:
                body = json.dumps({
                    "captchaType": "2",
                    "passToken": solved["pass_token"],
                    "platform": "ios-login",
                    "lotNumber": solved["lot_number"],
                    "captchaOutput": solved["captcha_output"],
                    "genTime": str(solved["gen_time"]),
                    "email": email,
                    "captchaScene": "101",
                }).encode("utf-8")
            except KeyError:
                continue
            last = self._send("POST", ACCOUNT_HOST,
                              "/cidpsso/captcha/v3/getCaptcha", headers, body)
            if last.get("success") or last.get("code") == _OK:
                return
        if last is None:
            raise GeelyError("send_failed")
        if last.get("code") and last.get("code") != _OK:
            raise GeelyError("send_failed", last.get("code"))

    def exchange_code(self, email: str, code: str, country: str,
                      device: Device) -> Account:
        """Exchange the emailed one-time code for the account session."""
        body = json.dumps({
            "countryCode": country,
            "account": email,
            "code": code,
            "registerSource": 102,
            "loginType": 3,      # email code
            "accountType": 2,    # email
        }).encode("utf-8")
        reply = self._send("POST", ACCOUNT_HOST, "/cidpsso/user/v3/login",
                           _app_headers(country=country, device=device), body)
        if reply.get("code") != _OK:
            raise GeelyError("bad_code", reply.get("code"))
        data = reply.get("data") or {}
        token = data.get("token")
        user_id = data.get("userId") or data.get("id")
        if isinstance(user_id, int) and not isinstance(user_id, bool):
            user_id = str(user_id)
        if not isinstance(token, str) or not token \
                or not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
            raise GeelyError("upstream")
        return Account(token=token, user_id=user_id, email=email,
                       country=country, device=device)

    def list_vehicles(self, account: Account) -> list[dict]:
        """The account's cars. An empty list from the main endpoint falls
        back to the APAC one, as in the published client."""
        headers = _app_headers(account, country=account.country,
                               device=account.device)
        reply = self._send("GET", LIST_HOST,
                           "/cidpcar/vehicleOwner/v2/controlCars", headers)
        if _code_of(reply) in _AUTH_FAILURE_CODES:
            raise GeelyError("auth", reply.get("code"))
        vehicles = reply.get("data") or []
        if not vehicles:
            try:
                fallback = self._send(
                    "GET", LIST_HOST_APAC,
                    "/cidpcar/vehicleOwner/v1/listControlCars", headers)
                vehicles = fallback.get("data") or []
            except GeelyError:
                vehicles = []
        if not isinstance(vehicles, list):
            return []
        return [v for v in vehicles if isinstance(v, dict) and valid_vin(v.get("vin"))]

    # -- one car --

    def open_vehicle(self, account: Account, vehicle: dict) -> VehicleLink:
        """Pick the car's regional backend and obtain a client certificate
        for it. The key pair is generated here and never leaves memory.

        Raises RegionUnavailable, before anything is sent, for an area the
        published client has no identity for.
        """
        vin = vehicle.get("vin")
        if not valid_vin(vin):
            raise GeelyError("upstream")
        region, backend = backend_for(vehicle)

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        common_name = hashlib.sha256(account.user_id.encode()).hexdigest()[:8]
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "ZheJiang"),
                x509.NameAttribute(NameOID.LOCALITY_NAME, "Hangzhou"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECARX"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "CloudDept"),
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            ]))
            .sign(key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
        # An opaque label for this page as a device of the account. Its own
        # suffix keeps it apart from any other client the owner runs.
        cert_device = hashlib.sha256(
            f"{account.user_id}_ha_geely".encode()).hexdigest()

        def challenge() -> str:
            reply = self._signed_post(backend, "/auth/cert/info",
                                      {"checkValue": account.user_id})
            if reply.get("code") != 1000:
                self._raise_cert_failure(reply)
            try:
                return reply["data"]["checkCode"]
            except (KeyError, TypeError):
                raise GeelyError("upstream") from None

        def issue(field_name: str, check: str) -> dict:
            return self._signed_post(backend, "/auth/cert/file", {
                "csr": csr_pem,
                "identityType": "geelyos",
                "accessToken": account.token,
                "deviceId": cert_device,
                field_name: check,
            }, timeout=30)

        reply = issue("checkCode", challenge())
        # APAC names the challenge field differently and says so with 8200.
        # The challenge is single-use, so the retry takes a fresh one.
        if _code_of(reply) == "8200" and "checkvalue" in str(
                reply.get("hint") or reply.get("message") or "").lower():
            reply = issue("checkValue", challenge())
        if reply.get("code") != 1000:
            self._raise_cert_failure(reply)
        try:
            cert_pem = reply["data"]["cert"]
        except (KeyError, TypeError):
            raise GeelyError("upstream") from None
        if not isinstance(cert_pem, str):
            raise GeelyError("upstream")

        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            identity = make_identity(cert_pem.encode(), key_pem)
        except Exception:  # noqa: BLE001 - a certificate that does not load
            raise GeelyError("upstream") from None

        series = (vehicle.get("modelCode") or vehicle.get("seriesCode")
                  or vehicle.get("appModelCode") or vehicle.get("series")
                  or vehicle.get("seriesName") or DEFAULT_VEHICLE_SERIES)
        return VehicleLink(
            region=region,
            backend=backend,
            vin=vin,
            series=str(series),
            device_id=hashlib.md5(
                f"ha:{account.user_id}:{vin}".encode()).hexdigest(),
            identity=identity,
            cert_pem=cert_pem,
            key_pem=key_pem.decode(),
        )

    def _signed_post(self, backend: dict[str, str], path: str, payload: dict,
                     timeout: float = 20.0) -> dict:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = _signed_headers(backend, "POST", path, body)
        return self._send("POST", backend["cert_host"], path, headers, body,
                          timeout=timeout)

    @staticmethod
    def _raise_cert_failure(reply: dict) -> None:
        if _code_of(reply) in _REGION_MISMATCH_CODES:
            raise GeelyError("region", reply.get("code"))
        raise GeelyError("upstream", reply.get("code"))

    # -- the rotating read session --

    def _access_code(self, account: Account, host: str) -> str:
        """A one-time code from the account gateway. It must be minted by the
        same area that will exchange it."""
        body = json.dumps({"state": str(uuid.uuid4())}).encode()
        reply = self._send("POST", host, "/cidpsso/oauth2/v1/getCode", {
            "token": account.token,
            "user-agent": "okhttp/4.11.0",
            "content-type": "application/json; charset=utf-8",
        }, body, timeout=15)
        if _code_of(reply) in _AUTH_FAILURE_CODES:
            raise GeelyError("auth", reply.get("code"))
        if reply.get("code") != _OK:
            raise GeelyError("upstream", reply.get("code"))
        try:
            return reply["data"]["accessCode"]
        except (KeyError, TypeError):
            raise GeelyError("upstream") from None

    def _refresh_session(self, account: Account, link: VehicleLink) -> None:
        backend = link.backend
        if backend["control_host"] == "apis.ecloudkr.com":
            data = self._apac_session(account, link)
            token = data.get("accessToken")
            user = data.get("userId") or account.user_id
        else:
            access_code = self._access_code(account, backend["mint_host"])
            body = json.dumps({"authCode": access_code}).encode()
            target = "/auth/account/session/secure?identity_type=geelyos"
            headers = _signed_headers(backend, "POST", target, body, link.device_id)
            reply = self._send("POST", backend["control_host"], target, headers,
                               body, link.identity, timeout=30)
            if _code_of(reply) in _AUTH_FAILURE_CODES:
                raise GeelyError("auth", reply.get("code"))
            if _code_of(reply) != "1000":
                raise GeelyError("upstream", reply.get("code"))
            data = reply.get("data") or {}
            token, user = data.get("accessToken"), data.get("userId")
        if not isinstance(token, str) or not token:
            raise GeelyError("upstream")
        link.jwt = token
        link.jwt_user = str(user) if user else account.user_id
        link.jwt_expires = time.time() + _lifetime(data.get("expiresIn"))

    def _apac_session(self, account: Account, link: VehicleLink) -> dict:
        """APAC exchanges the code on its public host, with the email in the
        body and upper-case signature headers."""
        backend = link.backend
        access_code = self._access_code(account, backend["mint_host"])
        body = json.dumps({
            "identityType": "geelyos",
            "authCode": access_code,
            "receiverId": account.email,
        }, separators=(",", ":")).encode()
        path = "/auth-center/account/session"
        accept = "application/json; charset=utf-8"
        nonce, ts_ms = _nonce(), int(time.time() * 1000)
        sign_string = build_sign_string(
            method="POST", path=path, query="", accept=accept, nonce=nonce,
            timestamp_ms=ts_ms, body=body)
        headers = {
            "Accept": accept,
            "X-APP-ID": backend["app_id"],
            "X-CLIENT-TYPE": "2",
            "X-ENV-TYPE": "production",
            "X-OPERATOR-CODE": "GEELY",
            "X-VIN-ID": link.vin,
            "urlname": "user-api",
            "Authorization": account.token,
            "Content-Type": "application/json; charset=utf-8",
            "X-api-signature-version": "1.0",
            "X-api-signature-nonce": nonce,
            "X-TIMESTAMP": str(ts_ms),
            "X-SIGNATURE": _signature(backend["app_secret"], sign_string),
            "user-agent": "okhttp/4.11.0",
        }
        reply = self._send("POST", backend["cert_host"], path, headers, body,
                           timeout=15)
        result = reply.get("resultCode")
        if str(result) == "0":
            return reply
        if str(result) in _AUTH_FAILURE_CODES:
            raise GeelyError("auth", result)
        raise GeelyError("upstream", result)

    def _session_token(self, account: Account, link: VehicleLink) -> str:
        with link.lock:
            if not link.jwt or time.time() > link.jwt_expires - 60:
                self._refresh_session(account, link)
            assert link.jwt is not None
            return link.jwt

    def _signed(self, account: Account, link: VehicleLink, method: str,
                target: str, body: bytes) -> dict:
        """A signed call on the car's gateway. If the session token was
        invalidated, which another sign-in to the account does, it is renewed
        once and the call repeated."""
        host = link.backend["control_host"]

        def attempt(token: str) -> dict:
            headers = _signed_headers(link.backend, method, target, body,
                                      link.device_id)
            headers.update({
                "Authorization": token,
                "X-CLIENT-ID": CLIENT_ID,
                "X-VEHICLE-SERIES": link.series,
                "X-VEHICLE-MODEL": link.series,
                "X-Vehicle-IDENTIFIER": link.vin,
            })
            return self._send(method, host, target, headers, body, link.identity,
                              timeout=30)

        token = self._session_token(account, link)
        reply = attempt(token)
        if _code_of(reply) == "1402":
            with link.lock:
                if link.jwt == token:
                    link.jwt, link.jwt_expires = None, 0.0
                    self._refresh_session(account, link)
                token = link.jwt or ""
            reply = attempt(token)
        if _code_of(reply) in _AUTH_FAILURE_CODES:
            raise GeelyError("auth", reply.get("code"))
        return reply

    def _read(self, account: Account, link: VehicleLink, target: str) -> dict:
        return self._signed(account, link, "GET", target, b"")

    def request_upload(self, account: Account, link: VehicleLink) -> None:
        """Ask the car to upload a fresh status. This is the call the Geely
        app sends when its map is opened. It wakes the car."""
        body = json.dumps({
            "command": "start",
            "creator": "tc",
            "latest": True,
            "serviceId": "PAI",
            "serviceParameters": [
                {"key": "operation", "value": "4"},
                {"key": "pai", "value": "1"},
            ],
            "timestamp": str(int(time.time() * 1000)),
            "userId": account.user_id,
        }, separators=(",", ":")).encode()
        reply = self._signed(
            account, link, "PUT",
            f"/remote-control/vehicle/telematics/{link.vin}", body)
        code = _code_of(reply)
        if code is not None and code not in _STATUS_OK_CODES:
            raise GeelyError("upstream", code)

    # -- the reads --

    def read_status(self, account: Account, link: VehicleLink) -> dict:
        """The car's latest uploaded snapshot."""
        target = (f"/remote-control/vehicle/status/{link.vin}"
                  f"?userId={account.user_id}&latest=&target=")
        reply = self._read(account, link, target)
        code = _code_of(reply)
        if code is not None and code not in _STATUS_OK_CODES:
            raise GeelyError("upstream", code)
        data = reply.get("data")
        return data if isinstance(data, dict) else {}

    def _read_data(self, account: Account, link: VehicleLink, target: str) -> Any:
        """The data of one extra read. None when that read did not succeed.
        An ended sign-in is still raised, so polling stops."""
        try:
            reply = self._read(account, link, target)
        except GeelyError as exc:
            if exc.kind == "auth":
                raise
            return None
        code = _code_of(reply)
        if code is not None and code not in _STATUS_OK_CODES:
            return None
        return reply.get("data")

    def read_vehicle_state(self, account: Account, link: VehicleLink) -> dict | None:
        """The second status record. It carries flags the main snapshot does
        not. A read of what the cloud already holds."""
        data = self._read_data(
            account, link, f"/remote-control/vehicle/status/state/{link.vin}")
        return data if isinstance(data, dict) else None

    def read_charging_reservation(self, account: Account,
                                  link: VehicleLink) -> dict | None:
        """The saved charging reservation, as a read. Nothing is written."""
        data = self._read_data(
            account, link, f"/remote-control/charging/reservation/{link.vin}")
        return data if isinstance(data, dict) else None

    def read_catalogue(self, account: Account, link: VehicleLink) -> list | None:
        """The car's capability catalogue: the names of the features it has.
        None when it could not be read."""
        target = (f"/geelyTCAccess/tcservices/capability/{link.vin}"
                  "?pageSize=2000&pageIndex=1&vehicleType=0&sortField=&direction=")
        try:
            reply = self._read(account, link, target)
        except GeelyError as exc:
            if exc.kind == "auth":
                raise
            return None
        items = (reply.get("data") or {}).get("list") if isinstance(
            reply.get("data"), dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else None


__all__ = [
    "Account", "Device", "GeelyClient", "GeelyError", "RegionUnavailable",
    "VehicleLink", "build_sign_string", "valid_vin",
]
