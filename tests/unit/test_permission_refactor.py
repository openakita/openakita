import pytest

from openakita.core.permission import check_permission
from openakita.core.policy import PolicyDecision, PolicyResult
from openakita.core.tool_executor import ToolExecutor


class _DummyRegistry:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in {"read_file", "create_todo"}

    async def execute_by_tool(self, tool_name: str, tool_input: dict) -> str:
        self.executed.append(tool_name)
        return f"ok:{tool_name}"

    def get_handler_name_for_tool(self, tool_name: str) -> str:
        return "dummy"

    def list_tools(self) -> list[str]:
        return ["read_file"]


class _CountingPolicyEngine:
    def __init__(self) -> None:
        self.calls = 0

    def assert_tool_allowed(self, tool_name: str, tool_input: dict) -> PolicyResult:
        self.calls += 1
        return PolicyResult(decision=PolicyDecision.ALLOW)


def test_permission_fail_closed_for_risky_tools(monkeypatch: pytest.MonkeyPatch):
    def _boom():
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr("openakita.core.policy.get_policy_engine", _boom)
    result = check_permission("run_shell", {"command": "echo hi"})
    assert result.behavior == "deny"
    assert "安全策略暂时不可用" in result.reason


def test_permission_still_allows_safe_reads_when_policy_unavailable(monkeypatch: pytest.MonkeyPatch):
    def _boom():
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr("openakita.core.policy.get_policy_engine", _boom)
    result = check_permission("read_file", {"path": "README.md"})
    assert result.behavior == "allow"


@pytest.mark.asyncio
async def test_execute_batch_only_runs_policy_once_in_non_agent_mode(monkeypatch: pytest.MonkeyPatch):
    engine = _CountingPolicyEngine()
    monkeypatch.setattr("openakita.core.policy.get_policy_engine", lambda: engine)

    executor = ToolExecutor(_DummyRegistry())
    executor._current_mode = "plan"

    results, executed, _ = await executor.execute_batch(
        [{"id": "tool-1", "name": "read_file", "input": {"path": "README.md"}}]
    )

    assert engine.calls == 1
    assert executed == ["read_file"]
    assert results[0]["content"] == "ok:read_file"


@pytest.mark.asyncio
async def test_execute_batch_blocks_plan_denials_before_policy(monkeypatch: pytest.MonkeyPatch):
    engine = _CountingPolicyEngine()
    monkeypatch.setattr("openakita.core.policy.get_policy_engine", lambda: engine)

    executor = ToolExecutor(_DummyRegistry())
    executor._current_mode = "plan"

    results, executed, _ = await executor.execute_batch(
        [{"id": "tool-1", "name": "run_shell", "input": {"command": "echo hi"}}]
    )

    assert engine.calls == 0
    assert executed == []
    assert "run_shell" in results[0]["content"]
    assert results[0]["is_error"] is True


@pytest.mark.asyncio
async def test_execute_tool_with_policy_still_enforces_plan_mode_rules():
    registry = _DummyRegistry()
    executor = ToolExecutor(registry)
    executor._current_mode = "plan"

    result = await executor.execute_tool_with_policy(
        "run_shell",
        {"command": "echo hi"},
        PolicyResult(decision=PolicyDecision.ALLOW),
    )

    assert "run_shell" in result
    assert registry.executed == []


@pytest.mark.asyncio
async def test_execute_tool_with_policy_normalizes_tool_aliases():
    registry = _DummyRegistry()
    executor = ToolExecutor(registry)

    result = await executor.execute_tool_with_policy(
        "create-todo",
        {"task_summary": "x", "steps": []},
        PolicyResult(decision=PolicyDecision.ALLOW),
        session_id="conv-1",
    )

    assert result == "ok:create_todo"
    assert registry.executed == ["create_todo"]
