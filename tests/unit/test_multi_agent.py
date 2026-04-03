"""
Multi-agent architecture comprehensive test suite.

Tests all core components:
- AgentProfile / ProfileStore
- FallbackResolver
- AgentInstancePool
- AgentOrchestrator
- AgentToolHandler
- AgentMailbox / AgentHealth
"""

from __future__ import annotations

import asyncio
import time
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.agents.profile import (
    AgentProfile,
    AgentType,
    ProfileStore,
    SkillsMode,
)
from openakita.agents.fallback import FallbackResolver, _AUTO_DEGRADE_THRESHOLD
from openakita.agents.orchestrator import (
    AgentHealth,
    AgentMailbox,
    AgentOrchestrator,
    DelegationRequest,
    MAX_DELEGATION_DEPTH,
)
from openakita.sessions.session import Session, SessionConfig, SessionContext


# ================================================================
# Helpers
# ================================================================

def _make_session(
    session_id: str = "test-session-1",
    agent_profile_id: str = "default",
) -> Session:
    ctx = SessionContext()
    ctx.agent_profile_id = agent_profile_id
    return Session(
        id=session_id,
        channel="cli",
        chat_id="chat-1",
        user_id="user-1",
        context=ctx,
        config=SessionConfig(),
    )


def _make_profile(
    pid: str = "test-agent",
    name: str = "Test Agent",
    agent_type: AgentType = AgentType.CUSTOM,
    **kwargs,
) -> AgentProfile:
    return AgentProfile(id=pid, name=name, type=agent_type, **kwargs)


# ================================================================
# AgentProfile Tests
# ================================================================

class TestAgentProfile:
    def test_default_values(self):
        p = AgentProfile(id="a", name="A")
        assert p.type == AgentType.CUSTOM
        assert p.skills_mode == SkillsMode.ALL
        assert p.skills == []
        assert p.ephemeral is False
        assert p.icon == "🤖"

    def test_string_type_coercion(self):
        p = AgentProfile(id="a", name="A", type="system", skills_mode="inclusive")
        assert p.type == AgentType.SYSTEM
        assert p.skills_mode == SkillsMode.INCLUSIVE

    def test_to_dict_roundtrip(self):
        original = _make_profile(
            skills=["web", "code"],
            skills_mode=SkillsMode.INCLUSIVE,
            custom_prompt="You are helpful",
            ephemeral=True,
            inherit_from="base-agent",
        )
        d = original.to_dict()
        restored = AgentProfile.from_dict(d)
        assert restored.id == original.id
        assert restored.skills == original.skills
        assert restored.skills_mode == SkillsMode.INCLUSIVE
        assert restored.custom_prompt == "You are helpful"
        assert restored.ephemeral is True
        assert restored.inherit_from == "base-agent"

    def test_from_dict_ignores_unknown_fields(self):
        data = {"id": "x", "name": "X", "unknown_field": 42}
        p = AgentProfile.from_dict(data)
        assert p.id == "x"
        assert not hasattr(p, "unknown_field")

    def test_get_display_name_i18n(self):
        p = _make_profile(name_i18n={"zh": "测试员", "en": "Tester"})
        assert p.get_display_name("zh") == "测试员"
        assert p.get_display_name("en") == "Tester"
        assert p.get_display_name("fr") == "Test Agent"  # fallback to name

    def test_is_system(self):
        p1 = _make_profile(agent_type=AgentType.SYSTEM)
        p2 = _make_profile(agent_type=AgentType.CUSTOM)
        assert p1.is_system is True
        assert p2.is_system is False

    def test_created_at_auto_set(self):
        p = AgentProfile(id="a", name="A")
        assert p.created_at != ""

    def test_preferred_endpoint_default_none(self):
        p = AgentProfile(id="a", name="A")
        assert p.preferred_endpoint is None

    def test_preferred_endpoint_roundtrip(self):
        p = _make_profile(preferred_endpoint="claude-primary")
        d = p.to_dict()
        assert d["preferred_endpoint"] == "claude-primary"
        restored = AgentProfile.from_dict(d)
        assert restored.preferred_endpoint == "claude-primary"

    def test_preferred_endpoint_none_roundtrip(self):
        p = _make_profile(preferred_endpoint=None)
        d = p.to_dict()
        assert d["preferred_endpoint"] is None
        restored = AgentProfile.from_dict(d)
        assert restored.preferred_endpoint is None

    def test_preferred_endpoint_from_dict_missing_key(self):
        data = {"id": "x", "name": "X"}
        p = AgentProfile.from_dict(data)
        assert p.preferred_endpoint is None

    def test_isolation_fields_roundtrip(self):
        original = _make_profile(
            identity_mode="custom",
            memory_mode="isolated",
            memory_inherit_global=False,
        )
        restored = AgentProfile.from_dict(original.to_dict())
        assert restored.identity_mode == "custom"
        assert restored.memory_mode == "isolated"
        assert restored.memory_inherit_global is False


# ================================================================
# ProfileStore Tests
# ================================================================

class TestProfileStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> ProfileStore:
        return ProfileStore(tmp_path / "agents")

    def test_save_and_get(self, store: ProfileStore):
        p = _make_profile()
        store.save(p)
        retrieved = store.get("test-agent")
        assert retrieved is not None
        assert retrieved.name == "Test Agent"

    def test_get_nonexistent(self, store: ProfileStore):
        assert store.get("nonexistent") is None

    def test_list_all(self, store: ProfileStore):
        store.save(_make_profile("a1", "Agent 1"))
        store.save(_make_profile("a2", "Agent 2"))
        all_profiles = store.list_all()
        assert len(all_profiles) == 2
        ids = {p.id for p in all_profiles}
        assert ids == {"a1", "a2"}

    def test_delete(self, store: ProfileStore):
        store.save(_make_profile())
        assert store.delete("test-agent") is True
        assert store.get("test-agent") is None

    def test_delete_nonexistent(self, store: ProfileStore):
        assert store.delete("nonexistent") is False

    def test_delete_system_profile_raises(self, store: ProfileStore):
        sys_profile = _make_profile("sys", "System", agent_type=AgentType.SYSTEM)
        store.save(sys_profile)
        with pytest.raises(PermissionError, match="Cannot delete SYSTEM"):
            store.delete("sys")

    def test_update(self, store: ProfileStore):
        store.save(_make_profile())
        updated = store.update("test-agent", {"description": "Updated desc"})
        assert updated.description == "Updated desc"
        assert updated.name == "Test Agent"

    def test_update_preferred_endpoint(self, store: ProfileStore):
        store.save(_make_profile())
        updated = store.update("test-agent", {"preferred_endpoint": "my-endpoint"})
        assert updated.preferred_endpoint == "my-endpoint"
        updated2 = store.update("test-agent", {"preferred_endpoint": None})
        assert updated2.preferred_endpoint is None

    def test_update_isolation_fields_persist(self, store: ProfileStore, tmp_path: Path):
        store.save(_make_profile())
        updated = store.update(
            "test-agent",
            {
                "identity_mode": "custom",
                "memory_mode": "isolated",
                "memory_inherit_global": False,
            },
        )
        assert updated.identity_mode == "custom"
        assert updated.memory_mode == "isolated"
        assert updated.memory_inherit_global is False

        reloaded_store = ProfileStore(tmp_path / "agents")
        reloaded = reloaded_store.get("test-agent")
        assert reloaded is not None
        assert reloaded.identity_mode == "custom"
        assert reloaded.memory_mode == "isolated"
        assert reloaded.memory_inherit_global is False

    def test_update_system_preferred_endpoint_marks_customized(self, store: ProfileStore):
        sys_profile = _make_profile("sys", "System", agent_type=AgentType.SYSTEM)
        store.save(sys_profile)
        updated = store.update("sys", {"preferred_endpoint": "claude-primary"})
        assert updated.preferred_endpoint == "claude-primary"
        assert updated.user_customized is True

    def test_update_system_blocks_immutable_fields(self, store: ProfileStore):
        sys_profile = _make_profile("sys", "System", agent_type=AgentType.SYSTEM)
        store.save(sys_profile)
        updated = store.update("sys", {
            "description": "New desc",
            "id": "changed-id",
            "skills": ["new-skill"],
        })
        assert updated.description == "New desc"
        assert updated.id == "sys"  # immutable, not changed
        assert updated.skills == ["new-skill"]  # skills is now a customizable field

    def test_update_nonexistent_raises(self, store: ProfileStore):
        with pytest.raises(KeyError):
            store.update("ghost", {"name": "Ghost"})

    def test_ephemeral_profile_memory_only(self, store: ProfileStore):
        eph = _make_profile("eph-1", "Ephemeral", ephemeral=True)
        store.save(eph)
        assert store.get("eph-1") is not None
        assert store.exists("eph-1")
        profiles_dir = store._profiles_dir
        assert not (profiles_dir / "eph-1.json").exists()

    def test_ephemeral_not_in_list_all_by_default(self, store: ProfileStore):
        store.save(_make_profile("normal", "Normal"))
        store.save(_make_profile("eph", "Eph", ephemeral=True))
        assert len(store.list_all(include_ephemeral=False)) == 1
        assert len(store.list_all(include_ephemeral=True)) == 2

    def test_remove_ephemeral(self, store: ProfileStore):
        store.save(_make_profile("eph", "Eph", ephemeral=True))
        assert store.remove_ephemeral("eph") is True
        assert store.get("eph") is None
        assert store.remove_ephemeral("eph") is False

    def test_cleanup_ephemeral_all(self, store: ProfileStore):
        for i in range(5):
            store.save(_make_profile(f"ephemeral_s1_{i}", f"E{i}", ephemeral=True))
        assert store.count(include_ephemeral=True) == 5
        removed = store.cleanup_ephemeral()
        assert removed == 5
        assert store.count(include_ephemeral=True) == 0

    def test_cleanup_ephemeral_by_prefix(self, store: ProfileStore):
        store.save(_make_profile("ephemeral_s1_1", "E1", ephemeral=True))
        store.save(_make_profile("ephemeral_s1_2", "E2", ephemeral=True))
        store.save(_make_profile("ephemeral_s2_1", "E3", ephemeral=True))
        removed = store.cleanup_ephemeral("s1")
        assert removed == 2
        assert store.get("ephemeral_s2_1") is not None

    def test_persistence_across_instances(self, tmp_path: Path):
        store1 = ProfileStore(tmp_path / "agents")
        store1.save(_make_profile("persist-me", "Persistent"))

        store2 = ProfileStore(tmp_path / "agents")
        p = store2.get("persist-me")
        assert p is not None
        assert p.name == "Persistent"

    def test_count(self, store: ProfileStore):
        assert store.count() == 0
        store.save(_make_profile("a", "A"))
        store.save(_make_profile("b", "B", ephemeral=True))
        assert store.count(include_ephemeral=False) == 1
        assert store.count(include_ephemeral=True) == 2

    def test_thread_safety(self, store: ProfileStore):
        """Concurrent writes should not corrupt internal state."""
        errors = []

        def writer(idx: int):
            try:
                for j in range(20):
                    store.save(_make_profile(f"thread_{idx}_{j}", f"T{idx}_{j}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.count() == 80


# ================================================================
# FallbackResolver Tests
# ================================================================

class TestFallbackResolver:
    @pytest.fixture
    def store(self, tmp_path: Path) -> ProfileStore:
        s = ProfileStore(tmp_path / "agents")
        s.save(_make_profile("main", "Main Agent", fallback_profile_id="fallback"))
        s.save(_make_profile("fallback", "Fallback Agent"))
        s.save(_make_profile("no-fb", "No Fallback Agent"))
        return s

    @pytest.fixture
    def resolver(self, store: ProfileStore) -> FallbackResolver:
        return FallbackResolver(store)

    def test_initial_state_no_fallback_needed(self, resolver: FallbackResolver):
        assert resolver.should_use_fallback("main") is False
        assert resolver.get_effective_profile("main") == "main"

    def test_resolve_fallback(self, resolver: FallbackResolver):
        fb = resolver.resolve_fallback("main")
        assert fb is not None
        assert fb.id == "fallback"

    def test_resolve_fallback_none_when_no_config(self, resolver: FallbackResolver):
        assert resolver.resolve_fallback("no-fb") is None

    def test_auto_degrade_after_consecutive_failures(self, resolver: FallbackResolver):
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("main")
        assert resolver.should_use_fallback("main") is True
        assert resolver.get_effective_profile("main") == "fallback"

    def test_success_resets_consecutive_failures(self, resolver: FallbackResolver):
        for _ in range(_AUTO_DEGRADE_THRESHOLD - 1):
            resolver.record_failure("main")
        resolver.record_success("main")
        resolver.record_failure("main")
        assert resolver.should_use_fallback("main") is False

    def test_success_recovers_from_degraded(self, resolver: FallbackResolver):
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("main")
        assert resolver.should_use_fallback("main") is True
        resolver.record_success("main")
        assert resolver.should_use_fallback("main") is False

    def test_failure_window_resets_counter(self, resolver: FallbackResolver):
        """Failures outside the 5-minute window reset the counter."""
        resolver.record_failure("main")
        resolver.record_failure("main")
        # Simulate time passing beyond the window
        entry = resolver._health["main"]
        entry.last_failure_time = time.monotonic() - 400  # > 300s window
        resolver.record_failure("main")
        assert entry.consecutive_failures == 1  # reset to 1, not 3

    def test_get_effective_profile_no_fallback_configured(self, resolver: FallbackResolver):
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("no-fb")
        assert resolver.should_use_fallback("no-fb") is True
        assert resolver.get_effective_profile("no-fb") == "no-fb"

    def test_health_stats(self, resolver: FallbackResolver):
        resolver.record_success("main")
        resolver.record_failure("main")
        stats = resolver.get_health_stats()
        assert "main" in stats
        assert stats["main"]["total_requests"] == 2
        assert stats["main"]["total_failures"] == 1

    def test_build_fallback_hint(self, resolver: FallbackResolver):
        assert resolver.build_fallback_hint("main") is None
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("main")
        hint = resolver.build_fallback_hint("main")
        assert hint is not None
        assert "Fallback Agent" in hint

    def test_build_fallback_hint_no_fallback(self, resolver: FallbackResolver):
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("no-fb")
        assert resolver.build_fallback_hint("no-fb") is None


# ================================================================
# AgentMailbox Tests
# ================================================================

class TestAgentMailbox:
    @pytest.mark.asyncio
    async def test_send_receive(self):
        mb = AgentMailbox("agent-1")
        await mb.send({"task": "hello"})
        assert mb.pending == 1
        msg = await mb.receive(timeout=1.0)
        assert msg == {"task": "hello"}
        assert mb.pending == 0

    @pytest.mark.asyncio
    async def test_receive_timeout(self):
        mb = AgentMailbox("agent-1")
        msg = await mb.receive(timeout=0.1)
        assert msg is None

    @pytest.mark.asyncio
    async def test_ordering(self):
        mb = AgentMailbox("agent-1")
        for i in range(5):
            await mb.send({"seq": i})
        for i in range(5):
            msg = await mb.receive(timeout=1.0)
            assert msg["seq"] == i


# ================================================================
# AgentHealth Tests
# ================================================================

class TestAgentHealth:
    def test_initial_metrics(self):
        h = AgentHealth(agent_id="a1")
        assert h.total_requests == 0
        assert h.successful == 0
        assert h.failed == 0
        assert h.success_rate == 0.0
        assert h.avg_latency_ms == 0.0

    def test_success_rate(self):
        h = AgentHealth(agent_id="a1", total_requests=10, successful=8, failed=2)
        assert h.success_rate == 0.8

    def test_avg_latency(self):
        h = AgentHealth(agent_id="a1", successful=4, total_latency_ms=400.0)
        assert h.avg_latency_ms == 100.0

    def test_zero_division_protection(self):
        h = AgentHealth(agent_id="a1")
        assert h.success_rate == 0.0
        assert h.avg_latency_ms == 0.0


# ================================================================
# DelegationRequest Tests
# ================================================================

class TestDelegationRequest:
    def test_fields(self):
        req = DelegationRequest(
            from_agent="main",
            to_agent="helper",
            message="do work",
            session_key="s1",
            depth=2,
        )
        assert req.from_agent == "main"
        assert req.to_agent == "helper"
        assert req.depth == 2
        assert req.parent_request_id is None


# ================================================================
# AgentFactory Tests
# ================================================================

class TestAgentFactory:
    @pytest.mark.asyncio
    async def test_create_sets_preferred_endpoint(self):
        from openakita.agents.factory import AgentFactory
        factory = AgentFactory()
        profile = _make_profile("ep-agent", "EP Agent", preferred_endpoint="my-endpoint")
        with patch("openakita.core.agent.Agent") as MockAgent:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance._agent_profile = None
            mock_instance._custom_prompt_suffix = ""
            mock_instance._preferred_endpoint = None
            mock_instance.skill_catalog = MagicMock()
            mock_instance.skill_catalog.get_registry.return_value = MagicMock(list_skills=MagicMock(return_value=[]))
            MockAgent.return_value = mock_instance
            agent = await factory.create(profile)
            assert agent._preferred_endpoint == "my-endpoint"

    @pytest.mark.asyncio
    async def test_create_no_preferred_endpoint(self):
        from openakita.agents.factory import AgentFactory
        factory = AgentFactory()
        profile = _make_profile("plain-agent", "Plain Agent")
        with patch("openakita.core.agent.Agent") as MockAgent:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance._agent_profile = None
            mock_instance._custom_prompt_suffix = ""
            mock_instance._preferred_endpoint = None
            mock_instance.skill_catalog = MagicMock()
            mock_instance.skill_catalog.get_registry.return_value = MagicMock(list_skills=MagicMock(return_value=[]))
            MockAgent.return_value = mock_instance
            agent = await factory.create(profile)
            assert agent._preferred_endpoint is None


# ================================================================
# AgentInstancePool Tests
# ================================================================

class TestAgentInstancePool:
    @pytest.fixture
    def mock_factory(self):
        factory = MagicMock()
        mock_agent = MagicMock()
        mock_agent.shutdown = AsyncMock()
        factory.create = AsyncMock(return_value=mock_agent)
        return factory

    @pytest.fixture
    def pool(self, mock_factory):
        from openakita.agents.factory import AgentInstancePool
        return AgentInstancePool(factory=mock_factory, idle_timeout=5.0)

    @pytest.mark.asyncio
    async def test_get_or_create_new(self, pool, mock_factory):
        profile = _make_profile("agent-a", "Agent A")
        agent = await pool.get_or_create("session-1", profile)
        assert agent is not None
        mock_factory.create.assert_awaited_once_with(profile, parent_brain=None)

    @pytest.mark.asyncio
    async def test_get_or_create_reuses_existing(self, pool, mock_factory):
        profile = _make_profile("agent-a", "Agent A")
        agent1 = await pool.get_or_create("session-1", profile)
        agent2 = await pool.get_or_create("session-1", profile)
        assert agent1 is agent2
        assert mock_factory.create.await_count == 1

    @pytest.mark.asyncio
    async def test_different_profiles_different_instances(self, pool, mock_factory):
        agents = [MagicMock(), MagicMock()]
        mock_factory.create = AsyncMock(side_effect=agents)
        p1 = _make_profile("agent-a", "A")
        p2 = _make_profile("agent-b", "B")
        a1 = await pool.get_or_create("session-1", p1)
        a2 = await pool.get_or_create("session-1", p2)
        assert a1 is not a2
        assert mock_factory.create.await_count == 2

    @pytest.mark.asyncio
    async def test_different_sessions_different_instances(self, pool, mock_factory):
        agents = [MagicMock(), MagicMock()]
        mock_factory.create = AsyncMock(side_effect=agents)
        profile = _make_profile("agent-a", "A")
        a1 = await pool.get_or_create("session-1", profile)
        a2 = await pool.get_or_create("session-2", profile)
        assert a1 is not a2

    def test_get_existing(self, pool):
        from openakita.agents.factory import _PoolEntry
        mock_agent = MagicMock()
        entry = _PoolEntry(mock_agent, "agent-a", "session-1")
        pool._pool["session-1::agent-a"] = entry
        assert pool.get_existing("session-1", "agent-a") is mock_agent
        assert pool.get_existing("session-1", "agent-b") is None
        assert pool.get_existing("session-2", "agent-a") is None

    def test_get_existing_without_profile(self, pool):
        from openakita.agents.factory import _PoolEntry
        mock_agent = MagicMock()
        entry = _PoolEntry(mock_agent, "agent-a", "session-1")
        pool._pool["session-1::agent-a"] = entry
        assert pool.get_existing("session-1") is mock_agent

    def test_release(self, pool):
        from openakita.agents.factory import _PoolEntry
        mock_agent = MagicMock()
        entry = _PoolEntry(mock_agent, "agent-a", "session-1")
        old_time = entry.last_used
        time.sleep(0.01)
        pool._pool["session-1::agent-a"] = entry
        pool.release("session-1", "agent-a")
        assert entry.last_used > old_time

    def test_reap_idle(self, pool):
        from openakita.agents.factory import _PoolEntry
        mock_agent = MagicMock()
        mock_agent.shutdown = AsyncMock()
        entry = _PoolEntry(mock_agent, "agent-a", "session-1")
        entry.last_used = time.monotonic() - 100  # idle for 100s > 5s timeout
        pool._pool["session-1::agent-a"] = entry
        pool._reap_idle()
        assert len(pool._pool) == 0

    def test_reap_keeps_active(self, pool):
        from openakita.agents.factory import _PoolEntry
        mock_agent = MagicMock()
        entry = _PoolEntry(mock_agent, "agent-a", "session-1")
        pool._pool["session-1::agent-a"] = entry
        pool._reap_idle()
        assert len(pool._pool) == 1

    def test_get_stats(self, pool):
        from openakita.agents.factory import _PoolEntry
        pool._pool["s1::a1"] = _PoolEntry(MagicMock(), "a1", "s1")
        pool._pool["s1::a2"] = _PoolEntry(MagicMock(), "a2", "s1")
        pool._pool["s2::a1"] = _PoolEntry(MagicMock(), "a1", "s2")
        stats = pool.get_stats()
        assert stats["total"] == 3
        assert len(stats["sessions"]) == 2

    def test_get_all_for_session(self, pool):
        from openakita.agents.factory import _PoolEntry
        pool._pool["s1::a1"] = _PoolEntry(MagicMock(), "a1", "s1")
        pool._pool["s1::a2"] = _PoolEntry(MagicMock(), "a2", "s1")
        pool._pool["s2::a1"] = _PoolEntry(MagicMock(), "a1", "s2")
        entries = pool.get_all_for_session("s1")
        assert len(entries) == 2

    def test_invalidate_profile_removes_matching_entries(self, pool):
        from openakita.agents.factory import _PoolEntry

        agent_a1 = MagicMock()
        agent_a1.shutdown = AsyncMock()
        agent_a2 = MagicMock()
        agent_a2.shutdown = AsyncMock()
        agent_b = MagicMock()
        agent_b.shutdown = AsyncMock()

        pool._pool["s1::agent-a"] = _PoolEntry(agent_a1, "agent-a", "s1")
        pool._pool["s2::agent-a"] = _PoolEntry(agent_a2, "agent-a", "s2")
        pool._pool["s1::agent-b"] = _PoolEntry(agent_b, "agent-b", "s1")

        removed = pool.invalidate_profile("agent-a")

        assert removed == 2
        assert "s1::agent-a" not in pool._pool
        assert "s2::agent-a" not in pool._pool
        assert "s1::agent-b" in pool._pool


# ================================================================
# AgentOrchestrator Tests
# ================================================================

class TestAgentOrchestrator:
    @pytest.fixture
    def mock_profile_store(self, tmp_path: Path):
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("default", "Default Agent"))
        store.save(_make_profile("helper", "Helper Agent"))
        store.save(_make_profile(
            "fragile", "Fragile Agent",
            fallback_profile_id="default",
        ))
        return store

    @pytest.fixture
    def mock_pool(self):
        pool = MagicMock()
        mock_agent = MagicMock()
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent.chat_with_session = AsyncMock(return_value="Agent response")
        pool.get_or_create = AsyncMock(return_value=mock_agent)
        pool.start = AsyncMock()
        pool.stop = AsyncMock()
        return pool

    @pytest.fixture
    def mock_fallback(self, mock_profile_store):
        return FallbackResolver(mock_profile_store)

    @pytest.fixture
    def orchestrator(self, mock_profile_store, mock_pool, mock_fallback, tmp_path):
        orch = AgentOrchestrator()
        orch._profile_store = mock_profile_store
        orch._pool = mock_pool
        orch._fallback = mock_fallback
        orch._log_dir = tmp_path / "delegation_logs"
        orch._log_dir.mkdir(parents=True, exist_ok=True)
        return orch

    @pytest.mark.asyncio
    async def test_handle_message_routes_correctly(self, orchestrator, mock_pool):
        session = _make_session(agent_profile_id="default")
        result = await orchestrator.handle_message(session, "Hello")
        assert result == "Agent response"
        mock_pool.get_or_create.assert_awaited()

    @pytest.mark.asyncio
    async def test_handle_message_resets_delegation_chain(self, orchestrator):
        session = _make_session()
        session.context.delegation_chain = [{"old": "data"}]
        await orchestrator.handle_message(session, "Hello")
        assert session.context.delegation_chain == []

    @pytest.mark.asyncio
    async def test_delegation_depth_limit(self, orchestrator):
        session = _make_session()
        result = await orchestrator._dispatch(
            session, "task", "default", depth=MAX_DELEGATION_DEPTH
        )
        assert "委派深度超限" in result

    @pytest.mark.asyncio
    async def test_delegation_records_chain(self, orchestrator):
        session = _make_session()
        await orchestrator._dispatch(
            session, "task", "helper", depth=1, from_agent="default"
        )
        chain = session.context.delegation_chain
        assert len(chain) == 1
        assert chain[0]["from"] == "default"
        assert chain[0]["to"] == "helper"
        assert chain[0]["depth"] == 1

    @pytest.mark.asyncio
    async def test_delegate_method(self, orchestrator):
        session = _make_session()
        result = await orchestrator.delegate(
            session, "main", "helper", "do something", reason="testing"
        )
        assert result == "Agent response"
        assert len(session.context.handoff_events) == 1
        assert session.context.handoff_events[0]["from_agent"] == "main"

    @pytest.mark.asyncio
    async def test_delegate_registers_sub_agent_state(self, orchestrator):
        session = _make_session(session_id="sess-xyz")
        await orchestrator.delegate(session, "main", "helper", "task")
        states = orchestrator.get_sub_agent_states("sess-xyz")
        assert len(states) >= 1

    @pytest.mark.asyncio
    async def test_health_tracking_on_success(self, orchestrator):
        session = _make_session()
        await orchestrator.handle_message(session, "Hello")
        stats = orchestrator.get_health_stats()
        assert "default" in stats
        assert stats["default"]["successful"] == 1

    @pytest.mark.asyncio
    async def test_health_tracking_on_failure(self, orchestrator, mock_pool):
        mock_pool.get_or_create = AsyncMock(side_effect=RuntimeError("boom"))
        session = _make_session()
        result = await orchestrator.handle_message(session, "Hello")
        assert "处理失败" in result
        stats = orchestrator.get_health_stats()
        assert stats["default"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_missing_profile_returns_error(self, orchestrator):
        session = _make_session(agent_profile_id="nonexistent-agent")
        # Also make "default" unavailable
        orchestrator._profile_store._cache.clear()
        result = await orchestrator.handle_message(session, "Hello")
        assert "无法找到" in result

    @pytest.mark.asyncio
    async def test_cancel_request(self, orchestrator):
        session = _make_session(session_id="cancel-sess")

        async def slow_agent(*args, **kwargs):
            await asyncio.sleep(10)
            return "done"

        mock_agent = MagicMock()
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent.chat_with_session = slow_agent
        orchestrator._pool.get_or_create = AsyncMock(return_value=mock_agent)

        task = asyncio.create_task(
            orchestrator.handle_message(session, "slow task")
        )
        await asyncio.sleep(0.1)
        assert orchestrator.cancel_request("cancel-sess") is True
        result = await task
        assert "取消" in result

    def test_cancel_nonexistent(self, orchestrator):
        assert orchestrator.cancel_request("ghost") is False

    @pytest.mark.asyncio
    async def test_fallback_on_repeated_failure(self, orchestrator, mock_pool):
        """After consecutive failures hit the threshold, the orchestrator
        should auto-fallback to the fallback profile on the triggering request.
        """
        call_count = 0

        async def failing_then_ok(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= _AUTO_DEGRADE_THRESHOLD:
                raise RuntimeError("agent failed")
            return "fallback response"

        mock_agent = MagicMock()
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent.chat_with_session = failing_then_ok
        mock_pool.get_or_create = AsyncMock(return_value=mock_agent)

        session = _make_session(agent_profile_id="fragile")
        results = []
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            result = await orchestrator.handle_message(session, "do task")
            results.append(result)

        # First 2 failures: fallback not yet triggered (< threshold), returns error
        for r in results[: _AUTO_DEGRADE_THRESHOLD - 1]:
            assert "处理失败" in r

        # 3rd failure: hits threshold, auto-degrades and dispatches to fallback
        # which succeeds — returning "fallback response"
        assert results[-1] == "fallback response"

    @pytest.mark.asyncio
    async def test_collaboration_start(self, orchestrator):
        session = _make_session()
        result = await orchestrator.start_collaboration(session, ["a1", "a2", "a1"])
        assert "2 agents" in result  # deduplication
        assert len(session.context.active_agents) == 2

    @pytest.mark.asyncio
    async def test_get_active_agents(self, orchestrator):
        session = _make_session()
        session.context.active_agents = ["a1", "a2"]
        agents = await orchestrator.get_active_agents(session)
        assert agents == ["a1", "a2"]

    def test_get_delegation_chain(self, orchestrator):
        session = _make_session()
        session.context.delegation_chain = [{"from": "a", "to": "b"}]
        chain = orchestrator.get_delegation_chain(session)
        assert len(chain) == 1

    def test_mailbox_management(self, orchestrator):
        mb = orchestrator.get_mailbox("agent-1")
        assert isinstance(mb, AgentMailbox)
        mb2 = orchestrator.get_mailbox("agent-1")
        assert mb is mb2  # same instance

    def test_evict_stale_agents(self, orchestrator):
        for i in range(10):
            h = orchestrator._get_health(f"agent-{i}")
            h.last_active = time.time() - i * 100
        orchestrator._evict_stale_agents()
        assert len(orchestrator._health) < 10

    @pytest.mark.asyncio
    async def test_sub_agent_states_lifecycle(self, orchestrator):
        session = _make_session(session_id="s1")
        await orchestrator.handle_message(session, "task")
        states = orchestrator.get_sub_agent_states("s1")
        for state in states:
            assert state["status"] in ("completed", "starting", "running")

    def test_get_sub_agent_states_by_chat_id(self, orchestrator):
        orchestrator._sub_agent_states["cli_chat123_20260228_abcd1234:helper"] = {
            "agent_id": "helper",
            "profile_id": "helper",
            "session_id": "cli_chat123_20260228_abcd1234",
            "status": "running",
        }
        states = orchestrator.get_sub_agent_states("chat123")
        assert len(states) == 1

    def test_delegation_logging(self, orchestrator, tmp_path):
        orchestrator._log_delegation({"event": "test", "data": "hello"})
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        log_path = orchestrator._log_dir / f"{today}.jsonl"
        assert log_path.exists()

    @pytest.mark.asyncio
    async def test_persist_and_load_sub_states(self, orchestrator, tmp_path):
        orchestrator._sub_agent_states["s1:a1"] = {
            "agent_id": "a1",
            "status": "running",
            "iteration": 3,
        }
        orchestrator._persist_sub_states()
        path = orchestrator._log_dir.parent / "sub_agent_states.json"
        assert path.exists()

        orchestrator._sub_agent_states.clear()
        orchestrator._load_sub_states()
        assert "s1:a1" in orchestrator._sub_agent_states
        assert orchestrator._sub_agent_states["s1:a1"]["status"] == "interrupted"


# ================================================================
# AgentToolHandler Tests
# ================================================================

class TestAgentToolHandler:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent._is_sub_agent_call = False
        agent._current_session = _make_session()
        agent._current_session.context.agent_profile_id = "default"
        return agent

    @pytest.fixture
    def handler(self, mock_agent):
        from openakita.tools.handlers.agent import AgentToolHandler
        return AgentToolHandler(mock_agent)

    @pytest.mark.asyncio
    async def test_sub_agent_blocked(self, handler):
        handler.agent._is_sub_agent_call = True
        for tool in ["delegate_to_agent", "delegate_parallel", "spawn_agent", "create_agent"]:
            result = await handler.handle(tool, {})
            assert "子 Agent" in result
            assert "不允许" in result

    @pytest.mark.asyncio
    async def test_delegate_missing_agent_id(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("delegate_to_agent", {"message": "task"})
            assert "agent_id is required" in result

    @pytest.mark.asyncio
    async def test_delegate_missing_message(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("delegate_to_agent", {"agent_id": "helper"})
            assert "message is required" in result

    @pytest.mark.asyncio
    async def test_delegate_no_orchestrator(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=None):
            result = await handler.handle("delegate_to_agent", {
                "agent_id": "helper",
                "message": "do task",
            })
            assert "Orchestrator not available" in result

    @pytest.mark.asyncio
    async def test_delegate_no_session(self, handler):
        handler.agent._current_session = None
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("delegate_to_agent", {
                "agent_id": "helper",
                "message": "do task",
            })
            assert "No active session" in result

    @pytest.mark.asyncio
    async def test_delegate_parallel_too_few_tasks(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("delegate_parallel", {
                "tasks": [{"agent_id": "a", "message": "m"}],
            })
            assert "at least 2 tasks" in result

    @pytest.mark.asyncio
    async def test_delegate_parallel_too_many_tasks(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            tasks = [{"agent_id": f"a{i}", "message": f"m{i}"} for i in range(6)]
            result = await handler.handle("delegate_parallel", {"tasks": tasks})
            assert "Maximum 5" in result

    @pytest.mark.asyncio
    async def test_delegate_parallel_invalid_tasks(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("delegate_parallel", {"tasks": "not a list"})
            assert "must be a list" in result

    @pytest.mark.asyncio
    async def test_spawn_missing_inherit_from(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("spawn_agent", {"message": "task"})
            assert "inherit_from is required" in result

    @pytest.mark.asyncio
    async def test_spawn_missing_message(self, handler):
        with patch.object(handler, '_get_orchestrator', return_value=MagicMock()):
            result = await handler.handle("spawn_agent", {"inherit_from": "base"})
            assert "message is required" in result

    @pytest.mark.asyncio
    async def test_create_missing_name(self, handler):
        result = await handler.handle("create_agent", {"description": "desc"})
        assert "name is required" in result

    @pytest.mark.asyncio
    async def test_create_missing_description(self, handler):
        result = await handler.handle("create_agent", {"name": "My Agent"})
        assert "description is required" in result

    @pytest.mark.asyncio
    async def test_create_agent_max_limit(self, handler):
        handler.agent._current_session.context.agent_switch_history = [
            {"type": "dynamic_create"} for _ in range(5)
        ]
        result = await handler.handle("create_agent", {
            "name": "Extra",
            "description": "One too many",
        })
        assert "Maximum dynamic agents" in result

    @pytest.mark.asyncio
    async def test_unknown_tool(self, handler):
        result = await handler.handle("nonexistent_tool", {})
        assert "Unknown agent tool" in result

    @pytest.mark.asyncio
    async def test_create_agent_success(self, handler, tmp_path):
        mock_store = ProfileStore(tmp_path / "agents")
        mock_orch = MagicMock()
        mock_orch._ensure_deps = MagicMock()
        mock_orch._profile_store = mock_store
        with patch.object(handler, '_get_orchestrator', return_value=mock_orch), \
             patch.object(handler, '_get_profile_store', return_value=mock_store):
            result = await handler.handle("create_agent", {
                "name": "SQL Expert",
                "description": "Handles SQL optimization",
                "force": True,
            })
            assert "Agent created" in result
            assert "ephemeral" in result.lower()

    @pytest.mark.asyncio
    async def test_create_agent_persistent(self, handler, tmp_path):
        mock_store = ProfileStore(tmp_path / "agents")
        mock_orch = MagicMock()
        mock_orch._ensure_deps = MagicMock()
        mock_orch._profile_store = mock_store
        with patch.object(handler, '_get_orchestrator', return_value=mock_orch), \
             patch.object(handler, '_get_profile_store', return_value=mock_store):
            result = await handler.handle("create_agent", {
                "name": "Persistent Bot",
                "description": "A lasting agent",
                "persistent": True,
                "force": True,
            })
            assert "persistent" in result.lower()
            assert "will be saved" in result

    @pytest.mark.asyncio
    async def test_spawn_base_not_found(self, handler, tmp_path):
        mock_store = ProfileStore(tmp_path / "agents")
        mock_orch = MagicMock()
        mock_orch._ensure_deps = MagicMock()
        mock_orch._profile_store = mock_store
        mock_orch.delegate = AsyncMock(return_value="spawned result")
        with patch.object(handler, '_get_orchestrator', return_value=mock_orch), \
             patch.object(handler, '_get_profile_store', return_value=mock_store):
            result = await handler.handle("spawn_agent", {
                "inherit_from": "nonexistent-base",
                "message": "do task",
            })
            assert "not found" in result

    @pytest.mark.asyncio
    async def test_spawn_success(self, handler, tmp_path):
        mock_store = ProfileStore(tmp_path / "agents")
        base = _make_profile(
            "browser-agent", "Browser",
            skills=["web_search"],
            custom_prompt="Browse the web",
        )
        mock_store.save(base)
        mock_orch = MagicMock()
        mock_orch._ensure_deps = MagicMock()
        mock_orch._profile_store = mock_store
        mock_orch.delegate = AsyncMock(return_value="spawned result")
        with patch.object(handler, '_get_orchestrator', return_value=mock_orch), \
             patch.object(handler, '_get_profile_store', return_value=mock_store):
            result = await handler.handle("spawn_agent", {
                "inherit_from": "browser-agent",
                "message": "search for React 19",
                "extra_skills": ["web_scrape"],
                "custom_prompt_overlay": "Focus on performance",
            })
            assert result == "spawned result"
            mock_orch.delegate.assert_awaited_once()
            # Verify ephemeral profile was created
            ephemeral_profiles = mock_store.list_all(include_ephemeral=True)
            assert any(p.ephemeral for p in ephemeral_profiles)
            eph = [p for p in ephemeral_profiles if p.ephemeral][0]
            assert "web_search" in eph.skills
            assert "web_scrape" in eph.skills
            assert "Focus on performance" in eph.custom_prompt


# ================================================================
# Integration: Full Delegation Flow
# ================================================================

class TestDelegationFlow:
    """Integration tests simulating realistic multi-agent workflows."""

    @pytest.fixture
    def setup(self, tmp_path):
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("coordinator", "Coordinator"))
        store.save(_make_profile("researcher", "Researcher"))
        store.save(_make_profile("coder", "Coder"))
        store.save(_make_profile(
            "weak-agent", "Weak Agent",
            fallback_profile_id="coordinator",
        ))

        pool = MagicMock()
        agents = {}

        async def get_or_create(session_id, profile):
            key = f"{session_id}::{profile.id}"
            if key not in agents:
                agent = MagicMock()
                agent._is_sub_agent_call = False
                agent._agent_profile = profile
                agent._last_finalized_trace = []
                agent.agent_state = None
                agent.chat_with_session = AsyncMock(
                    return_value=f"Response from {profile.id}"
                )
                agents[key] = agent
            return agents[key]

        pool.get_or_create = AsyncMock(side_effect=get_or_create)
        pool.start = AsyncMock()
        pool.stop = AsyncMock()

        fallback = FallbackResolver(store)

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._fallback = fallback
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()

        return orch, store, pool

    @pytest.mark.asyncio
    async def test_single_delegation(self, setup):
        orch, store, pool = setup
        session = _make_session(agent_profile_id="coordinator")
        result = await orch.delegate(
            session, "coordinator", "researcher",
            "Research Python 3.13 features"
        )
        assert "researcher" in result
        assert len(session.context.handoff_events) == 1

    @pytest.mark.asyncio
    async def test_chain_delegation(self, setup):
        orch, store, pool = setup
        session = _make_session(agent_profile_id="coordinator")

        r1 = await orch.delegate(
            session, "coordinator", "researcher", "Research topic A"
        )
        r2 = await orch.delegate(
            session, "coordinator", "coder", "Code feature B"
        )

        assert "researcher" in r1
        assert "coder" in r2
        assert len(session.context.handoff_events) == 2

    @pytest.mark.asyncio
    async def test_delegation_with_fallback(self, setup):
        """When agent is pre-degraded AND the dispatch fails, the orchestrator
        should fall back to the fallback profile.

        NOTE: Pre-degradation alone doesn't prevent a dispatch attempt — the
        agent is always tried first. Only if it fails will the fallback path
        be taken. A success resets the degraded flag (by design).
        """
        orch, store, pool = setup
        session = _make_session(agent_profile_id="weak-agent")

        # Pre-degrade by recording failures
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            orch._fallback.record_failure("weak-agent")
        assert orch._fallback.should_use_fallback("weak-agent") is True

        # Since the mock pool always returns a successful agent,
        # handle_message succeeds → record_success resets the degraded flag
        result = await orch.handle_message(session, "Please help")
        assert "weak-agent" in result  # dispatch succeeded
        assert orch._fallback.should_use_fallback("weak-agent") is False  # recovered

    @pytest.mark.asyncio
    async def test_delegation_fallback_on_actual_failure(self, setup):
        """When a pre-degraded agent fails, the fallback agent should be used."""
        orch, store, pool = setup
        session = _make_session(agent_profile_id="weak-agent")

        call_count = 0

        async def get_agent(session_id, profile):
            nonlocal call_count
            agent = MagicMock()
            agent._is_sub_agent_call = False
            agent._agent_profile = profile
            agent._last_finalized_trace = []
            agent.agent_state = None
            if profile.id == "weak-agent":
                call_count += 1

                async def fail(*a, **kw):
                    raise RuntimeError("weak agent down")

                agent.chat_with_session = fail
            else:
                agent.chat_with_session = AsyncMock(
                    return_value=f"Fallback handled: {profile.id}"
                )
            return agent

        pool.get_or_create = AsyncMock(side_effect=get_agent)

        # Trigger degradation: 3 consecutive failures
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            await orch.handle_message(session, "task")

        # Now the agent is degraded, next failure triggers fallback
        result = await orch.handle_message(session, "Please help")
        assert "coordinator" in result.lower() or "Fallback handled" in result

    @pytest.mark.asyncio
    async def test_health_metrics_accuracy(self, setup):
        orch, store, pool = setup
        session = _make_session(agent_profile_id="coordinator")

        for _ in range(3):
            await orch.handle_message(session, "task")

        stats = orch.get_health_stats()
        assert stats["coordinator"]["total_requests"] == 3
        assert stats["coordinator"]["successful"] == 3
        assert stats["coordinator"]["success_rate"] == 1.0


# ================================================================
# Edge Cases and Bug Detection
# ================================================================

class TestEdgeCasesAndBugs:
    """Tests targeting potential bugs and edge cases."""

    def test_fallback_failure_window_boundary(self):
        """BUG CHECK: Verify failure window correctly resets consecutive counter.

        The FallbackResolver uses time.monotonic() for the failure window.
        If failures span more than 300s, the counter should reset to 1.
        """
        store = MagicMock()
        store.get = MagicMock(return_value=None)
        resolver = FallbackResolver(store)

        resolver.record_failure("agent-a")
        resolver.record_failure("agent-a")
        entry = resolver._health["agent-a"]
        assert entry.consecutive_failures == 2

        entry.last_failure_time = time.monotonic() - 301
        resolver.record_failure("agent-a")
        assert entry.consecutive_failures == 1  # reset, not 3

    def test_fallback_get_effective_when_fallback_profile_missing(self, tmp_path):
        """BUG CHECK: If fallback_profile_id points to deleted profile,
        get_effective_profile should return original id.
        """
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("agent-a", "A", fallback_profile_id="deleted-profile"))
        resolver = FallbackResolver(store)
        for _ in range(_AUTO_DEGRADE_THRESHOLD):
            resolver.record_failure("agent-a")
        effective = resolver.get_effective_profile("agent-a")
        assert effective == "agent-a"  # should not crash

    @pytest.mark.asyncio
    async def test_orchestrator_concurrent_handle_message(self, tmp_path):
        """BUG CHECK: Concurrent handle_message calls for same session
        should not corrupt delegation_chain.
        """
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("default", "Default"))

        pool = MagicMock()
        mock_agent = MagicMock()
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None

        async def slow_chat(*args, **kwargs):
            await asyncio.sleep(0.1)
            return "ok"

        mock_agent.chat_with_session = slow_chat
        pool.get_or_create = AsyncMock(return_value=mock_agent)
        pool.start = AsyncMock()
        pool.stop = AsyncMock()

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._fallback = FallbackResolver(store)
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()

        session = _make_session()
        t1 = asyncio.create_task(orch.handle_message(session, "msg1"))
        t2 = asyncio.create_task(orch.handle_message(session, "msg2"))
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        assert all(r == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_delegation_depth_chain_not_accumulated_from_tool(self, tmp_path):
        """BUG CHECK: orchestrator.delegate() defaults depth=0, so each tool
        delegation starts a fresh depth counter at 1 (0+1).
        Verify the depth is actually tracked correctly.
        """
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("default", "Default"))
        store.save(_make_profile("helper", "Helper"))

        pool = MagicMock()
        mock_agent = MagicMock()
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent.chat_with_session = AsyncMock(return_value="done")
        pool.get_or_create = AsyncMock(return_value=mock_agent)

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._fallback = FallbackResolver(store)
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()

        session = _make_session()
        await orch.delegate(session, "default", "helper", "task", depth=0)
        chain = session.context.delegation_chain
        assert len(chain) == 1
        assert chain[0]["depth"] == 1  # depth 0+1

    def test_profile_store_system_immutable_validation(self, tmp_path):
        """BUG CHECK: Saving a SYSTEM profile with changed immutable fields
        (id, type, created_by) should raise PermissionError via _validate_system_update.
        """
        store = ProfileStore(tmp_path / "agents")
        sys_profile = _make_profile("sys", "System", agent_type=AgentType.SYSTEM)
        store.save(sys_profile)

        mutated = _make_profile(
            "sys", "System",
            agent_type=AgentType.CUSTOM,  # type is immutable on SYSTEM profiles
        )
        with pytest.raises(PermissionError):
            store.save(mutated)

    @pytest.mark.asyncio
    async def test_mailbox_max_size(self):
        """BUG CHECK: Mailbox should respect maxsize and block when full."""
        mb = AgentMailbox("agent-1", maxsize=2)
        await mb.send({"a": 1})
        await mb.send({"b": 2})
        assert mb.pending == 2
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(mb.send({"c": 3}), timeout=0.1)

    def test_pool_entry_idle_seconds(self):
        """Verify _PoolEntry.idle_seconds grows over time."""
        from openakita.agents.factory import _PoolEntry
        agent = MagicMock()
        entry = _PoolEntry(agent, "p1", "s1")
        time.sleep(0.05)
        assert entry.idle_seconds >= 0.04

    def test_pool_entry_touch_resets_idle(self):
        from openakita.agents.factory import _PoolEntry
        agent = MagicMock()
        entry = _PoolEntry(agent, "p1", "s1")
        time.sleep(0.05)
        entry.touch()
        assert entry.idle_seconds < 0.02

    @pytest.mark.asyncio
    async def test_orchestrator_ephemeral_cleanup_on_completion(self, tmp_path):
        """BUG CHECK: Ephemeral profiles should be cleaned up after task completion."""
        store = ProfileStore(tmp_path / "agents")
        eph = _make_profile("ephemeral_test_123", "Eph Test", ephemeral=True)
        store.save(eph)
        assert store.get("ephemeral_test_123") is not None

        orch = AgentOrchestrator()
        orch._profile_store = store

        orch._sub_agent_states["s1:ephemeral_test_123"] = {
            "agent_id": "ephemeral_test_123",
            "profile_id": "ephemeral_test_123",
            "status": "running",
        }
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()
        orch._update_sub_state("s1:ephemeral_test_123", "completed", 10.0)
        assert store.get("ephemeral_test_123") is None

    @pytest.mark.asyncio
    async def test_orchestrator_interrupted_state_on_reload(self, tmp_path):
        """BUG CHECK: Sub-agent states persisted as 'running' should become
        'interrupted' when loaded, to avoid stale 'running' indicators.
        """
        orch = AgentOrchestrator()
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()

        orch._sub_agent_states["s1:a1"] = {
            "agent_id": "a1",
            "status": "running",
        }
        orch._persist_sub_states()

        orch2 = AgentOrchestrator()
        orch2._log_dir = tmp_path / "logs"
        orch2._load_sub_states()
        assert orch2._sub_agent_states["s1:a1"]["status"] == "interrupted"

    def test_health_entry_first_failure_is_counted(self):
        """BUG CHECK: First failure should set consecutive_failures to 1,
        even though last_failure_time defaults to 0.0 (falsy).
        """
        from openakita.agents.fallback import _HealthEntry
        entry = _HealthEntry(profile_id="test")
        entry.record_failure()
        assert entry.consecutive_failures == 1
        assert entry.last_failure_time > 0

    def test_agent_health_success_rate_edge_cases(self):
        h = AgentHealth(agent_id="a")
        assert h.success_rate == 0.0  # 0/max(0,1)
        h.total_requests = 1
        h.successful = 1
        assert h.success_rate == 1.0

    @pytest.mark.asyncio
    async def test_delegate_parallel_duplicate_ids_clone(self, tmp_path):
        """BUG CHECK: delegate_parallel with duplicate agent_ids should
        auto-create ephemeral clones for ALL occurrences of the duplicate.
        """
        from openakita.tools.handlers.agent import AgentToolHandler

        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("browser-agent", "Browser Agent"))

        agent = MagicMock()
        agent._is_sub_agent_call = False
        session = _make_session()
        session.context.agent_profile_id = "default"
        agent._current_session = session

        mock_orch = MagicMock()
        mock_orch._ensure_deps = MagicMock()
        mock_orch._profile_store = store
        mock_orch.delegate = AsyncMock(return_value="done")

        handler = AgentToolHandler(agent)

        with patch.object(handler, '_get_orchestrator', return_value=mock_orch), \
             patch.object(handler, '_get_profile_store', return_value=store):
            result = await handler.handle("delegate_parallel", {
                "tasks": [
                    {"agent_id": "browser-agent", "message": "Research topic A"},
                    {"agent_id": "browser-agent", "message": "Research topic B"},
                ],
            })
            assert "Agent: browser-agent" in result
            assert mock_orch.delegate.await_count == 2

            # Ephemeral clones are cleaned up after delegate_parallel completes
            # (Issue #7 fix), so they should no longer be in the store
            ephemeral_profiles = store.list_all(include_ephemeral=True)
            eph_count = sum(1 for p in ephemeral_profiles if p.ephemeral)
            assert eph_count == 0

    def test_session_context_multi_agent_serialization(self):
        """BUG CHECK: Multi-agent fields should survive serialization roundtrip."""
        ctx = SessionContext()
        ctx.agent_profile_id = "special-agent"
        ctx.delegation_chain = [{"from": "a", "to": "b", "depth": 1}]
        ctx.sub_agent_records = [{"agent_id": "sub1", "result_preview": "ok"}]
        ctx.handoff_events = [{"from_agent": "a", "to_agent": "b"}]
        ctx.active_agents = ["a", "b"]

        d = ctx.to_dict()
        restored = SessionContext.from_dict(d)

        assert restored.agent_profile_id == "special-agent"
        assert restored.delegation_chain == ctx.delegation_chain
        assert restored.sub_agent_records == ctx.sub_agent_records
        assert restored.handoff_events == ctx.handoff_events
        assert restored.active_agents == ["a", "b"]

    def test_session_multi_agent_fields_in_session_dict(self):
        """BUG CHECK: Session.to_dict should include all multi-agent context."""
        session = _make_session()
        session.context.agent_profile_id = "special"
        session.context.sub_agent_records = [{"test": "data"}]
        d = session.to_dict()
        assert d["context"]["agent_profile_id"] == "special"
        assert d["context"]["sub_agent_records"] == [{"test": "data"}]

    @pytest.mark.asyncio
    async def test_orchestrator_max_tracked_agents_eviction(self):
        """BUG CHECK: When > 200 agents tracked, stale ones should be evicted."""
        orch = AgentOrchestrator()
        for i in range(210):
            h = orch._get_health(f"agent-{i}")
            h.last_active = time.time() - i
        assert len(orch._health) <= 200

    @pytest.mark.asyncio
    async def test_progress_fingerprint_with_no_state(self):
        """The fingerprint should handle agents with no state gracefully."""
        agent = MagicMock(spec=[])  # no agent_state attribute
        fp = AgentOrchestrator._get_progress_fingerprint(agent, "s1")
        assert fp == (-1, "", 0)

    @pytest.mark.asyncio
    async def test_tools_executed_with_no_state(self):
        """_get_tools_executed should return empty list for agents with no state."""
        agent = MagicMock(spec=[])
        tools = AgentOrchestrator._get_tools_executed(agent, "s1")
        assert tools == []


# ================================================================
# Concurrency Stress Tests
# ================================================================

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_pool_concurrent_get_or_create(self, tmp_path):
        """Multiple coroutines requesting the same pool key should
        only create one agent instance.
        """
        from openakita.agents.factory import AgentInstancePool

        create_count = 0

        async def mock_create(profile, **kwargs):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(0.05)
            agent = MagicMock()
            return agent

        factory = MagicMock()
        factory.create = mock_create
        pool = AgentInstancePool(factory=factory)

        profile = _make_profile("agent-a", "A")
        tasks = [
            pool.get_or_create("session-1", profile)
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks)
        assert create_count == 1
        assert all(r is results[0] for r in results)

    @pytest.mark.asyncio
    async def test_fallback_resolver_thread_safety(self, tmp_path):
        """Concurrent success/failure recording should not corrupt state."""
        store = ProfileStore(tmp_path / "agents")
        store.save(_make_profile("agent", "Agent"))
        resolver = FallbackResolver(store)

        errors = []

        def record_ops():
            try:
                for _ in range(100):
                    resolver.record_failure("agent")
                    resolver.record_success("agent")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_ops) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = resolver.get_health_stats()
        assert stats["agent"]["total_requests"] == 800  # 4 threads × 100 × 2

    @pytest.mark.asyncio
    async def test_multiple_delegations_concurrent(self, tmp_path):
        """Multiple concurrent delegations to different agents
        should all complete without interference.
        """
        store = ProfileStore(tmp_path / "agents")
        for i in range(5):
            store.save(_make_profile(f"agent-{i}", f"Agent {i}"))

        pool = MagicMock()

        async def mock_get_or_create(session_id, profile):
            agent = MagicMock()
            agent._is_sub_agent_call = False
            agent._agent_profile = profile
            agent._last_finalized_trace = []
            agent.agent_state = None

            async def chat(*args, **kwargs):
                await asyncio.sleep(0.05)
                return f"result-{profile.id}"

            agent.chat_with_session = chat
            return agent

        pool.get_or_create = AsyncMock(side_effect=mock_get_or_create)
        pool.start = AsyncMock()
        pool.stop = AsyncMock()

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._fallback = FallbackResolver(store)
        orch._log_dir = tmp_path / "logs"
        orch._log_dir.mkdir()

        session = _make_session()
        tasks = [
            orch.delegate(session, "coordinator", f"agent-{i}", f"task-{i}")
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        for i, r in enumerate(results):
            assert f"agent-{i}" in r
