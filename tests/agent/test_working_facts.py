"""Tests for ``openakita.agent.working_facts``.

Anchors the move from ``openakita.agent.working_facts``. The legacy
shim must produce the same callables as the new module so existing
prompt-builder / session-manager / intent-gate code paths see no
behaviour change. Behavioural correctness (regex matches, merge
semantics, render formatting) is anchored alongside.
"""

from __future__ import annotations

from openakita.agent.working_facts import (
    extract_working_facts,
    format_working_facts,
    merge_working_facts,
)

# ---------------------------------------------------------------------------
# extract_working_facts
# ---------------------------------------------------------------------------


def test_extract_test_code() -> None:
    facts = extract_working_facts("测试代号是 Maple-42", source_turn=20)
    assert "test_code" in facts
    assert facts["test_code"]["value"] == "Maple-42"
    assert facts["test_code"]["source_turn"] == 20
    assert "updated_at" in facts["test_code"]


def test_extract_temporary_name_chinese_value() -> None:
    facts = extract_working_facts("当前临时名称是阿琪")
    assert facts.get("temporary_name", {}).get("value") == "阿琪"


def test_empty_message_returns_empty_dict() -> None:
    assert extract_working_facts("") == {}
    assert extract_working_facts("   ") == {}


def test_no_match_returns_empty_dict() -> None:
    assert extract_working_facts("hello world") == {}


def test_trailing_punctuation_stripped() -> None:
    facts = extract_working_facts("测试代号是 maple-42。")
    assert facts["test_code"]["value"] == "maple-42"


# ---------------------------------------------------------------------------
# merge_working_facts
# ---------------------------------------------------------------------------


def test_merge_overwrites_existing_keys() -> None:
    existing = {"test_code": {"value": "old"}}
    updates = {"test_code": {"value": "new"}}
    merged = merge_working_facts(existing, updates)
    assert merged["test_code"]["value"] == "new"


def test_merge_handles_none_existing() -> None:
    merged = merge_working_facts(None, {"x": {"value": "y"}})
    assert merged == {"x": {"value": "y"}}


def test_merge_does_not_mutate_input() -> None:
    existing = {"a": {"value": "1"}}
    merge_working_facts(existing, {"b": {"value": "2"}})
    assert "b" not in existing


# ---------------------------------------------------------------------------
# format_working_facts
# ---------------------------------------------------------------------------


def test_format_empty_returns_empty_string() -> None:
    assert format_working_facts(None) == ""
    assert format_working_facts({}) == ""


def test_format_renders_value_and_source_turn() -> None:
    rendered = format_working_facts({"test_code": {"value": "Maple-42", "source_turn": 20}})
    assert "Session Working Facts" in rendered
    assert "test_code: Maple-42" in rendered
    assert "source_turn=20" in rendered


def test_format_handles_plain_string_payload() -> None:
    rendered = format_working_facts({"foo": "bar"})
    assert "foo: bar" in rendered
    assert "source_turn=" not in rendered
