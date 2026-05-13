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
import contextvars
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from ..core.log_health import record_health_event
from .consolidator import MemoryConsolidator
from .extractor import MemoryExtractor
from .json_utils import coerce_text
from .retention import apply_retention
from .retrieval import RetrievalEngine
from .types import (
    Attachment,
    AttachmentDirection,
    ConversationTurn,
    Memory,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
    normalize_tags,
)
from .unified_store import UnifiedStore
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


_apply_retention = apply_retention
_UNSET_OWNER = object()


class MemoryManager:
    """记忆管理器 (v2)"""

    @property
    def _current_session_id(self) -> str | None:
        return self._current_session_id_var.get()

    @_current_session_id.setter
    def _current_session_id(self, value: str | None) -> None:
        self._current_session_id_var.set(value)

    @property
    def _current_user_id(self) -> str:
        return self._current_user_id_var.get()

    @_current_user_id.setter
    def _current_user_id(self, value: str) -> None:
        self._current_user_id_var.set(value or "default")

    @property
    def _current_workspace_id(self) -> str:
        return self._current_workspace_id_var.get()

    @_current_workspace_id.setter
    def _current_workspace_id(self, value: str) -> None:
        self._current_workspace_id_var.set(value or "default")

    @property
    def _session_turns(self) -> list[ConversationTurn]:
        turns = self._session_turns_var.get()
        if turns is None:
            turns = []
            self._session_turns_var.set(turns)
        return turns

    @_session_turns.setter
    def _session_turns(self, value: list[ConversationTurn]) -> None:
        self._session_turns_var.set(value)

    @property
    def _recent_messages(self) -> list[dict]:
        messages = self._recent_messages_var.get()
        if messages is None:
            messages = []
            self._recent_messages_var.set(messages)
        return messages

    @_recent_messages.setter
    def _recent_messages(self, value: list[dict]) -> None:
        self._recent_messages_var.set(value)

    @property
    def _session_cited_memories(self) -> list[dict]:
        memories = self._session_cited_memories_var.get()
        if memories is None:
            memories = []
            self._session_cited_memories_var.set(memories)
        return memories

    @_session_cited_memories.setter
    def _session_cited_memories(self, value: list[dict]) -> None:
        self._session_cited_memories_var.set(value)

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
        agent_id: str = "",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.agent_id = agent_id

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

        # v3: Relational Memory (Mode 2) — initialized lazily on first use
        self.relational_store = None
        self.relational_encoder = None
        self.relational_graph = None
        self.relational_consolidator = None
        self._relational_pending_nodes = []

        # v1 compat: in-memory cache
        self.memories_file = self.data_dir / "memories.json"
        self._memories: dict[str, Memory] = {}
        self._memories_lock = threading.RLock()

        self._current_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"openakita_memory_session_id_{id(self)}",
            default=None,
        )
        self._current_user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
            f"openakita_memory_user_id_{id(self)}",
            default="default",
        )
        self._current_workspace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
            f"openakita_memory_workspace_id_{id(self)}",
            default="default",
        )
        self._session_turns_var: contextvars.ContextVar[list[ConversationTurn] | None] = (
            contextvars.ContextVar(f"openakita_memory_session_turns_{id(self)}", default=None)
        )
        self._recent_messages_var: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
            f"openakita_memory_recent_messages_{id(self)}",
            default=None,
        )
        self._session_cited_memories_var: contextvars.ContextVar[list[dict] | None] = (
            contextvars.ContextVar(f"openakita_memory_cited_{id(self)}", default=None)
        )

        self._current_session_id: str | None = None
        self._current_user_id: str = "default"
        self._current_workspace_id: str = "default"
        self._session_turns: list[ConversationTurn] = []
        self._recent_messages: list[dict] = []

        # Citation tracking: memories retrieved via search_memory this session
        self._session_cited_memories: list[dict] = []

        # Track pending async tasks to await on shutdown
        self._pending_tasks: set[asyncio.Task] = set()

        # Global store fallback for isolated agents (set by AgentFactory)
        self._global_store_ref: UnifiedStore | None = None

        # Plugin-provided memory backends (shared dict from host_refs)
        self._plugin_backends: dict | None = None

        # Load existing memories
        self._load_memories()

    def _stamp_agent_id(self, mem: Memory) -> Memory:
        """Set agent_id on a memory if not already set."""
        if self.agent_id and not mem.agent_id:
            mem.agent_id = self.agent_id
        return mem

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
        and legacy JSON will be backfilled into SQLite when needed.
        """
        try:
            all_mems = self.store.load_all_memories()
            migrated = self._backfill_legacy_json_memories(all_mems)
            if migrated > 0:
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

    def _backfill_legacy_json_memories(self, existing_mems: list[Memory]) -> int:
        """Backfill old memories.json into SQLite when SQLite is incomplete.

        This protects users upgrading from old versions where memories were
        primarily persisted in JSON.
        """
        if not self.memories_file.exists():
            return 0

        try:
            raw = json.loads(self.memories_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[Manager] Failed to read legacy memories.json: {e}")
            return 0

        if not isinstance(raw, list) or not raw:
            return 0

        existing_ids = {m.id for m in existing_mems if getattr(m, "id", "")}
        if len(existing_ids) >= len(raw):
            return 0

        existing_fingerprints = {
            (
                (getattr(m, "subject", "") or "").strip().lower(),
                (getattr(m, "predicate", "") or "").strip().lower(),
                (getattr(m, "content", "") or "").strip(),
            )
            for m in existing_mems
            if (getattr(m, "content", "") or "").strip()
        }

        migrated = 0
        skipped = 0
        for item in raw:
            if not isinstance(item, dict):
                skipped += 1
                continue

            try:
                mem = Memory.from_dict(item)
            except Exception:
                content = str(item.get("content", "")).strip()
                if not content:
                    skipped += 1
                    continue
                mem = Memory(
                    content=content,
                    type=MemoryType.FACT,
                    priority=MemoryPriority.SHORT_TERM,
                    source=str(item.get("source", "legacy_json")),
                    subject=str(item.get("subject", "")).strip(),
                    predicate=str(item.get("predicate", "")).strip(),
                    importance_score=float(item.get("importance_score", 0.5) or 0.5),
                )

            if not (mem.content or "").strip():
                skipped += 1
                continue

            fingerprint = (
                (mem.subject or "").strip().lower(),
                (mem.predicate or "").strip().lower(),
                (mem.content or "").strip(),
            )
            if mem.id in existing_ids or fingerprint in existing_fingerprints:
                skipped += 1
                continue

            self.store.save_semantic(
                self._stamp_agent_id(mem),
                scope="legacy_quarantine",
                scope_owner="",
                user_id="legacy",
                workspace_id=self._current_workspace_id,
            )
            existing_ids.add(mem.id)
            existing_fingerprints.add(fingerprint)
            migrated += 1

        if migrated:
            logger.info(
                f"[Manager] Backfilled {migrated} memories from legacy JSON "
                f"(skipped={skipped}, sqlite_before={len(existing_mems)}, json_total={len(raw)})"
            )
        return migrated

    def _save_memories(self) -> None:
        """Save to memories.json (backward compat, dual-write)"""
        import tempfile

        try:
            with self._memories_lock:
                data = [m.to_dict() for m in self._memories.values()]
            target_dir = self.memories_file.parent
            fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", prefix="memories_", dir=target_dir
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.memories_file)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"Failed to save memories.json: {e}")

    async def _save_memories_async(self) -> None:
        await asyncio.to_thread(self._save_memories)

    # ==================== Session Management ====================

    def start_session(
        self,
        session_id: str,
        *,
        user_id: str | None | object = _UNSET_OWNER,
        workspace_id: str | None | object = _UNSET_OWNER,
        focus_terms: list[str] | None = None,
    ) -> None:
        self._current_session_id = session_id
        if user_id is not _UNSET_OWNER:
            self._current_user_id = str(user_id).strip() if user_id else "anonymous"
        if workspace_id is not _UNSET_OWNER:
            self._current_workspace_id = str(workspace_id).strip() if workspace_id else "default"
        # P1-5：每次切换会话时显式记录当前 (user_id, workspace_id) 范围，
        # 让运维能从日志直接看出"本会话能看见的长期记忆来自哪个租户"，
        # 排查跨用户串扰时不必再去翻代码或 DB。
        # 同时 user_id 仍为默认 "default" 时降级为 warning：在多用户 IM 通道下
        # 这往往意味着上游忘了把真实 OpenID 传下来，会导致所有人共用同一份长期记忆。
        if self._current_user_id in ("default", "anonymous", ""):
            logger.warning(
                "[Memory] start_session(%s) using fallback user_id=%r workspace_id=%r — "
                "long-term memories will be shared across all 'default' callers; "
                "upstream channel/API entry should pass a real user_id to enforce tenant isolation.",
                session_id, self._current_user_id, self._current_workspace_id,
            )
        else:
            logger.info(
                "[Memory] start_session(%s) tenant=(user=%s workspace=%s)",
                session_id, self._current_user_id, self._current_workspace_id,
            )
        self._session_turns = []
        self._recent_messages = []
        self._session_cited_memories = []
        self._set_retrieval_scope_context()
        if hasattr(self.retrieval_engine, "set_focus_terms"):
            self.retrieval_engine.set_focus_terms(focus_terms or [])
        snapshot = getattr(self, "_precompact_snapshot", None)
        if isinstance(snapshot, dict) and snapshot.get("session_id") != session_id:
            self._precompact_snapshot = None
        try:
            self._turn_offset = self.store.get_max_turn_index(session_id)
        except Exception:
            self._turn_offset = 0
        if self._turn_offset > 0:
            logger.info(
                f"[Memory] start_session({session_id}): resuming at turn_offset={self._turn_offset}"
            )

        backends = self._iter_memory_backends()
        if backends:
            with contextlib.suppress(Exception):
                loop = asyncio.get_event_loop()
                for backend in backends:
                    start = getattr(backend, "start_session", None)
                    if start:
                        loop.create_task(start(session_id))
        if self._get_replace_backend() is None:
            logger.debug(f"[Memory] start_session({session_id}): fresh session (offset=0)")

    def _visible_scope_entries(self) -> list[tuple[str, str, str, str]]:
        """Return scopes visible to the current turn, ordered from private to shared.

        会话记忆优先，随后是当前用户长期记忆，最后是系统记忆。
        legacy_quarantine 永不默认进入推理链。
        """
        current = (self._current_session_id or "").strip()
        user_id = self._current_user_id or "default"
        workspace_id = self._current_workspace_id or "default"
        entries: list[tuple[str, str, str, str]] = []
        if current:
            entries.append(("session", current, user_id, workspace_id))
        entries.append(("user", "", user_id, workspace_id))
        entries.append(("system", "", "system", workspace_id))
        return entries

    def _visible_scope_pairs(self) -> list[tuple[str, str]]:
        """Backward-compatible view for callers that only understand scope pairs."""
        return [(scope, owner) for scope, owner, _user, _workspace in self._visible_scope_entries()]

    def _set_retrieval_scope_context(self) -> None:
        retrieval_engine = getattr(self, "retrieval_engine", None)
        setter = getattr(retrieval_engine, "set_scope_context", None)
        if setter:
            setter(self._visible_scope_entries())

    def _current_write_scope(self) -> tuple[str, str]:
        current = (self._current_session_id or "").strip()
        if current:
            return "session", current
        return "user", ""

    def _current_owner(self) -> tuple[str, str]:
        return self._current_user_id or "default", self._current_workspace_id or "default"

    _IDENTITY_SLOT_ALIASES: dict[str, str] = {
        "姓名": "user.name",
        "名字": "user.name",
        "称呼": "user.name",
        "name": "user.name",
        "年龄": "user.age",
        "age": "user.age",
        "城市": "user.city",
        "所在地": "user.city",
        "位置": "user.city",
        "居住地": "user.city",
        "location": "user.city",
        "city": "user.city",
        "职业": "user.job",
        "工作": "user.job",
        "职位": "user.job",
        "job": "user.job",
        "profession": "user.job",
        "宠物": "user.pet",
        "pet": "user.pet",
    }

    @classmethod
    def _identity_slot_for(cls, memory: SemanticMemory) -> str:
        subject = (memory.subject or "").strip().lower()
        if subject not in {"用户", "user", "当前用户", "我"}:
            return ""
        predicate = (memory.predicate or "").strip().lower()
        for alias, slot in cls._IDENTITY_SLOT_ALIASES.items():
            if alias.lower() == predicate:
                return slot
        if predicate.startswith("preference.") or predicate.startswith("偏好."):
            return f"user.preference.{predicate.split('.', 1)[1]}"
        return ""

    def _save_identity_slot_memory(
        self,
        memory: SemanticMemory,
        *,
        scope: str,
        scope_owner: str,
        user_id: str,
        workspace_id: str,
        slot: str,
    ) -> str:
        """Save identity facts as active slots and supersede older conflicting values."""
        old_active = self.store.query_semantic(
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            limit=20,
            include_inactive=False,
        )
        memory.tags = sorted({*normalize_tags(memory.tags), "identity_slot", slot})
        self.store.save_semantic(
            self._stamp_agent_id(memory),
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            skip_dedup=True,
        )
        for old in old_active:
            if old.id == memory.id or old.superseded_by:
                continue
            if self._identity_slot_for(old) != slot:
                continue
            self.store.update_semantic(old.id, {"superseded_by": memory.id})
            with self._memories_lock:
                cached = self._memories.get(old.id)
                if cached:
                    cached.superseded_by = memory.id
        with self._memories_lock:
            self._memories[memory.id] = memory
            self._save_memories()
        return memory.id

    def _normalize_scope_for_owner(self, scope: str, source: str = "") -> tuple[str, str, str, str]:
        """Normalize legacy/global user writes into owner-scoped user memories."""
        norm_scope = (scope or "user").strip() or "user"
        if norm_scope == "global":
            norm_scope = "user"
        user_id, workspace_id = self._current_owner()
        if norm_scope == "system":
            user_id = "system"
        if norm_scope == "legacy_quarantine":
            user_id = "legacy"
        return norm_scope, source or "", user_id, workspace_id

    def save_user_memory(
        self,
        memory: SemanticMemory,
        *,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        skip_dedup: bool = False,
    ) -> str:
        """Save user-owned semantic memory through the single owner-aware path."""
        write_scope, write_owner = self._current_write_scope()
        if scope:
            write_scope = "user" if scope == "global" else scope
        if scope_owner is not None:
            write_owner = scope_owner
        if write_scope == "system":
            write_user = "system"
        elif write_scope == "legacy_quarantine":
            write_user = "legacy"
        else:
            write_user = user_id or self._current_user_id or "default"
        write_workspace = workspace_id or self._current_workspace_id or "default"
        memory.scope = write_scope
        memory.scope_owner = write_owner
        memory.user_id = write_user
        memory.workspace_id = write_workspace
        identity_slot = self._identity_slot_for(memory)
        if identity_slot:
            write_scope = "user"
            write_owner = ""
            memory.scope = write_scope
            memory.scope_owner = write_owner
            return self._save_identity_slot_memory(
                memory,
                scope=write_scope,
                scope_owner=write_owner,
                user_id=write_user,
                workspace_id=write_workspace,
                slot=identity_slot,
            )
        saved_id = self.store.save_semantic(
            self._stamp_agent_id(memory),
            scope=write_scope,
            scope_owner=write_owner,
            user_id=write_user,
            workspace_id=write_workspace,
            skip_dedup=skip_dedup,
        )
        if saved_id == memory.id:
            with self._memories_lock:
                self._memories[memory.id] = memory
                self._save_memories()
        return saved_id

    # 任务流水账识别：包含这些动词或客观操作描述的提取项几乎一定是
    # 一次性任务记录（删除 / 创建 / 上传 / 编辑 / 调用工具 / 帮我做 ...），
    # 不应被自动升级为 PERMANENT，否则 USER.md 会被任务日志污染（P1-7）。
    _TASK_MARKER_RE: re.Pattern[str] | None = None

    @classmethod
    def _task_marker_re_for_extraction(cls) -> re.Pattern[str]:
        if cls._TASK_MARKER_RE is None:
            cls._TASK_MARKER_RE = re.compile(
                r"(?:用户(?:希望|想|想要|要求|让|请|让我|让你|交给|安排)|"
                r"任务|操作|执行|完成|交付|生成|创建|删除|清理|清掉|搜索|查询|"
                r"整理|发送|上传|下载|导出|导入|配置|安装|卸载|启动|关闭|访问|"
                r"截图|录制|提交|推送|拉取|更新|修复|测试|部署|帮[我你他她]|"
                r"call_mcp_tool|run_shell|run_powershell|write_file|edit_file)"
            )
        return cls._TASK_MARKER_RE

    def query_visible_semantic(self, **kwargs) -> list[SemanticMemory]:
        """Query memories visible to the current turn without leaking other sessions."""
        limit = int(kwargs.pop("limit", 50) or 50)
        merged: list[SemanticMemory] = []
        seen: set[str] = set()
        per_scope_limit = max(limit, 1)
        for scope, scope_owner, user_id, workspace_id in self._visible_scope_entries():
            results = self.store.query_semantic(
                **kwargs,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
                limit=per_scope_limit,
            )
            for mem in results:
                if mem.id in seen:
                    continue
                seen.add(mem.id)
                merged.append(mem)
                if len(merged) >= limit:
                    return merged
        return merged

    def search_visible_semantic_scored(
        self,
        query: str,
        *,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[tuple[SemanticMemory, float]]:
        """Search current-session memories first, then explicitly global memories."""
        merged: list[tuple[SemanticMemory, float]] = []
        seen: set[str] = set()
        for scope, scope_owner, user_id, workspace_id in self._visible_scope_entries():
            scored = self.store.search_semantic_scored(
                query,
                limit=limit,
                filter_type=filter_type,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            for mem, score in scored:
                if mem.id in seen:
                    continue
                seen.add(mem.id)
                merged.append((mem, score))
                if len(merged) >= limit:
                    return merged
        return merged

    def search_visible_semantic(
        self,
        query: str,
        *,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[SemanticMemory]:
        return [
            mem
            for mem, _score in self.search_visible_semantic_scored(
                query,
                limit=limit,
                filter_type=filter_type,
            )
        ]

    def record_turn(
        self,
        role: str,
        content: str,
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
        content = coerce_text(content)

        backends = self._iter_memory_backends()
        if backends:
            with contextlib.suppress(Exception):
                loop = asyncio.get_event_loop()
                for backend in backends:
                    record = getattr(backend, "record_turn", None)
                    if record:
                        loop.create_task(record(role, content))

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

        带 30s 超时保护，防止后台提取任务挂起。

        Returns:
            提取并保存的记忆条数
        """
        turns = list(self._session_turns)
        if len(turns) < 3:
            return 0

        try:
            cited = self._consume_cited_memories()
            items, scores = await asyncio.wait_for(
                self.extractor.extract_from_conversation(turns, cited_memories=cited or None),
                timeout=30.0,
            )

            if scores:
                self._apply_citation_scores(scores)

            saved = 0
            for item in items:
                await self._save_extracted_item(item)
                saved += 1
            if saved:
                logger.info(
                    f"[Memory] Topic-change extraction: {saved} items saved from {len(turns)} turns"
                )
            # Reset turn buffer — new topic starts fresh
            self._session_turns.clear()
            return saved
        except TimeoutError:
            logger.warning("[Memory] Topic-change extraction timed out (30s), skipping")
            self._session_turns.clear()
            return 0
        except Exception as e:
            if record_health_event(
                "memory",
                "topic_change_extraction",
                str(e),
                suggestion="记忆抽取失败已降级跳过本轮，不影响聊天主链路；请检查当前 LLM 端点稳定性。",
            ):
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

        # PERMANENT 是最贵的层（不会被衰减、永远进 USER.md / MEMORY.md），
        # 因此只允许 *用户身份层* 的记忆走到这里。否则一次任务记录就被永久化，
        # USER.md 会被「[experience] 用户希望删除工作区 py 文件」这种一次性
        # 流水账填满（P1-7）。
        _persona_types = {
            MemoryType.PERSONA_TRAIT,
            MemoryType.PREFERENCE,
            MemoryType.RULE,
        }
        # 内容含有任务/操作动词 → 几乎一定是任务流水账，不是身份层信息。
        _task_marker_re = self._task_marker_re_for_extraction()
        _looks_like_task = bool(_task_marker_re.search(content)) if content else False

        if (
            importance >= 0.9
            and mem_type in _persona_types
            and not _looks_like_task
        ):
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        write_scope, write_owner = self._current_write_scope()
        write_user, write_workspace = self._current_owner()

        # Dedup layer 1: exact subject+predicate match → evolve existing
        subject = item.get("subject", "")
        predicate = item.get("predicate", "")
        self._sync_profile_fact(subject, predicate, content)
        _identity_probe = SemanticMemory(subject=subject, predicate=predicate, content=content)
        if subject and predicate and not self._identity_slot_for(_identity_probe):
            existing = self.store.find_similar(
                subject,
                predicate,
                scope=write_scope,
                scope_owner=write_owner,
                user_id=write_user,
                workspace_id=write_workspace,
            )
            if existing:
                self._evolve_memory(existing, content, importance)
                logger.debug(f"[Memory] Dedup L1: evolved {existing.id[:8]} (subject+predicate)")
                return existing.id

        # Dedup layer 2: content similarity search
        if content and len(content) >= 10:
            try:
                similar = self.store.search_semantic(
                    content,
                    limit=5,
                    scope=write_scope,
                    scope_owner=write_owner,
                    user_id=write_user,
                    workspace_id=write_workspace,
                )
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
                            logger.debug(
                                f"[Memory] Dedup L2: LLM confirmed dup, evolved {s.id[:8]}"
                            )
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
        saved_id = self.save_user_memory(
            mem,
            scope=write_scope,
            scope_owner=write_owner,
            user_id=write_user,
            workspace_id=write_workspace,
        )

        return saved_id

    def _sync_profile_fact(self, subject: str, predicate: str, content: str) -> None:
        """Mirror structured user identity facts into UserProfileManager when possible."""
        if not content:
            return
        if (subject or "").strip().lower() not in {"用户", "user", "当前用户"}:
            return
        profile_mgr = getattr(self, "profile_manager", None)
        if profile_mgr is None:
            return
        try:
            from openakita.core.user_profile import resolve_profile_key
        except Exception:
            return
        key = resolve_profile_key((predicate or "").strip())
        available = set(getattr(profile_mgr, "get_available_keys", lambda: [])())
        if key not in available:
            return
        value = self._extract_profile_value(key, content)
        if value:
            with contextlib.suppress(Exception):
                profile_mgr.update_profile(key, value)

    @staticmethod
    def _extract_profile_value(key: str, content: str) -> str:
        import re

        text = (content or "").strip()
        if key == "age":
            m = re.search(r"(\d{1,3})\s*岁?", text)
            return m.group(1) if m else ""
        if key in {"city", "location"}:
            m = re.search(r"(?:在|位于|住在|居住在|城市[是为:]?)\s*([^，。；,;\s]+)", text)
            return m.group(1) if m else text[:40]
        if key in {"name", "profession", "work_field", "preferred_language"}:
            m = re.search(r"(?:叫|是|为|使用|喜欢)\s*([^，。；,;\s]+)", text)
            return m.group(1) if m else text[:40]
        return text[:80]

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
            bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)}
            bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)}
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
                enable_thinking=False,
                max_tokens=16,
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
        # Same subject+predicate with a different value is an update/conflict,
        # not a duplicate. Always move the active fact forward so "28 -> 29"
        # cannot be swallowed by near-duplicate logic.
        should_update_content = bool(new_content and new_content != (existing.content or ""))
        if should_update_content:
            updates["content"] = new_content
        updates["importance_score"] = max(existing.importance_score, new_importance)

        self.store.update_semantic(existing.id, updates)

    # ==================== Relational Memory (Mode 2) ====================

    def _ensure_relational(self) -> bool:
        """Lazily initialize relational memory components. Returns True if available."""
        if self.relational_store is not None:
            return True
        try:
            from .relational.consolidator import RelationalConsolidator
            from .relational.encoder import MemoryEncoder
            from .relational.entity_resolver import EntityResolver
            from .relational.graph_engine import GraphEngine
            from .relational.store import RelationalMemoryStore

            conn = self.store.db._conn
            if conn is None:
                return False

            try:
                from openakita.config import settings as _cfg
                ui_lang = getattr(_cfg, "ui_language", "zh")
            except Exception:
                ui_lang = "zh"

            self.relational_store = RelationalMemoryStore(conn)
            self.relational_encoder = MemoryEncoder(
                brain=self.brain,
                session_id=self._current_session_id or "",
                language=ui_lang,
            )
            self.relational_graph = GraphEngine(self.relational_store)
            resolver = EntityResolver(
                self.relational_store, brain=self.brain, language=ui_lang
            )
            self.relational_consolidator = RelationalConsolidator(
                self.relational_store, entity_resolver=resolver
            )
            logger.info("[Memory] Relational memory (Mode 2) initialized")
            return True
        except Exception as e:
            logger.debug(f"[Memory] Relational memory init skipped: {e}")
            return False

    def _get_memory_mode(self) -> str:
        """Read memory_mode from config. Defaults to 'auto'."""
        try:
            from openakita.config import settings

            return getattr(settings, "memory_mode", "auto")
        except Exception:
            return "auto"

    def end_session(
        self, task_description: str = "", success: bool = True, errors: list | None = None
    ) -> None:
        """结束会话: 生成 Episode + 双轨提取（用户画像 + 任务经验）+ 引用评分"""
        if not self._current_session_id:
            return

        backends = self._iter_memory_backends()
        if backends:
            with contextlib.suppress(Exception):
                loop_for_backends = asyncio.get_event_loop()
                for backend in backends:
                    end = getattr(backend, "end_session", None)
                    if end:
                        loop_for_backends.create_task(end())

        session_id = self._current_session_id
        turns = list(self._session_turns)
        cited = self._consume_cited_memories()

        relational_pending_snapshot = list(self._relational_pending_nodes)
        self._relational_pending_nodes.clear()

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
                    if record_health_event(
                        "memory",
                        "episode_generation",
                        str(e),
                        suggestion="会话 episode 生成失败已跳过；通常是 LLM 连接瞬断。",
                    ):
                        logger.warning(f"[Memory] Episode generation failed: {e}")

                ep_id = episode.id if episode else None
                saved_memory_ids: list[str] = []

                # Track 1: User profile extraction (+ citation scoring in same LLM call)
                try:
                    items, scores = await asyncio.wait_for(
                        self.extractor.extract_from_conversation(
                            turns,
                            cited_memories=cited or None,
                        ),
                        timeout=30.0,
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
                        logger.info(
                            f"[Memory] Profile extraction: {saved}/{len(items)} items saved"
                        )
                except Exception as e:
                    if record_health_event(
                        "memory",
                        "profile_extraction",
                        str(e),
                        suggestion="用户画像抽取失败已跳过本轮；主聊天不受影响。",
                    ):
                        logger.warning(f"[Memory] Profile extraction failed: {e}")

                # Track 2: Task experience extraction
                try:
                    exp_items = await asyncio.wait_for(
                        self.extractor.extract_experience_from_conversation(turns),
                        timeout=30.0,
                    )
                    exp_saved = 0
                    for item in exp_items:
                        mid = await self._save_extracted_item(item, episode_id=ep_id)
                        if mid:
                            saved_memory_ids.append(mid)
                        exp_saved += 1
                    if exp_saved:
                        logger.info(
                            f"[Memory] Experience extraction: {exp_saved}/{len(exp_items)} items saved"
                        )
                except Exception as e:
                    if record_health_event(
                        "memory",
                        "experience_extraction",
                        str(e),
                        suggestion="经验抽取失败已跳过本轮；可稍后手动整理记忆。",
                    ):
                        logger.warning(f"[Memory] Experience extraction failed: {e}")

                # Back-fill bidirectional links between episode, memories, and turns
                if ep_id:
                    try:
                        if saved_memory_ids:
                            self.store.update_episode(
                                ep_id, {"linked_memory_ids": saved_memory_ids}
                            )
                        linked = self.store.link_turns_to_episode(session_id, ep_id)
                        logger.info(
                            f"[Memory] Episode links: {len(saved_memory_ids)} memories, "
                            f"{linked} turns linked to {ep_id[:8]}"
                        )
                    except Exception as e:
                        logger.warning(f"[Memory] Failed to back-fill episode links: {e}")

                # Relational memory (Mode 2) — batch encode at session end
                mode = self._get_memory_mode()
                if mode in ("mode2", "auto") and self._ensure_relational():
                    try:
                        turn_dicts = [
                            {
                                "role": t.role,
                                "content": t.content,
                                "tool_calls": t.tool_calls,
                                "tool_results": t.tool_results,
                            }
                            for t in turns
                        ]
                        existing = relational_pending_snapshot
                        result = await self.relational_encoder.encode_session(
                            turn_dicts,
                            existing_nodes=existing or None,
                            session_id=session_id,
                        )
                        if result.nodes:
                            for n in result.nodes:
                                if self.agent_id and not n.agent_id:
                                    n.agent_id = self.agent_id
                            self.relational_store.save_nodes_batch(result.nodes)
                        if result.edges:
                            self.relational_store.save_edges_batch(result.edges)
                        if result.nodes or result.edges:
                            logger.info(
                                f"[Memory] Relational encoding: "
                                f"{len(result.nodes)} nodes, {len(result.edges)} edges"
                            )
                    except Exception as e:
                        logger.warning(f"[Memory] Relational session encoding failed: {e}")

            task = loop.create_task(_finalize_session())
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except RuntimeError:
            self._enqueue_session_turns_for_extraction(session_id, turns)

        try:
            self.store.cleanup_expired()
        except Exception:
            pass

        logger.info(f"Ended session {session_id}: finalization scheduled")
        self._current_session_id = None
        self._session_turns = []
        self._set_retrieval_scope_context()

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
                logger.info(
                    f"[Memory] Enqueued {enqueued} turns for deferred extraction (no event loop)"
                )
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

                fallback_file = (
                    fallback_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{turn_index}.json"
                )
                fallback_file.write_text(
                    json.dumps(
                        {"session_id": session_id, "turn_index": turn_index, "content": content},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                logger.warning(
                    f"[Memory] Enqueue failed ({e}), saved to fallback file: {fallback_file}"
                )
            except Exception as e2:
                logger.error(
                    f"[Memory] Both enqueue and fallback failed: enqueue={e}, fallback={e2}"
                )

    # ==================== Context Compression Hook ====================

    async def on_context_compressing(self, messages: list[dict]) -> None:
        """Called before context compression — extract quick facts and save to queue."""
        quick_facts = self.extractor.extract_quick_facts(messages)
        write_scope, write_owner = self._current_write_scope()
        write_user, write_workspace = self._current_owner()
        for fact in quick_facts:
            self.save_user_memory(
                fact,
                scope=write_scope,
                scope_owner=write_owner,
                user_id=write_user,
                workspace_id=write_workspace,
            )
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

        # Relational memory (Mode 2) — quick encode before messages are lost
        mode = self._get_memory_mode()
        if mode in ("mode2", "auto") and self._ensure_relational():
            try:
                result = self.relational_encoder.encode_quick(
                    messages, self._current_session_id or ""
                )
                if result.nodes:
                    for n in result.nodes:
                        if self.agent_id and not n.agent_id:
                            n.agent_id = self.agent_id
                        if not getattr(n, "user_id", "") or n.user_id == "default":
                            n.user_id = write_user
                        if not getattr(n, "workspace_id", "") or n.workspace_id == "default":
                            n.workspace_id = write_workspace
                    self.relational_store.save_nodes_batch(result.nodes)
                    self._relational_pending_nodes.extend(result.nodes)
                if result.edges:
                    self.relational_store.save_edges_batch(result.edges)
                if result.nodes:
                    logger.info(
                        f"[Memory] Relational quick encode: "
                        f"{len(result.nodes)} nodes, {len(result.edges)} edges"
                    )
            except Exception as e:
                logger.warning(f"[Memory] Relational quick encode failed: {e}")

    async def on_summary_generated(self, summary: str) -> None:
        """Called after context compression generates a summary — Layer 2 backfill."""
        mode = self._get_memory_mode()
        if mode not in ("mode2", "auto") or not self._ensure_relational():
            return
        pending = list(self._relational_pending_nodes)
        if not pending or not summary:
            return
        try:
            result = self.relational_encoder.backfill_from_summary(summary, pending)
            if result.nodes:
                for n in result.nodes:
                    if self.agent_id and not n.agent_id:
                        n.agent_id = self.agent_id
                self.relational_store.save_nodes_batch(result.nodes)
            if result.edges:
                self.relational_store.save_edges_batch(result.edges)
            if result.nodes or result.edges:
                logger.info(
                    f"[Memory] Relational backfill from summary: "
                    f"{len(result.nodes)} nodes, {len(result.edges)} edges"
                )
        except Exception as e:
            logger.warning(f"[Memory] Relational backfill failed: {e}")

    # ==================== Memory CRUD (v1 compat) ====================

    DUPLICATE_DISTANCE_THRESHOLD = 0.12

    COMMON_PREFIXES = [
        "任务执行复盘发现问题：",
        "任务执行复盘：",
        "复盘发现：",
        "系统自检发现：",
        "自检发现的典型问题模式：",
        "系统自检发现的典型问题模式：",
        "用户偏好：",
        "用户习惯：",
        "学习到：",
        "记住：",
    ]

    def _strip_common_prefix(self, content: str) -> str:
        for prefix in self.COMMON_PREFIXES:
            if content.startswith(prefix):
                return content[len(prefix) :]
        return content

    def add_memory(
        self,
        memory: Memory,
        scope: str = "user",
        scope_owner: str = "",
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        """添加记忆 (v1 compat: writes to both v1 and v2 stores)"""
        if scope == "global":
            scope = "user"
        if scope == "system":
            user_id = "system"
        elif scope == "legacy_quarantine":
            user_id = "legacy"
        else:
            user_id = user_id or self._current_user_id or "default"
        workspace_id = workspace_id or self._current_workspace_id or "default"
        memory.scope = scope
        memory.scope_owner = scope_owner
        memory.user_id = user_id
        memory.workspace_id = workspace_id
        with self._memories_lock:
            existing = [
                m
                for m in self._memories.values()
                if (getattr(m, "scope", "global") or "global") == scope
                and (getattr(m, "scope_owner", "") or "") == scope_owner
                and (getattr(m, "user_id", "default") or "default") == user_id
                and (getattr(m, "workspace_id", "default") or "default") == workspace_id
            ]
            unique = self.extractor.deduplicate([memory], existing)
            if not unique:
                return ""
            memory = unique[0]
            memory.scope = scope
            memory.scope_owner = scope_owner

            if (
                self.vector_store is not None
                and self.vector_store.enabled
                and len(self._memories) > 0
            ):
                core_content = self._strip_common_prefix(memory.content)
                similar = self.vector_store.search(core_content, limit=3)
                for mid, distance in similar:
                    if distance < self.DUPLICATE_DISTANCE_THRESHOLD:
                        existing_mem = self._memories.get(mid)
                        if existing_mem:
                            if (
                                (getattr(existing_mem, "scope", "global") or "global") != scope
                                or (getattr(existing_mem, "scope_owner", "") or "") != scope_owner
                                    or (getattr(existing_mem, "user_id", "default") or "default")
                                    != user_id
                                    or (
                                        getattr(existing_mem, "workspace_id", "default")
                                        or "default"
                                    )
                                    != workspace_id
                            ):
                                continue
                            existing_core = self._strip_common_prefix(existing_mem.content)
                            if core_content != existing_core:
                                continue
                            return ""
            elif len(self._memories) > 0:
                try:
                    core_content = self._strip_common_prefix(memory.content)
                    fts_hits = self.store.search_semantic(
                        core_content,
                        limit=5,
                        scope=scope,
                        scope_owner=scope_owner,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                    core_lower = core_content.strip()[:80].lower()
                    for hit in fts_hits:
                        if hit.content and core_lower in hit.content.lower():
                            return ""
                except Exception:
                    pass

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
            subject=getattr(memory, "subject", "") or "",
            predicate=getattr(memory, "predicate", "") or "",
            importance_score=memory.importance_score,
            tags=memory.tags,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        if hasattr(memory, "expires_at"):
            sem.expires_at = memory.expires_at
        self.store.save_semantic(
            self._stamp_agent_id(sem),
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            skip_dedup=True,
        )

        backends = self._iter_memory_backends()
        if backends:
            with contextlib.suppress(Exception):
                loop = asyncio.get_event_loop()
                for backend in backends:
                    store = getattr(backend, "store", None)
                    if store:
                        loop.create_task(store(sem.to_dict()))

        # MDRM 同步：高重要性事实立即上图，避免只有等到下一次 quick_encode/
        # consolidate 时才能被关系召回（小白用户的"再问一次"路径会走这里）。
        try:
            if (
                memory.importance_score >= 0.6
                and self._get_memory_mode() in ("mode2", "auto")
                and self._ensure_relational()
            ):
                from .relational.types import MemoryNode, NodeType

                _node_type = (
                    NodeType.FACT
                    if memory.type.value in ("fact", "preference", "rule")
                    else NodeType.EVENT
                )
                node = MemoryNode(
                    id=memory.id,
                    content=memory.content,
                    node_type=_node_type,
                    session_id=self._current_session_id or "",
                    agent_id=self.agent_id or "",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    importance=memory.importance_score,
                    confidence=0.7,
                )
                self.relational_store.save_nodes_batch([node])
        except Exception as _rel_err:
            logger.debug(f"[Memory] relational upsert skipped (non-fatal): {_rel_err}")

        logger.debug(f"Added memory: {memory.id} - {memory.content}")
        return memory.id

    def get_memory(self, memory_id: str) -> Memory | None:
        with self._memories_lock:
            memory = self._memories.get(memory_id)
            if memory:
                now = datetime.now()
                if memory.superseded_by or (memory.expires_at and memory.expires_at < now):
                    return None
                memory.access_count += 1
                memory.updated_at = datetime.now()
            return memory

    def search_memories(
        self,
        query: str = "",
        memory_type: MemoryType | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
        scope: str = "user",
        scope_owner: str = "",
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Memory]:
        results = []
        now = datetime.now()
        if scope == "global":
            scope = "user"
        user_id = user_id or (
            "system" if scope == "system" else "legacy" if scope == "legacy_quarantine" else self._current_user_id
        ) or "default"
        workspace_id = workspace_id or self._current_workspace_id or "default"
        with self._memories_lock:
            for memory in self._memories.values():
                if memory.superseded_by:
                    continue
                if memory.expires_at and memory.expires_at < now:
                    continue
                mem_scope = getattr(memory, "scope", "global") or "global"
                if mem_scope == "global":
                    mem_scope = "user"
                mem_owner = getattr(memory, "scope_owner", "") or ""
                if mem_scope != scope or mem_owner != scope_owner:
                    continue
                if (getattr(memory, "user_id", "default") or "default") != user_id:
                    continue
                if (getattr(memory, "workspace_id", "default") or "default") != workspace_id:
                    continue
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

    # ==================== Plugin Memory Backends ====================

    def set_plugin_backends(self, backends: dict) -> None:
        """Bind the shared plugin memory_backends dict from host_refs."""
        self._plugin_backends = backends

    def _get_replace_backend(self):
        """Return the active replace-mode plugin backend, if any."""
        backends = getattr(self, "_plugin_backends", None)
        if not backends:
            return None
        for entry in backends.values():
            if isinstance(entry, dict) and entry.get("replace"):
                return entry.get("backend")
        return None

    def _iter_memory_backends(self) -> list:
        """Return all plugin-provided memory backends, replace and augment alike."""
        backends = getattr(self, "_plugin_backends", None)
        if not backends:
            return []
        result = []
        for entry in backends.values():
            if isinstance(entry, dict):
                backend = entry.get("backend")
                if backend is not None:
                    result.append(backend)
        return result

    # ==================== Injection (v1 compat) ====================

    def get_injection_context(
        self,
        task_description: str = "",
        max_related: int = 5,
        scope: str = "global",
        scope_owner: str = "",
    ) -> str:
        """v1 compat — prefer using builder.py's three-layer injection"""
        replace = self._get_replace_backend()
        if replace is not None:
            try:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        replace.get_injection_context(task_description, 700),
                    )
                    return future.result(timeout=10)
            except Exception:
                logger.warning(
                    "Replace-mode memory backend failed, falling back to built-in",
                    exc_info=True,
                )
        return self.retrieval_engine.retrieve(
            query=task_description,
            recent_messages=self._recent_messages,
            max_tokens=700,
        )

    def save_precompact_snapshot(self, snapshot: dict) -> None:
        """Store the latest session-scoped compaction snapshot."""
        if not isinstance(snapshot, dict):
            return
        self._precompact_snapshot = snapshot
        session_obj = getattr(self, "_current_session_obj", None)
        context = getattr(session_obj, "context", None)
        if context is not None and hasattr(context, "precompact_snapshot"):
            context.precompact_snapshot = snapshot

    def attach_session_context(self, session: object | None) -> None:
        """Attach current Session object so snapshots can be persisted with SessionContext."""
        self._current_session_obj = session
        context = getattr(session, "context", None)
        snapshot = getattr(context, "precompact_snapshot", None)
        if isinstance(snapshot, dict) and snapshot.get("facts"):
            self._precompact_snapshot = snapshot

    def get_precompact_snapshot_context(self, max_chars: int = 1200) -> str:
        """Return session-scoped compaction facts for prompt injection."""
        snapshot = getattr(self, "_precompact_snapshot", None)
        if not isinstance(snapshot, dict):
            return ""
        if snapshot.get("session_id") and snapshot.get("session_id") != self._current_session_id:
            return ""
        facts = [str(item).strip() for item in snapshot.get("facts", []) if str(item).strip()]
        if not facts:
            return ""
        return "\n".join(f"- {fact}" for fact in facts)[:max_chars]

    async def get_injection_context_async(
        self, task_description: str = "", scope: str = "global", scope_owner: str = ""
    ) -> str:
        replace = self._get_replace_backend()
        if replace is not None:
            try:
                return await asyncio.wait_for(
                    replace.get_injection_context(task_description, 700),
                    timeout=10,
                )
            except Exception:
                logger.warning(
                    "Replace-mode memory backend failed, falling back to built-in",
                    exc_info=True,
                )
        return await asyncio.to_thread(
            self.retrieval_engine.retrieve,
            task_description,
            self._recent_messages,
            None,
            700,
        )

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

    async def consolidate_daily(
        self,
        *,
        checkpoint: dict | None = None,
        checkpoint_callback=None,
        time_budget_seconds: int | None = None,
        review_max_batches: int | None = None,
    ) -> dict:
        """每日归纳 (v2: 委托给 LifecycleManager)"""
        try:
            from ..config import settings
            from .lifecycle import LifecycleManager

            lifecycle = LifecycleManager(
                store=self.store,
                extractor=self.extractor,
                identity_dir=settings.identity_path,
            )
            result = await lifecycle.consolidate_daily(
                checkpoint=checkpoint,
                checkpoint_callback=checkpoint_callback,
                time_budget_seconds=time_budget_seconds,
                review_max_batches=review_max_batches,
            )
            if (
                not result.get("partial")
                and self._get_memory_mode() in ("mode2", "auto")
                and self._ensure_relational()
            ):
                try:
                    relational_report = await self.relational_consolidator.consolidate()
                    result["relational_consolidation"] = relational_report
                except Exception as e:
                    logger.warning(f"[Manager] Relational consolidation failed: {e}")
                    result["relational_consolidation_error"] = str(e)
        except Exception as e:
            from ..llm.types import LLMError

            if isinstance(e, LLMError):
                self._reload_from_sqlite()
                raise  # LLM unavailable — legacy fallback would fail too
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
        logger.info(f"[Memory] Recorded attachment: {filename} ({direction}, {mime_type})")
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
            query=query,
            mime_type=mime_type,
            direction=direction,
            session_id=session_id,
            limit=limit,
        )

    def get_attachment(self, attachment_id: str) -> Attachment | None:
        return self.store.get_attachment(attachment_id)

    # ==================== Stats ====================

    def get_stats(self, scope: str = "global", scope_owner: str = "") -> dict:
        type_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {}
        for memory in self._memories.values():
            if scope != "global" or scope_owner:
                mem_scope = getattr(memory, "scope", "global") or "global"
                mem_owner = getattr(memory, "scope_owner", "") or ""
                if mem_scope != scope or mem_owner != scope_owner:
                    continue
            type_counts[memory.type.value] = type_counts.get(memory.type.value, 0) + 1
            priority_counts[memory.priority.value] = (
                priority_counts.get(memory.priority.value, 0) + 1
            )

        v2_stats = self.store.get_stats(scope=scope, scope_owner=scope_owner)

        total = sum(type_counts.values())
        return {
            "total": total,
            "by_type": type_counts,
            "by_priority": priority_counts,
            "sessions_today": len(self.consolidator.get_today_sessions()),
            "unprocessed_sessions": len(self.consolidator.get_unprocessed_sessions()),
            "v2_store": v2_stats,
        }
