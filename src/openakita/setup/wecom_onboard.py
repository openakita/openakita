"""
企业微信智能机器人扫码配置

用于 Setup Center QR 扫码快速获取 bot_id + secret：
- 调用企微 /ai/qc/generate 生成二维码（返回 auth_url + scode）
- 轮询 /ai/qc/query_result 获取扫码结果（返回 botid + secret）

接口来自企业微信智能机器人管理后台，对齐 @wecom/wecom-openclaw-cli 实现。

所有 HTTP 调用均为 async（httpx），bridge.py 通过 asyncio.run() 驱动。
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
    """扫码配置过程中的业务错误"""


def _get_plat_code() -> int:
    import sys
    return _PLAT_CODES.get(sys.platform, 3)


class WecomOnboard:
    """企业微信智能机器人扫码配置

    Flow:
    1. generate() -> auth_url (QR 扫码链接) + scode
    2. poll(scode) -> 成功时返回 bot_id + secret
    """

    def __init__(self, *, timeout: float = 30.0):
        self._timeout = timeout

    async def generate(self) -> dict[str, Any]:
        """Step 1: 生成二维码

        Returns:
            dict with:
                auth_url: str  — 二维码扫码链接
                scode: str     — 用于后续轮询的标识
        """
        params = {"source": "openakita", "plat": str(_get_plat_code())}
        data = await self._get(QC_GENERATE_PATH, params=params)
        resp_data = data.get("data", data)
        scode = resp_data.get("scode", "")
        auth_url = resp_data.get("auth_url", "")
        if not scode:
            raise WecomOnboardError(f"generate 未返回有效 scode: {data}")
        return {"auth_url": auth_url, "scode": scode}

    async def poll(self, scode: str) -> dict[str, Any]:
        """Step 2: 查询扫码结果

        Returns:
            成功: {bot_id: str, secret: str, status: "success"}
            等待: {status: "pending"}
            过期: {status: "expired"}
            失败: {status: "error", error: "..."}
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
        """持续轮询直到用户扫码完成或超时

        Returns:
            成功时的完整响应 (含 bot_id / secret)

        Raises:
            WecomOnboardError: 轮询超时或二维码过期
        """
        for _ in range(max_attempts):
            result = await self.poll(scode)

            if result.get("bot_id") and result.get("secret"):
                return result

            status = result.get("status", "")
            if status in ("expired", "error"):
                raise WecomOnboardError(
                    f"扫码终止: {status} - {result.get('error', '')}"
                )

            await asyncio.sleep(interval)

        raise WecomOnboardError(
            f"轮询超时: {max_attempts} 次尝试后仍未完成扫码"
        )

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        """发送 GET 请求到企微 QR 配置端点"""
        url = WECOM_QC_BASE + path
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
