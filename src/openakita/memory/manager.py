"""
记忆管理器 (v2) — 核心协调器

v2 架构:
- UnifiedStore (SQLite + SearchBackend) 取代 memories.json + ChromaDB 直接操作
- RetrievalEngine 多路召回取代手动向量/关键词搜索
- 支持 v2 提取 (工具感知/实体-属性) 和 Episode/Scratchpad
- 向后兼容 v1 接口

注入策略:
- 三层注入: Scratchpad + Core Memory + Dynamic Memories
- 由 builder.py 调用, 不再在本模块组装

子组件:
- store: UnifiedStore
- extractor: MemoryExtractor
- retrieval_engine: RetrievalEngine
- consolidator: MemoryConsolidator (保留, JSONL 双写)
- vector_store: VectorStore (可选, 由 SearchBackend 封装)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .consolidator import MemoryConsolidator
from .extractor import MemoryExtractor
from .retrieval import RetrievalEngine
from .search_backends import create_search_backend
from .types import (
    Attachment, AttachmentDirection, ConversationTurn, Episode,
    Memory, MemoryPriority, MemoryType, SemanticMemory,
)
from .unified_store import UnifiedStore
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """记忆管理器 (v2)"""

    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        brain=None,
        embedding_model: str | None = None,
        embedding_device: str = "cpu",
        model_download_source: str = "auto",
        # v2 params
        search_backend: str = "fts5",
        embedding_api_provider: str = "",
        embedding_api_key: str = "",
        embedding_api_model: str = "",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.memory_md_path = Path(memory_md_path)
        self.brain = brain
        self._ensure_memory_md_exists()

        # Sub-components
        self.extractor = MemoryExtractor(brain)
        self.consolidator = MemoryConsolidator(data_dir, brain, self.extractor)

        # v1: VectorStore (kept for ChromaDB backend and backward compat)
        self.vector_store = VectorStore(
            data_dir=self.data_dir,
            model_name=embedding_model,
            device=embedding_device,
            download_source=model_download_source,
        )

        # v2: Unified Store + Search Backend
        db_path = self.data_dir / "openakita.db"
        self.store = UnifiedStore(
            db_path,
            vector_store=self.vector_store if search_backend == "chromadb" else None,
            backend_type=search_backend,
            api_provider=embedding_api_provider,
            api_key=embedding_api_key,
            api_model=embedding_api_model,
        )

        # v2: Retrieval Engine
        self.retrieval_engine = RetrievalEngine(self.store)

        # v1 compat: in-memory cache
        self.memories_file = self.data_dir / "memories.json"
        self._memories: dict[str, Memory] = {}
        self._memories_lock = threading.RLock()

        self._current_session_id: str | None = None
        self._session_turns: list[ConversationTurn] = []
        self._recent_messages: list[dict] = []

        # Load existing memories
        self._load_memories()

    # ==================== Initialization ====================

    def _ensure_memory_md_exists(self) -> None:
        if self.memory_md_path.exists():
            return
        self.memory_md_path.parent.mkdir(parents=True, exist_ok=True)
        default_content = """# Core Memory

> Agent 核心记忆，每次对话都会加载。每日凌晨自动刷新。
> 最后更新: {timestamp}

## 用户偏好

[待学习]

## 重要规则

[待添加]

## 关键事实

[待记录]
""".format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.memory_md_path.write_text(default_content, encoding="utf-8")
        logger.info(f"Created default MEMORY.md at {self.memory_md_path}")

    def _load_memories(self) -> None:
        if not self.memories_file.exists():
            bak = self.memories_file.with_suffix(self.memories_file.suffix + ".bak")
            tmp = self.memories_file.with_suffix(self.memories_file.suffix + ".tmp")
            for candidate in [bak, tmp]:
                if candidate.exists():
                    logger.info(f"Recovering memories from {candidate.name}")
                    candidate.rename(self.memories_file)
                    break

        if self.memories_file.exists():
            try:
                with open(self.memories_file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    with self._memories_lock:
                        for item in data:
                            memory = Memory.from_dict(item)
                            self._memories[memory.id] = memory
                    logger.info(f"Loaded {len(self._memories)} memories")
            except Exception as e:
                logger.error(f"Failed to load memories.json: {e}")

        # Sync v1 memories to v2 SQLite
        self._sync_v1_to_v2()

    def _sync_v1_to_v2(self) -> None:
        """One-time migration: push v1 memories into SQLite if not already there."""
        if not self._memories:
            return
        existing_count = self.store.count_memories()
        if existing_count >= len(self._memories):
            return
        logger.info(f"[Manager] Syncing {len(self._memories)} v1 memories to SQLite...")
        for mem in self._memories.values():
            self.store.db.save_memory(mem.to_dict())
        self.store.db.rebuild_fts_index()
        logger.info("[Manager] v1→v2 sync complete")

    def _save_memories(self) -> None:
        """Save to memories.json (backward compat, dual-write)"""
        try:
            with self._memories_lock:
                data = [m.to_dict() for m in self._memories.values()]
            tmp = self.memories_file.with_suffix(self.memories_file.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            bak = self.memories_file.with_suffix(self.memories_file.suffix + ".bak")
            if self.memories_file.exists():
                self.memories_file.replace(bak)
            tmp.rename(self.memories_file)
        except Exception as e:
            logger.error(f"Failed to save memories.json: {e}")

    async def _save_memories_async(self) -> None:
        await asyncio.to_thread(self._save_memories)

    # ==================== Session Management ====================

    def start_session(self, session_id: str) -> None:
        self._current_session_id = session_id
        self._session_turns = []
        self._recent_messages = []

    def record_turn(
        self, role: str, content: str,
        tool_calls: list | None = None,
        tool_results: list | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        """记录对话轮次 (v2: 写入 SQLite + JSONL + 异步提取 + 附件)

        Args:
            attachments: 本轮携带的文件/媒体信息列表, 每项包含:
                filename, mime_type, local_path, url, description,
                transcription, extracted_text, tags, direction, file_size
        """
        turn = ConversationTurn(
            role=role,
            content=content,
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
        )
        self._session_turns.append(turn)

        if attachments:
            direction = "inbound" if role == "user" else "outbound"
            for att_data in attachments:
                self.record_attachment(
                    filename=att_data.get("filename", ""),
                    mime_type=att_data.get("mime_type", ""),
                    local_path=att_data.get("local_path", ""),
                    url=att_data.get("url", ""),
                    description=att_data.get("description", ""),
                    transcription=att_data.get("transcription", ""),
                    extracted_text=att_data.get("extracted_text", ""),
                    tags=att_data.get("tags", []),
                    direction=att_data.get("direction", direction),
                    file_size=att_data.get("file_size", 0),
                    original_filename=att_data.get("original_filename", ""),
                )

        self._recent_messages.append({"role": role, "content": content})
        if len(self._recent_messages) > 10:
            self._recent_messages = self._recent_messages[-10:]

        # v2: Write to SQLite
        if self._current_session_id:
            self.store.save_turn(
                session_id=self._current_session_id,
                turn_index=len(self._session_turns) - 1,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )

        # v1 compat: Write to JSONL
        if self._current_session_id:
            self.consolidator.save_conversation_turn(self._current_session_id, turn)

        # Async extraction (user messages only)
        if role == "user":
            try:
                loop = asyncio.get_running_loop()

                async def _extract_and_add() -> None:
                    try:
                        # v2 extraction preferred
                        items = await self.extractor.extract_from_turn_v2(turn)
                        if items:
                            for item in items:
                                self._save_extracted_item(item)
                            logger.info(f"[Memory] v2 extraction: {len(items)} items")
                        else:
                            # Fallback to v1
                            memories = await self.extractor.extract_from_turn_with_ai(turn)
                            for memory in memories:
                                await asyncio.to_thread(self.add_memory, memory)
                            if memories:
                                logger.info(f"[Memory] v1 extraction: {len(memories)} memories")
                    except Exception as e:
                        logger.warning(f"[Memory] Extraction failed (isolated): {e}")
                        # Enqueue for retry
                        if self._current_session_id:
                            self.store.enqueue_extraction(
                                session_id=self._current_session_id,
                                turn_index=len(self._session_turns) - 1,
                                content=content,
                                tool_calls=tool_calls,
                                tool_results=tool_results,
                            )

                loop.create_task(_extract_and_add())
            except RuntimeError:
                pass
            except Exception as e:
                logger.warning(f"[Memory] Extraction scheduling failed: {e}")

    def _save_extracted_item(self, item: dict, episode_id: str | None = None) -> None:
        """Save a v2 extracted item as SemanticMemory."""
        type_map = {
            "PREFERENCE": MemoryType.PREFERENCE,
            "FACT": MemoryType.FACT,
            "SKILL": MemoryType.SKILL,
            "ERROR": MemoryType.ERROR,
            "RULE": MemoryType.RULE,
            "PERSONA_TRAIT": MemoryType.PERSONA_TRAIT,
        }
        mem_type = type_map.get(item.get("type", "FACT"), MemoryType.FACT)
        importance = item.get("importance", 0.5)

        if importance >= 0.85 or mem_type == MemoryType.RULE:
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        # v2: update detection
        if item.get("is_update"):
            existing = self.store.find_similar(
                item.get("subject", ""), item.get("predicate", "")
            )
            if existing:
                self.store.update_semantic(existing.id, {
                    "content": item["content"],
                    "importance_score": max(existing.importance_score, importance),
                })
                return

        mem = SemanticMemory(
            type=mem_type,
            priority=priority,
            content=item["content"],
            source="realtime_extraction",
            subject=item.get("subject", ""),
            predicate=item.get("predicate", ""),
            importance_score=importance,
            source_episode_id=episode_id,
            tags=[item.get("type", "fact").lower()],
        )
        self.store.save_semantic(mem)

        # v1 compat: also save to in-memory cache
        with self._memories_lock:
            self._memories[mem.id] = mem
            self._save_memories()

    def end_session(
        self, task_description: str = "", success: bool = True, errors: list | None = None
    ) -> None:
        """结束会话 (v2: 生成 Episode + 更新 Scratchpad)"""
        if not self._current_session_id:
            return

        # v1: task completion extraction
        tool_calls = []
        for turn in self._session_turns:
            tool_calls.extend(turn.tool_calls)

        memories = self.extractor.extract_from_task_completion(
            task_description=task_description,
            success=success,
            tool_calls=tool_calls,
            errors=errors or [],
        )
        for memory in memories:
            self.add_memory(memory)

        # v2: Generate episode + update scratchpad (async)
        session_id = self._current_session_id
        turns = list(self._session_turns)

        try:
            loop = asyncio.get_running_loop()

            async def _finalize_session():
                try:
                    episode = await self.extractor.generate_episode(
                        turns, session_id, source="session_end"
                    )
                    if episode:
                        self.store.save_episode(episode)
                        pad = self.store.get_scratchpad()
                        new_pad = await self.extractor.update_scratchpad(pad, episode)
                        self.store.save_scratchpad(new_pad)
                        logger.info(f"[Memory] Session finalized: episode + scratchpad updated")
                except Exception as e:
                    logger.warning(f"[Memory] Session finalization failed: {e}")

            loop.create_task(_finalize_session())
        except RuntimeError:
            pass

        logger.info(f"Ended session {session_id}: {len(memories)} memories extracted")
        self._current_session_id = None
        self._session_turns = []

    # ==================== Context Compression Hook ====================

    async def on_context_compressing(self, messages: list[dict]) -> None:
        """Called before context compression — extract quick facts and save to queue."""
        quick_facts = self.extractor.extract_quick_facts(messages)
        for fact in quick_facts:
            self.store.save_semantic(fact)
            with self._memories_lock:
                self._memories[fact.id] = fact
        if quick_facts:
            logger.info(f"[Memory] Quick extraction before compression: {len(quick_facts)} facts")

        if self._current_session_id:
            for i, msg in enumerate(messages[:10]):
                content = msg.get("content", "")
                if content and isinstance(content, str) and len(content) > 20:
                    self.store.enqueue_extraction(
                        session_id=self._current_session_id,
                        turn_index=i,
                        content=content,
                        tool_calls=msg.get("tool_calls"),
                        tool_results=msg.get("tool_results"),
                    )

    # ==================== Memory CRUD (v1 compat) ====================

    DUPLICATE_DISTANCE_THRESHOLD = 0.12

    COMMON_PREFIXES = [
        "任务执行复盘发现问题：", "任务执行复盘：", "复盘发现：",
        "系统自检发现：", "自检发现的典型问题模式：",
        "系统自检发现的典型问题模式：",
        "用户偏好：", "用户习惯：", "学习到：", "记住：",
    ]

    def _strip_common_prefix(self, content: str) -> str:
        for prefix in self.COMMON_PREFIXES:
            if content.startswith(prefix):
                return content[len(prefix):]
        return content

    def add_memory(self, memory: Memory) -> str:
        """添加记忆 (v1 compat: writes to both v1 and v2 stores)"""
        with self._memories_lock:
            existing = list(self._memories.values())
            unique = self.extractor.deduplicate([memory], existing)
            if not unique:
                return ""
            memory = unique[0]

            if self.vector_store.enabled and len(self._memories) > 0:
                core_content = self._strip_common_prefix(memory.content)
                similar = self.vector_store.search(core_content, limit=3)
                for mid, distance in similar:
                    if distance < self.DUPLICATE_DISTANCE_THRESHOLD:
                        existing_mem = self._memories.get(mid)
                        if existing_mem:
                            existing_core = self._strip_common_prefix(existing_mem.content)
                            if core_content != existing_core:
                                continue
                            return ""

            self._memories[memory.id] = memory
            self._save_memories()

            self.vector_store.add_memory(
                memory_id=memory.id,
                content=memory.content,
                memory_type=memory.type.value,
                priority=memory.priority.value,
                importance=memory.importance_score,
                tags=memory.tags,
            )

        # v2: also save to SQLite
        self.store.db.save_memory(memory.to_dict())

        logger.debug(f"Added memory: {memory.id} - {memory.content}")
        return memory.id

    def get_memory(self, memory_id: str) -> Memory | None:
        with self._memories_lock:
            memory = self._memories.get(memory_id)
            if memory:
                memory.access_count += 1
                memory.updated_at = datetime.now()
            return memory

    def search_memories(
        self,
        query: str = "",
        memory_type: MemoryType | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Memory]:
        results = []
        with self._memories_lock:
            for memory in self._memories.values():
                if memory_type and memory.type != memory_type:
                    continue
                if tags and not any(tag in memory.tags for tag in tags):
                    continue
                if query and query.lower() not in memory.content.lower():
                    continue
                results.append(memory)
        results.sort(key=lambda m: (m.importance_score, m.access_count), reverse=True)
        return results[:limit]

    def delete_memory(self, memory_id: str) -> bool:
        with self._memories_lock:
            if memory_id in self._memories:
                del self._memories[memory_id]
                self._save_memories()
                self.vector_store.delete_memory(memory_id)
                self.store.delete_semantic(memory_id)
                return True
            return False

    # ==================== Injection (v1 compat) ====================

    def get_injection_context(self, task_description: str = "", max_related: int = 5) -> str:
        """v1 compat — prefer using builder.py's three-layer injection"""
        return self.retrieval_engine.retrieve(
            query=task_description,
            recent_messages=self._recent_messages,
            max_tokens=700,
        )

    async def get_injection_context_async(self, task_description: str = "") -> str:
        return await asyncio.to_thread(self.get_injection_context, task_description)

    def _keyword_search(self, query: str, limit: int = 5) -> list[Memory]:
        keywords = [kw for kw in query.lower().split() if len(kw) > 2]
        if not keywords:
            return []
        results = []
        for memory in self._memories.values():
            content_lower = memory.content.lower()
            if any(kw in content_lower for kw in keywords):
                results.append(memory)
        results.sort(key=lambda m: m.importance_score, reverse=True)
        return results[:limit]

    # ==================== Daily Consolidation ====================

    async def consolidate_daily(self) -> dict:
        """每日归纳 (v2: 委托给 LifecycleManager)"""
        try:
            from ..config import settings
            from .lifecycle import LifecycleManager

            lifecycle = LifecycleManager(
                store=self.store,
                extractor=self.extractor,
                identity_dir=settings.identity_path,
            )
            return await lifecycle.consolidate_daily()
        except Exception as e:
            logger.error(f"[Manager] Daily consolidation failed, using legacy: {e}")
            from .daily_consolidator import DailyConsolidator
            from ..config import settings
            dc = DailyConsolidator(
                data_dir=self.data_dir,
                memory_md_path=self.memory_md_path,
                memory_manager=self,
                brain=self.brain,
                identity_dir=settings.identity_path,
            )
            return await dc.consolidate_daily()

    def _cleanup_expired_memories(self) -> int:
        now = datetime.now()
        expired = []
        with self._memories_lock:
            for memory_id, memory in list(self._memories.items()):
                if memory.priority == MemoryPriority.SHORT_TERM:
                    if (now - memory.updated_at) > timedelta(days=3):
                        expired.append(memory_id)
                elif memory.priority == MemoryPriority.TRANSIENT:
                    if (now - memory.updated_at) > timedelta(days=1):
                        expired.append(memory_id)
            for memory_id in expired:
                with contextlib.suppress(KeyError):
                    del self._memories[memory_id]
        if expired:
            self._save_memories()
            for memory_id in expired:
                with contextlib.suppress(Exception):
                    self.vector_store.delete_memory(memory_id)
                    self.store.delete_semantic(memory_id)
            logger.info(f"Cleaned up {len(expired)} expired memories")
        return len(expired)

    # ==================== Attachments (文件/媒体记忆) ====================

    def record_attachment(
        self,
        filename: str,
        mime_type: str = "",
        local_path: str = "",
        url: str = "",
        description: str = "",
        transcription: str = "",
        extracted_text: str = "",
        tags: list[str] | None = None,
        direction: str = "inbound",
        file_size: int = 0,
        original_filename: str = "",
    ) -> str:
        """记录一个文件/媒体附件, 返回 attachment ID"""
        try:
            dir_enum = AttachmentDirection(direction)
        except ValueError:
            dir_enum = AttachmentDirection.INBOUND

        attachment = Attachment(
            session_id=self._current_session_id or "",
            filename=filename,
            original_filename=original_filename or filename,
            mime_type=mime_type,
            file_size=file_size,
            local_path=local_path,
            url=url,
            direction=dir_enum,
            description=description,
            transcription=transcription,
            extracted_text=extracted_text,
            tags=tags or [],
        )
        self.store.save_attachment(attachment)
        logger.info(
            f"[Memory] Recorded attachment: {filename} ({direction}, {mime_type})"
        )
        return attachment.id

    def search_attachments(
        self,
        query: str = "",
        mime_type: str | None = None,
        direction: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[Attachment]:
        """搜索附件 — 用户问"那天发给你的猫图"时调用"""
        return self.store.search_attachments(
            query=query, mime_type=mime_type,
            direction=direction, session_id=session_id, limit=limit,
        )

    def get_attachment(self, attachment_id: str) -> Attachment | None:
        return self.store.get_attachment(attachment_id)

    # ==================== Stats ====================

    def get_stats(self) -> dict:
        type_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for memory in self._memories.values():
            type_counts[memory.type.value] = type_counts.get(memory.type.value, 0) + 1
            priority_counts[memory.priority.value] = (
                priority_counts.get(memory.priority.value, 0) + 1
            )

        v2_stats = self.store.get_stats()

        return {
            "total": len(self._memories),
            "by_type": type_counts,
            "by_priority": priority_counts,
            "sessions_today": len(self.consolidator.get_today_sessions()),
            "unprocessed_sessions": len(self.consolidator.get_unprocessed_sessions()),
            "v2_store": v2_stats,
        }
