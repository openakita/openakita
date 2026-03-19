"""
OneBot 适配器

基于 OneBot v11 协议实现，可对接任何兼容 OneBot 的实现:
- NapCat, Lagrange, go-cqhttp 等
- WebSocket 正向连接 (forward): OpenAkita 作为客户端连接 OneBot 实现的 WS 服务器
- WebSocket 反向连接 (reverse): OpenAkita 作为 WS 服务端，OneBot 实现主动连入（推荐）
- 文本/图片/语音/文件收发
"""

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)

websockets = None


def _import_websockets():
    global websockets
    if websockets is None:
        try:
            import websockets as ws

            websockets = ws
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("websockets"))


def _decode_cq_entities(s: str) -> str:
    """解码 CQ 码中的 HTML 实体 (OneBot v11 规范)"""
    return s.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


@dataclass
class OneBotConfig:
    """OneBot 配置"""

    mode: str = "reverse"
    ws_url: str = "ws://127.0.0.1:8080"
    reverse_host: str = "0.0.0.0"
    reverse_port: int = 6700
    access_token: str | None = None


class OneBotAdapter(ChannelAdapter):
    """
    OneBot 适配器 (OneBot v11 协议)

    支持两种连接模式:
    - reverse (默认): OpenAkita 作为 WS 服务端，NapCat/Lagrange 配置 Websocket 客户端连入
    - forward: OpenAkita 主动连接 NapCat/Lagrange 的 Websocket 服务器
    """

    channel_name = "onebot"

    capabilities = {
        "streaming": False,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": True,
        "edit_message": False,
        "get_chat_info": False,
        "get_user_info": True,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": False,
    }

    def __init__(
        self,
        ws_url: str = "ws://127.0.0.1:8080",
        access_token: str | None = None,
        media_dir: Path | None = None,
        *,
        mode: str = "reverse",
        reverse_host: str = "0.0.0.0",
        reverse_port: int = 6700,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
    ):
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)

        self.config = OneBotConfig(
            mode=mode,
            ws_url=ws_url,
            reverse_host=reverse_host,
            reverse_port=reverse_port,
            access_token=access_token,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/onebot")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._ws: Any | None = None
        self._api_callbacks: dict[str, asyncio.Future] = {}
        self._receive_task: asyncio.Task | None = None
        self._server: Any | None = None  # reverse 模式的 websockets Server
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._SEEN_CAPACITY = 500

        # chat_id → chat_type 映射（send_typing 需要区分群/私聊）
        self._chat_type_map: OrderedDict[str, str] = OrderedDict()
        self._CHAT_TYPE_MAP_CAPACITY = 2000

        # group_id → group_name 缓存
        self._group_name_cache: dict[str, str] = {}

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        _import_websockets()
        self._running = True

        if self.config.mode == "reverse":
            self._receive_task = asyncio.create_task(self._run_reverse_server())
            logger.info(
                f"OneBot adapter starting in reverse mode, "
                f"listening on {self.config.reverse_host}:{self.config.reverse_port}"
            )
        else:
            self._receive_task = asyncio.create_task(self._receive_loop_with_reconnect())
            logger.info(f"OneBot adapter starting in forward mode, will connect to {self.config.ws_url}")

    async def stop(self) -> None:
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self._reject_pending_callbacks("adapter stopped")
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

        logger.info("OneBot adapter stopped")

    # ==================== 反向 WebSocket (reverse 模式) ====================

    async def _run_reverse_server(self) -> None:
        try:
            self._server = await websockets.serve(
                self._reverse_ws_handler,
                self.config.reverse_host,
                self.config.reverse_port,
            )
            logger.info(
                f"OneBot reverse WS server listening on "
                f"ws://{self.config.reverse_host}:{self.config.reverse_port}"
            )
            await self._server.wait_closed()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"OneBot reverse WS server error: {e}")

    async def _reverse_ws_handler(self, ws) -> None:
        """处理反向 WS 客户端连入"""
        if not self._verify_access_token(ws):
            logger.warning("OneBot reverse WS: access token mismatch, rejecting connection")
            await ws.close(4001, "Unauthorized")
            return

        old_ws = self._ws
        self._ws = ws
        if old_ws and not old_ws.closed:
            logger.info("OneBot reverse WS: replacing old connection")
            self._reject_pending_callbacks("connection replaced")
            with contextlib.suppress(Exception):
                await old_ws.close(4000, "replaced by new connection")

        remote = getattr(ws, "remote_address", ("?", "?"))
        logger.info(f"OneBot reverse WS: client connected from {remote}")

        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict):
                        logger.warning(f"OneBot: expected dict, got {type(data).__name__}")
                        continue
                    await self._handle_event(data)
                except json.JSONDecodeError:
                    logger.warning("OneBot: invalid JSON received")
                except Exception as e:
                    logger.error(f"OneBot: error handling event: {e}")
        except websockets.ConnectionClosed:
            logger.warning("OneBot reverse WS: client disconnected")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"OneBot reverse WS error: {e}")
        finally:
            if self._ws is ws:
                self._ws = None
                self._reject_pending_callbacks("connection closed")

    def _verify_access_token(self, ws) -> bool:
        if not self.config.access_token:
            return True
        # Authorization: Bearer <token> 请求头
        headers = getattr(ws, "request_headers", getattr(ws, "request", None))
        if headers:
            auth = None
            if hasattr(headers, "get"):
                auth = headers.get("Authorization", "")
            elif hasattr(headers, "headers") and hasattr(headers.headers, "get"):
                auth = headers.headers.get("Authorization", "")
            if auth and auth == f"Bearer {self.config.access_token}":
                return True
        # ?access_token=<token> 查询参数
        path = getattr(ws, "path", "") or ""
        if not path and hasattr(ws, "request") and hasattr(ws.request, "path"):
            path = ws.request.path or ""
        qs = parse_qs(urlparse(path).query)
        tokens = qs.get("access_token", [])
        if tokens and tokens[0] == self.config.access_token:
            return True
        return False

    # ==================== 正向 WebSocket (forward 模式) ====================

    async def _connect_ws(self) -> bool:
        headers = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        try:
            self._ws = await websockets.connect(
                self.config.ws_url,
                additional_headers=headers,
                open_timeout=10,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info(f"OneBot adapter connected to {self.config.ws_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to OneBot: {e}")
            self._ws = None
            return False

    async def _receive_loop_with_reconnect(self) -> None:
        retry_delay = 1
        max_delay = 60

        while self._running:
            if not await self._connect_ws():
                logger.warning(f"OneBot adapter: reconnect in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
                continue

            retry_delay = 1
            try:
                async for message in self._ws:
                    try:
                        data = json.loads(message)
                        if not isinstance(data, dict):
                            continue
                        await self._handle_event(data)
                    except json.JSONDecodeError:
                        logger.warning("OneBot: invalid JSON received")
                    except Exception as e:
                        logger.error(f"OneBot: error handling event: {e}")
            except websockets.ConnectionClosed:
                logger.warning("OneBot WebSocket connection closed, will reconnect...")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"OneBot WebSocket error: {e}")

            self._ws = None
            self._reject_pending_callbacks("connection lost")

            if self._running:
                logger.info(f"OneBot adapter: reconnecting in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    # ==================== 事件处理 ====================

    async def _handle_event(self, data: dict) -> None:
        if "echo" in data:
            echo = str(data["echo"])
            future = self._api_callbacks.pop(echo, None)
            if future and not future.done():
                try:
                    if data.get("status") == "ok":
                        future.set_result(data.get("data"))
                    else:
                        future.set_exception(RuntimeError(data.get("message", "API call failed")))
                except asyncio.InvalidStateError:
                    pass
            return

        post_type = data.get("post_type")
        if post_type == "message":
            await self._handle_message_event(data)
        elif post_type == "notice":
            await self._emit_event("notice", data)
        elif post_type == "request":
            await self._emit_event("request", data)

    async def _handle_message_event(self, data: dict) -> None:
        msg_id = str(data.get("message_id", ""))
        if msg_id and msg_id in self._seen_message_ids:
            return
        if msg_id:
            self._seen_message_ids[msg_id] = None
            if len(self._seen_message_ids) > self._SEEN_CAPACITY:
                self._seen_message_ids.popitem(last=False)

        import time as _time
        event_time = data.get("time")
        if event_time and isinstance(event_time, (int, float)):
            age_s = _time.time() - event_time
            if age_s > self.STALE_MESSAGE_THRESHOLD_S:
                logger.info(
                    f"OneBot: stale message discarded (age={age_s:.0f}s): {msg_id}"
                )
                return

        message_type = data.get("message_type")
        raw_message = data.get("message")
        if raw_message is None:
            raw_message = []
        if isinstance(raw_message, str):
            raw_message = self._parse_cq_code(raw_message)
        if not isinstance(raw_message, list):
            raw_message = []

        content, _reply_to_id = await self._parse_message(raw_message)

        if message_type == "private":
            chat_type = "private"
            chat_id = str(data.get("user_id"))
        elif message_type == "group":
            chat_type = "group"
            chat_id = str(data.get("group_id"))
        else:
            chat_type = "group" if data.get("group_id") else "private"
            chat_id = str(data.get("group_id") or data.get("user_id"))

        self._chat_type_map[chat_id] = chat_type
        if len(self._chat_type_map) > self._CHAT_TYPE_MAP_CAPACITY:
            self._chat_type_map.popitem(last=False)
        is_direct_message = chat_type == "private"

        is_mentioned = False
        bot_id = str(data.get("self_id", ""))
        if isinstance(raw_message, list):
            for seg in raw_message:
                if seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == bot_id or qq == "all":
                        is_mentioned = True
                        break

        sender = data.get("sender") or {}
        user_id = str(data.get("user_id"))

        chat_name = ""
        if chat_type == "group" and data.get("group_id"):
            gid = str(data["group_id"])
            chat_name = self._group_name_cache.get(gid, "")
            if not chat_name:
                try:
                    info = await self.get_group_info(data["group_id"])
                    if info:
                        chat_name = info.get("name") or ""
                        if chat_name:
                            self._group_name_cache[gid] = chat_name
                except Exception:
                    pass

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msg_id,
            user_id=f"onebot_{user_id}",
            channel_user_id=user_id,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            reply_to=_reply_to_id,
            raw=data,
            metadata={
                "nickname": sender.get("nickname"),
                "card": sender.get("card"),
                "is_group": chat_type == "group",
                "sender_name": sender.get("card") or sender.get("nickname") or "",
                "chat_name": chat_name,
            },
        )

        self._log_message(unified)
        await self._emit_message(unified)

    # ==================== CQ 码解析 ====================

    _CQ_PATTERN = re.compile(r"\[CQ:(\w+)(?:,([^\]]+))?\]")

    def _parse_cq_code(self, message: str) -> list[dict]:
        result = []
        last_end = 0
        for match in self._CQ_PATTERN.finditer(message):
            if match.start() > last_end:
                text = message[last_end: match.start()]
                if text:
                    result.append({"type": "text", "data": {"text": _decode_cq_entities(text)}})

            cq_type = match.group(1)
            params = {}
            if match.group(2):
                for param in match.group(2).split(","):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        params[_decode_cq_entities(key)] = _decode_cq_entities(value)
            result.append({"type": cq_type, "data": params})
            last_end = match.end()

        if last_end < len(message):
            text = message[last_end:]
            if text:
                result.append({"type": "text", "data": {"text": _decode_cq_entities(text)}})

        return result

    # ==================== 消息解析 ====================

    async def _parse_message(self, message: list) -> tuple[MessageContent, str | None]:
        """解析 OneBot 消息段列表，返回 (content, reply_to_id)"""
        content = MessageContent()
        text_parts = []
        reply_to_id: str | None = None

        for segment in message:
            if not isinstance(segment, dict):
                continue
            seg_type = segment.get("type")
            data = segment.get("data") or {}

            if seg_type == "text":
                text_parts.append(data.get("text") or "")
            elif seg_type == "image":
                import mimetypes
                img_file = data.get("file") or "image.jpg"
                img_mime = mimetypes.guess_type(img_file)[0] or "image/jpeg"
                media = MediaFile.create(
                    filename=img_file,
                    mime_type=img_mime,
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.images.append(media)
            elif seg_type == "record":
                media = MediaFile.create(
                    filename=data.get("file") or "voice.amr",
                    mime_type="audio/amr",
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.voices.append(media)
            elif seg_type == "video":
                media = MediaFile.create(
                    filename=data.get("file") or "video.mp4",
                    mime_type="video/mp4",
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.videos.append(media)
            elif seg_type == "file":
                media = MediaFile.create(
                    filename=data.get("name") or "file",
                    mime_type="application/octet-stream",
                    file_id=data.get("id"),
                )
                content.files.append(media)
            elif seg_type == "at":
                text_parts.append(f"@{data.get('qq', data.get('id', ''))}")
            elif seg_type == "face":
                text_parts.append(f"[表情:{data.get('id', '')}]")
            elif seg_type == "reply":
                rid = data.get("id", "")
                if rid:
                    reply_to_id = str(rid)
            elif seg_type == "forward":
                fwd_id = data.get("id", "")
                text_parts.append(f"[合并转发:{fwd_id}]")
            elif seg_type == "share":
                url = data.get("url", "")
                title = data.get("title", "链接")
                text_parts.append(f"[分享: {title} {url}]")
            elif seg_type == "json":
                json_data = str(data.get("data", ""))
                text_parts.append(f"[JSON消息: {json_data[:200]}]")
            elif seg_type == "xml":
                xml_data = str(data.get("data", ""))
                text_parts.append(f"[XML消息: {xml_data[:200]}]")
            elif seg_type == "location":
                lat = data.get("lat", "")
                lon = data.get("lon", data.get("long", ""))
                title = data.get("title", "")
                content.location = {"lat": lat, "lng": lon, "name": title}

        content.text = "".join(text_parts) if text_parts else None
        return content, reply_to_id

    # ==================== API 调用 ====================

    async def _call_api(self, action: str, params: dict = None) -> Any:
        ws = self._ws
        if not ws or getattr(ws, "closed", True):
            mode_hint = "反向 WS 尚无客户端连接" if self.config.mode == "reverse" else "WebSocket 未连接"
            raise RuntimeError(f"OneBot {mode_hint}")

        echo = str(uuid.uuid4())
        request = {"action": action, "params": params or {}, "echo": echo}

        future = asyncio.get_running_loop().create_future()
        self._api_callbacks[echo] = future

        try:
            await ws.send(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except TimeoutError:
            self._api_callbacks.pop(echo, None)
            raise RuntimeError(f"API call timeout: {action}")
        except Exception:
            self._api_callbacks.pop(echo, None)
            raise

    def _reject_pending_callbacks(self, reason: str) -> None:
        """拒绝所有等待中的 API 回调"""
        callbacks = list(self._api_callbacks.items())
        self._api_callbacks.clear()
        for _, future in callbacks:
            if not future.done():
                try:
                    future.set_exception(ConnectionError(f"OneBot: {reason}"))
                except asyncio.InvalidStateError:
                    pass

    # ==================== 消息发送 ====================

    def _is_group_message(self, message: OutgoingMessage) -> bool:
        if "is_group" in message.metadata:
            return bool(message.metadata["is_group"])
        return self._chat_type_map.get(message.chat_id, "group") == "group"

    async def send_message(self, message: OutgoingMessage) -> str:
        msg_array = []

        if message.reply_to:
            msg_array.append({"type": "reply", "data": {"id": str(message.reply_to)}})

        if message.content.text:
            msg_array.append({"type": "text", "data": {"text": message.content.text}})

        for img in message.content.images:
            if img.local_path:
                normalized = img.local_path.replace("\\", "/")
                msg_array.append({"type": "image", "data": {"file": f"file:///{normalized}"}})
            elif img.url:
                msg_array.append({"type": "image", "data": {"file": img.url}})
            else:
                logger.warning("OneBot: image has no local_path or url, skipped")

        for voice in message.content.voices:
            if voice.local_path:
                normalized = voice.local_path.replace("\\", "/")
                msg_array.append({"type": "record", "data": {"file": f"file:///{normalized}"}})

        try:
            chat_id = int(message.chat_id)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid chat_id for OneBot: {message.chat_id!r}")

        if self._is_group_message(message):
            result = await self._call_api("send_group_msg", {"group_id": chat_id, "message": msg_array})
        else:
            result = await self._call_api("send_private_msg", {"user_id": chat_id, "message": msg_array})

        return str((result or {}).get("message_id", ""))

    async def send_group_message(self, group_id: int, message: str) -> str:
        result = await self._call_api("send_group_msg", {"group_id": group_id, "message": message})
        return str((result or {}).get("message_id", ""))

    async def send_private_message(self, user_id: int, message: str) -> str:
        result = await self._call_api("send_private_msg", {"user_id": user_id, "message": message})
        return str((result or {}).get("message_id", ""))

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送"正在输入"状态（NapCat 扩展 API: set_input_status）。

        非标准 OneBot v11 接口，对其他实现（go-cqhttp 等）会静默失败。
        """
        try:
            chat_id_int = int(chat_id)
            is_group = self._chat_type_map.get(chat_id, "group") == "group"
            if is_group:
                await self._call_api("set_input_status", {
                    "group_id": chat_id_int, "event_type": "1",
                })
            else:
                await self._call_api("set_input_status", {
                    "user_id": chat_id_int, "event_type": "1",
                })
        except Exception:
            pass

    # ==================== 媒体 ====================

    async def download_media(self, media: MediaFile) -> Path:
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if media.url:
            try:
                import httpx as hx
            except ImportError:
                raise ImportError("httpx not installed. Run: pip install httpx")

            async with hx.AsyncClient() as client:
                response = await client.get(media.url, timeout=60.0)
                response.raise_for_status()

                stem = Path(media.filename).stem
                suffix = Path(media.filename).suffix or ".bin"
                local_path = self.media_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

                with open(local_path, "wb") as f:
                    f.write(response.content)

                media.local_path = str(local_path)
                media.status = MediaStatus.READY
                return local_path

        raise ValueError("Media has no url")

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        return MediaFile.create(filename=path.name, mime_type=mime_type)

    # ==================== 用户/群查询 ====================

    async def get_user_info(self, user_id: str) -> dict | None:
        try:
            result = await self._call_api("get_stranger_info", {"user_id": int(user_id)})
            return {
                "id": str(result.get("user_id")),
                "nickname": result.get("nickname"),
                "sex": result.get("sex"),
                "age": result.get("age"),
            }
        except Exception:
            return None

    async def get_group_info(self, group_id: int) -> dict | None:
        try:
            result = await self._call_api("get_group_info", {"group_id": group_id})
            return {
                "id": str(result.get("group_id")),
                "name": result.get("group_name"),
                "member_count": result.get("member_count"),
                "max_member_count": result.get("max_member_count"),
            }
        except Exception:
            return None

    # ==================== 文件/语音发送 ====================

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        *,
        is_group: bool | None = None,
    ) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        chat_id_int = int(chat_id)
        _is_grp = is_group if is_group is not None else True  # 文件上传默认尝试群

        if caption:
            text_msg = [{"type": "text", "data": {"text": caption}}]
            api = "send_group_msg" if _is_grp else "send_private_msg"
            key = "group_id" if _is_grp else "user_id"
            with contextlib.suppress(Exception):
                await self._call_api(api, {key: chat_id_int, "message": text_msg})

        file_str = str(path.resolve()).replace("\\", "/")

        api = "upload_group_file" if _is_grp else "upload_private_file"
        key = "group_id" if _is_grp else "user_id"
        try:
            result = await self._call_api(api, {key: chat_id_int, "file": file_str, "name": path.name})
            return str((result or {}).get("message_id", f"file_{chat_id}"))
        except Exception as e:
            raise RuntimeError(f"Failed to send file via OneBot: {e}") from e

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
        *,
        is_group: bool | None = None,
    ) -> str:
        path = Path(voice_path)
        if not path.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")

        chat_id_int = int(chat_id)
        _is_grp = is_group if is_group is not None else True

        normalized = str(path.resolve()).replace("\\", "/")
        msg_array = [{"type": "record", "data": {"file": f"file:///{normalized}"}}]

        api = "send_group_msg" if _is_grp else "send_private_msg"
        key = "group_id" if _is_grp else "user_id"

        result = await self._call_api(api, {key: chat_id_int, "message": msg_array})

        if caption:
            caption_msg = [{"type": "text", "data": {"text": caption}}]
            with contextlib.suppress(Exception):
                await self._call_api(api, {key: chat_id_int, "message": caption_msg})

        return str((result or {}).get("message_id", ""))

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        try:
            await self._call_api("delete_msg", {"message_id": int(message_id)})
            return True
        except Exception:
            return False
