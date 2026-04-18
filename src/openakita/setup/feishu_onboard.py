"""
Feishu Device Flow QR-code app creation & credential validation

Used by Setup Center and CLI Wizard:
- Create a Feishu bot app via QR code scanning (Device Flow)
- Validate existing App ID / App Secret
- Terminal QR code rendering

Device Flow steps (reverse-engineered from @larksuite/openclaw-lark-tools):
  1. init   → Get supported_auth_methods (handshake)
  2. begin  → Submit archetype/auth_method → returns device_code + verification_uri
  3. poll   → Periodically check authorization status; on success returns client_id + client_secret

Note: Endpoints are on accounts.feishu.cn (not open.feishu.cn).

All HTTP calls are async (httpx); bridge.py drives them via asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FEISHU_ACCOUNTS_BASE = "https://accounts.feishu.cn"
LARK_ACCOUNTS_BASE = "https://accounts.larksuite.com"

_DEVICE_FLOW_PATH = "/oauth/v1/app/registration"

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
LARK_TOKEN_URL = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"


class FeishuOnboardError(Exception):
    """Business error during the Device Flow process"""


class FeishuOnboard:
    """Feishu Device Flow QR-code app creation

    Attributes:
        domain: "feishu" or "lark", determines the API base URL
    """

    def __init__(self, domain: str = "feishu", *, timeout: float = 30.0):
        self.domain = domain
        self._timeout = timeout

    @property
    def _base_url(self) -> str:
        return LARK_ACCOUNTS_BASE if self.domain == "lark" else FEISHU_ACCOUNTS_BASE

    @property
    def _endpoint(self) -> str:
        return self._base_url + _DEVICE_FLOW_PATH

    def set_domain(self, domain: str) -> None:
        if domain not in ("feishu", "lark"):
            raise ValueError(f"domain must be 'feishu' or 'lark', got {domain!r}")
        self.domain = domain

    async def init(self) -> dict[str, Any]:
        """Step 1: Handshake to get supported auth methods

        Returns:
            dict with supported_auth_methods etc.
        """
        return await self._post(action="init")

    async def begin(self) -> dict[str, Any]:
        """Step 2: Start Device Flow, get device_code and QR code URL

        Returns:
            dict with at least:
                device_code: str
                verification_uri_complete: str  — URL for the user to scan
                interval: int  — Recommended polling interval (seconds)
                expire_in: int  — device_code validity period (seconds)
        """
        data = await self._post(
            action="begin",
            archetype="PersonalAgent",
            auth_method="client_secret",
            request_user_info="open_id",
        )
        if "device_code" not in data:
            raise FeishuOnboardError(f"begin did not return device_code: {data}")
        return data

    async def poll(self, device_code: str) -> dict[str, Any]:
        """Step 3: Check authorization result

        Returns:
            Success: {client_id, client_secret, user_info: {open_id, tenant_brand}}
            Pending: {error: "authorization_pending"}
            Expired: {error: "expired_token"}
            Denied: {error: "access_denied"}
            Rate-limited: {error: "slow_down"}

        For backward compatibility, app_id/app_secret fields are additionally mapped on success.
        """
        data = await self._post(action="poll", device_code=device_code)
        if data.get("client_id") and data.get("client_secret"):
            data["app_id"] = data["client_id"]
            data["app_secret"] = data["client_secret"]
            user_info = data.get("user_info", {})
            if isinstance(user_info, dict):
                data["user_open_id"] = user_info.get("open_id", "")
                brand = user_info.get("tenant_brand", "")
                if brand:
                    data["domain"] = brand
        error = data.get("error")
        if error:
            data["status"] = error
        return data

    async def poll_until_done(
        self,
        device_code: str,
        *,
        interval: float = 5.0,
        max_attempts: int = 120,
    ) -> dict[str, Any]:
        """Poll continuously until the user completes scanning or times out

        Returns:
            Full response on successful authorization (including app_id / app_secret)

        Raises:
            FeishuOnboardError: Polling timeout or server rejection
        """
        for _i in range(max_attempts):
            result = await self.poll(device_code)

            if result.get("app_id") and result.get("app_secret"):
                return result

            error = result.get("error", "")
            if error in ("authorization_pending",):
                await asyncio.sleep(interval)
                continue
            if error == "slow_down":
                interval += 5
                await asyncio.sleep(interval)
                continue
            if error in ("expired_token", "access_denied"):
                raise FeishuOnboardError(f"Device flow terminated: {error}")

            await asyncio.sleep(interval)

        raise FeishuOnboardError(f"Polling timeout: authorization not completed after {max_attempts} attempts")

    async def _post(self, **form_fields: str) -> dict[str, Any]:
        """Send an x-www-form-urlencoded POST request to the Device Flow endpoint"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._endpoint,
                data=form_fields,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()


async def validate_credentials(
    app_id: str,
    app_secret: str,
    *,
    domain: str = "feishu",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Validate whether Feishu App ID / App Secret are valid

    Validates by requesting a tenant_access_token.

    Returns:
        {"valid": True, "tenant_access_token": "t-xxx"} or
        {"valid": False, "error": "..."}
    """
    url = LARK_TOKEN_URL if domain == "lark" else FEISHU_TOKEN_URL
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                url,
                json={"app_id": app_id, "app_secret": app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code", -1) == 0:
                return {
                    "valid": True,
                    "tenant_access_token": data.get("tenant_access_token", ""),
                }
            return {"valid": False, "error": data.get("msg", "Unknown error")}
        except httpx.HTTPStatusError as e:
            return {"valid": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}


def render_qr_terminal(url: str) -> None:
    """Render a QR code in the terminal (requires qrcode package; falls back to printing the URL)"""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        logger.info("qrcode package not installed, outputting URL directly")
        print(f"\nPlease open the following link in your browser or Feishu:\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR rendering failed: {e}")
        print(f"\nPlease open the following link in your browser or Feishu:\n  {url}\n")
