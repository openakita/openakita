"""Regression tests for repeated-tool rate limit keys."""

from __future__ import annotations

from openakita.core._reasoning_runtime import _tool_rate_limit_key


def test_update_todo_step_distinct_steps_have_distinct_rate_limit_keys():
    keys = {
        _tool_rate_limit_key(
            "update_todo_step",
            {"step_id": f"step_{i}", "status": "completed"},
        )
        for i in range(8)
    }

    assert len(keys) == 8


def test_identical_tool_calls_share_rate_limit_key():
    first = _tool_rate_limit_key(
        "update_todo_step",
        {"step_id": "step_4", "status": "completed"},
    )
    second = _tool_rate_limit_key(
        "update_todo_step",
        {"status": "completed", "step_id": "step_4"},
    )

    assert first == second
