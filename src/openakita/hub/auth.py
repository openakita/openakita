"""
Platform authentication client.

Implements GitHub Device Authorization Flow for desktop app login.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass
class AuthResult:
    status: str  # "ok", "pending", "slow_down", "expired", "needs_registration", "error"
    api_key: str = ""
    user_id: str = ""
    username: str = ""
    tier: str = ""
    ap_balance: int = 0
    pioneer_number: int = 0
    error: str = ""
    github_user: dict | None = None


class HubAuthClient:
    """Client for authenticating with OpenAkita Platform via Device Auth Flow."""

    def __init__(self, hub_url: str | None = None):
        raw = (hub_url or settings.hub_api_url).rstrip("/")
        # hub_api_url already ends with /api; strip it so we can build full paths
        self.hub_url = raw.removesuffix("/api")

    async def request_device_code(self) -> DeviceCodeResponse | None:
        """Step 1: Request a device code from the platform."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.hub_url}/api/auth/device/code")
                resp.raise_for_status()
                data = resp.json()
                return DeviceCodeResponse(
                    device_code=data["device_code"],
                    user_code=data["user_code"],
                    verification_uri=data["verification_uri"],
                    expires_in=data.get("expires_in", 900),
                    interval=data.get("interval", 5),
                )
        except Exception as e:
            logger.error("Failed to request device code: %s", e)
            return None

    async def poll_for_token(
        self, device_code: str, interval: int = 5, timeout: int = 900
    ) -> AuthResult:
        """Step 2: Poll for token until user authorizes or timeout."""
        deadline = time.monotonic() + timeout
        current_interval = interval

        async with httpx.AsyncClient(timeout=15) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(current_interval)
                try:
                    resp = await client.post(
                        f"{self.hub_url}/api/auth/device/token",
                        json={"device_code": device_code},
                    )
                    data = resp.json()
                    status = data.get("status", "error")

                    if status == "ok":
                        user = data.get("user") or {}
                        return AuthResult(
                            status="ok",
                            api_key=data.get("api_key", ""),
                            user_id=user.get("id", ""),
                            username=user.get("username", ""),
                            tier=user.get("tier", "explorer"),
                            ap_balance=user.get("apBalance", 0),
                            pioneer_number=user.get("pioneerNumber", 0),
                        )
                    elif status == "needs_registration":
                        return AuthResult(
                            status="needs_registration",
                            github_user=data.get("github_user"),
                        )
                    elif status == "slow_down":
                        current_interval = data.get("interval", current_interval + 5)
                    elif status == "expired":
                        return AuthResult(status="expired", error="Device code expired")
                except Exception as e:
                    logger.warning("Poll error (will retry): %s", e)

        return AuthResult(status="expired", error="Polling timed out")
