"""Security helpers: pseudonymisation, password hashing, signed tokens.

Only Python's standard library is used so the cryptography is easy to audit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from core import config


# ---------------------------------------------------------------- pseudonymisation
def pseudonymise(raw_id: str, key: Optional[str] = None) -> str:
    """Replace a real identifier with a keyed hash token.

    The same input always gives the same token (so journeys can be linked),
    but the token cannot be reversed without the secret key.
    """
    secret = (key or config.SECRET_KEY).encode("utf-8")
    digest = hmac.new(secret, raw_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return "acct_" + digest[:12]


# ---------------------------------------------------------------- passwords
_ITERATIONS = 200_000


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(username: str, role: str, ttl_hours: Optional[int] = None) -> str:
    payload = {"sub": username, "role": role,
               "exp": int(time.time() + 3600 * (ttl_hours or config.TOKEN_TTL_HOURS))}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(config.SECRET_KEY.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def verify_token(token: str) -> Optional[dict]:
    """Return the token payload if the signature is valid and not expired."""
    try:
        body, sig = token.split(".")
        expected = hmac.new(config.SECRET_KEY.encode("utf-8"), body.encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(sig), expected):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, json.JSONDecodeError):
        return None


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
