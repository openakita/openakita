"""
IM 通道处理器

处理 IM 通道相关的系统技能：
- deliver_artifacts: 通过网关交付附件并返回回执（推荐）
- send_to_chat: 旧的发送入口（已弃用，保留兼容；模型侧不再暴露）
- get_voice_file: 获取语音文件
- get_image_file: 获取图片文件
- get_chat_history: 获取聊天历史

通用性设计：
- 通过 gateway/adapter 发送消息，不依赖 Session 类的发送方法
- 各 adapter 实现统一接口，新增 IM 平台只需实现 ChannelAdapter 基类
- 对于平台不支持的功能（如某些平台不支持语音），返回友好提示
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ...channels.base import ChannelAdapter
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class IMChannelHandler:
    """
    IM 通道处理器

    通过 gateway 获取对应的 adapter 来发送消息，保持通用性。
    各 IM 平台的 adapter 需要实现 ChannelAdapter 基类的方法：
    - send_text(chat_id, text): 发送文本消息
    - send_file(chat_id, file_path, caption): 发送文件
    - send_image(chat_id, image_path, caption): 发送图片（可选）
    - send_voice(chat_id, voice_path, caption): 发送语音（可选）
    """

    TOOLS = [
        "deliver_artifacts",
        "send_to_chat",
        "get_voice_file",
        "get_image_file",
        "get_chat_history",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        from ...core.im_context import get_im_session

        if not get_im_session():
            # CLI 模式下静默成功，避免死循环（建议 3）
            if tool_name == "send_to_chat":
                message = params.get("message", "")[:50]
                logger.info(f"[CLI Mode] send_to_chat called but no IM session: {message}...")
                return "（CLI 模式，消息已记录但未发送到 IM）"
            return "❌ 当前不在 IM 会话中，无法使用此工具"

        if tool_name == "send_to_chat":
            return await self._send_to_chat(params)
        elif tool_name == "deliver_artifacts":
            return await self._deliver_artifacts(params)
        elif tool_name == "get_voice_file":
            return self._get_voice_file(params)
        elif tool_name == "get_image_file":
            return self._get_image_file(params)
        elif tool_name == "get_chat_history":
            return await self._get_chat_history(params)
        else:
            return f"❌ Unknown IM channel tool: {tool_name}"

    def _get_adapter_and_chat_id(self) -> tuple[Optional["ChannelAdapter"], str | None, str | None]:
        """
        获取当前 IM 会话的 adapter 和 chat_id

        Returns:
            (adapter, chat_id, channel_name) 或 (None, None, None) 如果获取失败
        """
        from ...core.im_context import get_im_session

        session = get_im_session()
        if not session:
            return None, None, None

        # 从 session metadata 获取 gateway 和当前消息
        gateway = session.get_metadata("_gateway")
        current_message = session.get_metadata("_current_message")

        if not gateway or not current_message:
            logger.warning("Missing gateway or current_message in session metadata")
            return None, None, None

        # 获取对应的 adapter
        channel = current_message.channel
        # 避免访问私有属性：优先使用公开接口
        adapter = gateway.get_adapter(channel) if hasattr(gateway, "get_adapter") else None
        if adapter is None:
            adapter = getattr(gateway, "_adapters", {}).get(channel)

        if not adapter:
            logger.warning(f"Adapter not found for channel: {channel}")
            return None, None, channel

        return adapter, current_message.chat_id, channel

    async def _send_to_chat(self, params: dict) -> str:
        """
        发送消息到聊天

        支持发送：
        - text: 文本消息
        - file_path: 文件（包括图片）
        - image_path: 图片（会自动检测并使用图片发送方式）
        - voice_path: 语音

        Args:
            params: 参数字典
                - text: 文本内容
                - file_path: 文件路径
                - image_path: 图片路径
                - voice_path: 语音路径
                - caption: 文件/图片说明文字
        """
        adapter, chat_id, channel = self._get_adapter_and_chat_id()

        if not adapter:
            if channel:
                return f"❌ 找不到通道适配器: {channel}"
            return "❌ 无法发送消息：缺少 gateway 或消息上下文"

        text = params.get("text")
        file_path = params.get("file_path")
        image_path = params.get("image_path")
        voice_path = params.get("voice_path")
        caption = params.get("caption", "")

        try:
            # 优先处理语音
            if voice_path:
                return await self._send_voice(adapter, chat_id, voice_path, caption, channel)

            # 处理图片（显式指定或文件是图片格式）
            if image_path:
                return await self._send_image(adapter, chat_id, image_path, caption, channel)

            # 处理文件（自动检测图片）
            if file_path:
                # 检测是否是图片文件
                if self._is_image_file(file_path):
                    return await self._send_image(adapter, chat_id, file_path, caption, channel)
                else:
                    return await self._send_file(adapter, chat_id, file_path, caption, channel)

            # 处理文本
            if text:
                return await self._send_text(adapter, chat_id, text, channel)

            return "❌ 请指定 text、file_path、image_path 或 voice_path"

        except Exception as e:
            logger.error(f"Send message failed: {e}", exc_info=True)
            return f"❌ 发送失败: {e}"

    async def _deliver_artifacts(self, params: dict) -> str:
        """
        统一交付入口：显式 manifest 交付附件，并返回回执 JSON。
        """
        import hashlib
        import json
        import re

        adapter, chat_id, channel = self._get_adapter_and_chat_id()
        if not adapter:
            if channel:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"adapter_not_found:{channel}",
                        "error_code": "adapter_not_found",
                        "receipts": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "ok": False,
                    "error": "missing_gateway_or_message_context",
                    "error_code": "missing_context",
                    "receipts": [],
                },
                ensure_ascii=False,
            )

        artifacts = params.get("artifacts") or []
        receipts = []

        # 会话内去重（仅运行时有效，不落盘）
        session = getattr(self.agent, "_current_session", None)
        dedupe_set: set[str] = set()
        try:
            if session and hasattr(session, "get_metadata"):
                dedupe_set = set(session.get_metadata("_delivered_dedupe_keys") or [])
        except Exception:
            dedupe_set = set()

        for idx, art in enumerate(artifacts):
            art_type = (art or {}).get("type", "")
            path = (art or {}).get("path", "")
            caption = (art or {}).get("caption", "") or ""
            dedupe_key = (art or {}).get("dedupe_key", "") or ""
            mime = (art or {}).get("mime", "") or ""
            name = (art or {}).get("name", "") or ""

            size = None
            sha256 = None
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    size = p.stat().st_size
                    h = hashlib.sha256()
                    with p.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            h.update(chunk)
                    sha256 = h.hexdigest()
            except Exception:
                pass

            if not dedupe_key and sha256:
                dedupe_key = f"{art_type}:{sha256}"
            elif not dedupe_key and path:
                dedupe_key = f"{art_type}:{hashlib.sha1((path + '|' + caption).encode('utf-8', errors='ignore')).hexdigest()[:12]}"
            receipt = {
                "index": idx,
                "type": art_type,
                "path": path,
                "status": "failed",
                "error_code": "",
                "name": name,
                "mime": mime,
                "size": size,
                "sha256": sha256,
                "dedupe_key": dedupe_key,
            }
            try:
                if not art_type or not path:
                    receipt["error"] = "missing_type_or_path"
                    receipt["error_code"] = "missing_type_or_path"
                elif dedupe_key and dedupe_key in dedupe_set:
                    receipt["status"] = "skipped"
                    receipt["error"] = "deduped"
                    receipt["error_code"] = "deduped"
                elif art_type == "voice":
                    msg = await self._send_voice(adapter, chat_id, path, caption, channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "image":
                    msg = await self._send_image(adapter, chat_id, path, caption, channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                elif art_type == "file":
                    msg = await self._send_file(adapter, chat_id, path, caption, channel)
                    receipt["status"] = "delivered" if msg.startswith("✅") else "failed"
                    receipt["message"] = msg
                    m = re.search(r"message_id=([^)]+)\)", msg)
                    if m:
                        receipt["message_id"] = m.group(1)
                    if receipt["status"] != "delivered":
                        receipt["error_code"] = "send_failed"
                else:
                    receipt["error"] = f"unsupported_type:{art_type}"
                    receipt["error_code"] = "unsupported_type"
            except Exception as e:
                receipt["error"] = str(e)
                receipt["error_code"] = "exception"
            receipts.append(receipt)

            if receipt.get("status") == "delivered" and dedupe_key:
                dedupe_set.add(dedupe_key)

        # 保存回 session metadata（下划线开头：不落盘，仅运行时）
        try:
            if session and hasattr(session, "set_metadata"):
                session.set_metadata("_delivered_dedupe_keys", list(dedupe_set))
        except Exception:
            pass

        ok = (
            all(r.get("status") in ("delivered", "skipped") for r in receipts)
            if receipts
            else False
        )
        result_json = json.dumps({"ok": ok, "receipts": receipts}, ensure_ascii=False, indent=2)

        # 进度事件由网关统一发送（节流/合并）
        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                delivered = sum(1 for r in receipts if r.get("status") == "delivered")
                total = len(receipts)
                await gateway.emit_progress_event(
                    session, f"📦 附件交付回执：{delivered}/{total} delivered"
                )
        except Exception as e:
            logger.warning(f"Failed to emit deliver progress: {e}")

        return result_json

    def _is_image_file(self, file_path: str) -> bool:
        """检测文件是否是图片"""
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        return Path(file_path).suffix.lower() in image_extensions

    async def _send_text(
        self, adapter: "ChannelAdapter", chat_id: str, text: str, channel: str
    ) -> str:
        """发送文本消息"""
        message_id = await adapter.send_text(chat_id, text)
        logger.info(f"[IM] Sent text to {channel}:{chat_id}")
        return f"✅ 已发送消息 (message_id={message_id})"

    async def _send_file(
        self, adapter: "ChannelAdapter", chat_id: str, file_path: str, caption: str, channel: str
    ) -> str:
        """发送文件"""
        # 检查文件是否存在
        if not Path(file_path).exists():
            return f"❌ 文件不存在: {file_path}"

        # 所有 adapter 都应该实现 send_file
        if hasattr(adapter, "send_file"):
            message_id = await adapter.send_file(chat_id, file_path, caption)
            logger.info(f"[IM] Sent file to {channel}:{chat_id}: {file_path}")
            return f"✅ 已发送文件: {file_path} (message_id={message_id})"
        else:
            return f"❌ 当前平台 ({channel}) 不支持发送文件"

    async def _send_image(
        self, adapter: "ChannelAdapter", chat_id: str, image_path: str, caption: str, channel: str
    ) -> str:
        """发送图片"""
        # 检查文件是否存在
        if not Path(image_path).exists():
            return f"❌ 图片不存在: {image_path}"

        # 优先使用 send_image（如果有），否则回退到 send_file
        if hasattr(adapter, "send_image"):
            message_id = await adapter.send_image(chat_id, image_path, caption)
            logger.info(f"[IM] Sent image to {channel}:{chat_id}: {image_path}")
            return f"✅ 已发送图片: {image_path} (message_id={message_id})"
        elif hasattr(adapter, "send_file"):
            # 回退到文件发送
            message_id = await adapter.send_file(chat_id, image_path, caption)
            logger.info(f"[IM] Sent image as file to {channel}:{chat_id}: {image_path}")
            return f"✅ 已发送图片: {image_path} (message_id={message_id})"
        else:
            return f"❌ 当前平台 ({channel}) 不支持发送图片"

    async def _send_voice(
        self, adapter: "ChannelAdapter", chat_id: str, voice_path: str, caption: str, channel: str
    ) -> str:
        """发送语音"""
        # 检查文件是否存在
        if not Path(voice_path).exists():
            return f"❌ 语音文件不存在: {voice_path}"

        # 语音是可选功能，不是所有平台都支持
        if hasattr(adapter, "send_voice"):
            message_id = await adapter.send_voice(chat_id, voice_path, caption)
            logger.info(f"[IM] Sent voice to {channel}:{chat_id}: {voice_path}")
            return f"✅ 已发送语音: {voice_path} (message_id={message_id})"
        else:
            return f"❌ 当前平台 ({channel}) 不支持发送语音，可以尝试用 file_path 发送文件"

    def _get_voice_file(self, params: dict) -> str:
        """获取语音文件路径"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # 从 session metadata 获取语音信息
        pending_voices = session.get_metadata("pending_voices")
        if pending_voices and len(pending_voices) > 0:
            voice = pending_voices[0]
            local_path = voice.get("local_path")
            if local_path and Path(local_path).exists():
                return f"语音文件路径: {local_path}"

        return "❌ 当前消息没有语音文件"

    def _get_image_file(self, params: dict) -> str:
        """获取图片文件路径"""
        from ...core.im_context import get_im_session

        session = get_im_session()

        # 从 session metadata 获取图片信息
        pending_images = session.get_metadata("pending_images")
        if pending_images and len(pending_images) > 0:
            image = pending_images[0]
            local_path = image.get("local_path")
            if local_path and Path(local_path).exists():
                return f"图片文件路径: {local_path}"

        return "❌ 当前消息没有图片文件"

    async def _get_chat_history(self, params: dict) -> str:
        """获取聊天历史"""
        from ...core.im_context import get_im_session

        session = get_im_session()
        limit = params.get("limit", 20)

        # 从 session context 获取消息历史
        messages = session.context.get_messages(limit=limit)

        if not messages:
            return "没有聊天历史"

        output = f"最近 {len(messages)} 条消息:\n\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                output += f"[{role}] {content[:200]}{'...' if len(content) > 200 else ''}\n"
            else:
                output += f"[{role}] [复杂内容]\n"

        return output


def create_handler(agent: "Agent"):
    """创建 IM 通道处理器"""
    handler = IMChannelHandler(agent)
    return handler.handle
