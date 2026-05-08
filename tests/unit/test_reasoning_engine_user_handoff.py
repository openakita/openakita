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
async def test_recoverable_tool_error_does_not_become_user_handoff():
    response_handler = AsyncMock()
    response_handler.verify_task_completion = AsyncMock(return_value=False)
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=response_handler,
        agent_state=AgentState(),
    )
    working_messages = []
    reply = "?????????????????????"

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "??????????"}],
        tools_executed_in_task=True,
        executed_tool_names=["browser_fill"],
        delivery_receipts=[],
        all_tool_results=[
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "? ????: browser_fill???????: browser_type?",
                "is_error": True,
            }
        ],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=2,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
    )

    assert isinstance(result, tuple)
    assert engine._last_exit_reason != "waiting_user"
    response_handler.verify_task_completion.assert_awaited_once()
    assert working_messages[-1]["role"] == "user"


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


@pytest.mark.asyncio
async def test_tool_evidence_required_blocks_implicit_long_reply_without_tools():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    working_messages = []
    reply = "这是一段看起来完整的分析。" * 20

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "分析这个 GitHub issue 是否仍存在"}],
        tools_executed_in_task=False,
        executed_tool_names=[],
        delivery_receipts=[],
        all_tool_results=[],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
        tool_evidence_required=True,
    )

    assert isinstance(result, tuple)
    assert result[1] == 1
    assert working_messages[-1]["role"] == "user"
    assert "需要外部证据或工具验证" in working_messages[-1]["content"]


@pytest.mark.asyncio
async def test_tool_evidence_required_blocks_reply_tag_without_tools():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    working_messages = []

    result = await engine._handle_final_answer(
        decision=Decision(
            type=DecisionType.FINAL_ANSWER,
            text_content="[REPLY]\n我已经分析过这个 issue，当前代码没有类似问题。",
        ),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "分析这个 GitHub issue 是否仍存在"}],
        tools_executed_in_task=False,
        executed_tool_names=[],
        delivery_receipts=[],
        all_tool_results=[],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
        tool_evidence_required=True,
    )

    assert isinstance(result, tuple)
    assert result[1] == 1
    assert "需要外部证据或工具验证" in working_messages[-1]["content"]


@pytest.mark.asyncio
async def test_plain_long_reply_without_tools_is_still_accepted():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    reply = "这是纯知识解释，不涉及外部状态。" * 20

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=[],
        original_messages=[{"role": "user", "content": "解释一下 API 是什么"}],
        tools_executed_in_task=False,
        executed_tool_names=[],
        delivery_receipts=[],
        all_tool_results=[],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
        tool_evidence_required=False,
    )

    assert result == reply


@pytest.mark.asyncio
async def test_plain_short_analysis_without_tools_is_accepted():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    working_messages = []
    reply = "??????????????????????????????"

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "?????????????"}],
        tools_executed_in_task=False,
        executed_tool_names=[],
        delivery_receipts=[],
        all_tool_results=[],
        no_tool_call_count=0,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
        tool_evidence_required=False,
    )

    assert result == reply
    assert working_messages == []


@pytest.mark.asyncio
async def test_tool_evidence_required_exhausts_to_unverified_without_repeated_prompts():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content="这是未经工具验证的分析。"),
        working_messages=[],
        original_messages=[{"role": "user", "content": "分析这个 GitHub issue 是否仍存在"}],
        tools_executed_in_task=False,
        executed_tool_names=[],
        delivery_receipts=[],
        all_tool_results=[],
        no_tool_call_count=1,
        verify_incomplete_count=0,
        no_confirmation_text_count=0,
        max_no_tool_retries=1,
        max_verify_retries=1,
        max_confirmation_text_retries=1,
        base_force_retries=1,
        conversation_id="c1",
        tool_evidence_required=True,
    )

    assert result == "未执行任何工具，无法验证该结论。请允许我读取、搜索或调用相关工具后再继续核对。"
    assert engine._last_exit_reason == "tool_evidence_missing"
