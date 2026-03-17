"""
飞书 Device Flow 扫码建应用 & 凭证校验

用于 Setup Center 和 CLI Wizard：
- 扫码创建飞书机器人应用（Device Flow）
- 验证已有 App ID / App Secret 的有效性
- 终端 QR 码渲染

Device Flow 三步（逆向自 @larksuite/openclaw-lark-tools）：
  1. init   → 获取 supported_auth_methods（握手）
  2. begin  → 提交 archetype/auth_method → 返回 device_code + verification_uri
  3. poll   → 定期查询授权状态，成功后返回 client_id + client_secret

注意：端点在 accounts.feishu.cn（非 open.feishu.cn）。

所有 HTTP 调用均为 async（httpx），bridge.py 通过 asyncio.run() 驱动。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    import aiohttp as _aiohttp
except ImportError:
    _aiohttp = None  # type: ignore[assignment]

if _httpx is None and _aiohttp is None:
    raise ImportError(
        "feishu_onboard 需要 httpx 或 aiohttp 其中之一，请 pip install httpx 或 pip install aiohttp"
    )

FEISHU_ACCOUNTS_BASE = "https://accounts.feishu.cn"
LARK_ACCOUNTS_BASE = "https://accounts.larksuite.com"

_DEVICE_FLOW_PATH = "/oauth/v1/app/registration"

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
LARK_TOKEN_URL = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"


def _aiohttp_timeout(total: float) -> Any:
    """Build an aiohttp timeout object, or None if unavailable."""
    if _aiohttp is None:
        return None
    cls = getattr(_aiohttp, "ClientTimeout", None)
    if cls is not None:
        return cls(total=total)
    return None


async def _async_post_form(
    url: str, data: dict[str, str], *, timeout: float = 30.0
) -> dict[str, Any]:
    """POST form-urlencoded, returns parsed JSON. Works with httpx or aiohttp."""
    if _httpx is not None:
        async with _httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()

    import aiohttp

    kwargs: dict[str, Any] = {}
    tout = _aiohttp_timeout(timeout)
    if tout is not None:
        kwargs["timeout"] = tout
    async with aiohttp.ClientSession(**kwargs) as session:
        async with session.post(url, data=data) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


async def _async_post_json(
    url: str, json_body: dict[str, Any], *, timeout: float = 30.0
) -> dict[str, Any]:
    """POST JSON body, returns parsed JSON. Works with httpx or aiohttp."""
    if _httpx is not None:
        async with _httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=json_body)
            resp.raise_for_status()
            return resp.json()

    import aiohttp

    kwargs: dict[str, Any] = {}
    tout = _aiohttp_timeout(timeout)
    if tout is not None:
        kwargs["timeout"] = tout
    async with aiohttp.ClientSession(**kwargs) as session:
        async with session.post(url, json=json_body) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


class FeishuOnboardError(Exception):
    """Device Flow 过程中的业务错误"""


class FeishuOnboard:
    """飞书 Device Flow 扫码建应用

    Attributes:
        domain: "feishu" 或 "lark"，决定 API 基址
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
        """Step 1: 握手，获取支持的 auth 方法

        Returns:
            dict with supported_auth_methods etc.
        """
        return await self._post(action="init")

    async def begin(self) -> dict[str, Any]:
        """Step 2: 启动 Device Flow，获取 device_code 和二维码 URL

        Returns:
            dict with at least:
                device_code: str
                verification_uri_complete: str  — 用户扫码的 URL
                interval: int  — 推荐轮询间隔(秒)
                expire_in: int  — device_code 有效时长(秒)
        """
        data = await self._post(
            action="begin",
            archetype="PersonalAgent",
            auth_method="client_secret",
            request_user_info="open_id",
        )
        if "device_code" not in data:
            raise FeishuOnboardError(f"begin 未返回 device_code: {data}")
        return data

    async def poll(self, device_code: str) -> dict[str, Any]:
        """Step 3: 查询授权结果

        Returns:
            成功: {client_id, client_secret, user_info: {open_id, tenant_brand}}
            等待: {error: "authorization_pending"}
            过期: {error: "expired_token"}
            拒绝: {error: "access_denied"}
            限速: {error: "slow_down"}

        为保持向后兼容，成功时额外映射 app_id/app_secret 字段。
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
        """持续轮询直到用户完成扫码或超时

        Returns:
            授权成功时的完整响应 (含 app_id / app_secret)

        Raises:
            FeishuOnboardError: 轮询超时或服务端拒绝
        """
        for i in range(max_attempts):
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
                raise FeishuOnboardError(f"Device flow 终止: {error}")

            await asyncio.sleep(interval)

        raise FeishuOnboardError(
            f"轮询超时: {max_attempts} 次尝试后仍未完成授权"
        )

    async def _post(self, **form_fields: str) -> dict[str, Any]:
        """发送 x-www-form-urlencoded POST 请求到 Device Flow 端点"""
        return await _async_post_form(
            self._endpoint, form_fields, timeout=self._timeout
        )


async def validate_credentials(
    app_id: str,
    app_secret: str,
    *,
    domain: str = "feishu",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """验证飞书 App ID / App Secret 是否有效

    通过请求 tenant_access_token 来验证。

    Returns:
        {"valid": True, "tenant_access_token": "t-xxx"} 或
        {"valid": False, "error": "..."}
    """
    url = LARK_TOKEN_URL if domain == "lark" else FEISHU_TOKEN_URL
    try:
        data = await _async_post_json(
            url, {"app_id": app_id, "app_secret": app_secret}, timeout=timeout
        )
        if data.get("code", -1) == 0:
            return {
                "valid": True,
                "tenant_access_token": data.get("tenant_access_token", ""),
            }
        return {"valid": False, "error": data.get("msg", "未知错误")}
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
        print(f"\n请在浏览器或飞书中打开以下链接：\n  {url}\n")
    except Exception as e:
        logger.warning(f"QR 渲染失败: {e}")
        print(f"\n请在浏览器或飞书中打开以下链接：\n  {url}\n")
