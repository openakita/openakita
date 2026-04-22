"""Tests for AgentFactory EXTERNAL_CLI branch + Pool special-case."""
from __future__ import annotations

import pytest

from openakita.config import Settings


def test_settings_external_cli_max_concurrent_default():
    s = Settings()
    assert s.external_cli_max_concurrent == 3


from openakita.agents.factory import AgentFactory
from openakita.agents.cli_runner import ExternalCliLimiter


def test_factory_builds_external_cli_limiter_from_settings(monkeypatch):
    from openakita import config as cfg
    monkeypatch.setattr(cfg.settings, "external_cli_max_concurrent", 7, raising=False)

    factory = AgentFactory()

    assert isinstance(factory._external_cli_limiter, ExternalCliLimiter)
    assert factory._external_cli_limiter._max_concurrent == 7


# ---------------------------------------------------------------------------
# Task 3 — EXTERNAL_CLI branch in create()
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

from openakita.agents.profile import AgentProfile, AgentType, CliProviderId
from openakita.agents.external_cli import ExternalCliAgent


def _make_cli_profile(**overrides) -> AgentProfile:
    defaults = dict(
        id="claude-code-pair",
        name="Claude Code Pair",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
        mcp_servers=["alpha", "beta"],
        mcp_mode="all",
    )
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
