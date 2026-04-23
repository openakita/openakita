from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_runner import ExitReason, ExternalCliLimiter, ProviderRunResult
from openakita.agents.external_cli import ExternalCliAgent
from openakita.agents.profile import AgentProfile, AgentType
from openakita.core.agent_state import TaskStatus


def _profile() -> AgentProfile:
    return AgentProfile(
        id="cli-test",
        name="CLI Test",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
    )


@pytest.mark.asyncio
async def test_external_cli_progress_updates_agent_state():
    captured = {}

    async def run(req, argv, env, *, on_spawn):
        captured["request"] = req
        await req.on_progress("assistant_text", text="working")
        await req.on_progress("tool_use", tool_name="Edit")
        await req.on_progress("assistant_text", text="done")
        return ProviderRunResult(
            final_text="done",
            tools_used=["Edit"],
            artifacts=[],
            session_id="resume-1",
            input_tokens=0,
            output_tokens=0,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    adapter = MagicMock()
    adapter.build_argv.return_value = ["fake"]
    adapter.build_env.return_value = {}
    adapter.run = run
    adapter.cleanup = AsyncMock()

    agent = ExternalCliAgent(_profile(), adapter, limiter=ExternalCliLimiter(1))
    session = MagicMock(id="sid-1", conversation_id="conv-1", cwd=str(Path.cwd()))
    result = await agent.chat_with_session(session, "do work")

    task = agent.agent_state.current_task
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.iteration == 1
    assert task.tools_executed == ["Edit"]
    assert result["text"] == "done"
    assert callable(captured["request"].on_progress)


@pytest.mark.asyncio
async def test_external_cli_cancel_sets_event_and_terminates_runner():
    adapter = MagicMock()
    adapter.build_argv.return_value = ["fake"]
    adapter.build_env.return_value = {}
    adapter.run = AsyncMock()
    adapter.cleanup = AsyncMock()

    agent = ExternalCliAgent(_profile(), adapter, limiter=ExternalCliLimiter(1))
    agent._runner.terminate_and_wait = AsyncMock()

    await agent.cancel()

    assert agent._cancelled.is_set()
    agent._runner.terminate_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_cli_returned_cancelled_result_cancels_task_and_terminates_runner():
    adapter = MagicMock()
    adapter.build_argv.return_value = ["fake"]
    adapter.build_env.return_value = {}
    adapter.run = AsyncMock(
        return_value=ProviderRunResult(
            final_text="cancelled",
            tools_used=[],
            artifacts=[],
            session_id="cancelled-resume",
            input_tokens=0,
            output_tokens=0,
            exit_reason=ExitReason.CANCELLED,
            errored=False,
            error_message=None,
        )
    )
    adapter.cleanup = AsyncMock()

    agent = ExternalCliAgent(_profile(), adapter, limiter=ExternalCliLimiter(1))
    agent.last_session_id = "previous-resume"
    agent._runner.terminate_and_wait = AsyncMock()
    session = MagicMock(id="sid-1", conversation_id="conv-1", cwd=str(Path.cwd()))

    result = await agent.chat_with_session(session, "do work")

    task = agent.agent_state.current_task
    assert task is not None
    assert task.status == TaskStatus.CANCELLED
    assert result["exit_reason"] == "cancelled"
    assert agent.last_session_id == "previous-resume"
    assert agent._cancelled.is_set()
    agent._runner.terminate_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_cli_requested_cancel_wins_over_returned_completed_result():
    async def run(req, argv, env, *, on_spawn):
        await agent.cancel()
        return ProviderRunResult(
            final_text="completed after cancel",
            tools_used=[],
            artifacts=[],
            session_id="cancelled-completed-resume",
            input_tokens=0,
            output_tokens=0,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    adapter = MagicMock()
    adapter.build_argv.return_value = ["fake"]
    adapter.build_env.return_value = {}
    adapter.run = run
    adapter.cleanup = AsyncMock()

    agent = ExternalCliAgent(_profile(), adapter, limiter=ExternalCliLimiter(1))
    agent.last_session_id = "previous-resume"
    agent._runner.terminate_and_wait = AsyncMock()
    session = MagicMock(id="sid-1", conversation_id="conv-1", cwd=str(Path.cwd()))

    result = await agent.chat_with_session(session, "do work")

    task = agent.agent_state.current_task
    assert task is not None
    assert task.status == TaskStatus.CANCELLED
    assert result["exit_reason"] == "cancelled"
    assert agent.last_session_id == "previous-resume"
    agent._runner.terminate_and_wait.assert_awaited()


@pytest.mark.parametrize(
    "terminal_status",
    [TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.COMPLETED],
)
@pytest.mark.asyncio
async def test_external_cli_late_progress_after_terminal_task_does_not_resurrect_task(
    terminal_status,
):
    adapter = MagicMock()
    adapter.build_argv.return_value = ["fake"]
    adapter.build_env.return_value = {}
    adapter.run = AsyncMock()
    adapter.cleanup = AsyncMock()

    agent = ExternalCliAgent(_profile(), adapter, limiter=ExternalCliLimiter(1))
    task = agent.agent_state.begin_task(session_id="sid-1", conversation_id="conv-1")
    task.transition(TaskStatus.REASONING)
    if terminal_status == TaskStatus.CANCELLED:
        task.cancel("already cancelled")
    else:
        task.transition(terminal_status)

    await agent._handle_progress("tool_use", tool_name="Edit")
    await agent._handle_progress("assistant_text", text="late text")

    assert task.status == terminal_status
    assert task.tools_executed == []
