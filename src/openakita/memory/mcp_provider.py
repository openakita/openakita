"""MCP-backed memory provider adapters.

This keeps external memory services on the same MemoryManager path as built-in
memory: prompt retrieval uses RetrievalEngine sources, while replace-mode
providers implement the MemoryBackendProtocol methods.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MCPMemoryProvider:
    """Adapt a configured MCP server into OpenAkita's memory provider protocol."""

    source_name: str

    def __init__(
        self,
        *,
        client: Any,
        server: str,
        tools: dict[str, str],
        mode: str = "augment",
        default_limit: int = 5,
    ) -> None:
        self.client = client
        self.server = server
        self.tools = {str(k): str(v) for k, v in (tools or {}).items() if v}
        self.mode = mode
        self.default_limit = max(1, int(default_limit or 5))
        self.source_name = f"mcp:{server}"

    @property
    def replace(self) -> bool:
        return self.mode == "replace"

    async def _ensure_connected(self) -> bool:
        if self.client.is_connected(self.server):
            return True
        result = await self.client.connect(self.server)
        if not result.success:
            logger.warning(
                "[MCPMemory] failed to connect %s: %s",
                self.server,
                result.error,
            )
            return False
        return True

    async def _call(self, purpose: str, arguments: dict[str, Any]) -> Any:
        tool_name = self.tools.get(purpose)
        if not tool_name:
            return None
        if not await self._ensure_connected():
            return None
        result = await self.client.call_tool(self.server, tool_name, arguments)
        if not result.success:
            logger.warning(
                "[MCPMemory] %s.%s failed: %s",
                self.server,
                tool_name,
                result.error,
            )
            return None
        return result.data

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        """RetrievalSource protocol for augment-mode memory."""
        data = await self._call(
            "search",
            {"query": query, "limit": limit or self.default_limit},
        )
        return self._coerce_items(data)

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """MemoryBackendProtocol search."""
        return await self.retrieve(query, limit=limit)

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        items = await self.retrieve(query, limit=self.default_limit)
        if not items:
            return ""
        max_chars = max(200, max_tokens * 4)
        lines = []
        for item in items:
            content = str(item.get("content") or item.get("text") or item).strip()
            if content:
                lines.append(f"- {content}")
        text = "\n".join(lines)
        return text[:max_chars]

    async def store(self, memory: dict) -> str:
        data = await self._call("store", {"memory": memory})
        if isinstance(data, dict):
            return str(data.get("id") or data.get("memory_id") or "")
        return str(data or "")

    async def delete(self, memory_id: str) -> bool:
        data = await self._call("delete", {"memory_id": memory_id})
        if isinstance(data, dict) and "success" in data:
            return bool(data["success"])
        return bool(data)

    async def start_session(self, session_id: str) -> None:
        await self._call("start_session", {"session_id": session_id})

    async def end_session(self) -> None:
        await self._call("end_session", {})

    async def record_turn(self, role: str, content: str) -> None:
        await self._call("record_turn", {"role": role, "content": content})

    @staticmethod
    def _coerce_items(data: Any) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, str):
            stripped = data.strip()
            if not stripped:
                return []
            try:
                data = json.loads(stripped)
            except Exception:
                return [{"content": stripped, "relevance": 0.5}]
        if isinstance(data, dict):
            for key in ("items", "results", "memories", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    data = value
                    break
            else:
                data = [data]
        if not isinstance(data, list):
            return [{"content": str(data), "relevance": 0.5}]

        items: list[dict] = []
        for item in data:
            if isinstance(item, dict):
                content = item.get("content") or item.get("text") or item.get("memory")
                if content:
                    normalized = dict(item)
                    normalized["content"] = str(content)
                    normalized.setdefault("relevance", item.get("score", 0.5))
                    items.append(normalized)
            elif item:
                items.append({"content": str(item), "relevance": 0.5})
        return items
