"""
AgentFactory -- Creates differentiated Agent instances according to AgentProfile
AgentInstancePool -- per-session + per-profile instance management + idle reclamation

Pool key format: ``{session_id}::{profile_id}``
Same session can hold multiple Agent instances with different profiles running in parallel.
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

_IDLE_TIMEOUT_SECONDS = 30 * 60  # 30 minute idle reclamation
_REAP_INTERVAL_SECONDS = 60  # Check every minute

# Base system tools always kept in INCLUSIVE mode.
# All child Agents (including user-created ones) need these tools to function properly.
# Only specialized tools like browser, desktop control, MCP, scheduled tasks need explicit listing in profile.skills.
ESSENTIAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "run_shell",
        "read_file",
        "write_file",
        "list_directory",
        "web_search",
        "deliver_artifacts",
        "get_chat_history",
        "search_memory",
        "add_memory",
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        "list_skills",
        "get_skill_info",
        "get_tool_info",
        "set_task_timeout",
        "get_image_file",
        "get_voice_file",
    }
)

ESSENTIAL_SYSTEM_SKILLS: frozenset[str] = frozenset(
    {
        # Planning (core of multi-step tasks)
        "create-todo",
        "update-todo-step",
        "get-todo-status",
        "complete-todo",
        # Skill discovery (progressive disclosure entry — external skills must call get_skill_info first)
        "get-skill-info",
        "list-skills",
        # File system (foundation for external skill execution — read instructions → write code → run-shell)
        "run-shell",
        "read-file",
        "write-file",
        "list-directory",
        # IM channel (receive user input, deliver files)
        "deliver-artifacts",
        "get-chat-history",
        "get-image-file",
        "get-voice-file",
        # Memory
        "search-memory",
        "add-memory",
        # Information retrieval
        "web-search",
        # System
        "get-tool-info",
        "set-task-timeout",
    }
)


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
            results.append(
                {
                    "id": f"global::{mem.id}",
                    "content": mem.to_markdown(),
                    "relevance": 0.6,
                }
            )
        return results


class AgentFactory:
    """
    Create Agent instances according to AgentProfile.

    - Filter skills based on profile configuration
    - Inject custom prompts
    - Set agent name/icon
    """

    async def create(
        self,
        profile: AgentProfile,
        *,
        parent_brain: Any = None,
        **kwargs: Any,
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
        # INCLUSIVE mode always needs rebuild (empty list = hide all non-essential skills).
        needs_rebuild = (
            (profile.tools_mode != "all" and profile.tools)
            or (profile.mcp_mode != "all" and profile.mcp_servers)
            or profile.skills_mode == SkillsMode.INCLUSIVE
            or (profile.skills_mode == SkillsMode.EXCLUSIVE and profile.skills)
        )
        if needs_rebuild and hasattr(agent, "_context"):
            agent._context.system = agent._build_system_prompt()

        # ── Identity isolation ──
        if profile.identity_mode == "custom":
            self._apply_identity_override(agent, profile)

        # ── Memory isolation ──
        if profile.memory_mode == "isolated":
            self._apply_memory_isolation(agent, profile)

        # ── Permission rule injection (MA1) ──
        if profile.permission_rules:
            try:
                from ..core.permission import from_config

                ruleset = from_config(
                    {
                        r["permission"]: {r.get("pattern", "*"): r["action"]}
                        for r in profile.permission_rules
                        if "permission" in r and "action" in r
                    }
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
        """Normalize skill name: underscores to hyphens, lowercase."""
        return name.lower().replace("_", "-")

    @staticmethod
    def _build_skill_match_set(names: list[str]) -> tuple[set[str], set[str]]:
        """Build skill name match set, supporting both full namespace and short name matching.

        Returns:
            (exact_set, short_set) — exact_set contains fully normalized names,
            short_set contains short names after ``@`` (for cross-format fallback matching).
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
        """Check if a skill name is in the match set (compatible with namespace and short name)."""
        norm = AgentFactory._normalize_skill_name(skill_name)
        if norm in exact_set:
            return True
        return (norm.split("@", 1)[-1] if "@" in norm else norm) in short_set

    @staticmethod
    def _is_essential(skill_name: str) -> bool:
        """Check if this is an essential system tool (always retained in INCLUSIVE mode)."""
        return AgentFactory._normalize_skill_name(skill_name) in ESSENTIAL_SYSTEM_SKILLS

    @staticmethod
    def _apply_skill_filter(agent: Agent, profile: AgentProfile) -> None:
        if profile.skills_mode == SkillsMode.ALL:
            return

        # EXCLUSIVE with empty list → nothing to exclude
        if profile.skills_mode == SkillsMode.EXCLUSIVE and not profile.skills:
            return

        registry = agent.skill_registry
        all_skills = [skill.skill_id for skill in registry.list_all(include_disabled=True)]
        changed = 0

        if profile.skills_mode == SkillsMode.INCLUSIVE:
            # INCLUSIVE: progressive disclosure — unselected skills are hidden from L1 (system prompt catalog),
            # but remain in the registry; LLM can discover them on-demand via list_skills / get_skill_info.
            if profile.skills:
                exact, short = AgentFactory._build_skill_match_set(profile.skills)
            else:
                exact, short = set(), set()

            for skill_name in all_skills:
                if AgentFactory._is_essential(skill_name):
                    continue
                if not AgentFactory._skill_in_set(skill_name, exact, short):
                    registry.set_catalog_hidden(skill_name, True)
                    changed += 1

            # Explicitly selected skills should be available on this Agent even if globally disabled
            if profile.skills:
                for skill in registry.list_all(include_disabled=True):
                    if skill.disabled and AgentFactory._skill_in_set(skill.skill_id, exact, short):
                        skill.disabled = False

        elif profile.skills_mode == SkillsMode.EXCLUSIVE:
            # EXCLUSIVE: completely remove blacklisted skills (not discoverable)
            exact, short = AgentFactory._build_skill_match_set(profile.skills)
            for skill_name in all_skills:
                if AgentFactory._is_essential(skill_name):
                    continue
                if AgentFactory._skill_in_set(skill_name, exact, short):
                    registry.unregister(skill_name)
                    changed += 1

        if changed:
            agent.skill_catalog.invalidate_cache()
            agent.skill_catalog.generate_catalog()
            agent._update_skill_tools()

    @staticmethod
    def _apply_tool_filter(agent: Agent, profile: AgentProfile) -> None:
        """Filter agent tools by profile.tools + tools_mode.

        The tools field supports a mix of category names (e.g. "research") and specific tool names.
        ESSENTIAL_TOOL_NAMES are always retained in INCLUSIVE mode.
        """
        if profile.tools_mode == "all" or not profile.tools:
            return

        from ..orgs.tool_categories import expand_tool_categories

        specified = expand_tool_categories(profile.tools)

        if profile.tools_mode == "inclusive":
            agent._tools = [
                t
                for t in agent._tools
                if t["name"] in specified or t["name"] in ESSENTIAL_TOOL_NAMES
            ]
        elif profile.tools_mode == "exclusive":
            agent._tools = [
                t
                for t in agent._tools
                if t["name"] not in specified or t["name"] in ESSENTIAL_TOOL_NAMES
            ]

        agent._tools.sort(key=lambda t: t["name"])

        from ..tools.catalog import ToolCatalog

        agent.tool_catalog = ToolCatalog(agent._tools)
        logger.info(
            f"Tool filter applied: mode={profile.tools_mode}, remaining={len(agent._tools)} tools"
        )

    @staticmethod
    def _apply_mcp_filter(agent: Agent, profile: AgentProfile) -> None:
        """Filter agent MCP catalog by profile.mcp_servers + mcp_mode.

        Creates a filtered clone to replace agent.mcp_catalog,
        so call_mcp_tool handler can only access servers in the clone.
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
        """Load profile-specific identity files, override agent.identity, and rebuild system prompt."""
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
            agent._context.system = agent._build_system_prompt()

        logger.info(f"Identity override applied: profile={profile.id}, dir={profile_identity_dir}")

    @staticmethod
    def _apply_memory_isolation(agent: Agent, profile: AgentProfile) -> None:
        """Replace agent.memory_manager with an isolated MemoryManager instance."""
        from ..config import settings
        from ..memory.manager import MemoryManager
        from .profile import get_profile_store

        store = get_profile_store()
        profile_dir = store.ensure_profile_dir(profile.id)
        memory_dir = profile_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        memory_md_path = profile_dir / "identity" / "MEMORY.md"
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
            isolated_mm.retrieval_engine._external_sources.append(_GlobalStoreSource(global_store))

        agent.memory_manager = isolated_mm

        logger.info(
            f"Memory isolation applied: profile={profile.id}, "
            f"dir={memory_dir}, inherit_global={profile.memory_inherit_global}"
        )

    @staticmethod
    async def _apply_plugin_filter(agent: Agent, profile: AgentProfile) -> None:
        """Filter agent plugins by profile.plugins + plugins_mode.

        Unload plugins that should not be retained, cleaning up their hooks, tools, and channels.
        """
        if profile.plugins_mode == "all" or not profile.plugins:
            return

        pm = getattr(agent, "_plugin_manager", None)
        if pm is None:
            return

        specified = set(profile.plugins)
        loaded_ids = list(pm.loaded_plugins.keys())

        for plugin_id in loaded_ids:
            should_keep = (profile.plugins_mode == "inclusive" and plugin_id in specified) or (
                profile.plugins_mode == "exclusive" and plugin_id not in specified
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
    Agent instance pool — per-session + per-profile binding + idle auto-reclamation.

    Pool key format: ``{session_id}::{profile_id}``

    A single session can hold multiple Agent instances with different profiles.
    For example, session_123 can run default, browser-agent, and data-analyst simultaneously.
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

    @staticmethod
    def _schedule_shutdown(agent: Any) -> None:
        if not hasattr(agent, "shutdown"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.create_task(agent.shutdown())
        except Exception:
            pass

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
        """Global skill change notification — increment version so existing pooled Agents are rebuilt on next use."""
        self._skills_version += 1
        logger.info(f"Pool skills version bumped to {self._skills_version}")

    def invalidate_profile(self, profile_id: str) -> int:
        """Drop all pooled Agent instances bound to *profile_id*."""
        to_remove = [key for key, entry in self._pool.items() if entry.profile_id == profile_id]
        removed = 0
        for key in to_remove:
            entry = self._pool.pop(key, None)
            if entry is None:
                continue
            removed += 1
            self._schedule_shutdown(entry.agent)

        stale_locks = [k for k in self._create_locks if k not in self._pool]
        for key in stale_locks:
            lock = self._create_locks[key]
            if not lock.locked():
                self._create_locks.pop(key, None)

        if removed:
            logger.info(f"Pool invalidated profile={profile_id} across {removed} session(s)")
        return removed

    async def get_or_create(
        self,
        session_id: str,
        profile: AgentProfile,
    ) -> Agent:
        """Get an existing instance or create a new one.

        Key = session_id::profile_id; different profiles within the same session are independent.
        All dict operations are safe under asyncio's single-threaded event loop;
        only the async create_lock is needed to serialize factory.create() calls.

        When the global skills version changes, old Agents are discarded and rebuilt,
        ensuring skill install/uninstall/enable/disable operations are synced to all pooled Agents.
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
            self._schedule_shutdown(entry.agent)

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
                e
                for e in self._pool.values()
                if e.session_id == session_id and hasattr(e.agent, "brain")
            ]
            if session_entries:
                # Prefer default/system profiles, then earliest created
                def _sort_key(e: _PoolEntry) -> tuple:
                    profile = getattr(e.agent, "_agent_profile", None)
                    is_default = e.profile_id == "default"
                    is_system = (
                        profile is not None and getattr(profile, "type", None) == AgentType.SYSTEM
                    )
                    return (not is_default, not is_system, e.created_at)

                best = min(session_entries, key=_sort_key)
                parent_brain = best.agent.brain

            if parent_brain is None:
                agent = await self._factory.create(profile)
            else:
                agent = await self._factory.create(profile, parent_brain=parent_brain)
            new_entry = _PoolEntry(agent, profile.id, session_id, current_version)
            self._pool[key] = new_entry

        logger.info(f"Pool created agent: session={session_id}, profile={profile.id}")
        return agent

    def get_existing(
        self,
        session_id: str,
        profile_id: str | None = None,
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
        """Mark instances as idle, awaiting reclamation."""
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
            sessions.setdefault(e.session_id, []).append(
                {
                    "profile_id": e.profile_id,
                    "idle_seconds": round(e.idle_seconds, 1),
                }
            )

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
                self._schedule_shutdown(entry.agent)
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
