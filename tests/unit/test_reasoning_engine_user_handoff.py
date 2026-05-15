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
    reply = "浏览器自动化失败，请你手动登录后截图给我。"

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "打开路由器后台并登录"}],
        tools_executed_in_task=True,
        executed_tool_names=["browser_fill"],
        delivery_receipts=[],
        all_tool_results=[
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "❌ 未知工具: browser_fill。你是否想使用: browser_type？",
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
async def test_tool_evidence_required_appends_soft_disclaimer_to_implicit_long_reply():
    """``tool_evidence_required=True`` + 0 tool calls + plain analysis text.

    Contract change (P0-2 阶段 1 重构后)：旧实现会把这种场景判定为"幻觉"，
    在 ``working_messages`` 里塞 "需要外部证据或工具验证" 强制重试一次。
    实战中发现：

    * 大量没真正"幻觉"的解释 / 分析回答被无谓重试，浪费 token
    * 重试反而把上下文越搅越脏，让 ``OrgRuntime`` 把任务记成 ``task_failed``
    * 长链编排会因为这类伪重试出现死锁

    重构后改为软兜底：原样返回 LLM 文本，并在末尾追加一段 disclaimer 提醒
    用户"这条回答没核对外部状态"。``working_messages`` 不再被改写。
    阶段 3 的 ``_check_source_tag_consistency()`` 与 ``_get_action_done_re()``
    形成 belt-and-suspenders 双重保险，伪造"已查到/已读取"的回答仍会被标记。
    """
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    working_messages: list[dict] = []
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

    assert isinstance(result, str), (
        "tool_evidence_required 路径不再返回 tuple 触发重试；"
        "新契约是返回带 disclaimer 的字符串"
    )
    assert result.startswith(reply), "原 LLM 文本必须保留在前面，不被替换"
    assert "本次回答未调用工具核对外部状态" in result, (
        "soft disclaimer 必须追加到末尾"
    )
    assert working_messages == [], (
        "不再向 working_messages 注入重试 prompt，避免触发 OrgRuntime 误判"
    )
    # 不再设置 _last_exit_reason="tool_evidence_missing"——那个 reason 会被
    # OrgRuntime 错误映射成 task_failed 导致组织链路死锁。
    assert engine._last_exit_reason != "tool_evidence_missing"


@pytest.mark.asyncio
async def test_tool_evidence_required_appends_soft_disclaimer_to_reply_tag_response():
    """``[REPLY]`` 显式声明 + ``tool_evidence_required=True`` + 0 tool calls。

    旧实现：把 ``[REPLY]`` 也当作可疑路径挡下来，强制重试。
    新实现：``[REPLY]`` 被剥离，原文 + soft disclaimer 直接返回，
    让用户看到 LLM 真实输出 + 来源不确定提示。
    """
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=AsyncMock(),
        agent_state=AgentState(),
    )
    working_messages: list[dict] = []

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

    assert isinstance(result, str)
    # [REPLY] tag 已被 clean_llm_response 剥离
    assert "[REPLY]" not in result
    assert "我已经分析过这个 issue" in result
    assert "本次回答未调用工具核对外部状态" in result
    assert working_messages == []


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
    reply = "好人赢得直接，是因为狼人连续暴露站边，关键票型很快形成闭环。"

    result = await engine._handle_final_answer(
        decision=Decision(type=DecisionType.FINAL_ANSWER, text_content=reply),
        working_messages=working_messages,
        original_messages=[{"role": "user", "content": "分析一下为什么这么直接获胜"}],
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
async def test_tool_evidence_required_with_exhausted_retry_budget_returns_text_with_disclaimer():
    """重试预算耗尽（``no_tool_call_count >= max_no_tool_retries``）路径。

    旧实现：硬替换 LLM 文本为固定提示
    "未执行任何工具，无法验证该结论。请允许我读取、搜索或调用相关工具后再继续核对。"
    并把 ``_last_exit_reason`` 设为 ``"tool_evidence_missing"``。

    新实现（P0-2 阶段 0 修正）：原文 + soft disclaimer，``_last_exit_reason``
    保留为 None 或正常 ``"normal"``。原因是 ``"tool_evidence_missing"`` 会被
    ``OrgRuntime`` 映射成 ``task_failed``，让组织链路误判任务失败而触发
    ``org_wait_for_deliverable`` 死锁。
    """
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

    assert isinstance(result, str)
    assert "这是未经工具验证的分析。" in result, "原 LLM 文本必须保留"
    assert "本次回答未调用工具核对外部状态" in result, "soft disclaimer 追加"
    assert engine._last_exit_reason != "tool_evidence_missing", (
        "tool_evidence_missing exit_reason 已被移除（会让 OrgRuntime 误判 "
        "task_failed），新契约下不应再设置"
    )
