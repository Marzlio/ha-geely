"""HTTPS transport: verified TLS, pinned keys for Geely's private-CA hosts,
and a client certificate that exists only in process memory.

The trust rules follow the published integration YossiKon/geely-connect
(MIT, see NOTICE.txt): strict public-CA validation first, and a public-key
pin for the regional read gateways, which present a certificate from Geely's
own CA. A connection that fails either check sends nothing.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import os
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Mapping, NamedTuple, Protocol

from . import guard

_LEGACY_SERVER_CONNECT = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
_MAX_BODY = 4 * 1024 * 1024

# Hosts whose certificate is issued by Geely's own CA and cannot chain to a
# public root. Only these may fall back to the key pin.
PRIVATE_PKI_HOSTS: frozenset[str] = frozenset({
    "apis.ecloudeu.com",
    "apis.ecloudus.com",
    "apis.ecloudkr.com",
})

# SHA-256 of each host's SubjectPublicKeyInfo, base64, as the published
# integration ships them. These are public keys, not secrets.
BUNDLED_PINS: dict[str, tuple[str, ...]] = {
    "apis.ecloudeu.com": ("Hm0olBoClunXgMp4wFvdrr8SC5iSt+LX6iyB4N828C8=",),
    "apis.ecloudus.com": ("yTrWM8YjYq6ivb6HP1usQiBdgZYGiCwR6yuQYOm2KVs=",),
    "apis.ecloudkr.com": ("XFUV7VhyECWtALG5wEQoAj3FSWtsf3IpBq6l2mBgVWA=",),
}

# OpenSSL verify codes that mean "the chain is fine, this CA is just not in
# the trust store". An expired certificate or a wrong host name never
# qualifies for the pin.
_PRIVATE_CA_VERIFY_CODES: frozenset[int] = frozenset({18, 19, 20})


class TlsRefused(Exception):
    """A server failed the certificate or key-pin check. Nothing was sent."""


class Response(NamedTuple):
    status: int
    body: bytes


@dataclass(frozen=True)
class Identity:
    """A client certificate and its key, loaded into TLS contexts.

    The PEM bytes are not kept. The contexts live in process memory and are
    gone when the session object is dropped or the process exits.
    """
    strict: ssl.SSLContext
    pinned: ssl.SSLContext


class Transport(Protocol):
    def request(self, method: str, host: str, target: str,
                headers: Mapping[str, str], body: bytes = b"",
                identity: Identity | None = None,
                timeout: float = 20.0) -> Response: ...


def _strict_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - the system store is already loaded
        pass
    # Geely's account hosts need the old handshake mode. This relaxes the
    # handshake only; the certificate and host name are still verified.
    ctx.options |= _LEGACY_SERVER_CONNECT
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _pinning_context() -> ssl.SSLContext:
    """Used only for the pinned hosts. The chain is not validated by the
    context; the server key is compared with the pin before anything is sent."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.options |= _LEGACY_SERVER_CONNECT
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _load_chain_from_memory(ctx: ssl.SSLContext, pem: bytes) -> None:
    """Load a certificate and key into a context without a file on disk.

    Python's TLS module takes a path, so the PEM goes into an anonymous
    memory file (memfd), which has no name in any filesystem, and is read
    back through /proc/self/fd. The descriptor is closed straight after.
    """
    fd = os.memfd_create("identity", os.MFD_CLOEXEC)
    try:
        os.write(fd, pem)
        ctx.load_cert_chain(f"/proc/self/fd/{fd}")
    finally:
        os.close(fd)


def make_identity(cert_pem: bytes, key_pem: bytes) -> Identity:
    pem = cert_pem.strip() + b"\n" + key_pem.strip() + b"\n"
    strict, pinned = _strict_context(), _pinning_context()
    _load_chain_from_memory(strict, pem)
    _load_chain_from_memory(pinned, pem)
    return Identity(strict=strict, pinned=pinned)


def spki_sha256_b64(der_cert: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_der_x509_certificate(der_cert)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


class TlsTransport:
    """The only code in this service that opens an outbound connection."""

    def __init__(self) -> None:
        self._strict = _strict_context()
        self._pinned = _pinning_context()
        # Hosts that have validated against a public CA in this process. Such
        # a host may never be pushed down to the pin afterwards.
        self._validated: set[str] = set()
        self._lock = threading.Lock()

    def connect(self, host: str, identity: Identity | None = None,
                timeout: float = 20.0) -> ssl.SSLSocket:
        """Open a verified TLS connection to host:443, or raise."""
        strict = identity.strict if identity else self._strict
        pinned = identity.pinned if identity else self._pinned
        pins = set(BUNDLED_PINS.get(host, ()))

        raw = socket.create_connection((host, 443), timeout=timeout)
        try:
            tls = strict.wrap_socket(raw, server_hostname=host)
        except ssl.SSLCertVerificationError as exc:
            _close(raw)
            failure = exc
        except Exception:
            _close(raw)
            raise
        else:
            # A pin is a requirement, not a fallback: a chain that validates
            # through some other root is not enough for a pinned host.
            if pins:
                der = tls.getpeercert(binary_form=True)
                if not der or spki_sha256_b64(der) not in pins:
                    _close(tls)
                    raise TlsRefused(f"{host}: server key does not match the pin")
            with self._lock:
                self._validated.add(host)
            return tls

        with self._lock:
            validated_before = host in self._validated
        if validated_before:
            raise TlsRefused(f"{host}: certificate stopped validating")
        if host not in PRIVATE_PKI_HOSTS or not pins:
            raise TlsRefused(f"{host}: certificate validation failed")
        if getattr(failure, "verify_code", None) not in _PRIVATE_CA_VERIFY_CODES:
            raise TlsRefused(f"{host}: certificate is invalid")

        raw = socket.create_connection((host, 443), timeout=timeout)
        try:
            tls = pinned.wrap_socket(raw, server_hostname=host)
        except Exception:
            _close(raw)
            raise
        der = tls.getpeercert(binary_form=True)
        if not der or spki_sha256_b64(der) not in pins:
            _close(tls)
            raise TlsRefused(f"{host}: server key does not match the pin")
        return tls

    def request(self, method: str, host: str, target: str,
                headers: Mapping[str, str], body: bytes = b"",
                identity: Identity | None = None,
                timeout: float = 20.0) -> Response:
        guard.check(method, host, target)
        tls = self.connect(host, identity, timeout)
        conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
        conn.sock = tls
        try:
            conn.putrequest(method, target, skip_accept_encoding=True)
            for name, value in headers.items():
                if name.lower() in ("host", "content-length", "connection"):
                    continue
                conn.putheader(name, value)
            conn.putheader("Connection", "close")
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body if body else None)
            reply = conn.getresponse()
            data = reply.read(_MAX_BODY + 1)
            if len(data) > _MAX_BODY:
                raise OSError("reply larger than this service accepts")
            return Response(reply.status, data)
        finally:
            conn.close()


class GuardedTransport:
    """Wraps any transport so the allow list is checked before it is used."""

    def __init__(self, inner: Transport) -> None:
        self._inner = inner

    def request(self, method: str, host: str, target: str,
                headers: Mapping[str, str], body: bytes = b"",
                identity: Identity | None = None,
                timeout: float = 20.0) -> Response:
        guard.check(method, host, target)
        return self._inner.request(method, host, target, headers, body,
                                   identity, timeout)
