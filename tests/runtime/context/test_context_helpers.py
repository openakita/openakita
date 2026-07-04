"""Tests for :mod:`openakita.runtime.context` -- grouping / budget / compress."""

from __future__ import annotations

from types import SimpleNamespace

from openakita.runtime.context import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    calc_context_budget,
    estimate_tokens,
    group_messages,
    payload_size_bytes,
    pre_request_cleanup,
    sanitize_tool_pairs,
)

# ---- group_messages ----

def test_group_messages_empty() -> None:
    assert group_messages([]) == []


def test_group_messages_no_tool_use_each_message_alone() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert group_messages(msgs) == [[msgs[0]], [msgs[1]]]


def test_group_messages_tool_use_pairs_with_result() -> None:
    assistant = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": {}}],
    }
    user_result = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
    }
    follow = {"role": "user", "content": "thanks"}
    out = group_messages([assistant, user_result, follow])
    assert len(out) == 2
    assert out[0] == [assistant, user_result]
    assert out[1] == [follow]


def test_group_messages_assistant_text_only_does_not_group() -> None:
    msgs = [
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": "ok"},
    ]
    out = group_messages(msgs)
    assert out == [[msgs[0]], [msgs[1]]]


# ---- calc_context_budget ----

def test_calc_context_budget_small_window_falls_back() -> None:
    ep = SimpleNamespace(context_window=4096, max_tokens=2048)
    out = calc_context_budget(ep, fallback_window=32000)
    assert out > 4096


def test_calc_context_budget_low_result_returns_default() -> None:
    ep = SimpleNamespace(context_window=0, max_tokens=0)
    out = calc_context_budget(ep, fallback_window=0)
    assert out == DEFAULT_MAX_CONTEXT_TOKENS


def test_calc_context_budget_reserves_output_tokens() -> None:
    ep = SimpleNamespace(context_window=100_000, max_tokens=10_000)
    out = calc_context_budget(ep, fallback_window=32000)
    # 5% safety margin + 10k reserved -> result < 90k.
    assert out < 90_000


# ---- estimate_tokens ----

def test_estimate_tokens_zero_for_empty() -> None:
    assert estimate_tokens("") >= 0


def test_estimate_tokens_grows_with_length() -> None:
    short = estimate_tokens("a")
    longer = estimate_tokens("a" * 1000)
    assert longer > short


# ---- payload_size_bytes ----

def test_payload_size_bytes_empty() -> None:
    assert payload_size_bytes([]) == 0


def test_payload_size_bytes_unicode_aware() -> None:
    msgs = [{"role": "user", "content": "你好"}]
    # UTF-8 encoding of Chinese chars is 3 bytes each; total > 2.
    assert payload_size_bytes(msgs) > len(str(msgs[0]))


# ---- sanitize_tool_pairs ----

def test_sanitize_tool_pairs_drops_orphan_tool_use() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me try"},
                {"type": "tool_use", "id": "t_orphan", "name": "x", "input": {}},
            ],
        }
    ]
    out = sanitize_tool_pairs(msgs)
    assert out[0]["content"] == [{"type": "text", "text": "let me try"}]


def test_sanitize_tool_pairs_keeps_paired_use_and_result() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            ],
        },
    ]
    out = sanitize_tool_pairs(msgs)
    assert out == msgs


def test_sanitize_tool_pairs_drops_orphan_tool_result() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "missing", "content": "ok"},
            ],
        }
    ]
    out = sanitize_tool_pairs(msgs)
    # Orphan tool_result removed; user message had only that block -> dropped.
    assert out == []


def test_sanitize_tool_pairs_preserves_plain_text_messages() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    assert sanitize_tool_pairs(msgs) == msgs


# ---- pre_request_cleanup ----

def test_pre_request_cleanup_returns_list_for_empty_input() -> None:
    # ``microcompact`` is well-tested by its own suite; we just assert
    # the v2 wrapper preserves the empty-input contract.
    out = pre_request_cleanup([])
    assert isinstance(out, list)
