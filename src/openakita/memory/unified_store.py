"""
统一存储层

协调 MemoryStorage (SQLite) + SearchBackend (搜索引擎):
- 写入: SQLite 主写 + SearchBackend 索引同步
- 查询: 结构化查询走 SQLite, 语义搜索走 SearchBackend
- 降级: SearchBackend 不可用时回退到 FTS5
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .search_backends import SearchBackend, FTS5Backend, create_search_backend
from .storage import MemoryStorage
from .types import (
    SemanticMemory,
    Episode,
    Scratchpad,
    Attachment,
)

logger = logging.getLogger(__name__)


class UnifiedStore:
    """统一存储层: SQLite 为主存储, SearchBackend 为搜索引擎"""

    def __init__(
        self,
        db_path: str | Path,
        search_backend: SearchBackend | None = None,
        *,
        vector_store: Any = None,
        backend_type: str = "fts5",
        api_provider: str = "",
        api_key: str = "",
        api_model: str = "",
    ) -> None:
        self.db = MemoryStorage(db_path)

        if search_backend is not None:
            self.search = search_backend
        else:
            self.search = create_search_backend(
                backend_type,
                storage=self.db,  # now self.db is already initialized
                vector_store=vector_store,
                api_provider=api_provider,
                api_key=api_key,
                api_model=api_model,
            )

        self._fts5_fallback: FTS5Backend | None = None
        if self.search.backend_type != "fts5":
            self._fts5_fallback = FTS5Backend(self.db)

    # ======================================================================
    # Semantic Memory
    # ======================================================================

    def save_semantic(self, memory: SemanticMemory) -> str:
        d = memory.to_dict()
        self.db.save_memory(d)
        self.search.add(memory.id, memory.content, {
            "type": memory.type.value,
            "priority": memory.priority.value,
            "importance": memory.importance_score,
            "tags": memory.tags,
        })
        return memory.id

    def update_semantic(self, memory_id: str, updates: dict) -> bool:
        ok = self.db.update_memory(memory_id, updates)
        if ok and "content" in updates:
            self.search.delete(memory_id)
            mem = self.db.get_memory(memory_id)
            if mem:
                self.search.add(memory_id, mem["content"], {
                    "type": mem.get("type", "fact"),
                    "priority": mem.get("priority", "short_term"),
                    "importance": mem.get("importance_score", 0.5),
                    "tags": mem.get("tags", []),
                })
        return ok

    def delete_semantic(self, memory_id: str) -> bool:
        self.search.delete(memory_id)
        return self.db.delete_memory(memory_id)

    def get_semantic(self, memory_id: str) -> SemanticMemory | None:
        d = self.db.get_memory(memory_id)
        if d is None:
            return None
        self.db.update_memory(memory_id, {
            "access_count": d.get("access_count", 0) + 1,
            "last_accessed_at": datetime.now().isoformat(),
        })
        return SemanticMemory.from_dict(d)

    def search_semantic(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[SemanticMemory]:
        results = self.search.search(query, limit=limit, filter_type=filter_type)
        if not results and self._fts5_fallback is not None:
            results = self._fts5_fallback.search(query, limit=limit, filter_type=filter_type)

        memories: list[SemanticMemory] = []
        for memory_id, _score in results:
            d = self.db.get_memory(memory_id)
            if d:
                memories.append(SemanticMemory.from_dict(d))
        return memories

    def query_semantic(self, **kwargs: Any) -> list[SemanticMemory]:
        rows = self.db.query(**kwargs)
        return [SemanticMemory.from_dict(r) for r in rows]

    def find_similar(self, subject: str, predicate: str) -> SemanticMemory | None:
        """Find existing memory with same subject+predicate for update detection."""
        rows = self.db.query(subject=subject, limit=10)
        for row in rows:
            if row.get("predicate", "").lower() == predicate.lower():
                return SemanticMemory.from_dict(row)
        query = f"{subject} {predicate}"
        results = self.search.search(query, limit=5)
        for mid, score in results:
            if score > 0.8:
                d = self.db.get_memory(mid)
                if d and d.get("subject", "").lower() == subject.lower():
                    return SemanticMemory.from_dict(d)
        return None

    def count_memories(self, memory_type: str | None = None) -> int:
        return self.db.count(memory_type)

    def load_all_memories(self) -> list[SemanticMemory]:
        rows = self.db.load_all()
        return [SemanticMemory.from_dict(r) for r in rows]

    # ======================================================================
    # Episode Memory
    # ======================================================================

    def save_episode(self, episode: Episode) -> str:
        self.db.save_episode(episode.to_dict())
        return episode.id

    def get_episode(self, episode_id: str) -> Episode | None:
        d = self.db.get_episode(episode_id)
        return Episode.from_dict(d) if d else None

    def search_episodes(self, **kwargs: Any) -> list[Episode]:
        rows = self.db.search_episodes(**kwargs)
        return [Episode.from_dict(r) for r in rows]

    def get_recent_episodes(self, days: int = 7, limit: int = 10) -> list[Episode]:
        return self.search_episodes(days=days, limit=limit)

    # ======================================================================
    # Scratchpad
    # ======================================================================

    def get_scratchpad(self, user_id: str = "default") -> Scratchpad | None:
        d = self.db.get_scratchpad(user_id)
        return Scratchpad.from_dict(d) if d else None

    def save_scratchpad(self, scratchpad: Scratchpad) -> None:
        self.db.save_scratchpad(scratchpad.to_dict())

    # ======================================================================
    # Conversation Turns
    # ======================================================================

    def save_turn(self, **kwargs: Any) -> None:
        self.db.save_turn(**kwargs)

    def get_unextracted_turns(self, limit: int = 100) -> list[dict]:
        return self.db.get_unextracted_turns(limit)

    def mark_turns_extracted(self, session_id: str, turn_indices: list[int]) -> None:
        self.db.mark_turns_extracted(session_id, turn_indices)

    def get_session_turns(self, session_id: str) -> list[dict]:
        return self.db.get_session_turns(session_id)

    # ======================================================================
    # Extraction Queue
    # ======================================================================

    def enqueue_extraction(self, **kwargs: Any) -> None:
        self.db.enqueue_extraction(**kwargs)

    def dequeue_extraction(self, batch_size: int = 10) -> list[dict]:
        return self.db.dequeue_extraction(batch_size)

    def complete_extraction(self, queue_id: int, success: bool = True) -> None:
        self.db.complete_extraction(queue_id, success)

    # ======================================================================
    # Attachments (文件/媒体记忆)
    # ======================================================================

    def save_attachment(self, attachment: Attachment) -> str:
        self.db.save_attachment(attachment.to_dict())
        return attachment.id

    def get_attachment(self, attachment_id: str) -> Attachment | None:
        d = self.db.get_attachment(attachment_id)
        return Attachment.from_dict(d) if d else None

    def search_attachments(
        self,
        query: str = "",
        mime_type: str | None = None,
        direction: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[Attachment]:
        rows = self.db.search_attachments(
            query=query, mime_type=mime_type,
            direction=direction, session_id=session_id, limit=limit,
        )
        return [Attachment.from_dict(r) for r in rows]

    def delete_attachment(self, attachment_id: str) -> bool:
        return self.db.delete_attachment(attachment_id)

    def get_session_attachments(self, session_id: str) -> list[Attachment]:
        rows = self.db.get_session_attachments(session_id)
        return [Attachment.from_dict(r) for r in rows]

    # ======================================================================
    # Utilities
    # ======================================================================

    def get_stats(self) -> dict:
        return {
            "memory_count": self.db.count(),
            "search_backend": self.search.backend_type,
            "search_available": self.search.available,
        }

    def close(self) -> None:
        self.db.close()
