"""
QQ Bot OpenClaw QR-code onboarding & credential validation

Used by Setup Center and CLI Wizard:
- Scan QR code to log in to QQ developer console (OpenClaw)
- Automatically create a QQ bot and obtain AppID / AppSecret
- Validate existing App ID / App Secret

OpenClaw three-step flow:
  1. create_session -> obtain session_id (used to generate QR code content)
  2. poll           -> poll scan-login status; returns developer_id on success
  3. create_bot     -> call lite_create to create bot; returns appid + client_secret

Validation via the official QQ getAppAccessToken endpoint.

All HTTP calls are async (httpx); bridge.py drives them via asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_Q = "https://q.qq.com"
BASE_BOT = "https://bot.q.qq.com"
BKN = "5381"
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

_COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Referer": "https://q.qq.com/qqbot/openclaw/login.html",
}


class QQBotOnboardError(Exception):
    """Business error during the OpenClaw flow"""


class QQBotOnboard:
    """QQ Bot OpenClaw QR-code onboarding

    Full flow: create_session -> (user scans QR) -> poll -> create_bot
    """

    def __init__(self, *, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=_COMMON_HEADERS,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def create_session(self) -> dict[str, Any]:
        """Step 1: Create a login session and obtain session_id.

        Returns:
            {
                "session_id": "...",
                "qr_url": "https://q.qq.com/qqbot/openclaw/login.html?session_id=..."
            }
        """
        client = await self._get_client()
        resp = await client.get(
            f"{BASE_Q}/lite/create_session",
            params={"bkn": BKN},
        )
        resp.raise_for_status()
        data = resp.json()

        retcode = data.get("retcode", -1)
        inner = data.get("data", {})
        if retcode != 0 or inner.get("code", -1) != 0:
            msg = inner.get("message", data.get("msg", "unknown error"))
            raise QQBotOnboardError(f"create_session failed: {msg}")

        session_id = inner["session_id"]
        qr_url = f"https://q.qq.com/qqbot/openclaw/login.html?session_id={session_id}"
        return {"session_id": session_id, "qr_url": qr_url}

    async def poll(self, session_id: str) -> dict[str, Any]:
        """Step 2: Single poll of login status.

        Returns:
            Waiting: {"status": "waiting"}
            Success: {"status": "ok", "developer_id": "..."}
            Failure: {"status": "error", "message": "..."}
        """
        client = await self._get_client()
        resp = await client.get(
            f"{BASE_Q}/lite/poll",
            params={"session_id": session_id, "bkn": BKN},
        )
        resp.raise_for_status()
        data = resp.json()

        inner = data.get("data", {})
        code = inner.get("code", -1)

        if code == 1:
            return {"status": "waiting"}

        if code == 0:
            developer_id = inner.get("developer_id", "")
            if developer_id:
                return {"status": "ok", "developer_id": developer_id}
            return {"status": "error", "message": "Login succeeded but developer_id was not returned"}

        return {"status": "error", "message": inner.get("message", "unknown status")}

    async def poll_until_done(
        self,
        session_id: str,
        *,
        interval: float = 2.0,
        max_attempts: int = 150,
    ) -> dict[str, Any]:
        """Poll continuously until the user completes the QR scan or times out.

        Returns:
            Success: {"status": "ok", "developer_id": "..."}

        Raises:
            QQBotOnboardError: Polling timed out
        """
        for _ in range(max_attempts):
            result = await self.poll(session_id)
            if result["status"] == "ok":
                return result
            if result["status"] == "error":
                raise QQBotOnboardError(result.get("message", "Login failed"))
            await asyncio.sleep(interval)

        raise QQBotOnboardError(f"Polling timed out: scan not completed after {max_attempts} attempts")

    async def list_bots(self, developer_id: str) -> list[dict[str, Any]]:
        """Query the list of existing bots.

        Returns:
            [{"app_id": "...", "app_name": "...", "bot_uin": "...", "is_lite_bot": 1}, ...]
        """
        client = await self._get_client()
        resp = await client.post(
            f"{BASE_Q}/lite/list_bots",
            params={"bkn": BKN},
            json={"developer_id": developer_id},
        )
        resp.raise_for_status()
        data = resp.json()

        apps = data.get("data", {}).get("data", {}).get("apps", [])
        return apps

    async def check_remain(self) -> int:
        """Query remaining creation quota.

        Returns:
            Remaining quota (0 = no more bots can be created)
        """
        client = await self._get_client()
        resp = await client.get(
            f"{BASE_BOT}/cgi-bin/create/lite_remain",
            params={"bkn": BKN},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("create_remain", 0)

    async def create_bot(self) -> dict[str, Any]:
        """Step 3: Create a bot.

        Returns:
            {
                "app_id": "...",
                "app_secret": "...",
                "bot_name": "...",
                "bot_uin": "..."
            }

        Raises:
            QQBotOnboardError: Creation failed (quota exhausted, cookie required, etc.)
        """
        client = await self._get_client()
        resp = await client.post(
            f"{BASE_BOT}/cgi-bin/lite_create",
            params={"bkn": BKN},
            json={
                "apply_source": 1,
                "idempotency_key": str(int(time.time() * 1000)),
            },
        )
        resp.raise_for_status()
        data = resp.json()

        retcode = data.get("retcode", -1)
        inner = data.get("data", {})

        if retcode != 0:
            msg = data.get("msg", inner.get("message", "creation failed"))
            raise QQBotOnboardError(f"lite_create failed (retcode={retcode}): {msg}")

        appid = inner.get("appid", "")
        secret = inner.get("client_secret", "")
        if not appid or not secret:
            raise QQBotOnboardError(
                "lite_create did not return credentials; browser cookie authentication may be required. "
                "Try opening https://q.qq.com/qqbot/openclaw/ in a browser to create manually."
            )

        return {
            "app_id": appid,
            "app_secret": secret,
            "bot_name": inner.get("bot_name", ""),
            "bot_uin": str(inner.get("bot_uin", "")),
        }

    async def poll_and_create(self, session_id: str) -> dict[str, Any]:
        """Atomic operation: poll to confirm login state, then create a bot (same httpx client keeps cookie).

        The frontend calls this after detecting that poll returns ok. This method
        performs one more poll to obtain the login-state cookie on the current httpx
        client, then immediately calls create_bot.

        If create_bot fails (e.g. quota exhausted), automatically falls back to
        list_bots to retrieve the most recently created bot's info.

        Returns:
            Still waiting: {"status": "waiting"}
            Created successfully: {"status": "ok", "app_id": "...", "app_secret": "...", ...}
            Existing bot: {"status": "ok", "app_id": "...", "app_secret": "", ...}

        Raises:
            QQBotOnboardError: poll failed, or creation failed with no fallback
        """
        poll_result = await self.poll(session_id)

        if poll_result["status"] == "waiting":
            return {"status": "waiting"}

        if poll_result["status"] == "error":
            raise QQBotOnboardError(poll_result.get("message", "Login failed"))

        developer_id = poll_result.get("developer_id", "")

        try:
            bot = await self.create_bot()
            bot["status"] = "ok"
            return bot
        except QQBotOnboardError as e:
            logger.warning(f"lite_create failed, trying list_bots fallback: {e}")

        if not developer_id:
            raise QQBotOnboardError("Creation failed and cannot list existing bots (missing developer_id)")

        apps = await self.list_bots(developer_id)
        lite_bots = [a for a in apps if a.get("is_lite_bot")]
        if not lite_bots:
            lite_bots = apps

        if lite_bots:
            newest = lite_bots[-1]
            return {
                "status": "ok",
                "app_id": str(newest.get("app_id", "")),
                "app_secret": "",
                "bot_name": newest.get("app_name", ""),
                "bot_uin": str(newest.get("bot_uin", "")),
                "needs_secret": True,
            }

        raise QQBotOnboardError(
            "Bot creation failed and no existing bots found. "
            "Please go to https://q.qq.com/qqbot/openclaw/ to create one manually."
        )


async def validate_credentials(
    app_id: str,
    app_secret: str,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Validate QQ Bot AppID / AppSecret.

    Validates by requesting getAppAccessToken.

    Returns:
        {"valid": True} or
        {"valid": False, "error": "..."}
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                QQ_TOKEN_URL,
                json={"appId": app_id, "clientSecret": app_secret},
            )
            data = resp.json()

            if resp.status_code == 200 and data.get("access_token"):
                return {"valid": True}

            error_msg = data.get("message", data.get("msg", f"HTTP {resp.status_code}"))
            return {"valid": False, "error": error_msg}
        except httpx.HTTPStatusError as e:
            return {"valid": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}


def render_qr_terminal(url: str) -> None:
    """Render a QR code in the terminal (requires the qrcode package; falls back to printing the URL)"""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        logger.info("qrcode package not installed, printing URL directly")
        print(f"\nPlease scan the following QR code URL with QQ on your phone:\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR rendering failed: {e}")
        print(f"\nPlease scan the following QR code URL with QQ on your phone:\n  {url}\n")
