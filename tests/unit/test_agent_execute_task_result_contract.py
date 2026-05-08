from types import SimpleNamespace

import pytest

from openakita.core.agent import (
    Agent,
    _looks_like_progress_only_task_text,
    _prefer_task_final_response,
)
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


def test_task_final_response_prefers_substantive_report_over_meta_summary():
    full_report = """## 晚间资讯报告

- 中东局势升级，油价波动加剧
- A 股收盘回落，避险资产走强

### 明日关注
继续关注能源价格和政策发布。"""
    meta_summary = "已完成今日晚间资讯播报任务。晚报已整理完毕，系统会自动推送。"

    assert _prefer_task_final_response(full_report, "") is True
    assert _prefer_task_final_response(meta_summary, full_report) is False


def test_task_progress_text_gets_one_gentle_continue_chance():
    assert _looks_like_progress_only_task_text("我来执行今日资讯播报任务，先访问凤凰网。")
    assert not _looks_like_progress_only_task_text("## 今日资讯\n\n- 已整理三条关键新闻")
