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
from .types import (
    Attachment,
    AttachmentDirection,
    ConversationTurn,
    Memory,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
)
from .unified_store import UnifiedStore
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


def _apply_retention(memory: SemanticMemory, duration: str | None = None) -> None:
    """Set expires_at based on duration hint or memory priority."""
    if memory.expires_at is not None:
        return
    if duration and duration in _DURATION_MAP:
        delta = _DURATION_MAP[duration]
        memory.expires_at = (datetime.now() + delta) if delta else None
        return
    _PRIORITY_TTL = {
        MemoryPriority.TRANSIENT: timedelta(days=1),
        MemoryPriority.SHORT_TERM: timedelta(days=3),
        MemoryPriority.LONG_TERM: timedelta(days=30),
        MemoryPriority.PERMANENT: None,
    }
    delta = _PRIORITY_TTL.get(memory.priority)
    memory.expires_at = (datetime.now() + delta) if delta else None


_DURATION_MAP = {
    "permanent": None,
    "7d": timedelta(days=7),
    "24h": timedelta(hours=24),
    "session": timedelta(hours=2),
}


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

        # VectorStore: only create when chromadb backend is selected
        if search_backend == "chromadb":
            self.vector_store = VectorStore(
                data_dir=self.data_dir,
                model_name=embedding_model,
                device=embedding_device,
                download_source=model_download_source,
            )
        else:
            self.vector_store = None

        # v2: Unified Store + Search Backend
        db_path = self.data_dir / "openakita.db"
        self.store = UnifiedStore(
            db_path,
            vector_store=self.vector_store,
            backend_type=search_backend,
            api_provider=embedding_api_provider,
            api_key=embedding_api_key,
            api_model=embedding_api_model,
        )

        # v2: Retrieval Engine (with brain for LLM query decomposition)
        self.retrieval_engine = RetrievalEngine(self.store, brain=brain)

        # v1 compat: in-memory cache
        self.memories_file = self.data_dir / "memories.json"
        self._memories: dict[str, Memory] = {}
        self._memories_lock = threading.RLock()

        self._current_session_id: str | None = None
        self._session_turns: list[ConversationTurn] = []
        self._recent_messages: list[dict] = []

        # Citation tracking: memories retrieved via search_memory this session
        self._session_cited_memories: list[dict] = []

        # Track pending async tasks to await on shutdown
        self._pending_tasks: set[asyncio.Task] = set()

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
        """Load memories from SQLite (authoritative source) into in-memory cache.

        memories.json is kept as a secondary copy for backward compat,
        but SQLite is the single source of truth — never restore from JSON.
        """
        try:
            all_mems = self.store.load_all_memories()
            with self._memories_lock:
                for mem in all_mems:
                    self._memories[mem.id] = mem
            if all_mems:
                logger.info(f"Loaded {len(all_mems)} memories from SQLite")
        except Exception as e:
            logger.warning(f"[Manager] Failed to load from SQLite: {e}")

        # Sync in-memory cache → JSON (keep JSON in sync, not the other way around)
        if self._memories:
            self._save_memories()

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
        self._session_cited_memories = []
        try:
            self._turn_offset = self.store.get_max_turn_index(session_id)
        except Exception:
            self._turn_offset = 0
        if self._turn_offset > 0:
            logger.info(f"[Memory] start_session({session_id}): resuming at turn_offset={self._turn_offset}")
        else:
            logger.debug(f"[Memory] start_session({session_id}): fresh session (offset=0)")

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
            offset = getattr(self, "_turn_offset", 0)
            self.store.save_turn(
                session_id=self._current_session_id,
                turn_index=offset + len(self._session_turns) - 1,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )

        # v1 compat: Write to JSONL
        if self._current_session_id:
            self.consolidator.save_conversation_turn(self._current_session_id, turn)

    def record_cited_memories(self, memories: list[dict]) -> None:
        """Record memories retrieved via search_memory for later LLM scoring.

        Args:
            memories: list of {id, content} dicts
        """
        seen = {m["id"] for m in self._session_cited_memories}
        for m in memories:
            mid = m.get("id", "")
            if mid and mid not in seen:
                self._session_cited_memories.append({"id": mid, "content": m.get("content", "")})
                seen.add(mid)

    def _consume_cited_memories(self) -> list[dict]:
        """Consume and return accumulated cited memories, clearing the buffer."""
        cited = list(self._session_cited_memories)
        self._session_cited_memories = []
        return cited

    def _apply_citation_scores(self, scores: list[dict]) -> int:
        """Apply LLM citation scores: bump access_count for useful memories.

        Args:
            scores: list of {memory_id, useful} dicts from LLM
        Returns:
            Number of memories marked as useful
        """
        useful_ids = [s["memory_id"] for s in scores if s.get("useful")]
        if useful_ids:
            self.store.bump_access(useful_ids)
        return len(useful_ids)

    async def extract_on_topic_change(self) -> int:
        """主题切换时，从已积累的对话中提取记忆，然后重置 turns 缓冲。

        Returns:
            提取并保存的记忆条数
        """
        turns = list(self._session_turns)
        if len(turns) < 3:
            return 0

        try:
            cited = self._consume_cited_memories()
            items, scores = await self.extractor.extract_from_conversation(turns, cited_memories=cited or None)

            if scores:
                self._apply_citation_scores(scores)

            saved = 0
            for item in items:
                await self._save_extracted_item(item)
                saved += 1
            if saved:
                logger.info(f"[Memory] Topic-change extraction: {saved} items saved from {len(turns)} turns")
            # Reset turn buffer — new topic starts fresh
            self._session_turns.clear()
            return saved
        except Exception as e:
            logger.warning(f"[Memory] Topic-change extraction failed: {e}")
            return 0

    async def _save_extracted_item(self, item: dict, episode_id: str | None = None) -> str | None:
        """Save a v2 extracted item as SemanticMemory, with multi-layer dedup.

        Returns the memory ID (new or evolved), or None on failure.
        """
        type_map = {
            "PREFERENCE": MemoryType.PREFERENCE,
            "FACT": MemoryType.FACT,
            "SKILL": MemoryType.SKILL,
            "ERROR": MemoryType.ERROR,
            "RULE": MemoryType.RULE,
            "PERSONA_TRAIT": MemoryType.PERSONA_TRAIT,
            "EXPERIENCE": MemoryType.EXPERIENCE,
        }
        mem_type = type_map.get(item.get("type", "FACT"), MemoryType.FACT)
        importance = item.get("importance", 0.5)
        content = item.get("content", "").strip()

        if importance >= 0.85 or mem_type == MemoryType.RULE:
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        # Dedup layer 1: exact subject+predicate match → evolve existing
        subject = item.get("subject", "")
        predicate = item.get("predicate", "")
        if subject and predicate:
            existing = self.store.find_similar(subject, predicate)
            if existing:
                self._evolve_memory(existing, content, importance)
                logger.debug(f"[Memory] Dedup L1: evolved {existing.id[:8]} (subject+predicate)")
                return existing.id

        # Dedup layer 2: content similarity search
        if content and len(content) >= 10:
            try:
                similar = self.store.search_semantic(content, limit=5)
                for s in similar:
                    existing_content = (s.content or "").strip()
                    dup_level = self._fast_dedup_check(content, existing_content)

                    if dup_level == "exact":
                        self._evolve_memory(s, content, importance)
                        logger.debug(f"[Memory] Dedup L2: exact match, evolved {s.id[:8]}")
                        return s.id

                    if dup_level == "likely":
                        is_dup = await self._check_duplicate_with_llm(content, existing_content)
                        if is_dup:
                            self._evolve_memory(s, content, importance)
                            logger.debug(f"[Memory] Dedup L2: LLM confirmed dup, evolved {s.id[:8]}")
                            return s.id
            except Exception as e:
                logger.debug(f"[Memory] Dedup search failed: {e}")

        mem = SemanticMemory(
            type=mem_type,
            priority=priority,
            content=content,
            source="session_extraction",
            subject=subject,
            predicate=predicate,
            importance_score=importance,
            source_episode_id=episode_id,
            tags=[item.get("type", "fact").lower()],
        )
        _apply_retention(mem, item.get("duration"))
        self.store.save_semantic(mem)

        with self._memories_lock:
            self._memories[mem.id] = mem
            self._save_memories()

        return mem.id

    @staticmethod
    def _fast_dedup_check(new: str, existing: str) -> str:
        """Fast local dedup: returns 'exact', 'likely', or 'no'.

        - exact: definitely duplicate (skip without LLM)
        - likely: might be duplicate (needs LLM confirmation)
        - no: not duplicate
        """
        if not new or not existing:
            return "no"
        a, b = new.lower().strip(), existing.lower().strip()
        if a == b:
            return "exact"
        if len(a) > 15 and len(b) > 15 and (a in b or b in a):
            return "exact"
        if len(a) >= 10 and len(b) >= 10:
            bigrams_a = {a[i:i+2] for i in range(len(a) - 1)}
            bigrams_b = {b[i:i+2] for i in range(len(b) - 1)}
            if bigrams_a and bigrams_b:
                overlap = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
                if overlap > 0.8:
                    return "exact"
                if overlap > 0.3:
                    return "likely"
        return "no"

    async def _check_duplicate_with_llm(self, new_content: str, existing_content: str) -> bool:
        """Ask LLM whether two memory entries are semantically the same."""
        brain = getattr(self.extractor, "brain", None)
        if not brain:
            return False
        try:
            resp = await brain.think(
                f"判断这两条记忆是否表达相同的信息（语义重复）。\n"
                f"记忆A: {new_content}\n"
                f"记忆B: {existing_content}\n\n"
                f"只回答 YES 或 NO。",
                system="你是记忆去重判断器。如果两条记忆表达的核心信息相同（即使措辞不同），回答YES。否则回答NO。只输出一个词。",
            )
            text = (getattr(resp, "content", None) or str(resp)).strip().upper()
            return "YES" in text and "NO" not in text
        except Exception:
            return False

    def _evolve_memory(
        self, existing: SemanticMemory, new_content: str, new_importance: float
    ) -> None:
        """Evolve an existing memory: boost confidence/importance, optionally update content.

        If the new content is longer or higher importance, update the content.
        Always boost confidence (capped at 1.0) to signal repeated reinforcement.
        """
        updates: dict = {
            "confidence": min(1.0, existing.confidence + 0.1),
        }
        should_update_content = (
            new_importance > existing.importance_score
            or (new_importance >= existing.importance_score and len(new_content) > len(existing.content or ""))
        )
        if should_update_content:
            updates["content"] = new_content
        updates["importance_score"] = max(existing.importance_score, new_importance)

        self.store.update_semantic(existing.id, updates)

    def end_session(
        self, task_description: str = "", success: bool = True, errors: list | None = None
    ) -> None:
        """结束会话: 生成 Episode + 双轨提取（用户画像 + 任务经验）+ 引用评分"""
        if not self._current_session_id:
            return

        session_id = self._current_session_id
        turns = list(self._session_turns)
        cited = self._consume_cited_memories()

        try:
            loop = asyncio.get_running_loop()

            async def _finalize_session():
                episode = None
                try:
                    episode = await self.extractor.generate_episode(
                        turns, session_id, source="session_end"
                    )
                    if episode:
                        self.store.save_episode(episode)
                        logger.info("[Memory] Session finalized: episode saved")
                except Exception as e:
                    logger.warning(f"[Memory] Episode generation failed: {e}")

                ep_id = episode.id if episode else None
                saved_memory_ids: list[str] = []

                # Track 1: User profile extraction (+ citation scoring in same LLM call)
                try:
                    items, scores = await self.extractor.extract_from_conversation(
                        turns, cited_memories=cited or None,
                    )
                    if scores:
                        useful = self._apply_citation_scores(scores)
                        logger.info(f"[Memory] Citation scores applied: {useful} useful")
                    saved = 0
                    for item in items:
                        mid = await self._save_extracted_item(item, episode_id=ep_id)
                        if mid:
                            saved_memory_ids.append(mid)
                        saved += 1
                    if saved:
                        logger.info(f"[Memory] Profile extraction: {saved}/{len(items)} items saved")
                except Exception as e:
                    logger.warning(f"[Memory] Profile extraction failed: {e}")

                # Track 2: Task experience extraction
                try:
                    exp_items = await self.extractor.extract_experience_from_conversation(turns)
                    exp_saved = 0
                    for item in exp_items:
                        mid = await self._save_extracted_item(item, episode_id=ep_id)
                        if mid:
                            saved_memory_ids.append(mid)
                        exp_saved += 1
                    if exp_saved:
                        logger.info(f"[Memory] Experience extraction: {exp_saved}/{len(exp_items)} items saved")
                except Exception as e:
                    logger.warning(f"[Memory] Experience extraction failed: {e}")

                # Back-fill bidirectional links between episode, memories, and turns
                if ep_id:
                    try:
                        if saved_memory_ids:
                            self.store.update_episode(ep_id, {"linked_memory_ids": saved_memory_ids})
                        linked = self.store.link_turns_to_episode(session_id, ep_id)
                        logger.info(
                            f"[Memory] Episode links: {len(saved_memory_ids)} memories, "
                            f"{linked} turns linked to {ep_id[:8]}"
                        )
                    except Exception as e:
                        logger.warning(f"[Memory] Failed to back-fill episode links: {e}")

                self._pending_tasks.discard(task)

            task = loop.create_task(_finalize_session())
            self._pending_tasks.add(task)
        except RuntimeError:
            self._enqueue_session_turns_for_extraction(session_id, turns)

        try:
            self.store.db.cleanup_expired()
        except Exception:
            pass

        logger.info(f"Ended session {session_id}: finalization scheduled")
        self._current_session_id = None
        self._session_turns = []

    def _enqueue_session_turns_for_extraction(
        self, session_id: str, turns: list[ConversationTurn]
    ) -> None:
        """Fallback: 将会话 turns 入队提取（用于无 event loop 的同步场景）"""
        try:
            enqueued = 0
            for i, turn in enumerate(turns):
                if turn.content and len(turn.content) >= 20:
                    self.store.enqueue_extraction(
                        session_id=session_id,
                        turn_index=i,
                        content=turn.content,
                        tool_calls=turn.tool_calls or None,
                        tool_results=turn.tool_results or None,
                    )
                    enqueued += 1
            if enqueued:
                logger.info(f"[Memory] Enqueued {enqueued} turns for deferred extraction (no event loop)")
        except Exception as e:
            logger.warning(f"[Memory] Failed to enqueue session turns: {e}")

    async def await_pending_tasks(self, timeout: float = 30.0) -> None:
        """等待所有挂起的异步任务完成（在 shutdown 时调用）"""
        if not self._pending_tasks:
            return
        pending = list(self._pending_tasks)
        logger.info(f"[Memory] Awaiting {len(pending)} pending tasks (timeout={timeout}s)...")
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        if not_done:
            logger.warning(f"[Memory] {len(not_done)} tasks did not complete within timeout")
            for t in not_done:
                t.cancel()
        self._pending_tasks.clear()

    def _safe_enqueue_extraction(
        self,
        session_id: str | None,
        turn_index: int,
        content: str,
        tool_calls: list | None = None,
        tool_results: list | None = None,
    ) -> None:
        """安全入队提取 — 捕获所有异常，永不抛出"""
        try:
            sid = session_id or self._current_session_id or "unknown"
            self.store.enqueue_extraction(
                session_id=sid,
                turn_index=turn_index,
                content=content,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
            logger.info(f"[Memory] Enqueued extraction for retry: session={sid}, turn={turn_index}")
        except Exception as e:
            # 最终 fallback: 写到本地文件，防止数据永久丢失
            try:
                fallback_dir = self.data_dir / "extraction_fallback"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                import json
                from datetime import datetime

                fallback_file = fallback_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{turn_index}.json"
                fallback_file.write_text(
                    json.dumps(
                        {"session_id": session_id, "turn_index": turn_index, "content": content},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                logger.warning(f"[Memory] Enqueue failed ({e}), saved to fallback file: {fallback_file}")
            except Exception as e2:
                logger.error(f"[Memory] Both enqueue and fallback failed: enqueue={e}, fallback={e2}")

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

            if self.vector_store is not None and self.vector_store.enabled and len(self._memories) > 0:
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

            if self.vector_store is not None:
                self.vector_store.add_memory(
                    memory_id=memory.id,
                    content=memory.content,
                    memory_type=memory.type.value,
                    priority=memory.priority.value,
                    importance=memory.importance_score,
                    tags=memory.tags,
                )

        # v2: set TTL then save to SQLite + FTS
        _apply_retention(memory)
        sem = SemanticMemory(
            id=memory.id,
            type=memory.type,
            priority=memory.priority,
            content=memory.content,
            source=memory.source,
            importance_score=memory.importance_score,
            tags=memory.tags,
        )
        if hasattr(memory, "expires_at"):
            sem.expires_at = memory.expires_at
        self.store.save_semantic(sem)

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
                if self.vector_store is not None:
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
            result = await lifecycle.consolidate_daily()
        except Exception as e:
            logger.error(f"[Manager] Daily consolidation failed, using legacy: {e}")
            from ..config import settings
            from .daily_consolidator import DailyConsolidator
            dc = DailyConsolidator(
                data_dir=self.data_dir,
                memory_md_path=self.memory_md_path,
                memory_manager=self,
                brain=self.brain,
                identity_dir=settings.identity_path,
            )
            result = await dc.consolidate_daily()

        # After consolidation, sync SQLite → in-memory cache → JSON
        self._reload_from_sqlite()
        return result

    def _reload_from_sqlite(self) -> None:
        """Reload in-memory cache from SQLite and flush to JSON."""
        try:
            all_mems = self.store.load_all_memories()
            with self._memories_lock:
                self._memories.clear()
                for m in all_mems:
                    self._memories[m.id] = m
            self._save_memories()
            logger.debug(f"[Manager] Synced {len(all_mems)} memories: SQLite → cache → JSON")
        except Exception as e:
            logger.warning(f"[Manager] SQLite→JSON sync failed: {e}")

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
                    if self.vector_store is not None:
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
