"""
QQ 开放平台 Bot API v2 — 原生 WebSocket 网关 + REST（不依赖 qq-botpy）。

协议与 botpy 一致：GET /gateway/bot、WSS Identify/Heartbeat/Resume，
Dispatch 的 d 与 Webhook 结构相同。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .qq_official import QQBotAdapter

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

_INTENTS_DEFAULT = (1 << 25) | (1 << 30)


class _ApiResult:
    __slots__ = ("id", "raw")

    def __init__(self, data: dict[str, Any]):
        self.raw = data
        self.id = str(data.get("id", "")) if isinstance(data, dict) else ""


class QQNativeRestApi:
    """httpx 版 QQ v2 REST，方法签名对齐本仓库对 botpy.BotAPI 的用法。"""

    def __init__(self, adapter: QQBotAdapter):
        self._a = adapter

    async def _headers(self) -> dict[str, str]:
        h = await self._a._build_api_headers()
        h["X-Union-Appid"] = self._a.app_id
        return h

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._a._api_base_url(),
            headers=await self._headers(),
            timeout=30.0,
        ) as client:
            resp = await client.post(path, json=body)
            resp.raise_for_status()
            if not resp.content:
                return {}
            return resp.json()

    async def _delete(self, path: str) -> None:
        async with httpx.AsyncClient(
            base_url=self._a._api_base_url(),
            headers=await self._headers(),
            timeout=30.0,
        ) as client:
            resp = await client.delete(path)
            resp.raise_for_status()

    @staticmethod
    def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if v is not None}

    async def post_group_message(self, group_openid: str, **kwargs: Any) -> _ApiResult:
        body = self._strip_none(dict(kwargs))
        data = await self._post_json(f"/v2/groups/{group_openid}/messages", body)
        return _ApiResult(data)

    async def post_c2c_message(self, openid: str, **kwargs: Any) -> _ApiResult:
        body = self._strip_none(dict(kwargs))
        data = await self._post_json(f"/v2/users/{openid}/messages", body)
        return _ApiResult(data)

    async def post_group_file(
        self,
        group_openid: str,
        file_type: int,
        url: str,
        srv_send_msg: bool = False,
    ) -> Any:
        body = self._strip_none({"file_type": file_type, "url": url, "srv_send_msg": srv_send_msg})
        return await self._post_json(f"/v2/groups/{group_openid}/files", body)

    async def post_c2c_file(
        self,
        openid: str,
        file_type: int,
        url: str,
        srv_send_msg: bool = False,
    ) -> Any:
        body = self._strip_none({"file_type": file_type, "url": url, "srv_send_msg": srv_send_msg})
        return await self._post_json(f"/v2/users/{openid}/files", body)

    async def recall_group_message(self, group_openid: str, message_id: str) -> str:
        await self._delete(f"/v2/groups/{group_openid}/messages/{message_id}")
        return ""

    async def recall_message(self, channel_id: str, message_id: str, hidetip: bool = False) -> str:
        q = f"?hidetip={str(hidetip).lower()}"
        await self._delete(f"/channels/{channel_id}/messages/{message_id}{q}")
        return ""

    async def post_message(self, channel_id: str, **kwargs: Any) -> _ApiResult:
        file_image = kwargs.pop("file_image", None)
        payload = self._strip_none(dict(kwargs))
        hdr = await self._headers()
        base = self._a._api_base_url()
        url = f"{base}/channels/{channel_id}/messages"

        if file_image is not None and isinstance(file_image, bytes):
            files: list[tuple[str, tuple[str, Any, str | None]]] = []
            for k, v in payload.items():
                if isinstance(v, dict):
                    files.append(
                        (
                            k,
                            (
                                k,
                                json.dumps(v, ensure_ascii=False),
                                "application/json; charset=utf-8",
                            ),
                        )
                    )
                else:
                    files.append((k, (k, str(v), None)))
            files.append(("file_image", ("file_image", file_image, "application/octet-stream")))
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=hdr, files=files)
                resp.raise_for_status()
                data = resp.json() if resp.content else {}
            return _ApiResult(data)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=hdr, json=payload)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        return _ApiResult(data)


async def _fetch_gateway_bot(adapter: QQBotAdapter) -> dict[str, Any]:
    headers = await adapter._build_api_headers()
    headers["X-Union-Appid"] = adapter.app_id
    async with httpx.AsyncClient(
        base_url=adapter._api_base_url(), headers=headers, timeout=15.0
    ) as client:
        resp = await client.get("/gateway/bot")
        resp.raise_for_status()
        return resp.json()


async def _dispatch_event(adapter: QQBotAdapter, event_t: str, d: dict[str, Any]) -> None:
    try:
        if event_t == "GROUP_AT_MESSAGE_CREATE":
            unified = adapter._convert_webhook_group_message(d)
            if adapter._is_duplicate(unified.channel_message_id):
                return
            adapter._log_message(unified)
            await adapter._emit_message(unified)
            await adapter._flush_pending_messages(unified.chat_id)
        elif event_t == "C2C_MESSAGE_CREATE":
            unified = adapter._convert_webhook_c2c_message(d)
            if adapter._is_duplicate(unified.channel_message_id):
                return
            adapter._log_message(unified)
            await adapter._emit_message(unified)
        elif event_t == "AT_MESSAGE_CREATE":
            unified = adapter._convert_webhook_channel_message(d)
            if adapter._is_duplicate(unified.channel_message_id):
                return
            adapter._log_message(unified)
            await adapter._emit_message(unified)
        else:
            logger.debug("QQ native WS: unhandled event %s", event_t)
    except Exception as e:
        logger.error("QQ native WS: error handling %s: %s", event_t, e)


async def _heartbeat_sender(ws: Any, interval_s: float, seq_holder: list[int]) -> None:
    import websockets

    while True:
        try:
            await asyncio.sleep(interval_s)
            if getattr(ws, "closed", False):
                return
            await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": seq_holder[0]}))
        except websockets.ConnectionClosed:
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("QQ native WS heartbeat end: %s", e)
            return


class NativeQQBotClient:
    """替代 botpy.Client：.api、.robot、async with、.start() 阻塞至连接结束。"""

    def __init__(self, adapter: QQBotAdapter):
        self._adapter = adapter
        self.api = QQNativeRestApi(adapter)
        self.robot = SimpleNamespace(name="QQBot")
        self._heartbeat_task: asyncio.Task | None = None
        self._closed = False

    async def __aenter__(self) -> NativeQQBotClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

    async def start(self, appid: str, secret: str) -> None:
        import websockets

        _ = appid, secret
        gw = await _fetch_gateway_bot(self._adapter)
        ws_url = (gw.get("url") or "").strip()
        if not ws_url:
            raise RuntimeError("QQ gateway/bot 未返回 url")
        shards = max(int(gw.get("shards", 1) or 1), 1)

        session_id = ""
        seq_holder = [0]
        token_str = f"QQBot {await self._adapter._get_access_token()}"
        extra_headers = {"X-Union-Appid": self._adapter.app_id}

        async with websockets.connect(
            ws_url,
            additional_headers=extra_headers,
            open_timeout=30,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            raw0 = await asyncio.wait_for(ws.recv(), timeout=60.0)
            hello = json.loads(raw0)
            if hello.get("op") != OP_HELLO:
                raise RuntimeError(f"QQ WS: expected Hello, got op={hello.get('op')}")
            hb_ms = float((hello.get("d") or {}).get("heartbeat_interval", 30000))
            heartbeat_interval_s = max(hb_ms / 1000.0, 5.0)

            if session_id and seq_holder[0] > 0:
                await ws.send(
                    json.dumps(
                        {
                            "op": OP_RESUME,
                            "d": {
                                "token": token_str,
                                "session_id": session_id,
                                "seq": seq_holder[0],
                            },
                        }
                    )
                )
            else:
                await ws.send(
                    json.dumps(
                        {
                            "op": OP_IDENTIFY,
                            "d": {
                                "token": token_str,
                                "intents": _INTENTS_DEFAULT,
                                "shard": [0, shards],
                            },
                        }
                    )
                )

            while self._adapter._running and not self._closed:
                try:
                    raw = await ws.recv()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.info("QQ native WS recv ended: %s", e)
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                op = msg.get("op")
                if op == OP_HEARTBEAT_ACK:
                    continue
                if op == OP_RECONNECT:
                    logger.info("QQ native WS: Reconnect")
                    break
                if op == OP_INVALID_SESSION:
                    logger.warning("QQ native WS: Invalid Session")
                    session_id = ""
                    seq_holder[0] = 0
                    break

                if op != OP_DISPATCH:
                    continue

                t = msg.get("t") or ""
                d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
                s = msg.get("s")
                if isinstance(s, int) and s > 0:
                    seq_holder[0] = s

                if t == "READY":
                    session_id = str(d.get("session_id") or "").strip()
                    user = d.get("user") or {}
                    self.robot.name = user.get("username") or "QQBot"
                    self._adapter._retry_delay = 5
                    logger.info("QQ native WS ready (user: %s)", self.robot.name)
                    if self._heartbeat_task:
                        self._heartbeat_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await self._heartbeat_task
                    self._heartbeat_task = asyncio.create_task(
                        _heartbeat_sender(ws, heartbeat_interval_s, seq_holder)
                    )
                elif t == "RESUMED":
                    logger.info("QQ native WS resumed")
                else:
                    asyncio.create_task(_dispatch_event(self._adapter, t, d))

            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None
