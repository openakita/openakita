"""
微信 iLink Bot 扫码登录

用于 Setup Center 和 CLI Wizard：
- 获取登录二维码 (get_bot_qrcode)
- 轮询扫码状态 (get_qrcode_status)
- 扫码确认后返回 Bearer token + base_url

iLink Bot API 扫码流程（对齐 @tencent-weixin/openclaw-weixin v1.0.2）：
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


class WeChatOnboardError(Exception):
    """扫码登录过程中的业务错误"""


class WeChatOnboard:
    """微信 iLink Bot 扫码登录

    完整流程：fetch_qrcode → (用户扫码) → poll_status → 获取 token
    """

    def __init__(self, *, base_url: str = "", timeout: float = 30.0):
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
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
        resp = await client.get(url, params={"bot_type": DEFAULT_ILINK_BOT_TYPE})
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
        url = f"{self._base_url}/ilink/bot/get_qrcode_status"
        headers = {"iLink-App-ClientVersion": "1"}
        try:
            resp = await client.get(
                url,
                params={"qrcode": qrcode},
                headers=headers,
                timeout=_QR_LONG_POLL_TIMEOUT_S + 5,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ReadTimeout:
            return {"status": "wait"}

        status = data.get("status", "")

        if status == "wait":
            return {"status": "wait"}
        if status == "scaned":
            return {"status": "scaned"}
        if status == "confirmed":
            token = data.get("bot_token", "")
            if not token:
                return {"status": "error", "message": "确认成功但未返回 bot_token"}
            return {
                "status": "confirmed",
                "token": token,
                "base_url": data.get("baseurl", ""),
                "bot_id": data.get("ilink_bot_id", ""),
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
    ) -> dict[str, Any]:
        """持续轮询直到用户完成扫码或超时

        Returns:
            成功: {"status": "confirmed", "token": "...", "base_url": "..."}

        Raises:
            WeChatOnboardError: 超时或二维码过期
        """
        for _ in range(max_attempts):
            result = await self.poll_status(qrcode)
            if result["status"] == "confirmed":
                return result
            if result["status"] == "expired":
                raise WeChatOnboardError("二维码已过期，请重新获取")
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
