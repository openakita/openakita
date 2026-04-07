"""
Authentication API routes for web access.

POST /api/auth/login     — password login, returns access token + sets refresh cookie
POST /api/auth/refresh   — exchange refresh cookie for new access token
POST /api/auth/logout    — clear refresh cookie
GET  /api/auth/check     — check current auth status
POST /api/auth/change-password  — change password (local only)
GET  /api/auth/password-hint    — get password hint (local only)
"""

from __future__ import annotations

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
)

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
    """Check if request is truly from localhost, respecting TRUST_PROXY."""
    trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
    real_ip = get_client_ip(request, trust_proxy=trust_proxy)
    if real_ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if real_ip.startswith("::ffff:") and real_ip[7:] == "127.0.0.1":
        return True
    return False


# ── POST /api/auth/login ──


@router.post("/login")
async def login(request: Request, response: Response):
    config = _get_config(request)
    trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
    client_ip = get_client_ip(request, trust_proxy=trust_proxy)

    if not _login_limiter.is_allowed(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many login attempts, please try again later"},
        )

    body = await _parse_body(request)
    password = body.get("password", "")

    if not config.verify_password(password):
        logger.warning("Failed login attempt from %s", client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid password"},
        )

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
# Local: no current_password needed.
# Remote: must provide correct current_password (old password).


@router.post("/change-password")
async def change_password(request: Request):
    config = _get_config(request)
    body = await _parse_body(request)
    is_local = _is_local_from_real_ip(request)

    if not is_local:
        current_password = body.get("current_password", "")
        if not current_password or not config.verify_password(current_password):
            return JSONResponse(
                status_code=403,
                content={"detail": "Current password is required for remote password change"},
            )

    new_password = body.get("new_password", "")
    if not new_password:
        return JSONResponse(status_code=400, content={"detail": "new_password is required"})
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
