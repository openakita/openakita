"""Fix-10 回归测试：deferred 调度工具提至首轮 + get_tool_info fuzzy 重定向。"""

from __future__ import annotations

import pytest

from openakita.tools.catalog import ToolCatalog
from openakita.tools.defer_config import ALWAYS_LOAD_TOOLS, should_defer


# ---------------------------------------------------------------------------
# Fix-10 (a): 高频调度/记忆/网络工具必须在 ALWAYS_LOAD_TOOLS 中
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "search_memory",
        "add_memory",
        "web_search",
        "web_fetch",
        "update_user_profile",
    ],
)
def test_high_frequency_tools_always_loaded(tool_name: str):
    assert tool_name in ALWAYS_LOAD_TOOLS, f"{tool_name} should be promoted to first-turn schema"
    assert not should_defer(tool_name), f"{tool_name} should NOT be deferred"


def test_should_defer_still_works_for_individual_defers():
    """非高频工具仍按原规则 defer。"""
    assert should_defer("install_skill")  # 在 DEFER_INDIVIDUAL_TOOLS 里
    assert should_defer("uninstall_skill")
    assert should_defer("set_persona_trait")


# ---------------------------------------------------------------------------
# Fix-10 (b): get_tool_info fuzzy 重定向
# ---------------------------------------------------------------------------


class _StubCatalog(ToolCatalog):
    """Build a ToolCatalog with a hand-set _tools dict; bypass real loading."""

    def __init__(self, tools: dict):
        # Skip parent __init__ (which scans real definitions) — we just need
        # the lookup helpers (_resolve_tool_alias / get_tool_info).
        self._tools = tools
        self._deferred_tools: set[str] = set()


def _stub(tool_names: list[str]) -> _StubCatalog:
    return _StubCatalog(
        {n: {"name": n, "description": "x", "input_schema": {}} for n in tool_names}
    )


def test_resolve_tool_alias_hyphen_to_underscore():
    cat = _stub(["schedule_task", "edit_file"])
    assert cat._resolve_tool_alias("schedule-task") == "schedule_task"


def test_resolve_tool_alias_camelcase_to_snake():
    cat = _stub(["schedule_task"])
    assert cat._resolve_tool_alias("ScheduleTask") == "schedule_task"
    assert cat._resolve_tool_alias("scheduleTask") == "schedule_task"


def test_resolve_tool_alias_spaces_to_underscore():
    cat = _stub(["search_memory"])
    assert cat._resolve_tool_alias("search memory") == "search_memory"


def test_resolve_tool_alias_returns_none_when_no_match():
    cat = _stub(["edit_file"])
    assert cat._resolve_tool_alias("totally-unknown-thing") is None


def test_get_tool_info_redirects_with_marker():
    cat = _stub(["schedule_task"])
    info = cat.get_tool_info("schedule-task")
    assert info is not None
    assert info["name"] == "schedule_task"
    assert info["_resolved_from"] == "schedule-task"


def test_get_tool_info_returns_none_for_unrelated_name():
    cat = _stub(["edit_file"])
    assert cat.get_tool_info("foobar") is None


def test_get_tool_info_formatted_includes_redirect_warning():
    cat = _stub(["schedule_task"])
    text = cat.get_tool_info_formatted("schedule-task")
    assert "schedule_task" in text
    assert "重定向" in text
    assert "schedule-task" in text  # 原名也要出现，提醒用户
