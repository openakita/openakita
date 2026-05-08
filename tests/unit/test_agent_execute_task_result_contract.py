from types import SimpleNamespace

import pytest

from openakita.core.agent import Agent
from openakita.core.ralph import TaskResult
from openakita.llm.types import AllEndpointsFailedError


def _make_task_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent._initialized = True
    agent._current_session_id = None
    agent._context = SimpleNamespace(system="")
    agent._tools = []
    agent._is_sub_agent_call = False
    agent._agent_tool_names = set()
    agent.agent_state = None
    agent.brain = SimpleNamespace(
        model="test-model",
        max_tokens=1000,
        get_fallback_model=lambda _session_id=None: None,
        restore_default_model=lambda **_kwargs: None,
    )
    return agent


@pytest.mark.asyncio
async def test_execute_task_from_message_returns_task_result_on_llm_failure():
    agent = _make_task_agent()

    async def _fail_llm(*_args, **_kwargs):
        raise AllEndpointsFailedError(
            "All endpoints failed: deepseek unavailable",
            is_structural=True,
        )

    agent._cancellable_llm_call = _fail_llm

    result = await agent.execute_task_from_message("你好")

    assert isinstance(result, TaskResult)
    assert result.success is False
    assert result.error is not None
    assert "All endpoints failed" in result.error
