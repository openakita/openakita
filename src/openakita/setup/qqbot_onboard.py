"""
QQ 机器人 OpenClaw 扫码建应用 & 凭证校验

用于 Setup Center 和 CLI Wizard：
- 扫码登录 QQ 开发者后台（OpenClaw）
- 自动创建 QQ 机器人并获取 AppID / AppSecret
- 验证已有 App ID / App Secret 的有效性

OpenClaw 三步：
  1. create_session → 获取 session_id（生成 QR 码内容）
  2. poll            → 轮询扫码登录状态，成功返回 developer_id
  3. create_bot      → 调用 lite_create 创建机器人，返回 appid + client_secret

验证通过 QQ 官方 getAppAccessToken 接口。

所有 HTTP 调用均为 async（httpx），bridge.py 通过 asyncio.run() 驱动。
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
    """OpenClaw 过程中的业务错误"""


class QQBotOnboard:
    """QQ 机器人 OpenClaw 扫码建应用

    完整流程：create_session → (用户扫码) → poll → create_bot
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
        """Step 1: 创建登录会话，获取 session_id

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
            msg = inner.get("message", data.get("msg", "未知错误"))
            raise QQBotOnboardError(f"create_session 失败: {msg}")

        session_id = inner["session_id"]
        qr_url = f"https://q.qq.com/qqbot/openclaw/login.html?session_id={session_id}"
        return {"session_id": session_id, "qr_url": qr_url}

    async def poll(self, session_id: str) -> dict[str, Any]:
        """Step 2: 单次轮询登录状态

        Returns:
            等待: {"status": "waiting"}
            成功: {"status": "ok", "developer_id": "..."}
            失败: {"status": "error", "message": "..."}
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
            return {"status": "error", "message": "登录成功但未返回 developer_id"}

        return {"status": "error", "message": inner.get("message", "未知状态")}

    async def poll_until_done(
        self,
        session_id: str,
        *,
        interval: float = 2.0,
        max_attempts: int = 150,
    ) -> dict[str, Any]:
        """持续轮询直到用户完成扫码或超时

        Returns:
            成功: {"status": "ok", "developer_id": "..."}

        Raises:
            QQBotOnboardError: 轮询超时
        """
        for _ in range(max_attempts):
            result = await self.poll(session_id)
            if result["status"] == "ok":
                return result
            if result["status"] == "error":
                raise QQBotOnboardError(result.get("message", "登录失败"))
            await asyncio.sleep(interval)

        raise QQBotOnboardError(f"轮询超时: {max_attempts} 次尝试后仍未完成扫码")

    async def list_bots(self, developer_id: str) -> list[dict[str, Any]]:
        """查询已有机器人列表

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
        """查询剩余可创建数量

        Returns:
            剩余配额数（0 = 无法再创建）
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
        """Step 3: 创建机器人

        Returns:
            {
                "app_id": "...",
                "app_secret": "...",
                "bot_name": "...",
                "bot_uin": "..."
            }

        Raises:
            QQBotOnboardError: 创建失败（配额不足、需要 cookie 等）
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
            msg = data.get("msg", inner.get("message", "创建失败"))
            raise QQBotOnboardError(f"lite_create 失败 (retcode={retcode}): {msg}")

        appid = inner.get("appid", "")
        secret = inner.get("client_secret", "")
        if not appid or not secret:
            raise QQBotOnboardError(
                "lite_create 未返回凭证，可能需要浏览器 cookie 认证。"
                "请尝试在浏览器中打开 https://q.qq.com/qqbot/openclaw/ 手动创建。"
            )

        return {
            "app_id": appid,
            "app_secret": secret,
            "bot_name": inner.get("bot_name", ""),
            "bot_uin": str(inner.get("bot_uin", "")),
        }

    async def poll_and_create(self, session_id: str) -> dict[str, Any]:
        """原子操作：poll 确认登录态 + 创建机器人（同一 httpx 客户端保持 cookie）

        前端在检测到 poll 返回 ok 后调用此方法。此方法会再做一次 poll
        以在当前 httpx 客户端中获取登录态 cookie，然后立即调用 create_bot。

        如果 create_bot 失败（如配额不足），自动 fallback 到 list_bots
        获取最近创建的机器人信息。

        Returns:
            仍在等待: {"status": "waiting"}
            创建成功: {"status": "ok", "app_id": "...", "app_secret": "...", ...}
            已有机器人: {"status": "ok", "app_id": "...", "app_secret": "", ...}

        Raises:
            QQBotOnboardError: poll 失败或创建失败且无 fallback
        """
        poll_result = await self.poll(session_id)

        if poll_result["status"] == "waiting":
            return {"status": "waiting"}

        if poll_result["status"] == "error":
            raise QQBotOnboardError(poll_result.get("message", "登录失败"))

        developer_id = poll_result.get("developer_id", "")

        try:
            bot = await self.create_bot()
            bot["status"] = "ok"
            return bot
        except QQBotOnboardError as e:
            logger.warning(f"lite_create 失败，尝试 list_bots fallback: {e}")

        if not developer_id:
            raise QQBotOnboardError("创建失败且无法获取已有机器人列表（缺少 developer_id）")

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
            "创建机器人失败且未找到已有机器人。"
            "请前往 https://q.qq.com/qqbot/openclaw/ 手动操作。"
        )


async def validate_credentials(
    app_id: str,
    app_secret: str,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """验证 QQ 机器人 AppID / AppSecret 是否有效

    通过请求 getAppAccessToken 来验证。

    Returns:
        {"valid": True} 或
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
    """在终端渲染 QR 码（依赖 qrcode 包，不可用时 fallback 到打印 URL）"""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        logger.info("qrcode 包未安装，直接输出 URL")
        print(f"\n请用手机 QQ 扫描以下链接对应的二维码：\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR 渲染失败: {e}")
        print(f"\n请用手机 QQ 扫描以下链接对应的二维码：\n  {url}\n")
