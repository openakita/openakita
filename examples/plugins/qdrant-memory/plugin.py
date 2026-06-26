"""qdrant-memory: stub MemoryBackendProtocol implementation (no qdrant-client import)."""

from __future__ import annotations

import logging
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class QdrantMemoryBackend:
    """Stub backend: logs what a real Qdrant integration would do."""

    def __init__(self, get_config: Any) -> None:
        self._get_config = get_config

    async def store(self, memory: dict) -> str:
        cfg = self._get_config()
        logger.info(
            "[qdrant-memory] store would upsert vector qdrant_url=%s collection=%s keys=%s",
            cfg.get("qdrant_url"),
            cfg.get("collection_name"),
            list(memory.keys()) if isinstance(memory, dict) else type(memory).__name__,
        )
        return "stub-memory-id"

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        cfg = self._get_config()
        logger.info(
            "[qdrant-memory] search would query qdrant_url=%s collection=%s q=%r limit=%s",
            cfg.get("qdrant_url"),
            cfg.get("collection_name"),
            query,
            limit,
        )
        return []

    async def delete(self, memory_id: str) -> bool:
        logger.info("[qdrant-memory] delete would remove id=%s", memory_id)
        return True

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        cfg = self._get_config()
        logger.info(
            "[qdrant-memory] get_injection_context q=%r max_tokens=%s (url=%s)",
            query,
            max_tokens,
            cfg.get("qdrant_url"),
        )
        return ""

    async def start_session(self, session_id: str) -> None:
        logger.info("[qdrant-memory] start_session session_id=%s", session_id)

    async def end_session(self) -> None:
        logger.info("[qdrant-memory] end_session")

    async def record_turn(self, role: str, content: str) -> None:
        logger.info(
            "[qdrant-memory] record_turn role=%s content_len=%s",
            role,
            len(content),
        )


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        backend = QdrantMemoryBackend(api.get_config)
        api.register_memory_backend(backend)

    def on_unload(self) -> None:
        pass
