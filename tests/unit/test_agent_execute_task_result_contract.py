from types import SimpleNamespace

import pytest

from openakita.agent.core import Agent
from openakita.core.intent_analyzer import IntentResult, IntentType
from openakita.core.ralph import TaskResult
from openakita.llm.types import AllEndpointsFailedError


class _IntentAnalyzer:
    async def analyze(self, message, **_kwargs):
        return IntentResult(
            intent=IntentType.TASK,
            task_definition=message,
            requires_tools=True,
            force_tool=True,
        )


def _make_task_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent._initialized = True
    agent._current_session_id = None
    agent._tools = []
    agent._is_sub_agent_call = False
    agent._agent_tool_names = set()
    agent._cron_disabled_tools = set()
    agent._selfcheck_allowed_tools = None
    agent._discovered_tools = set()
    agent._intent_analyzer = _IntentAnalyzer()
    agent._get_raw_context_window = lambda: 0
    agent._resolve_agent_voice = lambda: "OpenAkita"
    agent._build_system_prompt_compiled = _async_value("system")
    agent.brain = SimpleNamespace(
        model="test-model",
        get_fallback_model=lambda _session_id=None: None,
        restore_default_model=lambda **_kwargs: None,
    )
    return agent


def _async_value(value):
    async def _value(*_args, **_kwargs):
        return value

    return _value


@pytest.mark.asyncio
async def test_execute_task_delegates_failure_to_reasoning_engine():
    agent = _make_task_agent()

    async def _fail(*_args, **_kwargs):
        raise AllEndpointsFailedError(
            "All endpoints failed: deepseek unavailable",
            is_structural=True,
        )

    agent.reasoning_engine = SimpleNamespace(
        run=_fail,
        _last_react_trace=[],
        _last_exit_reason="stream_error",
    )

    result = await agent.execute_task_from_message("你好")

    assert isinstance(result, TaskResult)
    assert result.success is False
    assert result.error is not None
    assert "All endpoints failed" in result.error


@pytest.mark.asyncio
async def test_execute_task_returns_stream_core_result():
    agent = _make_task_agent()
    calls = []

    async def _run(messages, **kwargs):
        calls.append((messages, kwargs))
        return "任务完成"

    agent.reasoning_engine = SimpleNamespace(
        run=_run,
        _last_react_trace=[{"iteration": 1}],
        _last_exit_reason="normal",
    )

    result = await agent.execute_task_from_message("整理资讯")

    assert result.success is True
    assert result.data == "任务完成"
    assert result.iterations == 1
    assert calls[0][0] == [{"role": "user", "content": "整理资讯"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_reason", ["user_cancelled", "stream_incomplete", "waiting_user"])
async def test_execute_task_does_not_complete_for_non_terminal_stream_exit(exit_reason):
    agent = _make_task_agent()

    async def _run(*_args, **_kwargs):
        return "部分输出"

    agent.reasoning_engine = SimpleNamespace(
        run=_run,
        _last_react_trace=[{"iteration": 1}],
        _last_exit_reason=exit_reason,
    )

    result = await agent.execute_task_from_message("整理资讯")

    assert result.success is False
    assert result.data is None
    assert result.iterations == 1
