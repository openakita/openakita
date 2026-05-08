"""Regression tests for stopping when tool tasks need user input."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openakita.core.agent_state import AgentState
from openakita.core.reasoning_engine import (
    Decision,
    DecisionType,
    ReasoningEngine,
    _looks_like_waiting_for_user_response,
)


def test_detects_user_handoff_blocker_text():
    assert _looks_like_waiting_for_user_response(
        "我已经登录并定位到患者页面，但新增患者弹窗无法打开。请你手动截图发给我，我再继续。"
    )


def test_does_not_treat_plain_completion_summary_as_handoff():
    assert not _looks_like_waiting_for_user_response(
        "已完成网站操作手册初版，包含首页、患者、预约和设置模块的主要入口。请你查看。"
    )


@pytest.mark.asyncio
async def test_user_handoff_reply_skips_completion_verify():
    response_handler = AsyncMock()
    response_handler.verify_task_completion = AsyncMock(return_value=False)
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=response_handler,
        agent_state=AgentState(),
    )

    reply = "浏览器已被用户关闭，我不能继续操作。请确认是否重新打开浏览器后我再继续。"
    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=[],
        original_messages=[{"role": "user", "content": "继续操作网站"}],
        tools_executed_in_task=True,
        executed_tool_names=["browser_navigate"],
        delivery_receipts=[],
        all_tool_results=[
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "浏览器连接已断开（可能被用户关闭）。",
                "is_error": True,
            }
        ],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
    )

    assert result == reply
    assert engine._last_exit_reason == "waiting_user"
    response_handler.verify_task_completion.assert_not_called()


@pytest.mark.asyncio
async def test_verify_incomplete_exhaustion_is_marked_non_normal():
    response_handler = AsyncMock()
    response_handler.verify_task_completion = AsyncMock(return_value=False)
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=response_handler,
        agent_state=AgentState(),
    )

    reply = "我排查了日志，但还没有定位到所有警告来源。"
    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=[],
        original_messages=[{"role": "user", "content": "排查日志里的警告原因"}],
        tools_executed_in_task=True,
        executed_tool_names=["read_file"],
        delivery_receipts=[],
        all_tool_results=[
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "日志内容",
                "is_error": False,
            }
        ],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
    )

    assert result == reply
    assert engine._last_exit_reason == "verify_incomplete"
    response_handler.verify_task_completion.assert_awaited_once()
