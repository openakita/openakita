"""Tests for the server-side causal reasoning-chain timeline projection.

``ChainTimelineBuilder`` mirrors the browser's ChainGroup assembly from the same
SSE events so the persisted history can restore a faithful chain (thinking /
narration / tool args / results, in order) on cross-window / cross-device reload
instead of the lossy ``chain_summary``.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.chain_timeline import (
    _ARGS_CAP,
    _MAX_ENTRIES_PER_GROUP,
    _MAX_GROUPS,
    _MAX_TOTAL_CHARS,
    _THINKING_CAP,
    ChainTimelineBuilder,
)
from openakita.api.message_parts import build_message_parts
from openakita.api.routes.sessions import router
from openakita.sessions import SessionManager


def _typical_turn(builder: ChainTimelineBuilder) -> None:
    builder.observe({"type": "iteration_start", "iteration": 1})
    builder.observe({"type": "thinking_delta", "content": "let me "})
    builder.observe({"type": "thinking_delta", "content": "think"})
    builder.observe({"type": "chain_text", "content": "I will read the file"})
    builder.observe(
        {
            "type": "tool_call_start",
            "id": "t1",
            "tool": "read_file",
            "args": {"path": "a.py"},
            "friendly_message": "Reading a.py",
        }
    )
    builder.observe(
        {
            "type": "tool_call_end",
            "id": "t1",
            "tool": "read_file",
            "result": "file body",
            "is_error": False,
        }
    )


def test_builder_preserves_causal_order_within_iteration():
    b = ChainTimelineBuilder()
    _typical_turn(b)
    timeline = b.build()
    assert timeline is not None
    assert len(timeline) == 1
    kinds = [e["kind"] for e in timeline[0]["entries"]]
    assert kinds == ["thinking", "text", "tool_start", "tool_end"]


def test_builder_coalesces_thinking_deltas():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "thinking_delta", "content": "foo "})
    b.observe({"type": "thinking_delta", "content": "bar"})
    entries = b.build()[0]["entries"]
    thinking = [e for e in entries if e["kind"] == "thinking"]
    assert len(thinking) == 1
    assert thinking[0]["content"] == "foo bar"


def test_builder_tool_start_status_marked_done_on_end():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "tool_call_start", "id": "t1", "tool": "x", "args": {}})
    b.observe({"type": "tool_call_end", "id": "t1", "tool": "x", "result": "ok", "is_error": False})
    entries = b.build()[0]["entries"]
    start = next(e for e in entries if e["kind"] == "tool_start")
    end = next(e for e in entries if e["kind"] == "tool_end")
    assert start["status"] == "done"
    assert end["status"] == "done"


def test_builder_tool_error_status():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "tool_call_start", "id": "t1", "tool": "x", "args": {}})
    b.observe(
        {"type": "tool_call_end", "id": "t1", "tool": "x", "result": "boom", "is_error": True}
    )
    entries = b.build()[0]["entries"]
    assert next(e for e in entries if e["kind"] == "tool_start")["status"] == "error"
    assert next(e for e in entries if e["kind"] == "tool_end")["status"] == "error"


def test_builder_persists_config_hint_after_tool_error():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "tool_call_start", "id": "t1", "tool": "web_search", "args": {}})
    b.observe(
        {
            "type": "tool_call_end",
            "id": "t1",
            "tool": "web_search",
            "result": "[搜索源未配置]",
            "is_error": True,
        }
    )
    b.observe(
        {
            "type": "config_hint",
            "tool_use_id": "t1",
            "scope": "web_search",
            "error_code": "missing_credential",
            "title": "搜索源未配置",
            "message": "请配置搜索源。",
            "actions": [{"id": "open_settings", "label": "前往配置", "view": "config"}],
        }
    )

    entries = b.build()[0]["entries"]
    assert [e["kind"] for e in entries] == ["tool_start", "tool_end", "config_hint"]
    hint_entry = entries[-1]
    assert hint_entry["toolId"] == "t1"
    assert hint_entry["hint"] == {
        "scope": "web_search",
        "error_code": "missing_credential",
        "title": "搜索源未配置",
        "message": "请配置搜索源。",
        "actions": [{"id": "open_settings", "label": "前往配置", "view": "config"}],
    }


def test_builder_context_compressed_prepended_to_next_group():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "thinking_delta", "content": "hi"})
    b.observe({"type": "context_compressed", "before_tokens": 1000, "after_tokens": 400})
    b.observe({"type": "iteration_start", "iteration": 2})
    timeline = b.build()
    assert timeline[1]["entries"][0] == {
        "kind": "compressed",
        "beforeTokens": 1000,
        "afterTokens": 400,
    }


def test_builder_events_before_iteration_go_to_synthetic_group():
    b = ChainTimelineBuilder()
    # No iteration_start yet — a pre-loop chain_text should still be captured.
    b.observe({"type": "chain_text", "content": "restored plan"})
    timeline = b.build()
    assert timeline is not None
    assert timeline[0]["iteration"] == 0
    assert timeline[0]["entries"][0]["content"] == "restored plan"


def test_builder_empty_returns_none():
    assert ChainTimelineBuilder().build() is None


def test_builder_caps_thinking_length():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "thinking_delta", "content": "x" * (_THINKING_CAP + 500)})
    entries = b.build()[0]["entries"]
    assert len(entries[0]["content"]) == _THINKING_CAP


def test_builder_caps_args_with_preview_marker():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    big = {"blob": "y" * (_ARGS_CAP + 200)}
    b.observe({"type": "tool_call_start", "id": "t1", "tool": "x", "args": big})
    start = next(e for e in b.build()[0]["entries"] if e["kind"] == "tool_start")
    assert start["args"].get("_truncated") is True
    assert len(start["args"]["_preview"]) == _ARGS_CAP


def test_builder_caps_entries_per_group():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    for i in range(_MAX_ENTRIES_PER_GROUP + 20):
        b.observe({"type": "chain_text", "content": f"line {i}"})
    entries = b.build()[0]["entries"]
    assert len(entries) == _MAX_ENTRIES_PER_GROUP
    assert b.truncated is True


def test_builder_caps_group_count_dropping_oldest():
    b = ChainTimelineBuilder()
    for i in range(_MAX_GROUPS + 5):
        b.observe({"type": "iteration_start", "iteration": i + 1})
        b.observe({"type": "chain_text", "content": f"g{i}"})
    timeline = b.build()
    assert len(timeline) == _MAX_GROUPS
    assert b.truncated is True
    # Oldest groups dropped, newest retained.
    assert timeline[-1]["entries"][0]["content"] == f"g{_MAX_GROUPS + 4}"


def test_builder_total_budget_caps_payload():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    # Each chain_text is capped at the text cap; keep pushing past the budget.
    for _ in range(500):
        b.observe({"type": "chain_text", "content": "z" * 1000})
    timeline = b.build()
    total = sum(
        len(e.get("content", "")) + len(e.get("result", ""))
        for g in (timeline or [])
        for e in g["entries"]
    )
    assert total <= _MAX_TOTAL_CHARS
    assert b.truncated is True


def test_builder_captures_thinking_duration_on_group():
    b = ChainTimelineBuilder()
    b.observe({"type": "iteration_start", "iteration": 1})
    b.observe({"type": "thinking_delta", "content": "hmm"})
    b.observe({"type": "thinking_end", "duration_ms": 1234})
    assert b.build()[0]["durationMs"] == 1234


def test_builder_never_raises_on_garbage():
    b = ChainTimelineBuilder()
    b.observe({})  # no type
    b.observe({"type": "tool_call_start"})  # missing fields
    b.observe({"type": "unknown_event", "foo": 1})
    # No crash; nothing meaningful accumulated.
    assert b.build() is None or isinstance(b.build(), list)


# ── parts marker + history exposure ──


def test_build_message_parts_emits_reasoning_for_timeline_only():
    msg = {
        "role": "assistant",
        "content": "done",
        "chain_timeline": [{"iteration": 1, "entries": [{"kind": "text", "content": "x"}]}],
    }
    kinds = [p["kind"] for p in build_message_parts(msg)]
    assert "reasoning" in kinds
    assert kinds.index("reasoning") < kinds.index("text")


def test_history_exposes_chain_timeline(tmp_path):
    app = FastAPI()
    app.include_router(router)
    manager = SessionManager(storage_path=tmp_path)
    session = manager.get_session("desktop", "conv1", "desktop_user")
    session.add_message("user", "do it")
    timeline = [
        {
            "iteration": 1,
            "entries": [
                {"kind": "thinking", "content": "t"},
                {
                    "kind": "tool_start",
                    "toolId": "t1",
                    "tool": "read_file",
                    "args": {"path": "a"},
                    "description": "Reading",
                    "status": "done",
                },
                {
                    "kind": "tool_end",
                    "toolId": "t1",
                    "tool": "read_file",
                    "result": "body",
                    "status": "done",
                },
                {
                    "kind": "config_hint",
                    "toolId": "t1",
                    "hint": {
                        "scope": "web_search",
                        "error_code": "missing_credential",
                        "title": "搜索源未配置",
                    },
                },
            ],
        }
    ]
    session.add_message("assistant", "answer", chain_timeline=timeline)
    app.state.session_manager = manager

    client = TestClient(app)
    body = client.get("/api/sessions/conv1/history").json()
    assistant = body["messages"][-1]
    assert assistant["chain_timeline"] == timeline
    assert any(p["kind"] == "reasoning" for p in assistant["parts"])
