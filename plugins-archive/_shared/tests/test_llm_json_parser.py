"""Tests for _shared.llm_json_parser."""

from __future__ import annotations

import pytest

from _shared import (
    parse_llm_json,
    parse_llm_json_array,
    parse_llm_json_object,
)


# ── Level 1: direct json.loads ──────────────────────────────────────────────


def test_l1_direct_object() -> None:
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_l1_direct_array() -> None:
    assert parse_llm_json("[1, 2, 3]") == [1, 2, 3]


def test_l1_strips_whitespace() -> None:
    assert parse_llm_json('   {"a": 1}\n\n') == {"a": 1}


# ── Level 2: markdown fence ─────────────────────────────────────────────────


def test_l2_json_fence_object() -> None:
    text = '```json\n{"a": 1}\n```'
    assert parse_llm_json(text) == {"a": 1}


def test_l2_unlabeled_fence() -> None:
    text = '```\n[1, 2]\n```'
    assert parse_llm_json(text) == [1, 2]


def test_l2_uppercase_fence() -> None:
    text = '```JSON\n{"x": true}\n```'
    assert parse_llm_json(text) == {"x": True}


def test_l2_fence_with_prose_outside() -> None:
    text = '好的，这是结果：\n```json\n{"ok": 1}\n```\n注意：……'
    assert parse_llm_json(text) == {"ok": 1}


# ── Level 3: outer brace span ───────────────────────────────────────────────


def test_l3_object_with_surrounding_prose() -> None:
    text = '答复：{"a": 1, "b": [1, 2]} 完。'
    assert parse_llm_json(text) == {"a": 1, "b": [1, 2]}


def test_l3_handles_nested_braces() -> None:
    text = '前缀 {"outer": {"inner": [1, {"deep": true}]}} 后缀'
    assert parse_llm_json(text) == {"outer": {"inner": [1, {"deep": True}]}}


def test_l3_ignores_braces_inside_strings() -> None:
    """A literal { inside a JSON string must not throw the matcher off."""
    text = '前缀 {"msg": "hello } world {"} 后缀'
    assert parse_llm_json(text) == {"msg": "hello } world {"}


def test_l3_array_extraction() -> None:
    text = "list:[1, 2, 3] done"
    assert parse_llm_json(text, expect=list) == [1, 2, 3]


# ── Level 4: regex scan, longest-first ──────────────────────────────────────


def test_l4_picks_largest_when_l3_fails() -> None:
    """L3 will pick the first {...} and may fail; L4 tries every span."""
    text = '解释 {bad text without quotes} 真正的 JSON: {"a": [1, 2]} 完。'
    assert parse_llm_json(text) == {"a": [1, 2]}


def test_l4_two_independent_objects_picks_longest() -> None:
    text = '前: {"x":1} 后: {"y":2,"z":[1,2,3]}'
    out = parse_llm_json(text)
    assert out == {"y": 2, "z": [1, 2, 3]}


# ── Level 5: fallback ───────────────────────────────────────────────────────


def test_l5_no_json_returns_fallback_default_none() -> None:
    assert parse_llm_json("this is just prose") is None


def test_l5_custom_fallback() -> None:
    assert parse_llm_json("oops", fallback={}) == {}


def test_l5_empty_input_returns_fallback() -> None:
    assert parse_llm_json("") is None
    assert parse_llm_json("   \n\t  ", fallback=[]) == []


def test_non_string_input_returns_fallback() -> None:
    assert parse_llm_json(None, fallback={}) == {}  # type: ignore[arg-type]
    assert parse_llm_json(123, fallback=[]) == []  # type: ignore[arg-type]


# ── Errors collection (for prompt feedback) ─────────────────────────────────


def test_errors_list_collects_failure_reasons() -> None:
    errors: list[str] = []
    out = parse_llm_json("not json", fallback=None, errors=errors)
    assert out is None
    assert len(errors) >= 1
    assert any("L1" in e for e in errors)
    assert any("L5" in e for e in errors)


def test_errors_empty_when_l1_succeeds() -> None:
    errors: list[str] = []
    parse_llm_json('{"a":1}', errors=errors)
    assert errors == []


# ── expect=dict / expect=list strict mode ───────────────────────────────────


def test_expect_dict_rejects_array() -> None:
    out = parse_llm_json("[1,2]", expect=dict, fallback={"f": True})
    assert out == {"f": True}


def test_expect_list_rejects_object() -> None:
    out = parse_llm_json('{"a":1}', expect=list, fallback=[42])
    assert out == [42]


def test_expect_dict_accepts_object_with_array_inside() -> None:
    out = parse_llm_json('{"a": [1,2,3]}', expect=dict)
    assert out == {"a": [1, 2, 3]}


# ── Convenience wrappers ────────────────────────────────────────────────────


def test_parse_llm_json_object_normalises_to_dict() -> None:
    assert parse_llm_json_object('{"a": 1}') == {"a": 1}
    assert parse_llm_json_object("oops") == {}
    assert parse_llm_json_object("[1,2]") == {}  # array rejected


def test_parse_llm_json_array_normalises_to_list() -> None:
    assert parse_llm_json_array("[1, 2, 3]") == [1, 2, 3]
    assert parse_llm_json_array("oops") == []
    assert parse_llm_json_array('{"a":1}') == []


def test_parse_llm_json_array_with_fallback() -> None:
    out = parse_llm_json_array("oops", fallback=["sentinel"])
    assert out == ["sentinel"]


# ── Real-world samples (regression cases for migrating plugins) ─────────────


def test_video_translator_safe_json_array_case() -> None:
    """Exact pattern that translator_engine._safe_json_array used to handle."""
    text = 'Sure! Here is the translation:\n[\n  "你好",\n  "世界"\n]\n'
    assert parse_llm_json_array(text) == ["你好", "世界"]


def test_seedance_decompose_storyboard_case() -> None:
    """Pattern from seedance long_video.decompose_storyboard."""
    text = (
        "I'll decompose this storyboard:\n"
        "```json\n"
        '{"segments":[{"index":1,"prompt":"a","duration":5},'
        '{"index":2,"prompt":"b","duration":5}],"total":10}\n'
        "```\n"
    )
    out = parse_llm_json(text)
    assert isinstance(out, dict)
    assert out["total"] == 10
    assert len(out["segments"]) == 2


def test_storyboard_engine_re_search_case() -> None:
    """Pattern from storyboard_engine: bare JSON between prose."""
    text = (
        "好，下面是分镜方案：\n"
        '{"shots": [{"index":1,"duration_sec":3,"visual":"特写"}]}\n'
        "如有不满意我再调整。"
    )
    out = parse_llm_json(text)
    assert isinstance(out, dict)
    assert out["shots"][0]["visual"] == "特写"


def test_brace_inside_string_with_escaped_quote() -> None:
    """Escaped quote inside string must not flip in_str state."""
    text = r'前 {"msg": "she said \"yes\" {ok}"} 后'
    out = parse_llm_json(text)
    assert out == {"msg": 'she said "yes" {ok}'}


@pytest.mark.parametrize("noisy,expected", [
    ('{"a":1}',                    {"a": 1}),
    ('  {"a":1}  ',                {"a": 1}),
    ('```json\n{"a":1}\n```',      {"a": 1}),
    ('foo {"a":1} bar',            {"a": 1}),
    ('解释\n```\n{"a":1}\n```\n', {"a": 1}),
    ('not at all',                 None),
])
def test_parametrized_levels(noisy: str, expected: object) -> None:
    assert parse_llm_json(noisy) == expected
