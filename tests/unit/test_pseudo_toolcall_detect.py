"""Regression tests for the pseudo-tool-call detector.

Ported from upstream v1.27.x fix ``3a789d83`` (P1 robustness): the LLM
occasionally writes a tool invocation as Markdown text (```` ```tool_call ````
fence or a bare ``org_accept_deliverable(...)`` literal) instead of actually
calling the tool. ReasoningEngine must recognise this so the caller can force a
corrective re-execution instead of leaving the orchestration chain open.

The upstream regex had an operator-precedence bug (``[a-z0-9_]+`` bound only to
the last prefix ``schedule_``); the local re-implementation fixes it by wrapping
the prefix alternation in a non-capturing group, so all known prefixes match a
full tool name.
"""

from openakita.core._reasoning_runtime import (
    _detect_text_toolcall_block,
    _guard_text_toolcall_block,
)


def test_detect_fenced_tool_call_block():
    text = "好的\n```tool_call\norg_accept_deliverable(task_chain_id=1)\n```"
    assert _detect_text_toolcall_block(text) == ["org_accept_deliverable"]


def test_detect_inline_bare_tool_call():
    text = "我现在调用 seedance_create(prompt=x) 来生成视频"
    assert _detect_text_toolcall_block(text) == ["seedance_create"]


def test_detect_ignores_ordinary_functions():
    # Ordinary builtins/functions must not be mistaken for tool calls.
    assert _detect_text_toolcall_block("普通回答，调用 list(x) 与 int(y)") == []


def test_detect_empty_text():
    assert _detect_text_toolcall_block("") == []
    assert _detect_text_toolcall_block(None) == []  # type: ignore[arg-type]


def test_detect_dedup_and_sort_multiple_prefixes():
    text = (
        "```tool_call\n"
        "tongyi_image_generate(a=1)\n"
        "org_submit_deliverable(b=2)\n"
        "org_submit_deliverable(b=3)\n"
        "```"
    )
    assert _detect_text_toolcall_block(text) == [
        "org_submit_deliverable",
        "tongyi_image_generate",
    ]


def test_guard_returns_empty_when_tool_actually_executed():
    # If a real tool ran this turn, there is nothing to correct.
    assert _guard_text_toolcall_block("org_accept_deliverable(a=1)", ["shell"], None) == []


def test_guard_returns_empty_for_reply_intent():
    # A [REPLY] turn is deliberately discussing a tool, not promising an action.
    assert _guard_text_toolcall_block("org_accept_deliverable(a=1)", [], "REPLY") == []


def test_guard_flags_pseudo_call_without_execution():
    assert _guard_text_toolcall_block("org_accept_deliverable(a=1)", [], "ACTION") == [
        "org_accept_deliverable"
    ]
