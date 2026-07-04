"""Unit coverage for the activity-feed description helper.

UI feedback: the canvas activity feed used to show only an action + node
name (``▶执行 主编`` / ``✓完成 视觉设计``) with no "做了什么" content.
``_describe_recent_event`` derives a Chinese, content-bearing snippet from
fields the events already persist. These tests pin that behaviour so the
feed never regresses to action-only lines (and never leaks raw English).
"""

from __future__ import annotations

from openakita.orgs.runtime import _args_preview_brief, _describe_recent_event


def test_subtask_assigned_uses_content_preview() -> None:
    ev = {"type": "subtask_assigned", "content_preview": "起草一份澄清请求"}
    assert _describe_recent_event("subtask_assigned", ev, {}) == "起草一份澄清请求"


def test_agent_run_started_pulls_last_assignment() -> None:
    last = {"planner": "起草一份澄清请求"}
    ev = {"type": "agent_run_started", "node_id": "planner"}
    assert _describe_recent_event("agent_run_started", ev, last) == "受理任务：起草一份澄清请求"


def test_agent_run_started_empty_when_no_assignment() -> None:
    ev = {"type": "agent_run_started", "node_id": "loner"}
    # No assignment known -> empty cell (NOT raw English / event type).
    assert _describe_recent_event("agent_run_started", ev, {}) == ""


def test_agent_run_finished_reports_output_and_artifact() -> None:
    ev = {
        "type": "agent_run_finished",
        "output_len": 452,
        "artifact_path": r"D:\OpenAkita\data\orgs\org_x\artifacts\cmd_1_editor_planner.md",
    }
    desc = _describe_recent_event("agent_run_finished", ev, {})
    assert "产出 452 字" in desc
    assert "交付 cmd_1_editor_planner.md" in desc


def test_agent_run_finished_output_only() -> None:
    ev = {"type": "agent_run_finished", "output_len": 120}
    assert _describe_recent_event("agent_run_finished", ev, {}) == "产出 120 字"


def test_node_tool_called_surfaces_tool_and_path() -> None:
    ev = {
        "type": "node_tool_called",
        "tool_name": "write_file",
        "args_preview": '{"path": "SEO_check.md", "content": "# long body ..."}',
    }
    assert _describe_recent_event("node_tool_called", ev, {}) == "write_file（SEO_check.md）"


def test_node_tool_completed_reports_result_len() -> None:
    ev = {"type": "node_tool_completed", "tool_name": "write_file", "result_len": 167}
    assert _describe_recent_event("node_tool_completed", ev, {}) == "write_file 完成，返回 167 字"


def test_node_tool_completed_no_result_len() -> None:
    ev = {"type": "node_tool_completed", "tool_name": "read_file"}
    assert _describe_recent_event("node_tool_completed", ev, {}) == "read_file 完成"


def test_unknown_type_falls_back_to_content_preview_not_english() -> None:
    ev = {"type": "weird_event", "content_preview": "some content"}
    assert _describe_recent_event("weird_event", ev, {}) == "some content"
    # And empty when there's nothing meaningful -> no raw type leak.
    assert _describe_recent_event("weird_event", {"type": "weird_event"}, {}) == ""


def test_args_preview_brief_handles_plain_string_and_truncation() -> None:
    assert _args_preview_brief("short") == "short"
    long = "x" * 200
    out = _args_preview_brief(long, limit=60)
    assert len(out) <= 60
    assert out.endswith("…")
    # Non-path JSON falls back to clipped raw string.
    assert _args_preview_brief("") == ""
