"""Tests for AgentFactory EXTERNAL_CLI branch + Pool special-case."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.agents.cli_runner import ExternalCliLimiter
from openakita.agents.external_cli import ExternalCliAgent
from openakita.agents.factory import AgentFactory
from openakita.agents.profile import AgentProfile, AgentType, CliProviderId
from openakita.config import Settings


def test_settings_external_cli_max_concurrent_default():
    s = Settings()
    assert s.external_cli_max_concurrent == 3


def test_factory_builds_external_cli_limiter_from_settings(monkeypatch):
    from openakita import config as cfg
    monkeypatch.setattr(cfg.settings, "external_cli_max_concurrent", 7, raising=False)

    factory = AgentFactory()

    assert isinstance(factory._external_cli_limiter, ExternalCliLimiter)
    assert factory._external_cli_limiter._max_concurrent == 7


# ---------------------------------------------------------------------------
# Task 3 — EXTERNAL_CLI branch in create()
# ---------------------------------------------------------------------------


def _make_cli_profile(**overrides) -> AgentProfile:
    defaults = {
        "id": "claude-code-pair",
        "name": "Claude Code Pair",
        "type": AgentType.EXTERNAL_CLI,
        "cli_provider_id": CliProviderId.CLAUDE_CODE,
        "mcp_servers": ["alpha", "beta"],
        "mcp_mode": "all",
    }
    defaults.update(overrides)
    return AgentProfile(**defaults)


@pytest.mark.asyncio
async def test_create_external_cli_returns_external_cli_agent(monkeypatch):
    # Replace the adapter registry with a fake so we don't hit real CLIs.
    fake_provider = MagicMock(name="ClaudeCodeFake")
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, fake_provider
    )

    factory = AgentFactory()
    agent = await factory.create(_make_cli_profile())

    assert isinstance(agent, ExternalCliAgent)
    assert agent.profile.id == "claude-code-pair"
    assert agent._adapter is fake_provider
    assert agent._runner._limiter is factory._external_cli_limiter


@pytest.mark.asyncio
async def test_create_external_cli_applies_mcp_filter(monkeypatch):
    """MCP filter still runs — the filtered list is passed to ExternalCliAgent."""
    fake_provider = MagicMock(name="ClaudeCodeFake")
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, fake_provider
    )

    factory = AgentFactory()
    # Profile declares servers alpha, beta; INCLUSIVE filter keeps only alpha.
    profile = _make_cli_profile(mcp_mode="inclusive", mcp_servers=["alpha"])
    agent = await factory.create(profile)

    assert agent._mcp_servers == ("alpha",)


@pytest.mark.asyncio
async def test_create_external_cli_skips_skill_and_tool_filter(monkeypatch):
    """Skill/tool/plugin/identity/memory filters must NOT run for EXTERNAL_CLI —
    the external process owns its own tool belt."""
    fake_provider = MagicMock(name="ClaudeCodeFake")
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, fake_provider
    )
    factory = AgentFactory()

    called = {"skill": 0, "tool": 0, "plugin": 0, "identity": 0, "memory": 0}
    monkeypatch.setattr(factory, "_apply_skill_filter",
                        lambda *a, **k: called.__setitem__("skill", called["skill"] + 1))
    monkeypatch.setattr(factory, "_apply_tool_filter",
                        lambda *a, **k: called.__setitem__("tool", called["tool"] + 1))
    async def _noop_plugin(*a, **k): called["plugin"] += 1
    monkeypatch.setattr(factory, "_apply_plugin_filter", _noop_plugin)
    monkeypatch.setattr(factory, "_apply_identity_override",
                        lambda *a, **k: called.__setitem__("identity", called["identity"] + 1))
    monkeypatch.setattr(factory, "_apply_memory_isolation",
                        lambda *a, **k: called.__setitem__("memory", called["memory"] + 1))

    await factory.create(_make_cli_profile())

    assert called == {"skill": 0, "tool": 0, "plugin": 0, "identity": 0, "memory": 0}


@pytest.mark.asyncio
async def test_create_external_cli_requires_cli_provider_id(monkeypatch):
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, MagicMock()
    )
    factory = AgentFactory()
    profile = _make_cli_profile(cli_provider_id=None)

    with pytest.raises(ValueError, match="cli_provider_id"):
        await factory.create(profile)


# ---------------------------------------------------------------------------
# Task 4 — Pool skips brain sharing for EXTERNAL_CLI
# ---------------------------------------------------------------------------

from openakita.agents.factory import AgentInstancePool


class _FakeNativeAgent:
    """Minimal stand-in so the pool thinks this is a native Agent with a brain."""
    def __init__(self, profile_id: str):
        self._agent_profile = _make_cli_profile(id=profile_id, type=AgentType.CUSTOM,
                                                cli_provider_id=None)
        self.brain = MagicMock(name=f"brain-for-{profile_id}")

    async def shutdown(self):
        pass


@pytest.mark.asyncio
async def test_pool_skips_brain_sharing_for_external_cli(monkeypatch):
    """When creating an EXTERNAL_CLI agent, the pool must NOT pass
    parent_brain to factory.create() even if native agents are in the pool."""
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, MagicMock()
    )

    factory = AgentFactory()
    pool = AgentInstancePool(factory=factory)

    # Pre-seed the pool with a native agent so _find_parent_brain has something to return.
    from openakita.agents.factory import _PoolEntry
    native = _FakeNativeAgent("default")
    pool._pool["sess-1::default"] = _PoolEntry(native, "default", "sess-1", 0)

    captured = {}
    orig_create = factory.create
    async def spy_create(prof, *, parent_brain=None, **kw):
        captured["parent_brain"] = parent_brain
        return await orig_create(prof, parent_brain=parent_brain, **kw)
    monkeypatch.setattr(factory, "create", spy_create)

    cli_profile = _make_cli_profile(id="claude-code-pair")
    agent = await pool.get_or_create("sess-1", cli_profile)

    assert isinstance(agent, ExternalCliAgent)
    assert captured["parent_brain"] is None, "EXTERNAL_CLI must not inherit a parent brain"


@pytest.mark.asyncio
async def test_pool_caches_external_cli_by_session_profile_key(monkeypatch):
    from openakita.agents import cli_providers
    monkeypatch.setitem(
        cli_providers.PROVIDERS, CliProviderId.CLAUDE_CODE, MagicMock()
    )

    factory = AgentFactory()
    pool = AgentInstancePool(factory=factory)
    profile = _make_cli_profile()

    first = await pool.get_or_create("sess-1", profile)
    second = await pool.get_or_create("sess-1", profile)

    assert first is second
    assert "sess-1::claude-code-pair" in pool._pool


# ---------------------------------------------------------------------------
# Task 5 — Regression: native agent path unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_native_agent_path_unchanged(monkeypatch):
    """Non-EXTERNAL_CLI profiles must still go through the normal filter chain."""
    from openakita.agents.profile import AgentProfile as _AP

    factory = AgentFactory()

    # Replace Agent constructor + initialize with lightweight fakes so we don't
    # spin up the real Agent machinery — we only care that the EXTERNAL_CLI
    # branch is NOT taken for type=CUSTOM.
    created_native = {"called": False}

    class _FakeAgent:
        def __init__(self, *a, **kw):
            created_native["called"] = True
            self._agent_profile = None
            self.tool_catalog = None
            self.mcp_catalog = None
            self.prompt_assembler = None
        async def initialize(self, **kw): pass

    import openakita.core.agent as core_agent_module
    monkeypatch.setattr(core_agent_module, "Agent", _FakeAgent)

    # Bypass the filter body — we only check that the native branch was chosen.
    monkeypatch.setattr(factory, "_apply_skill_filter", lambda *a, **k: None)
    monkeypatch.setattr(factory, "_apply_tool_filter", lambda *a, **k: None)
    monkeypatch.setattr(factory, "_apply_mcp_filter", lambda *a, **k: None)
    async def _noop_plugin(*a, **k): pass
    monkeypatch.setattr(factory, "_apply_plugin_filter", _noop_plugin)

    native_profile = _AP(
        id="default",
        name="Default",
        type=AgentType.CUSTOM,
    )
    agent = await factory.create(native_profile)

    assert created_native["called"] is True
    assert not isinstance(agent, ExternalCliAgent)
