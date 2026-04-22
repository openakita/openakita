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
    _TurnOutcome,
)
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
