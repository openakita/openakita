"""
Web access authentication for OpenAkita.

Single-password mode with JWT tokens. Local requests (127.0.0.1) are exempt
from authentication to preserve the desktop experience.

Storage: data/web_access.json
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..core.auth.tokens import TokenClaims, decode_jwt, encode_jwt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACCESS_TOKEN_TTL = 24 * 3600          # 24 hours
REFRESH_TOKEN_TTL = 90 * 24 * 3600    # 90 days
REFRESH_COOKIE_NAME = "openakita_refresh"
PASSWORD_ENV_VAR = "OPENAKITA_WEB_PASSWORD"

AUTH_EXEMPT_PATHS = frozenset({
    "/",
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/refresh",
    "/api/auth/check",
    "/api/logs/frontend",
})
AUTH_EXEMPT_PREFIXES = ("/web/", "/web", "/ws/", "/docs", "/openapi.json", "/redoc", "/user-docs")

# ---------------------------------------------------------------------------
# Password hashing (scrypt, stdlib)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Hash password with scrypt. Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_bytes(16)
    h = hashlib.scrypt(
        password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32,
    )
    return h.hex(), salt.hex()


def _verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    try:
        h = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=16384, r=8, p=1, dklen=32,
        )
        return hmac.compare_digest(h.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Web Access config (data/web_access.json)
# ---------------------------------------------------------------------------

class WebAccessConfig:
    """Manages the web_access.json file."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "web_access.json"
        self._data: dict[str, Any] = {}
        self._lock = __import__("threading").Lock()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text("utf-8"))
            except Exception:
                logger.warning("Failed to read web_access.json, will regenerate")
                self._data = {}

        env_password = os.environ.get(PASSWORD_ENV_VAR, "").strip()
        needs_save = False

        if not self._data.get("jwt_secret"):
            self._data["jwt_secret"] = secrets.token_hex(32)
            needs_save = True

        if not self._data.get("data_epoch"):
            self._data["data_epoch"] = secrets.token_hex(8)
            needs_save = True

        if not self._data.get("token_version"):
            self._data["token_version"] = 1
            needs_save = True

        if env_password:
            # Environment variable overrides stored password — but only update
            # if the password actually changed (avoids needless rehash on every start)
            existing_hash = self._data.get("password_hash", "")
            existing_salt = self._data.get("password_salt", "")
            if not existing_hash or not existing_salt or not _verify_password(env_password, existing_hash, existing_salt):
                hash_hex, salt_hex = _hash_password(env_password)
                self._data["password_hash"] = hash_hex
                self._data["password_salt"] = salt_hex
                self._data["password_plain_hint"] = _make_hint(env_password)
                self._data["password_user_set"] = True
                needs_save = True
            elif not self._data.get("password_user_set"):
                self._data["password_user_set"] = True
                needs_save = True
        elif not self._data.get("password_hash"):
            # First run: auto-generate random password
            generated = secrets.token_urlsafe(12)
            hash_hex, salt_hex = _hash_password(generated)
            self._data["password_hash"] = hash_hex
            self._data["password_salt"] = salt_hex
            self._data["password_plain_hint"] = _make_hint(generated)
            self._data["password_user_set"] = False
            self._data["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            needs_save = True
            logger.info(
                "═══════════════════════════════════════════════════════════\n"
                "  Web access password (auto-generated): %s\n"
                "  Save this password — it is only printed once.\n"
                "  You can reset it via the Desktop Setup Center or set\n"
                "  %s environment variable.\n"
                "═══════════════════════════════════════════════════════════",
                generated, PASSWORD_ENV_VAR,
            )

        if needs_save:
            self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save()

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2) + "\n", "utf-8")
            tmp.replace(self._path)

    @property
    def jwt_secret(self) -> str:
        return self._data["jwt_secret"]

    @property
    def token_version(self) -> int:
        return self._data.get("token_version", 1)

    @property
    def data_epoch(self) -> str:
        return self._data.get("data_epoch", "")

    @property
    def password_hint(self) -> str:
        return self._data.get("password_plain_hint", "")

    def verify_password(self, password: str) -> bool:
        h = self._data.get("password_hash", "")
        s = self._data.get("password_salt", "")
        if not h or not s:
            return False
        return _verify_password(password, h, s)

    @property
    def password_user_set(self) -> bool:
        return self._data.get("password_user_set", False)

    def change_password(self, new_password: str) -> None:
        hash_hex, salt_hex = _hash_password(new_password)
        self._data["password_hash"] = hash_hex
        self._data["password_salt"] = salt_hex
        self._data["password_plain_hint"] = _make_hint(new_password)
        self._data["password_user_set"] = True
        self._data["token_version"] = self.token_version + 1
        self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save()

    def create_access_token(self) -> str:
        claims = TokenClaims(
            token_type="access",
            subject="desktop_user",
            expires_in=ACCESS_TOKEN_TTL,
            version=self.token_version,
            scope=["web:access"],
        )
        return encode_jwt(claims.to_payload(), self.jwt_secret)

    def create_refresh_token(self) -> str:
        claims = TokenClaims(
            token_type="refresh",
            subject="desktop_user",
            expires_in=REFRESH_TOKEN_TTL,
            version=self.token_version,
            scope=["web:refresh"],
        )
        return encode_jwt(claims.to_payload(), self.jwt_secret)

    def validate_access_token(self, token: str) -> bool:
        payload = decode_jwt(token, self.jwt_secret)
        if not payload:
            return False
        if payload.get("type") != "access":
            return False
        if payload.get("ver") != self.token_version:
            return False
        return True

    def validate_refresh_token(self, token: str) -> dict[str, Any] | None:
        payload = decode_jwt(token, self.jwt_secret)
        if not payload:
            return None
        if payload.get("type") != "refresh":
            return None
        if payload.get("ver") != self.token_version:
            return None
        return payload


def _make_hint(password: str) -> str:
    if len(password) <= 6:
        return password[0] + "..." + password[-1] if len(password) >= 2 else "***"
    return password[:3] + "..." + password[-3:]


# ---------------------------------------------------------------------------
# Rate limiter (simple in-memory, per-IP)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        timestamps = self._hits.get(key, [])
        timestamps = [t for t in timestamps if now - t < self._window]
        if not timestamps:
            self._hits.pop(key, None)
        if len(timestamps) >= self._max:
            self._hits[key] = timestamps
            return False
        timestamps.append(now)
        self._hits[key] = timestamps
        return True


# Global rate limiters
_login_limiter = RateLimiter(max_requests=5, window_seconds=60)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def get_client_ip(request: Request, *, trust_proxy: bool = False) -> str:
    """Return the client IP, respecting X-Forwarded-For when trust_proxy is on."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_local_request(request: Request) -> bool:
    """Check if request originates from localhost (direct connection only).

    Handles plain IPv4/IPv6 loopback as well as IPv4-mapped IPv6 addresses
    (``::ffff:127.0.0.1``) which some OS/Uvicorn combinations report when the
    server binds to ``0.0.0.0`` on dual-stack systems (common on Windows).
    """
    if not request.client:
        return False
    host = request.client.host
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    # IPv4-mapped IPv6: ::ffff:127.0.0.1
    if host.startswith("::ffff:") and host[7:] == "127.0.0.1":
        return True
    return False


def _is_auth_exempt(path: str) -> bool:
    """Check if the path is exempt from authentication."""
    if path in AUTH_EXEMPT_PATHS:
        return True
    for prefix in AUTH_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def create_auth_middleware(config: WebAccessConfig):
    """Create the authentication middleware function."""

    async def auth_middleware(request: Request, call_next):
        # CORS preflight must always pass through (browser sends OPTIONS without auth)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Static files and auth endpoints are always accessible
        if _is_auth_exempt(path):
            return await call_next(request)

        # Read trust_proxy on every request so that changes made via
        # /api/config/env take effect immediately without a server restart.
        trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")

        # Local requests bypass auth.
        # When trust_proxy is on, a reverse proxy (Nginx/Caddy) connects from
        # 127.0.0.1 but always adds X-Forwarded-For.  A direct local connection
        # (e.g. Tauri desktop) also comes from 127.0.0.1 but has NO
        # X-Forwarded-For.  We use this to distinguish the two: direct local
        # connections are still exempt; proxy-forwarded ones must authenticate.
        if _is_local_request(request) and (
            not trust_proxy or not request.headers.get("x-forwarded-for")
        ):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if config.validate_access_token(token):
                return await call_next(request)

        # Check query parameter token (for <img> / <audio> tags that can't set headers)
        query_token = request.query_params.get("token", "")
        if query_token and config.validate_access_token(query_token):
            return await call_next(request)

        # Check X-API-Key header (for programmatic access)
        api_key = request.headers.get("x-api-key", "")
        if api_key and config.verify_password(api_key):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )

    return auth_middleware
