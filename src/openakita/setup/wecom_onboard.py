"""
WeCom smart bot QR-code onboarding.

Used by Setup Center to quickly obtain bot_id + secret via QR scan:
- Calls WeCom /ai/qc/generate to generate a QR code (returns auth_url + scode)
- Polls /ai/qc/query_result to get the scan result (returns botid + secret)

APIs are from the WeCom smart bot management console, aligned with the
@wecom/wecom-openclaw-cli implementation.

All HTTP calls are async (httpx); bridge.py drives them via asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WECOM_QC_BASE = "https://work.weixin.qq.com"
QC_GENERATE_PATH = "/ai/qc/generate"
QC_QUERY_RESULT_PATH = "/ai/qc/query_result"

# plat codes used by OpenClaw CLI: 0=macOS, 1=Windows, 2=Linux, 3=Other
_PLAT_CODES = {"darwin": 0, "win32": 1, "linux": 2}


class WecomOnboardError(Exception):
    """Business error during QR-code onboarding."""


def _get_plat_code() -> int:
    import sys

    return _PLAT_CODES.get(sys.platform, 3)


class WecomOnboard:
    """WeCom smart bot QR-code onboarding.

    Flow:
    1. generate() -> auth_url (QR scan link) + scode
    2. poll(scode) -> returns bot_id + secret on success
    """

    def __init__(self, *, timeout: float = 30.0):
        self._timeout = timeout

    async def generate(self) -> dict[str, Any]:
        """Step 1: Generate a QR code.

        Returns:
            dict with:
                auth_url: str  — QR code scan link
                scode: str     — identifier used for subsequent polling
        """
        params = {"source": "openakita", "plat": str(_get_plat_code())}
        data = await self._get(QC_GENERATE_PATH, params=params)
        resp_data = data.get("data", data)
        scode = resp_data.get("scode", "")
        auth_url = resp_data.get("auth_url", "")
        if not scode:
            raise WecomOnboardError(f"generate did not return a valid scode: {data}")
        return {"auth_url": auth_url, "scode": scode}

    async def poll(self, scode: str) -> dict[str, Any]:
        """Step 2: Query the scan result.

        Returns:
            success: {bot_id: str, secret: str, status: "success"}
            pending: {status: "pending"}
            expired: {status: "expired"}
            failure: {status: "error", error: "..."}
        """
        data = await self._get(QC_QUERY_RESULT_PATH, params={"scode": scode})
        resp_data = data.get("data", data)

        status = resp_data.get("status", "")
        bot_info = resp_data.get("bot_info", {})
        bot_id = bot_info.get("botid", "")
        secret = bot_info.get("secret", "")

        if bot_id and secret:
            return {"bot_id": bot_id, "secret": secret, "status": "success"}

        if status in ("expired", "error"):
            return {"status": status, "error": resp_data.get("errmsg", "")}

        if resp_data.get("errcode") or data.get("errcode"):
            return {"status": "error", "error": resp_data.get("errmsg", str(data))}

        return {"status": "pending"}

    async def poll_until_done(
        self,
        scode: str,
        *,
        interval: float = 3.0,
        max_attempts: int = 100,
    ) -> dict[str, Any]:
        """Poll continuously until the user completes the scan or times out.

        Returns:
            Full response on success (including bot_id / secret).

        Raises:
            WecomOnboardError: polling timed out or QR code expired.
        """
        for _ in range(max_attempts):
            result = await self.poll(scode)

            if result.get("bot_id") and result.get("secret"):
                return result

            status = result.get("status", "")
            if status in ("expired", "error"):
                raise WecomOnboardError(f"Scan terminated: {status} - {result.get('error', '')}")

            await asyncio.sleep(interval)

        raise WecomOnboardError(f"Polling timed out: scan not completed after {max_attempts} attempts")

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Send a GET request to the WeCom QR onboarding endpoint."""
        url = WECOM_QC_BASE + path
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
