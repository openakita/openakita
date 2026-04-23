from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_runner import (
    ExitReason,
    ExternalCliLimiter,
    ProviderRunResult,
)
from openakita.agents.external_cli import (
    _NULL_BRAIN,
    ExternalCliAgent,
    _NullBrain,
)
from openakita.agents.orchestrator import AgentOrchestrator
from openakita.agents.profile import (
    AgentProfile,
    AgentType,
    CliPermissionMode,
)
from openakita.agents.protocols import AgentLike, BrainLike


def test_null_brain_singleton_is_brainlike():
    assert isinstance(_NULL_BRAIN, BrainLike)
    _NULL_BRAIN.append_user("anything")
    _NULL_BRAIN.append_assistant("anything")
    _NULL_BRAIN.append_tool_result("anything")
    assert _NULL_BRAIN.is_loaded() is False


def test_null_brain_class_and_singleton_distinct():
    assert isinstance(_NullBrain(), BrainLike)
    assert _NULL_BRAIN is _NULL_BRAIN


@pytest.fixture
def cli_profile():
    return AgentProfile(
        id="cli-test",
        name="CLI Test",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
        cli_permission_mode=CliPermissionMode.WRITE,
    )


@pytest.fixture
def stub_adapter():
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=["claude"])
    adapter.build_env = MagicMock(return_value={})
    adapter.run = AsyncMock(return_value=ProviderRunResult(
        final_text="ok",
        tools_used=["Edit"],
        artifacts=["a.py"],
        session_id="sid-1",
        input_tokens=10,
        output_tokens=20,
        exit_reason=ExitReason.COMPLETED,
        errored=False,
        error_message=None,
    ))
    adapter.cleanup = AsyncMock()
    return adapter


def test_external_cli_agent_satisfies_protocol(cli_profile, stub_adapter):
    agent = ExternalCliAgent(
        cli_profile, stub_adapter, mcp_servers_filtered=[],
        limiter=ExternalCliLimiter(1),
    )
    assert isinstance(agent, AgentLike)


@pytest.mark.asyncio
async def test_chat_with_session_returns_delegation_result(cli_profile, stub_adapter):
    agent = ExternalCliAgent(cli_profile, stub_adapter,
                             limiter=ExternalCliLimiter(1))
    session = MagicMock(id="sid-x", conversation_id="conv-x", cwd="/tmp")
    result = await agent.chat_with_session(session, "hello")
    assert result["text"] == "ok"
    assert result["tools_used"] == ["Edit"]
    assert result["artifacts"] == ["a.py"]
    assert result["exit_reason"] == "completed"
    assert result["profile_id"] == "cli-test"
    assert agent.last_session_id == "sid-1"


@pytest.mark.asyncio
async def test_execute_task_from_message_maps_to_task_result(cli_profile, stub_adapter):
    agent = ExternalCliAgent(cli_profile, stub_adapter,
                             limiter=ExternalCliLimiter(1))
    result = await agent.execute_task_from_message("do work")
    assert result["success"] is True
    assert result["data"] == "ok"
    assert result["iterations"] == 1


@pytest.mark.asyncio
async def test_resume_prompt_prefixes_custom_suffix(cli_profile, stub_adapter):
    agent = ExternalCliAgent(cli_profile, stub_adapter,
                             limiter=ExternalCliLimiter(1))
    agent._custom_prompt_suffix = "ORG FACT"
    agent.last_session_id = "sid-1"  # simulate resume turn

    request_captured: dict = {}

    async def capture(req, argv, env, *, on_spawn):
        request_captured["req"] = req
        return ProviderRunResult(
            final_text="ok", tools_used=[], artifacts=[], session_id="sid-1",
            input_tokens=0, output_tokens=0, exit_reason=ExitReason.COMPLETED,
            errored=False, error_message=None,
        )

    stub_adapter.run = capture
    await agent.execute_task_from_message("user query")
    # On resume turns, suffix is prepended to message; system_prompt_extra is blank
    assert request_captured["req"].system_prompt_extra == ""
    assert request_captured["req"].message == "ORG FACT\n\nuser query"


@pytest.mark.asyncio
async def test_first_turn_sends_suffix_as_system_extra(cli_profile, stub_adapter):
    agent = ExternalCliAgent(cli_profile, stub_adapter,
                             limiter=ExternalCliLimiter(1))
    agent._custom_prompt_suffix = "ORG FACT"
    captured: dict = {}

    async def capture(req, argv, env, *, on_spawn):
        captured["req"] = req
        return ProviderRunResult(
            final_text="ok", tools_used=[], artifacts=[], session_id="sid-fresh",
            input_tokens=0, output_tokens=0, exit_reason=ExitReason.COMPLETED,
            errored=False, error_message=None,
        )

    stub_adapter.run = capture
    await agent.execute_task_from_message("user query")
    # First turn routes the suffix through system_prompt_extra so the adapter
    # can inject it as --system-prompt or AGENTS.override.md
    assert captured["req"].system_prompt_extra == "ORG FACT"
    assert captured["req"].message == "user query"


@pytest.mark.asyncio
async def test_shutdown_runs_adapter_cleanup(cli_profile, stub_adapter):
    agent = ExternalCliAgent(cli_profile, stub_adapter,
                             limiter=ExternalCliLimiter(1))
    await agent.shutdown()
    stub_adapter.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_accepts_structured_external_cli_result(cli_profile):
    class StructuredCliAgent:
        def __init__(self) -> None:
            self._agent_profile = cli_profile
            self.agent_state = MagicMock()
            self.agent_state.get_task_for_session.return_value = None
            self.agent_state.current_task = None

        async def chat_with_session(self, **kwargs):
            return {
                "text": "implemented fix",
                "tools_used": ["Edit"],
                "artifacts": ["src/openakita/example.py"],
                "elapsed_s": 0.2,
                "exit_reason": "completed",
            }

    session = MagicMock()
    session.id = "session-1"
    session.context.get_messages.return_value = []

    result = await AgentOrchestrator._call_agent(
        StructuredCliAgent(),
        session,
        "repair delegation",
    )

    assert "implemented fix" in result
    assert "Tool calls: 1 (Edit)" in result
    assert "src/openakita/example.py" in result
