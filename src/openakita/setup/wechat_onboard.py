"""
微信 iLink Bot 扫码登录

用于 Setup Center 和 CLI Wizard：
- 获取登录二维码 (get_bot_qrcode)
- 轮询扫码状态 (get_qrcode_status)
- 扫码确认后返回 Bearer token + base_url

iLink Bot API 扫码流程（对齐 @tencent-weixin/openclaw-weixin v2.1.6）：
  1. GET get_bot_qrcode?bot_type=3 → 获取 qrcode / qrcode_img_content
  2. GET get_qrcode_status?qrcode=... → 轮询状态 (wait → scaned → confirmed)
  3. confirmed 时返回 bot_token / ilink_bot_id / baseurl

所有 HTTP 调用均为 async（httpx），bridge.py 通过 asyncio.run() 驱动。
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
    """扫码登录过程中的业务错误"""


class WeChatOnboard:
    """微信 iLink Bot 扫码登录

    完整流程：fetch_qrcode → (用户扫码) → poll_status → 获取 token
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
        """Step 1: 获取登录二维码

        Calls GET /ilink/bot/get_bot_qrcode?bot_type=3

        Returns:
            {
                "qrcode": "...",       # 轮询时回传的 qrcode 标识
                "qrcode_url": "...",   # 二维码显示 URL
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
            raise WeChatOnboardError(
                f"get_bot_qrcode 返回数据不完整: {data}"
            )

        return {
            "qrcode": qrcode,
            "qrcode_url": qrcode_img,
        }

    async def poll_status(self, qrcode: str) -> dict[str, Any]:
        """Step 2: 单次轮询扫码状态 (long-poll)

        Calls GET /ilink/bot/get_qrcode_status?qrcode=...

        Returns:
            等待:   {"status": "wait"}
            已扫码: {"status": "scaned"}
            已确认: {"status": "confirmed", "token": "...", "base_url": "..."}
            已过期: {"status": "expired"}
            错误:   {"status": "error", "message": "..."}
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
            logger.warning(
                "QR poll HTTP error %s, treating as wait", exc.response.status_code
            )
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
                return {"status": "error", "message": "确认成功但未返回 bot_token"}
            if not bot_id:
                return {"status": "error", "message": "确认成功但未返回 ilink_bot_id"}
            return {
                "status": "confirmed",
                "token": token,
                "base_url": data.get("baseurl", ""),
                "bot_id": bot_id,
                "user_id": data.get("ilink_user_id", ""),
            }
        if status == "expired":
            return {"status": "expired"}

        return {"status": "error", "message": f"未知状态: {status}"}

    async def poll_until_done(
        self,
        qrcode: str,
        *,
        interval: float = 2.0,
        max_attempts: int = 150,
        on_qr_refresh: Any = None,
    ) -> dict[str, Any]:
        """持续轮询直到用户完成扫码或超时

        Args:
            on_qr_refresh: optional async callback(new_qrcode_info) called when QR is auto-refreshed

        Returns:
            成功: {"status": "confirmed", "token": "...", "base_url": "..."}

        Raises:
            WeChatOnboardError: 超时或二维码过期
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
                        f"二维码已过期且已刷新 {MAX_QR_REFRESH_COUNT} 次，请重试"
                    )
                logger.info("QR expired, auto-refreshing (%d/%d)", qr_refresh_count, MAX_QR_REFRESH_COUNT)
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
                raise WeChatOnboardError(result.get("message", "轮询失败"))
            await asyncio.sleep(interval)

        raise WeChatOnboardError(f"轮询超时: {max_attempts} 次尝试后仍未完成扫码")


def render_qr_terminal(url: str) -> None:
    """在终端渲染 QR 码"""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        logger.info("qrcode 包未安装，直接输出 URL")
        print(f"\n请用微信扫描以下链接对应的二维码：\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR 渲染失败: {e}")
        print(f"\n请用微信扫描以下链接对应的二维码：\n  {url}\n")
