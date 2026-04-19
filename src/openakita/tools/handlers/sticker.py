"""
Sticker handler

Handles sticker-related tool calls:
- send_sticker: search and send stickers
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
    """Sticker handler"""

    TOOLS = ["send_sticker"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool call"""
        if tool_name == "send_sticker":
            return await self._send_sticker(params)
        else:
            return f"❌ Unknown sticker tool: {tool_name}"

    async def _send_sticker(self, params: dict) -> str:
        """Search and send a sticker"""
        if not hasattr(self.agent, "sticker_engine") or not self.agent.sticker_engine:
            return "❌ Sticker engine not initialized"

        sticker_engine = self.agent.sticker_engine
        query = params.get("query") or ""
        mood = params.get("mood")
        category = params.get("category")

        # Search for sticker
        result = None
        if mood and not query:
            result = await sticker_engine.get_random_by_mood(mood)
        else:
            results = await sticker_engine.search(query, category=category, limit=3)
            result = random.choice(results) if results else None

        if not result:
            return "❌ No matching sticker found. Try a different keyword?"

        # Download to local cache
        url = result.get("url", "")
        if not url:
            return "❌ Invalid sticker URL"

        local_path = await sticker_engine.download_and_cache(url)
        if not local_path:
            return f"❌ Sticker download failed: {url}"

        # Get current IM session adapter and chat_id via im_context
        adapter, chat_id = self._get_adapter_and_chat_id()
        if adapter and chat_id:
            try:
                await adapter.send_image(chat_id, str(local_path), caption="")
                return f"✅ Sticker sent: {result.get('name', 'unknown')}"
            except Exception as e:
                logger.warning(f"Failed to send sticker via adapter: {e}")
                return f"❌ Failed to send sticker: {e}"

        # Desktop mode: return a deliver_artifacts-formatted JSON receipt directly,
        # so the chat API can inject an artifact event without another LLM call to deliver_artifacts.
        return self._build_desktop_receipt(local_path, result.get("name", "sticker"))

    @staticmethod
    def _build_desktop_receipt(local_path, name: str) -> str:
        """Build a JSON receipt in the same format as the deliver_artifacts desktop receipt,
        so the chat API send_sticker interception logic can inject an artifact event."""
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
        """Get current IM adapter and chat_id via im_context"""
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
    """Create a Sticker handler"""
    handler = StickerHandler(agent)
    return handler.handle
