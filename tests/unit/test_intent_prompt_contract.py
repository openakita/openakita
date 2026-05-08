from openakita.core.intent_analyzer import (
    IntentResult,
    IntentType,
    MemoryScope,
    PromptDepth,
    _make_default,
    _parse_intent_output,
    _try_fast_query_shortcut,
)
from openakita.core.agent import _resolve_force_tool_policy


def test_parse_prompt_contract_minimal_query():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: 计算数字
tool_hints: []
memory_keywords: []
capability_scope: [none]
prompt_depth: minimal
memory_scope: pinned_only
catalog_scope: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "what is 19 * 23 and add 4",
    )

    assert result.intent == IntentType.QUERY
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.force_tool is False


def test_unknown_prompt_contract_values_fall_back_safely():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: explain
tool_hints: []
memory_keywords: []
prompt_depth: huge
memory_scope: everything
requires_tools: false
requires_project_context: false
""",
        "什么是 API",
    )

    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.force_tool is False


def test_default_intent_is_minimal_non_tool_query():
    result = _make_default("解释一下 Python GIL")

    assert result.intent == IntentType.QUERY
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.force_tool is False


def test_log_investigation_query_is_guarded_as_tool_task():
    result = _try_fast_query_shortcut(
        "我看你的运行日志有很多报错和警告的内容，都是关于skills技能的，你排查一下是什么原因导致的"
    )

    assert result is not None
    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.force_tool is True
    assert result.fast_reply is False


def test_llm_query_misclassification_is_coerced_for_external_action():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: 分析日志警告原因
tool_hints: []
memory_keywords: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "我手动删除了，现在再看看很多警告的日志，是什么原因导致的",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.force_tool is True


def test_plain_concept_query_is_not_over_guarded():
    result = _try_fast_query_shortcut("什么是API")

    assert result is not None
    assert result.intent == IntentType.QUERY
    assert result.requires_tools is False
    assert result.force_tool is False


def test_execute_task_followup_is_guarded_as_tool_task():
    result = _parse_intent_output(
        """
intent: chat
task_type: other
goal: 请求继续执行任务而不中断
tool_hints: []
memory_keywords: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "执行任务，不要停掉",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.force_tool is True


def test_tool_required_query_keeps_force_tool_guard():
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="analysis",
        requires_tools=True,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries is None
    assert evidence_required is True


def test_plain_query_still_disables_force_tool_guard():
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="question",
        requires_tools=False,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 0
    assert evidence_required is False
