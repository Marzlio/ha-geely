"""The slide captcha Geely puts in front of the email code.

Ported from geetest_solver.py of the published integration
YossiKon/geely-connect, itself derived from nitaybz/geely-global-ha (both
MIT, see NOTICE.txt). The captcha id, the widget's public key and the serial
are the published values of Geely's own captcha deployment. It runs once per
"send code" press by the account owner and for nothing else.
"""
from __future__ import annotations

import io
import json
import random
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

from .transport import Transport

CAPTCHA_HOST = "captcha4.geely.com"
_CAPTCHA_ID = "4c1ef89633cbab987d1ee170115a8dd4"
_RSA_N_HEX = (
    "a07fe9d66006cb5ff61b6ab0c77208bca38a4674a96f121f9e8406c019ddd3b4"
    "c2fc0d76e54973328ea5cd08af91ac7cd166a200708f4f5650f405a3ab1d14f9"
    "c2dd6b94d788de87fa2249ff0826c0bb9b9a1d49d5662888afad8e891b235358"
    "7a89175cb4dc215764b067b8e4531414d4efb2d7c3cfe7b1f69355968cd9b2ab"
)
_AES_IV = b"0000000000000000"
_SERIAL = "7eb32de3afb26299903b0e5bfaee17ab!!3a304286ca668d724d20f4ae2edf4481"

_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept": "*/*",
}

_HOST_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.\-]{0,252}\Z")
_HOST_SUFFIXES = (".geely.com", ".geetest.com")


class CaptchaError(Exception):
    """The captcha exchange gave an answer this code cannot use."""


@dataclass
class _Load:
    lot_number: str
    challenge: str
    pt: str
    payload: str
    process_token: str
    payload_protocol: int
    captcha_id: str
    static_servers: list
    bg_path: str
    slice_path: str


# ---------- picture matching ----------

def _to_grayscale(png: bytes):
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    if img.mode in ("P", "PA"):
        img = img.convert("RGBA")
    arr = np.asarray(img)
    if arr.ndim == 2:
        return arr.astype(np.float32)
    if arr.shape[-1] in (2, 4):
        arr = arr[..., :-1]              # drop alpha, do not composite
    if arr.shape[-1] == 1:
        return arr[..., 0].astype(np.float32)
    if arr.shape[-1] == 3:
        return (0.299 * arr[..., 0] + 0.587 * arr[..., 1]
                + 0.114 * arr[..., 2]).astype(np.float32)
    return arr[..., 0].astype(np.float32)


def _canny_edges(im, low: float = 50.0, high: float = 100.0):
    """Canny edges in numpy + scipy.ndimage: blur, Sobel, non-maximum
    suppression, then hysteresis by connected components."""
    import numpy as np
    from scipy import ndimage as ndi

    blurred = ndi.gaussian_filter(im, sigma=1.0)
    gx = ndi.sobel(blurred, axis=1)
    gy = ndi.sobel(blurred, axis=0)
    mag = np.hypot(gx, gy)

    angle = np.degrees(np.arctan2(gy, gx)) % 180.0
    h, w = mag.shape
    nms = np.zeros_like(mag)
    mp = np.pad(mag, 1, mode="constant")
    bin0 = (angle < 22.5) | (angle >= 157.5)
    bin1 = (angle >= 22.5) & (angle < 67.5)
    bin2 = (angle >= 67.5) & (angle < 112.5)
    bin3 = (angle >= 112.5) & (angle < 157.5)
    center = mp[1:1 + h, 1:1 + w]
    n_h1, n_h2 = mp[1:1 + h, 0:w], mp[1:1 + h, 2:2 + w]
    n_v1, n_v2 = mp[0:h, 1:1 + w], mp[2:2 + h, 1:1 + w]
    n_m1, n_m2 = mp[0:h, 0:w], mp[2:2 + h, 2:2 + w]
    n_a1, n_a2 = mp[0:h, 2:2 + w], mp[2:2 + h, 0:w]
    keep = np.zeros_like(mag, dtype=bool)
    keep |= bin0 & (center >= n_h1) & (center >= n_h2)
    keep |= bin1 & (center >= n_m1) & (center >= n_m2)
    keep |= bin2 & (center >= n_v1) & (center >= n_v2)
    keep |= bin3 & (center >= n_a1) & (center >= n_a2)
    nms[keep] = mag[keep]

    strong = nms >= high
    weak = (nms >= low) & (nms < high)
    labeled, _ = ndi.label(strong | weak, structure=np.ones((3, 3), dtype=bool))
    has_strong = np.zeros(labeled.max() + 1, dtype=bool)
    has_strong[labeled[strong]] = True
    return has_strong[labeled].astype(np.float32)


def find_gap_x(bg_png: bytes, slice_png: bytes) -> int:
    """The X offset, in picture pixels, where the piece fits the gap. The
    piece starts at the left edge, so matches left of its width are ignored."""
    from scipy.signal import fftconvolve

    e_bg = _canny_edges(_to_grayscale(bg_png))
    e_sl = _canny_edges(_to_grayscale(slice_png))
    res = fftconvolve(e_bg, e_sl[::-1, ::-1], mode="valid")
    res[:, :e_sl.shape[1]] = -1.0
    return int(int(res.argmax()) % res.shape[1])


# ---------- the answer parameter ----------

def _encrypt_w(plaintext: str) -> str:
    """AES-128-CBC body under a random key, then that key under the
    widget's RSA public key. Output is hex(body) + hex(wrapped key)."""
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    aes_key = secrets.token_hex(8).encode("utf-8")
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(aes_key), modes.CBC(_AES_IV)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    public = rsa.RSAPublicNumbers(0x10001, int(_RSA_N_HEX, 16)).public_key()
    wrapped = public.encrypt(aes_key, asym_padding.PKCS1v15())
    return ct.hex() + wrapped.hex()


def _track(target_x: float, total_ms: int) -> str:
    pts = []
    n = 25
    for i in range(1, n + 1):
        eased = 1 - (1 - i / n) ** 3
        x = round(target_x * eased + random.uniform(-1.0, 1.0), 0)
        y = random.randint(-2, 2)
        t = round(total_ms * eased + random.uniform(-3, 3))
        pts.append([int(x), int(y), int(t)])
    return json.dumps(pts, separators=(",", ":"))


def _inner_payload(set_left: int, passtime_ms: int) -> dict:
    env = {"roe": {"aup": "3", "sep": "3", "egp": "3", "auh": "3",
                   "rew": "3", "snh": "1", "res": "3", "cdc": "3"}}
    return {
        "serial": _SERIAL,
        "env": quote(json.dumps(env, separators=(",", ":"))),
        "geetest": "captcha",
        "gct": str(random.randint(-2147483648, 2147483647)),
        "bjpr": "zc1h7e6d",
        "em": {"ph": 0, "cp": 0, "ek": "11", "wd": 1, "nt": 0, "si": 0, "sc": 0},
        "type": "slide",
        "answer": int(set_left),
        "passtime": int(passtime_ms),
        "trackOffset": _track(set_left, passtime_ms),
    }


# ---------- load, pictures, verify ----------

def _unwrap_jsonp(text: str, callback: str) -> Any:
    marker = f"{callback}("
    if marker not in text:
        raise CaptchaError("captcha reply was not in the expected envelope")
    return json.loads(text.split(marker, 1)[1].rsplit(")", 1)[0])


def _fetch_load(transport: Transport) -> _Load:
    callback = f"geetest_{int(time.time() * 1000)}"
    query = urlencode({
        "captcha_id": _CAPTCHA_ID,
        "challenge": str(uuid4()),
        "client_type": "web",
        "language": "eng",
        "encryption": "AES_RSA",
        "ext": json.dumps({"ua": _UA}, separators=(",", ":")),
        "callback": callback,
    })
    reply = transport.request("GET", CAPTCHA_HOST, f"/load?{query}", _HEADERS,
                              timeout=15)
    if reply.status >= 400:
        raise CaptchaError("captcha load was refused")
    j = _unwrap_jsonp(reply.body.decode("utf-8", "replace"), callback)
    if not isinstance(j, dict) or j.get("status") != "success":
        raise CaptchaError("captcha load did not succeed")
    d = j["data"]
    return _Load(
        lot_number=d["lot_number"],
        challenge=d["challenge"],
        pt=str(d.get("pt", "10")),
        payload=d["payload"],
        process_token=d["process_token"],
        payload_protocol=int(d.get("payload_protocol", 1)),
        captcha_id=d["captcha_id"],
        static_servers=d.get("static_servers") or [],
        bg_path=d["bg"],
        slice_path=d["slice"],
    )


def _picture_hosts(static_servers: list) -> list[str]:
    """The picture hosts the captcha names, filtered to bare host names under
    the expected domains, with the captcha host itself as the last resort."""
    hosts = []
    for host in static_servers:
        if not isinstance(host, str) or not _HOST_RE.match(host):
            continue
        if not host.lower().endswith(_HOST_SUFFIXES):
            continue
        hosts.append(host)
    return hosts + [CAPTCHA_HOST]


def _fetch_picture(transport: Transport, static_servers: list, path: str) -> bytes:
    failure: Exception | None = None
    for host in _picture_hosts(static_servers):
        try:
            reply = transport.request("GET", host, "/" + str(path).lstrip("/"),
                                      _HEADERS, timeout=15)
            if reply.status >= 400:
                raise CaptchaError("captcha picture was refused")
            return reply.body
        except Exception as exc:  # noqa: BLE001 - try the next host
            failure = exc
    if isinstance(failure, OSError):
        raise failure
    raise CaptchaError("captcha picture could not be fetched")


def _verify(transport: Transport, load: _Load, w: str) -> dict:
    callback = f"geetest_{int(time.time() * 1000)}"
    query = urlencode({
        "lot_number": load.lot_number,
        "captcha_id": load.captcha_id,
        "client_type": "web",
        "challenge": load.challenge,
        "pt": load.pt,
        "payload": load.payload,
        "payload_protocol": load.payload_protocol,
        "process_token": load.process_token,
        "w": w,
        "callback": callback,
    })
    reply = transport.request("GET", CAPTCHA_HOST, f"/verify?{query}", _HEADERS,
                              timeout=15)
    text = reply.body.decode("utf-8", "replace")
    try:
        if text.startswith(callback + "("):
            j = json.loads(text[len(callback) + 1:-1])
        else:
            j = json.loads(text)
    except ValueError:
        raise CaptchaError("captcha verify reply could not be read") from None
    return j if isinstance(j, dict) else {}


def solve(transport: Transport) -> dict | None:
    """One attempt. Returns the verified result block, or None when the
    answer was not accepted. The matcher is right about 85% of the time, so
    the caller retries."""
    load = _fetch_load(transport)
    bg = _fetch_picture(transport, load.static_servers, load.bg_path)
    piece = _fetch_picture(transport, load.static_servers, load.slice_path)
    gap_x = find_gap_x(bg, piece)
    inner = _inner_payload(gap_x, random.randint(900, 1500))
    w = _encrypt_w(json.dumps(inner, separators=(",", ":")))
    result = _verify(transport, load, w)
    data = result.get("data")
    if result.get("status") == "success" and isinstance(data, dict) \
            and data.get("result") == "success":
        return data
    return None
