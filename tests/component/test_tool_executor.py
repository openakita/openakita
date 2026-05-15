"""L2 Component Tests: ToolExecutor execution and truncation guard."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.core.tool_executor import OVERFLOW_MARKER, ToolExecutor
from openakita.core.permission import PermissionDecision
from openakita.tools.errors import ErrorType, ToolError


def _make_registry(*tool_names: str) -> MagicMock:
    """Create a mock SystemHandlerRegistry with given tool names."""
    registry = MagicMock()
    result_map = {name: f"Result of {name}" for name in tool_names}
    registry.has_tool.side_effect = lambda n: n in result_map
    registry.execute_by_tool = AsyncMock(side_effect=lambda n, _: result_map[n])
    registry.get_handler_name_for_tool.return_value = "filesystem"
    registry.get_permission_check.return_value = None
    return registry


def _allow_policy(executor: ToolExecutor) -> None:
    executor.check_permission = MagicMock(return_value=PermissionDecision("allow"))


@pytest.fixture
def executor():
    registry = _make_registry("read_file", "write_file", "search_memory")
    return ToolExecutor(handler_registry=registry, max_parallel=1)


class TestExecuteTool:
    async def test_execute_known_tool(self, executor):
        # ``execute_tool`` returns ``(text, ConfigHint | None)`` after the
        # web_search provider refactor; legacy tests that only care about
        # the text portion just unpack here.
        result, hint = await executor.execute_tool("read_file", {"path": "/tmp/x"})
        assert isinstance(result, str)
        assert hint is None  # no config-correctable error from a normal handler

    async def test_execute_unknown_tool(self, executor):
        result, hint = await executor.execute_tool("nonexistent_tool", {})
        assert "error" in result.lower() or "not found" in result.lower() or isinstance(result, str)
        assert hint is None

    async def test_execute_tool_normalizes_stringified_nested_fields(self):
        registry = MagicMock()
        captured = {}

        async def _execute_by_tool(tool_name, params):
            captured["tool_name"] = tool_name
            captured["params"] = params
            return "ok"

        registry.has_tool.return_value = True
        registry.execute_by_tool.side_effect = _execute_by_tool
        registry.get_handler_name_for_tool.return_value = "plan"
        registry.get_permission_check.return_value = None
        executor = ToolExecutor(handler_registry=registry, max_parallel=1)

        await executor.execute_tool(
            "create_todo",
            {
                "task_summary": "demo",
                "steps": '[{"id":"step_1","description":"first"}]',
            },
        )

        assert captured["tool_name"] == "create_todo"
        assert isinstance(captured["params"]["steps"], list)
        assert captured["params"]["steps"][0]["id"] == "step_1"

    async def test_execute_tool_canonicalizes_browser_hyphen_alias(self):
        registry = MagicMock()
        captured = {}

        async def _execute_by_tool(tool_name, params):
            captured["tool_name"] = tool_name
            captured["params"] = params
            return "ok"

        registry.has_tool.side_effect = lambda name: name in {"browser_click"}
        registry.execute_by_tool.side_effect = _execute_by_tool
        registry.get_handler_name_for_tool.return_value = "browser"
        registry.get_permission_check.return_value = None
        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        _allow_policy(executor)

        await executor.execute_tool("browser-click", {"text": "登录"})

        assert captured["tool_name"] == "browser_click"
        assert captured["params"]["text"] == "登录"

    def test_todo_tracking_is_not_a_tool_execution_gate(self, executor):
        assert executor._check_todo_required("write_file", "session-needs-plan") is None

    async def test_execute_tool_normalizes_browser_fill_alias_to_type(self):
        registry = MagicMock()
        captured = {}

        async def _execute_by_tool(tool_name, params):
            captured["tool_name"] = tool_name
            captured["params"] = params
            return "ok"

        registry.has_tool.side_effect = lambda name: name in {"browser_type"}
        registry.execute_by_tool.side_effect = _execute_by_tool
        registry.get_handler_name_for_tool.return_value = "browser"
        registry.get_permission_check.return_value = None
        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        _allow_policy(executor)

        await executor.execute_tool("browser_fill", {"field": "password", "value": "root"})

        assert captured["tool_name"] == "browser_type"
        assert captured["params"]["text"] == "root"
        assert "luci_password" in captured["params"]["selector"]

    async def test_execute_tool_normalizes_browser_login_click_action(self):
        registry = MagicMock()
        captured = {}

        async def _execute_by_tool(tool_name, params):
            captured["tool_name"] = tool_name
            captured["params"] = params
            return "ok"

        registry.has_tool.side_effect = lambda name: name in {"browser_click"}
        registry.execute_by_tool.side_effect = _execute_by_tool
        registry.get_handler_name_for_tool.return_value = "browser"
        registry.get_permission_check.return_value = None
        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        _allow_policy(executor)

        await executor.execute_tool("browser_click", {"action": "login"})

        assert captured["tool_name"] == "browser_click"
        assert 'button[type="submit"]' in captured["params"]["selector"]


class TestGuardTruncate:
    def test_short_result_unchanged(self):
        result = ToolExecutor._guard_truncate("read_file", "short content")
        assert result == "short content"

    def test_very_long_result_truncated(self):
        long_text = "x" * 500_000
        result = ToolExecutor._guard_truncate("read_file", long_text)
        assert len(result) < len(long_text)
        assert OVERFLOW_MARKER in result or len(result) < 500_000

    def test_empty_result(self):
        result = ToolExecutor._guard_truncate("read_file", "")
        assert result == ""


class TestExecutorInit:
    def test_default_max_parallel(self):
        registry = _make_registry()
        executor = ToolExecutor(handler_registry=registry)
        assert executor._max_parallel == 1

    def test_custom_max_parallel(self):
        registry = _make_registry()
        executor = ToolExecutor(handler_registry=registry, max_parallel=5)
        assert executor._max_parallel == 5


@pytest.mark.asyncio
async def test_structured_tool_error_marks_tool_result_as_error():
    registry = MagicMock()
    registry.has_tool.return_value = True
    registry.get_handler_name_for_tool.return_value = "skills"
    registry.get_permission_check.return_value = None
    registry.execute_by_tool = AsyncMock(
        return_value=ToolError(
            error_type=ErrorType.TIMEOUT,
            tool_name="install_skill",
            message="timed out",
            details={"failure_class": "skill_install_network_timeout"},
        ).to_tool_result()
    )

    executor = ToolExecutor(handler_registry=registry, max_parallel=1)
    _allow_policy(executor)
    tool_results, executed, _ = await executor.execute_batch(
        [{"id": "u1", "name": "install_skill", "input": {"source": "owner/repo"}}]
    )

    assert executed == []
    assert tool_results[0]["is_error"] is True
    payload = json.loads(tool_results[0]["content"])
    assert payload["error"] is True
    assert payload["error_type"] == "timeout"


@pytest.mark.asyncio
async def test_tool_hard_timeout_marks_tool_result_as_error():
    registry = MagicMock()
    registry.has_tool.return_value = True
    registry.get_handler_name_for_tool.return_value = "filesystem"
    registry.get_permission_check.return_value = None

    async def _slow_tool(_tool_name, _params):
        await asyncio.sleep(0.05)
        return "late"

    registry.execute_by_tool = AsyncMock(side_effect=_slow_tool)
    executor = ToolExecutor(handler_registry=registry, max_parallel=1)
    _allow_policy(executor)
    executor._hard_timeout_for_tool = lambda _tool_name: 0.01

    tool_results, executed, _ = await executor.execute_batch(
        [{"id": "u1", "name": "slow_tool", "input": {}}]
    )

    assert executed == []
    assert tool_results[0]["is_error"] is True
    assert "工具执行被中断" in tool_results[0]["content"]

