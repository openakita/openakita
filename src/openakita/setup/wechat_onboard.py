"""
WeChat iLink Bot QR-Code Login

Used by Setup Center and CLI Wizard:
- Fetch login QR code (get_bot_qrcode)
- Poll QR code scan status (get_qrcode_status)
- Return Bearer token + base_url after scan confirmation

iLink Bot API QR login flow (aligned with @tencent-weixin/openclaw-weixin v2.1.6):
  1. GET get_bot_qrcode?bot_type=3 -> fetch qrcode / qrcode_img_content
  2. GET get_qrcode_status?qrcode=... -> poll status (wait -> scaned -> confirmed)
  3. On confirmed, returns bot_token / ilink_bot_id / baseurl

All HTTP calls are async (httpx); bridge.py drives them via asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_ILINK_BOT_TYPE = "3"

_QR_LONG_POLL_TIMEOUT_S = 35.0
MAX_QR_REFRESH_COUNT = 3


def _onboard_common_headers() -> dict[str, str]:
    """Shared iLink headers for QR login requests (same constants as wechat adapter)."""
    import os

    compat_ver = os.environ.get("WECHAT_OPENCLAW_COMPAT_VERSION", "2.1.6")
    app_id = os.environ.get("WECHAT_ILINK_APP_ID", "bot")
    parts = compat_ver.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    client_ver = str((major << 16) | (minor << 8) | patch)
    return {
        "iLink-App-Id": app_id,
        "iLink-App-ClientVersion": client_ver,
    }


class WeChatOnboardError(Exception):
    """Business error during QR code login"""


class WeChatOnboard:
    """WeChat iLink Bot QR-Code Login

    Full flow: fetch_qrcode -> (user scans) -> poll_status -> obtain token
    """

    def __init__(self, *, base_url: str = "", timeout: float = 30.0):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._poll_base_url = self._base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_qrcode(self) -> dict[str, Any]:
        """Step 1: Fetch login QR code

        Calls GET /ilink/bot/get_bot_qrcode?bot_type=3

        Returns:
            {
                "qrcode": "...",       # QR code identifier to pass back when polling
                "qrcode_url": "...",   # QR code display URL
            }
        """
        client = await self._get_client()
        url = f"{self._base_url}/ilink/bot/get_bot_qrcode"
        resp = await client.get(
            url,
            params={"bot_type": DEFAULT_ILINK_BOT_TYPE},
            headers=_onboard_common_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        qrcode = data.get("qrcode", "")
        qrcode_img = data.get("qrcode_img_content", "")

        if not qrcode or not qrcode_img:
            raise WeChatOnboardError(f"get_bot_qrcode returned incomplete data: {data}")

        return {
            "qrcode": qrcode,
            "qrcode_url": qrcode_img,
        }

    async def poll_status(self, qrcode: str) -> dict[str, Any]:
        """Step 2: Single poll of QR code scan status (long-poll)

        Calls GET /ilink/bot/get_qrcode_status?qrcode=...

        Returns:
            Waiting:     {"status": "wait"}
            Scanned:     {"status": "scaned"}
            Confirmed:   {"status": "confirmed", "token": "...", "base_url": "..."}
            Expired:     {"status": "expired"}
            Error:       {"status": "error", "message": "..."}
        """
        client = await self._get_client()
        url = f"{self._poll_base_url}/ilink/bot/get_qrcode_status"
        headers = _onboard_common_headers()
        try:
            resp = await client.get(
                url,
                params={"qrcode": qrcode},
                headers=headers,
                timeout=_QR_LONG_POLL_TIMEOUT_S + 5,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            return {"status": "wait"}
        except httpx.HTTPStatusError as exc:
            logger.warning("QR poll HTTP error %s, treating as wait", exc.response.status_code)
            return {"status": "wait"}
        except httpx.TransportError as exc:
            logger.warning("QR poll network error, treating as wait: %s", exc)
            return {"status": "wait"}

        status = data.get("status", "")

        if status == "wait":
            return {"status": "wait"}
        if status == "scaned":
            return {"status": "scaned"}
        if status == "scaned_but_redirect":
            redirect_host = data.get("redirect_host", "")
            if redirect_host:
                self._poll_base_url = f"https://{redirect_host}"
                logger.info("IDC redirect, switching poll host to %s", redirect_host)
            return {"status": "scaned"}
        if status == "confirmed":
            token = data.get("bot_token", "")
            bot_id = data.get("ilink_bot_id", "")
            if not token:
                return {"status": "error", "message": "Confirmed but bot_token was not returned"}
            if not bot_id:
                return {"status": "error", "message": "Confirmed but ilink_bot_id was not returned"}
            return {
                "status": "confirmed",
                "token": token,
                "base_url": data.get("baseurl", ""),
                "bot_id": bot_id,
                "user_id": data.get("ilink_user_id", ""),
            }
        if status == "expired":
            return {"status": "expired"}

        return {"status": "error", "message": f"Unknown status: {status}"}

    async def poll_until_done(
        self,
        qrcode: str,
        *,
        interval: float = 2.0,
        max_attempts: int = 150,
        on_qr_refresh: Any = None,
    ) -> dict[str, Any]:
        """Continuously poll until the user completes scanning or times out

        Args:
            on_qr_refresh: optional async callback(new_qrcode_info) called when QR is auto-refreshed

        Returns:
            On success: {"status": "confirmed", "token": "...", "base_url": "..."}

        Raises:
            WeChatOnboardError: On timeout or QR code expiration
        """
        current_qrcode = qrcode
        qr_refresh_count = 0

        for _ in range(max_attempts):
            result = await self.poll_status(current_qrcode)
            if result["status"] == "confirmed":
                return result
            if result["status"] == "expired":
                qr_refresh_count += 1
                if qr_refresh_count > MAX_QR_REFRESH_COUNT:
                    raise WeChatOnboardError(
                        f"QR code expired after {MAX_QR_REFRESH_COUNT} refreshes, please try again"
                    )
                logger.info(
                    "QR expired, auto-refreshing (%d/%d)", qr_refresh_count, MAX_QR_REFRESH_COUNT
                )
                self._poll_base_url = self._base_url
                new_qr = await self.fetch_qrcode()
                current_qrcode = new_qr["qrcode"]
                if on_qr_refresh:
                    try:
                        await on_qr_refresh(new_qr)
                    except Exception:
                        logger.debug("on_qr_refresh callback failed", exc_info=True)
                continue
            if result["status"] == "error":
                raise WeChatOnboardError(result.get("message", "Polling failed"))
            await asyncio.sleep(interval)

        raise WeChatOnboardError(f"Polling timed out: scan not completed after {max_attempts} attempts")


def render_qr_terminal(url: str) -> None:
    """Render a QR code in the terminal"""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        logger.info("qrcode package not installed, outputting URL directly")
        print(f"\nPlease scan the following URL with WeChat:\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR rendering failed: {e}")
        print(f"\nPlease scan the following URL with WeChat:\n  {url}\n")
