from openakita.core.permission import (
    ASK_MODE_RULESET,
    PLAN_MODE_RULESET,
    check_mode_permission,
    disabled,
)


def test_plan_mode_hides_create_todo_write_tools():
    tools = ["create_todo", "update_todo_step", "complete_todo", "get_todo_status"]

    hidden = disabled(tools, PLAN_MODE_RULESET)

    assert "create_todo" in hidden
    assert "update_todo_step" in hidden
    assert "complete_todo" in hidden
    assert "get_todo_status" not in hidden


def test_ask_mode_hides_memory_write_tool():
    tools = ["search_memory", "add_memory"]

    hidden = disabled(tools, ASK_MODE_RULESET)

    assert "search_memory" not in hidden
    assert "add_memory" in hidden


def test_plan_mode_denies_non_plan_file_writes():
    decision = check_mode_permission("write_file", {"path": "data/temp/probe.md"}, mode="plan")

    assert decision is not None
    assert decision.behavior == "deny"


def test_plan_mode_allows_plan_file_writes_only():
    decision = check_mode_permission("write_file", {"path": "data/plans/probe.md"}, mode="plan")

    assert decision is not None
    assert decision.behavior == "allow"
