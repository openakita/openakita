from openakita.core.intent_analyzer import (
    IntentType,
    MemoryScope,
    PromptDepth,
    _make_default,
    _parse_intent_output,
)


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
