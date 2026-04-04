"""
AgentFactory — 根据 AgentProfile 创建差异化 Agent 实例
AgentInstancePool — per-session + per-profile 实例管理 + 空闲回收

Pool key 格式: ``{session_id}::{profile_id}``
同一会话可持有多个不同 profile 的 Agent 实例并行运行。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .profile import AgentProfile, AgentType, SkillsMode

if TYPE_CHECKING:
    from openakita.core.agent import Agent

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT_SECONDS = 30 * 60  # 30 分钟空闲回收
_REAP_INTERVAL_SECONDS = 60  # 每分钟检查一次

# INCLUSIVE 模式下始终保留的基础系统工具。
# 所有子 Agent（含用户手动创建的）都需要这些工具才能正常工作。
# 只有浏览器、桌面控制、MCP、定时任务等专用工具需在 profile.skills 显式列出。
ESSENTIAL_TOOL_NAMES: frozenset[str] = frozenset({
    "run_shell", "read_file", "write_file", "list_directory",
    "web_search", "deliver_artifacts", "get_chat_history",
    "search_memory", "add_memory",
    "create_todo", "update_todo_step", "get_todo_status", "complete_todo",
    "list_skills", "get_skill_info",
    "get_tool_info", "set_task_timeout",
    "get_image_file", "get_voice_file",
})

ESSENTIAL_SYSTEM_SKILLS: frozenset[str] = frozenset({
    # 规划（多步任务的核心）
    "create-todo", "update-todo-step", "get-todo-status", "complete-todo",
    # 技能发现（渐进式披露入口 — 外部技能必须先 get_skill_info 读指令）
    "get-skill-info", "list-skills",
    # 文件系统（外部技能执行的基础 — 读指令→写代码→run-shell 执行）
    "run-shell", "read-file", "write-file", "list-directory",
    # IM 通道（接收用户输入、交付文件）
    "deliver-artifacts", "get-chat-history", "get-image-file", "get-voice-file",
    # 记忆
    "search-memory", "add-memory",
    # 信息检索
    "web-search",
    # 系统
    "get-tool-info", "set-task-timeout",
})


class _GlobalStoreSource:
    """Adapter that exposes a global UnifiedStore as a RetrievalEngine external source.

    Used by isolated-memory agents with memory_inherit_global=True to also
    retrieve from the shared global memory during search.

    RetrievalEngine._call_external_sources_sync expects:
      - ``source.source_name: str``
      - ``async source.retrieve(query, limit) -> list[dict]``
        each dict with keys: id, content, relevance
    """

    source_name = "global_memory"

    def __init__(self, global_store):
        self._store = global_store

    async def retrieve(self, query: str, limit: int = 8) -> list[dict]:
        memories = self._store.search_semantic(query, limit=limit)
        results = []
        for mem in memories:
            results.append({
                "id": f"global::{mem.id}",
                "content": mem.to_markdown(),
                "relevance": 0.6,
            })
        return results


class AgentFactory:
    """
    根据 AgentProfile 创建 Agent 实例。

    - 按 profile 配置过滤技能
    - 注入自定义提示词
    - 设置 agent name/icon
    """

    async def create(
        self, profile: AgentProfile, *, parent_brain: Any = None, **kwargs: Any,
    ) -> Agent:
        from openakita.core.agent import Agent

        agent = Agent(name=profile.get_display_name(), brain=parent_brain, **kwargs)
        agent._agent_profile = profile

        await agent.initialize(start_scheduler=False, lightweight=True)

        self._apply_skill_filter(agent, profile)
        self._apply_tool_filter(agent, profile)
        self._apply_mcp_filter(agent, profile)
        await self._apply_plugin_filter(agent, profile)

        # Sync PromptAssembler catalog references after filtering.
        # _apply_tool_filter / _apply_mcp_filter may replace agent.tool_catalog /
        # mcp_catalog with new objects; the PromptAssembler still holds the old refs.
        pa = getattr(agent, "prompt_assembler", None)
        if pa is not None:
            pa._tool_catalog = agent.tool_catalog
            pa._mcp_catalog = agent.mcp_catalog

        # Rebuild the initial system prompt so it reflects the filtered catalogs.
        needs_rebuild = (
            (profile.tools_mode != "all" and profile.tools)
            or (profile.mcp_mode != "all" and profile.mcp_servers)
            or (profile.skills_mode != SkillsMode.ALL and profile.skills)
        )
        if needs_rebuild and hasattr(agent, "_context"):
            base_prompt = agent.identity.get_system_prompt()
            agent._context.system = agent._build_system_prompt(
                base_prompt, use_compiled=True,
            )

        # ── 身份隔离 ──
        if profile.identity_mode == "custom":
            self._apply_identity_override(agent, profile)

        # ── 记忆隔离 ──
        if profile.memory_mode == "isolated":
            self._apply_memory_isolation(agent, profile)

        # ── 权限规则注入 (MA1) ──
        if profile.permission_rules:
            try:
                from ..core.permission import from_config
                ruleset = from_config(
                    {r["permission"]: {r.get("pattern", "*"): r["action"]}
                     for r in profile.permission_rules
                     if "permission" in r and "action" in r}
                )
                if ruleset and hasattr(agent, "_tool_executor"):
                    agent._tool_executor._extra_permission_rules = ruleset
                    logger.info(
                        f"Injected {len(ruleset)} permission rule(s) from profile {profile.id}"
                    )
            except Exception as e:
                logger.warning(f"Failed to inject permission_rules for {profile.id}: {e}")

        if profile.custom_prompt:
            agent._custom_prompt_suffix = profile.custom_prompt

        if profile.preferred_endpoint:
            agent._preferred_endpoint = profile.preferred_endpoint

        logger.info(
            f"AgentFactory created: {profile.id} "
            f"(skills_mode={profile.skills_mode.value}, "
            f"skills={profile.skills}, "
            f"tools_mode={profile.tools_mode}, "
            f"mcp_mode={profile.mcp_mode}, "
            f"plugins_mode={profile.plugins_mode}, "
            f"identity_mode={profile.identity_mode}, "
            f"memory_mode={profile.memory_mode}, "
            f"preferred_endpoint={profile.preferred_endpoint or 'auto'})"
        )
        return agent

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        """归一化技能名：下划线转连字符、统一小写"""
        return name.lower().replace("_", "-")

    @staticmethod
    def _build_skill_match_set(names: list[str]) -> tuple[set[str], set[str]]:
        """构建技能名匹配集，同时支持完整命名空间和短名匹配。

        Returns:
            (exact_set, short_set) — exact_set 包含完整归一化名称，
            short_set 包含 ``@`` 后的短名（用于跨格式回退匹配）。
        """
        n = AgentFactory._normalize_skill_name
        exact: set[str] = set()
        short: set[str] = set()
        for s in names:
            norm = n(s)
            exact.add(norm)
            short.add(norm.split("@", 1)[-1] if "@" in norm else norm)
        return exact, short

    @staticmethod
    def _skill_in_set(skill_name: str, exact_set: set[str], short_set: set[str]) -> bool:
        """判断技能名是否在匹配集中（兼容命名空间和短名）。"""
        norm = AgentFactory._normalize_skill_name(skill_name)
        if norm in exact_set:
            return True
        return (norm.split("@", 1)[-1] if "@" in norm else norm) in short_set

    @staticmethod
    def _is_essential(skill_name: str) -> bool:
        """判断是否为基础设施系统工具（INCLUSIVE 模式始终保留）。"""
        return AgentFactory._normalize_skill_name(skill_name) in ESSENTIAL_SYSTEM_SKILLS

    @staticmethod
    def _apply_skill_filter(agent: Agent, profile: AgentProfile) -> None:
        if profile.skills_mode == SkillsMode.ALL or not profile.skills:
            return

        registry = agent.skill_registry
        all_skills = [skill.skill_id for skill in registry.list_all(include_disabled=True)]

        removed = 0
        if profile.skills_mode == SkillsMode.INCLUSIVE:
            exact, short = AgentFactory._build_skill_match_set(profile.skills)
            for skill_name in all_skills:
                if AgentFactory._is_essential(skill_name):
                    continue
                if not AgentFactory._skill_in_set(skill_name, exact, short):
                    registry.unregister(skill_name)
                    removed += 1

            # 子 Agent 显式选择的技能即使全局 disabled 也应在此 Agent 上可用
            for skill in registry.list_all(include_disabled=True):
                if skill.disabled:
                    skill.disabled = False

        elif profile.skills_mode == SkillsMode.EXCLUSIVE:
            exact, short = AgentFactory._build_skill_match_set(profile.skills)
            for skill_name in all_skills:
                if AgentFactory._is_essential(skill_name):
                    continue
                if AgentFactory._skill_in_set(skill_name, exact, short):
                    registry.unregister(skill_name)
                    removed += 1

        if removed:
            agent.skill_catalog.invalidate_cache()
            agent.skill_catalog.generate_catalog()
            agent._update_skill_tools()

    @staticmethod
    def _apply_tool_filter(agent: Agent, profile: AgentProfile) -> None:
        """按 profile.tools + tools_mode 过滤 Agent 的工具列表。

        tools 字段支持类目名（如 "research"）和具体工具名的混合。
        INCLUSIVE 模式下 ESSENTIAL_TOOL_NAMES 始终保留。
        """
        if profile.tools_mode == "all" or not profile.tools:
            return

        from ..orgs.tool_categories import expand_tool_categories

        specified = expand_tool_categories(profile.tools)

        if profile.tools_mode == "inclusive":
            agent._tools = [
                t for t in agent._tools
                if t["name"] in specified or t["name"] in ESSENTIAL_TOOL_NAMES
            ]
        elif profile.tools_mode == "exclusive":
            agent._tools = [
                t for t in agent._tools
                if t["name"] not in specified or t["name"] in ESSENTIAL_TOOL_NAMES
            ]

        agent._tools.sort(key=lambda t: t["name"])

        from ..tools.catalog import ToolCatalog
        agent.tool_catalog = ToolCatalog(agent._tools)
        logger.info(
            f"Tool filter applied: mode={profile.tools_mode}, "
            f"remaining={len(agent._tools)} tools"
        )

    @staticmethod
    def _apply_mcp_filter(agent: Agent, profile: AgentProfile) -> None:
        """按 profile.mcp_servers + mcp_mode 过滤 Agent 的 MCP catalog。

        创建一个 filtered clone 替换 agent.mcp_catalog，
        使 call_mcp_tool handler 只能访问 clone 中的 server。
        """
        if profile.mcp_mode == "all" or not profile.mcp_servers:
            return

        catalog = getattr(agent, "mcp_catalog", None)
        if catalog is None or not hasattr(catalog, "clone_filtered"):
            return

        filtered = catalog.clone_filtered(profile.mcp_servers, mode=profile.mcp_mode)
        agent.mcp_catalog = filtered
        logger.info(
            f"MCP filter applied: mode={profile.mcp_mode}, "
            f"servers={profile.mcp_servers}, "
            f"remaining={filtered.server_count} servers"
        )

    @staticmethod
    def _apply_identity_override(agent: Agent, profile: AgentProfile) -> None:
        """加载 Profile 专属身份文件，覆盖 agent.identity 并重建 system prompt。"""
        from .identity_resolver import ProfileIdentityResolver
        from .profile import get_profile_store

        store = get_profile_store()
        profile_dir = store.ensure_profile_dir(profile.id)
        profile_identity_dir = profile_dir / "identity"

        from ..config import settings
        global_identity_dir = settings.identity_path

        resolver = ProfileIdentityResolver(profile_identity_dir, global_identity_dir)
        identity = resolver.build_identity()
        identity.load()

        agent.identity = identity

        if hasattr(agent, "_context"):
            base_prompt = identity.get_system_prompt()
            agent._context.system = agent._build_system_prompt(
                base_prompt, use_compiled=True,
            )

        logger.info(
            f"Identity override applied: profile={profile.id}, "
            f"dir={profile_identity_dir}"
        )

    @staticmethod
    def _apply_memory_isolation(agent: Agent, profile: AgentProfile) -> None:
        """替换 agent.memory_manager 为独立的 MemoryManager 实例。"""
        from ..config import settings
        from ..memory.manager import MemoryManager
        from .profile import get_profile_store

        store = get_profile_store()
        profile_dir = store.ensure_profile_dir(profile.id)
        memory_dir = profile_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        memory_md_path = (profile_dir / "identity" / "MEMORY.md")
        if not memory_md_path.exists():
            memory_md_path = settings.memory_path

        isolated_mm = MemoryManager(
            data_dir=memory_dir,
            memory_md_path=memory_md_path,
            brain=agent.brain,
            embedding_model=settings.embedding_model,
            embedding_device=settings.embedding_device,
            model_download_source=settings.model_download_source,
            search_backend=settings.search_backend,
            embedding_api_provider=settings.embedding_api_provider,
            embedding_api_key=settings.embedding_api_key,
            embedding_api_model=settings.embedding_api_model,
            agent_id=profile.id,
        )

        if profile.memory_inherit_global:
            global_store = agent.memory_manager.store
            isolated_mm._global_store_ref = global_store
            isolated_mm.retrieval_engine._external_sources.append(
                _GlobalStoreSource(global_store)
            )

        agent.memory_manager = isolated_mm

        logger.info(
            f"Memory isolation applied: profile={profile.id}, "
            f"dir={memory_dir}, inherit_global={profile.memory_inherit_global}"
        )

    @staticmethod
    async def _apply_plugin_filter(agent: Agent, profile: AgentProfile) -> None:
        """按 profile.plugins + plugins_mode 过滤 Agent 的插件。

        对不应保留的插件执行 unload，清理其 hooks、tools、channels。
        """
        if profile.plugins_mode == "all" or not profile.plugins:
            return

        pm = getattr(agent, "_plugin_manager", None)
        if pm is None:
            return

        specified = set(profile.plugins)
        loaded_ids = list(pm.loaded_plugins.keys())

        for plugin_id in loaded_ids:
            should_keep = (
                (profile.plugins_mode == "inclusive" and plugin_id in specified)
                or (profile.plugins_mode == "exclusive" and plugin_id not in specified)
            )
            if not should_keep:
                try:
                    await pm.unload_plugin(plugin_id)
                    logger.info(f"Plugin filter: unloaded {plugin_id}")
                except Exception as e:
                    logger.warning(f"Plugin filter: failed to unload {plugin_id}: {e}")


class _PoolEntry:
    __slots__ = ("agent", "profile_id", "session_id", "created_at", "last_used", "skills_version")

    def __init__(self, agent: Agent, profile_id: str, session_id: str, skills_version: int = 0):
        self.agent = agent
        self.profile_id = profile_id
        self.session_id = session_id
        self.created_at = time.monotonic()
        self.last_used = time.monotonic()
        self.skills_version = skills_version

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    @property
    def pool_key(self) -> str:
        return f"{self.session_id}::{self.profile_id}"


class AgentInstancePool:
    """
    Agent 实例池 — per-session + per-profile 绑定 + 空闲自动回收。

    Pool key 格式: ``{session_id}::{profile_id}``

    同一会话可同时持有多个不同 profile 的 Agent 实例。
    例如 session_123 可以同时运行 default, browser-agent, data-analyst。
    """

    def __init__(
        self,
        factory: AgentFactory | None = None,
        idle_timeout: float = _IDLE_TIMEOUT_SECONDS,
        profile_store=None,
    ):
        self._factory = factory or AgentFactory()
        self._idle_timeout = idle_timeout
        self._profile_store = profile_store
        # Key: "{session_id}::{profile_id}"
        self._pool: dict[str, _PoolEntry] = {}
        # Per-composite-key locks for concurrent creation
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._reaper_task: asyncio.Task | None = None
        self._skills_version: int = 0

    @staticmethod
    def _make_key(session_id: str, profile_id: str) -> str:
        return f"{session_id}::{profile_id}"

    async def start(self) -> None:
        self._reaper_task = asyncio.create_task(self._reap_loop())
        logger.info("AgentInstancePool reaper started")

    async def stop(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        self._pool.clear()
        logger.info("AgentInstancePool stopped")

    def notify_skills_changed(self) -> None:
        """全局技能变更通知 — 递增版本号使池中已有 Agent 在下次使用时重建。"""
        self._skills_version += 1
        logger.info(f"Pool skills version bumped to {self._skills_version}")

    async def get_or_create(
        self, session_id: str, profile: AgentProfile,
    ) -> Agent:
        """获取已有实例或创建新实例。

        Key = session_id::profile_id，同 session 不同 profile 各自独立。
        All dict operations are safe under asyncio's single-threaded event loop;
        only the async create_lock is needed to serialize factory.create() calls.

        当全局技能版本变更时，旧的 Agent 会被丢弃并重建，
        确保技能安装/卸载/启禁用等操作能同步到所有池 Agent。
        """
        key = self._make_key(session_id, profile.id)
        current_version = self._skills_version

        entry = self._pool.get(key)
        if entry:
            if entry.skills_version >= current_version:
                entry.touch()
                return entry.agent
            logger.info(
                f"Pool agent stale (skills_version {entry.skills_version} < {current_version}), "
                f"recreating: session={session_id}, profile={profile.id}"
            )
            self._pool.pop(key, None)
            try:
                if hasattr(entry.agent, "shutdown"):
                    asyncio.ensure_future(entry.agent.shutdown())
            except Exception:
                pass

        if key not in self._create_locks:
            self._create_locks[key] = asyncio.Lock()
        create_lock = self._create_locks[key]

        async with create_lock:
            entry = self._pool.get(key)
            if entry and entry.skills_version >= current_version:
                entry.touch()
                return entry.agent

            parent_brain = None
            session_entries = [
                e for e in self._pool.values()
                if e.session_id == session_id and hasattr(e.agent, "brain")
            ]
            if session_entries:
                # Prefer default/system profiles, then earliest created
                def _sort_key(e: _PoolEntry) -> tuple:
                    profile = getattr(e.agent, "_agent_profile", None)
                    is_default = e.profile_id == "default"
                    is_system = profile is not None and getattr(profile, "type", None) == AgentType.SYSTEM
                    return (not is_default, not is_system, e.created_at)
                best = min(session_entries, key=_sort_key)
                parent_brain = best.agent.brain

            if parent_brain is None:
                agent = await self._factory.create(profile)
            else:
                agent = await self._factory.create(profile, parent_brain=parent_brain)
            new_entry = _PoolEntry(agent, profile.id, session_id, current_version)
            self._pool[key] = new_entry

        logger.info(
            f"Pool created agent: session={session_id}, profile={profile.id}"
        )
        return agent

    def get_existing(
        self, session_id: str, profile_id: str | None = None,
    ) -> Agent | None:
        """Return an existing Agent without creating a new one.

        If *profile_id* is given, looks up the exact (session, profile) pair.
        Otherwise returns the first (and typically only) agent for the session
        — used by control endpoints (cancel/skip/insert).
        """
        if profile_id:
            key = self._make_key(session_id, profile_id)
            entry = self._pool.get(key)
            if entry:
                entry.touch()
                return entry.agent
            return None

        for entry in self._pool.values():
            if entry.session_id == session_id:
                entry.touch()
                return entry.agent
        return None

    def get_all_for_session(self, session_id: str) -> list[_PoolEntry]:
        """Return all pool entries for a given session."""
        return [e for e in self._pool.values() if e.session_id == session_id]

    def release(self, session_id: str, profile_id: str | None = None) -> None:
        """标记实例进入空闲等待回收。"""
        if profile_id:
            key = self._make_key(session_id, profile_id)
            entry = self._pool.get(key)
            if entry:
                entry.touch()
        else:
            for entry in self._pool.values():
                if entry.session_id == session_id:
                    entry.touch()

    def get_stats(self) -> dict:
        entries = list(self._pool.values())

        sessions: dict[str, list[dict]] = {}
        for e in entries:
            sessions.setdefault(e.session_id, []).append({
                "profile_id": e.profile_id,
                "idle_seconds": round(e.idle_seconds, 1),
            })

        return {
            "total": len(entries),
            "sessions": [
                {
                    "session_id": sid,
                    "profile_id": agents[0]["profile_id"],
                    "idle_seconds": min(a["idle_seconds"] for a in agents),
                    "agents": agents,
                }
                for sid, agents in sessions.items()
            ],
        }

    def _get_shared_profile_store(self):
        """Get the ProfileStore — prefer the injected reference, fallback to module singleton."""
        if self._profile_store is not None:
            return self._profile_store
        try:
            from openakita.agents.profile import get_profile_store
            return get_profile_store()
        except Exception:
            return None

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_REAP_INTERVAL_SECONDS)
                self._reap_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AgentInstancePool reaper error: {e}")

    def _reap_idle(self) -> None:
        reaped_profile_ids: list[str] = []

        stale_locks = [k for k in self._create_locks if k not in self._pool]
        for k in stale_locks:
            lock = self._create_locks[k]
            if not lock.locked():
                self._create_locks.pop(k, None)

        to_remove = []
        for key, entry in self._pool.items():
            if entry.idle_seconds <= self._idle_timeout:
                continue
            astate = getattr(entry.agent, "agent_state", None)
            if astate is not None and getattr(astate, "has_active_task", False) is True:
                continue
            to_remove.append(key)
        for key in to_remove:
            entry = self._pool.pop(key)
            reaped_profile_ids.append(entry.profile_id)
            logger.info(
                f"Pool reaped idle agent: session={entry.session_id}, "
                f"profile={entry.profile_id}, "
                f"idle={entry.idle_seconds:.0f}s"
            )
            try:
                if hasattr(entry.agent, 'shutdown'):
                    asyncio.ensure_future(entry.agent.shutdown())
            except Exception:
                pass

        # Clean up ephemeral profiles for reaped agents (outside lock)
        if reaped_profile_ids:
            try:
                store = self._get_shared_profile_store()
                if store:
                    for pid in reaped_profile_ids:
                        p = store.get(pid)
                        if p and getattr(p, "ephemeral", False):
                            store.remove_ephemeral(pid)
                            logger.info(f"Pool reaper cleaned ephemeral profile: {pid}")
            except Exception as e:
                logger.warning(f"Pool reaper ephemeral cleanup failed: {e}")
