"""Shared token helpers for OpenAkita auth surfaces."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    msg = f"{header}.{payload_b64}".encode()
    sig = _b64url_encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest())
    return f"{header}.{payload_b64}.{sig}"


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        msg = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


@dataclass(frozen=True)
class TokenClaims:
    token_type: str
    subject: str = "desktop_user"
    expires_in: int = 0
    version: int = 1
    scope: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        now = int(time.time())
        payload = {
            "sub": self.subject,
            "iat": now,
            "exp": now + int(self.expires_in),
            "jti": secrets.token_hex(16),
            "type": self.token_type,
            "ver": self.version,
        }
        if self.scope:
            payload["scope"] = list(self.scope)
        if self.extra:
            payload.update(self.extra)
        return payload
