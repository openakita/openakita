"""
表情包处理器

处理表情包相关的工具调用:
- send_sticker: 搜索并发送表情包
"""

import json
import logging
import random
import urllib.parse
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class StickerHandler:
    """表情包处理器"""

    TOOLS = ["send_sticker"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "send_sticker":
            return await self._send_sticker(params)
        else:
            return f"❌ Unknown sticker tool: {tool_name}"

    async def _send_sticker(self, params: dict) -> str:
        """搜索并发送表情包"""
        if not hasattr(self.agent, "sticker_engine") or not self.agent.sticker_engine:
            return "❌ 表情包引擎未初始化"

        sticker_engine = self.agent.sticker_engine
        query = params.get("query") or ""
        mood = params.get("mood")
        category = params.get("category")

        # 搜索表情包
        result = None
        if mood and not query:
            result = await sticker_engine.get_random_by_mood(mood)
        else:
            results = await sticker_engine.search(query, category=category, limit=3)
            result = random.choice(results) if results else None

        if not result:
            return "❌ 没找到合适的表情包，换个关键词试试？"

        # 下载到本地缓存
        url = result.get("url", "")
        if not url:
            return "❌ 表情包 URL 无效"

        local_path = await sticker_engine.download_and_cache(url)
        if not local_path:
            return f"❌ 表情包下载失败: {url}"

        # 通过 im_context 获取当前 IM 会话的适配器和 chat_id
        adapter, chat_id = self._get_adapter_and_chat_id()
        if adapter and chat_id:
            try:
                await adapter.send_image(chat_id, str(local_path), caption="")
                return f"✅ 已发送表情包: {result.get('name', 'unknown')}"
            except Exception as e:
                logger.warning(f"Failed to send sticker via adapter: {e}")
                return f"❌ 表情包发送失败: {e}"

        # Desktop 模式：直接返回 deliver_artifacts 格式的 JSON 回执，
        # 让 chat API 能直接注入 artifact 事件，无需 LLM 再次调用 deliver_artifacts。
        return self._build_desktop_receipt(local_path, result.get("name", "sticker"))

    @staticmethod
    def _build_desktop_receipt(local_path, name: str) -> str:
        """构造与 deliver_artifacts desktop 回执相同格式的 JSON，
        让 chat API 的 send_sticker 拦截逻辑能注入 artifact 事件。"""
        from pathlib import Path as _Path

        resolved = _Path(local_path).resolve()
        abs_path = str(resolved)
        file_url = f"/api/files?path={urllib.parse.quote(abs_path, safe='')}"

        return json.dumps(
            {
                "ok": True,
                "channel": "desktop",
                "receipts": [
                    {
                        "index": 0,
                        "status": "delivered",
                        "type": "image",
                        "path": abs_path,
                        "file_url": file_url,
                        "caption": "",
                        "name": name,
                        "size": resolved.stat().st_size if resolved.exists() else 0,
                        "channel": "desktop",
                    }
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _get_adapter_and_chat_id():
        """通过 im_context 获取当前 IM 适配器和 chat_id"""
        from ...core.im_context import get_im_session

        session = get_im_session()
        if not session:
            return None, None

        gateway = session.get_metadata("_gateway")
        current_message = session.get_metadata("_current_message")

        if not gateway or not current_message:
            return None, None

        channel = current_message.channel
        adapter = gateway.get_adapter(channel) if hasattr(gateway, "get_adapter") else None
        if adapter is None:
            adapter = getattr(gateway, "_adapters", {}).get(channel)

        if not adapter:
            logger.warning(f"[Sticker] No adapter found for channel: {channel}")
            return None, None

        return adapter, current_message.chat_id


def create_handler(agent: "Agent"):
    """创建表情包处理器"""
    handler = StickerHandler(agent)
    return handler.handle
