"""
Authentication API routes for web access.

POST /api/auth/login           — password login, returns access token + sets refresh cookie
POST /api/auth/refresh         — exchange refresh cookie for new access token
POST /api/auth/logout          — clear refresh cookie
GET  /api/auth/check           — check current auth status
GET  /api/auth/setup-status    — whether first-run Setup flow is required
POST /api/auth/setup           — initial password assignment (loopback only)
POST /api/auth/change-password — change password (local: no current pw; remote: needs current)
GET  /api/auth/password-hint   — get password hint (local only)
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..auth import (
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_TTL,
    WebAccessConfig,
    _login_limiter,
    get_client_ip,
    is_trusted_local,
)
from ..setup_state import is_setup_complete, should_require_setup

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _parse_body(request: Request) -> dict:
    """Parse request body as JSON or form-urlencoded (for CORS-preflight-free mobile requests)."""
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        return await request.json()
    if "form" in ct or "urlencoded" in ct:
        form = await request.form()
        return dict(form)
    # fallback: try JSON
    try:
        return await request.json()
    except Exception:
        return {}


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Set the refresh token as an httpOnly cookie."""
    is_https = os.environ.get("API_HTTPS", "").lower() in ("1", "true", "yes")
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=REFRESH_TOKEN_TTL,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/api/auth",
    )


def _get_config(request: Request) -> WebAccessConfig:
    return request.app.state.web_access_config


def _is_local_from_real_ip(request: Request) -> bool:
    """Check if request is truly from localhost, respecting TRUST_PROXY.

    Thin wrapper around :func:`openakita.api.auth.is_trusted_local` kept so
    existing call-sites don't change. New code should call ``is_trusted_local``
    directly.
    """
    return is_trusted_local(request)


# Password strength rules — kept liberal because we trust the user to choose
# a sensible password while still blocking the most common footguns.
# - len(>=8) catches "1234", "abcd", "" etc.
# - "not entirely digits" catches "12345678" pad-locks.
# - "not entirely letters" catches "passwords" / "abcdefgh".
# Unicode strings are accepted; we count code points, not bytes, so emoji /
# CJK / Cyrillic passwords work as expected.
MIN_PASSWORD_LENGTH = 8


def _validate_password_strength(password: str) -> str | None:
    """Return an error code (string) when the password is too weak; else None.

    Error codes are short kebab-case identifiers so the frontend can map them
    to localised messages without parsing English prose.
    """
    if not isinstance(password, str):
        return "password_invalid"
    if len(password) < MIN_PASSWORD_LENGTH:
        return "password_too_short"
    if password.isdigit():
        return "password_all_digits"
    if password.isalpha():
        return "password_all_letters"
    return None


# Coordination for /api/auth/setup so two browsers racing each other don't both
# think they "won" the setup. The lock is module-scoped (one server, one
# WebAccessConfig instance) so an asyncio.Lock is sufficient.
_setup_lock = asyncio.Lock()


# ── POST /api/auth/login ──


@router.post("/login")
async def login(request: Request, response: Response):
    config = _get_config(request)
    trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
    client_ip = get_client_ip(request, trust_proxy=trust_proxy)

    if not _login_limiter.is_allowed(client_ip):
        retry_after = _login_limiter.retry_after_seconds(client_ip)
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many failed login attempts, please try again later",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    body = await _parse_body(request)
    password = body.get("password", "")

    if not config.verify_password(password):
        # Only failures count against the rate limit. A successful login
        # immediately clears the counter so the legitimate user isn't punished
        # for previous typos.
        _login_limiter.register_failure(client_ip)
        logger.warning("Failed login attempt from %s", client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid password"},
        )

    _login_limiter.clear(client_ip)
    access_token = config.create_access_token()
    refresh_token = config.create_refresh_token()

    _set_refresh_cookie(response, refresh_token)

    logger.info("Successful login from %s", client_ip)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
    }


# ── POST /api/auth/refresh ──


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    config = _get_config(request)
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if not cookie:
        _clear_refresh_cookie(response)
        return JSONResponse(status_code=401, content={"detail": "No refresh token"})

    payload = config.validate_refresh_token(cookie)
    if not payload:
        _clear_refresh_cookie(response)
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired refresh token"})

    # Issue new tokens
    access_token = config.create_access_token()
    new_refresh = config.create_refresh_token()
    _set_refresh_cookie(response, new_refresh)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
    }


# ── POST /api/auth/logout ──


@router.post("/logout")
async def logout(response: Response):
    _clear_refresh_cookie(response)
    return {"status": "ok"}


# ── GET /api/auth/setup-status ──


@router.get("/setup-status")
async def setup_status(request: Request, response: Response):
    """Tell the frontend whether the first-run Setup flow is needed.

    The result is the truth used by both the setup gate middleware and the
    SPA, so a single contract here is the source of truth. ``reason`` is a
    short identifier for the *why* rather than free-form prose so the frontend
    can pick the right copy.
    """
    response.headers["Cache-Control"] = "no-store"
    config = _get_config(request)
    if is_setup_complete(config):
        return {
            "setup_required": False,
            "reason": "already_set",
        }
    if not should_require_setup(request, config):
        return {
            "setup_required": False,
            "reason": "loopback_trusted",
        }
    return {
        "setup_required": True,
        "reason": "password_not_set",
    }


# ── POST /api/auth/setup ──
# First-run password assignment. Only valid when no password is yet stored.


@router.post("/setup")
async def setup_initial_password(request: Request, response: Response):
    """Set the first password for a fresh install.

    Why a dedicated endpoint instead of reusing ``change-password``:

    - The semantics are different: ``change-password`` requires the old
      password from non-loopback callers; ``setup`` requires that *no*
      password is stored yet, but is otherwise reachable from any caller (the
      setup gate has already filtered out non-trusted clients before the
      caller can hit a non-setup endpoint).
    - The success response logs the user in immediately by issuing
      access + refresh tokens, mirroring ``/login``.

    The refresh token is set as an ``httpOnly`` cookie (same as login) — it
    is *never* returned in the JSON body. Returning it in the body would
    defeat the point of the httpOnly flag.
    """
    config = _get_config(request)
    body = await _parse_body(request)
    new_password = body.get("new_password") or body.get("password") or ""
    confirm_password = body.get("confirm_password")

    if confirm_password is not None and new_password != confirm_password:
        return JSONResponse(
            status_code=400,
            content={"detail": "password_mismatch"},
        )

    err = _validate_password_strength(new_password)
    if err:
        return JSONResponse(status_code=400, content={"detail": err})

    async with _setup_lock:
        # Re-check inside the critical section: another tab may have just
        # completed setup while this request was in flight.
        if is_setup_complete(config):
            return JSONResponse(
                status_code=409,
                content={"detail": "already_set"},
            )
        config.change_password(new_password)

    access_token = config.create_access_token()
    refresh_token = config.create_refresh_token()
    _set_refresh_cookie(response, refresh_token)

    client_ip = get_client_ip(
        request,
        trust_proxy=os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes"),
    )
    logger.info("Setup flow completed; initial password set from %s", client_ip)

    return {
        "status": "ok",
        "setup_complete": True,
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
    }


# ── GET /api/auth/check ──


@router.get("/check")
async def check_auth(request: Request, response: Response):
    """Check whether the current request is authenticated."""
    response.headers["Cache-Control"] = "no-store"

    config = _get_config(request)
    is_local = _is_local_from_real_ip(request)

    # Local requests are always authenticated (unless behind proxy)
    if is_local:
        return {
            "authenticated": True,
            "method": "local",
            "password_user_set": config.password_user_set,
        }

    # Check bearer token
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if config.validate_access_token(token):
            return {
                "authenticated": True,
                "method": "token",
                "password_user_set": config.password_user_set,
            }

    # Check refresh cookie (means user has a valid session)
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie:
        payload = config.validate_refresh_token(cookie)
        if payload:
            return {
                "authenticated": True,
                "method": "refresh_cookie",
                "needs_refresh": True,
                "password_user_set": config.password_user_set,
            }

    return {"authenticated": False}


# ── POST /api/auth/change-password ──
# Cases:
# - First-run, loopback caller:    no current_password required (initial setup).
# - First-run, remote caller:      blocked (must use /setup gate).
# - Subsequent, loopback caller:   no current_password required.
# - Subsequent, remote caller:     must provide correct current_password.


@router.post("/change-password")
async def change_password(request: Request):
    config = _get_config(request)
    body = await _parse_body(request)
    is_local = _is_local_from_real_ip(request)
    already_set = is_setup_complete(config)

    if already_set and not is_local:
        current_password = body.get("current_password", "")
        if not current_password or not config.verify_password(current_password):
            return JSONResponse(
                status_code=403,
                content={"detail": "Current password is required for remote password change"},
            )
    elif not already_set and not is_local:
        # The setup gate normally catches this before we get here; this is a
        # belt-and-braces check so direct API calls can't bypass the flow.
        return JSONResponse(
            status_code=403,
            content={"detail": "Initial password must be set from a local connection"},
        )

    new_password = body.get("new_password", "")
    err = _validate_password_strength(new_password)
    if err:
        return JSONResponse(status_code=400, content={"detail": err})
    config.change_password(new_password)

    from .websocket import manager

    disconnected = await manager.disconnect_remote_clients()

    origin = "localhost" if is_local else "remote"
    logger.info(
        "Web access password changed from %s, disconnected %d remote session(s)",
        origin,
        disconnected,
    )
    return {
        "status": "ok",
        "message": "Password changed. All remote sessions invalidated.",
        "disconnected": disconnected,
    }


# ── GET /api/auth/password-hint (local only) ──


@router.get("/password-hint")
async def password_hint(request: Request):
    if not _is_local_from_real_ip(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "Password hint only available from localhost"},
        )

    config = _get_config(request)
    return {"hint": config.password_hint}
