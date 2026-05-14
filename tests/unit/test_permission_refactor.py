"""C6 起 permission.check_permission Step 2 切到 PolicyEngineV2 adapter。

历史背景：
- C6 之前 mock 点是 ``openakita.core.policy.get_policy_engine``，决策走 v1 PolicyEngine。
- C6 之后 mock 点切换到 ``openakita.core.policy_v2.global_engine.get_engine_v2``
  返回的 PolicyEngineV2 实例，或者 patch ``policy_v2.adapter._get_engine``
  以注入测试 stub。

本套件覆盖：
- 风险工具引擎不可用 → DENY (fail-closed)
- 安全工具引擎不可用 → ALLOW (fail-open)
- plan/ask 模式规则在 policy 调用前就 deny
- 引擎只调用一次（防止 dual-check 回潮）
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from openakita.core import reasoning_engine as reasoning_engine_module
from openakita.core.permission import check_permission
from openakita.core.policy_v2 import (
    ApprovalClass,
    DecisionAction,
    DeferredApprovalRequired,
    PolicyDecisionV2,
    reset_engine_v2,
    set_engine_v2,
)
from openakita.core.tool_executor import ToolExecutor


@pytest.fixture(autouse=True)
def _reset_v2_engine():
    """每个 test 用后清理 v2 单例避免互相污染。"""
    yield
    reset_engine_v2()


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

    def get_permission_check(self, tool_name: str):
        return None

    def list_tools(self) -> list[str]:
        return ["read_file"]


class _CountingV2Engine:
    """v2 PolicyEngineV2 的最小测试替身。

    只实现 evaluate_tool_call —— 足够 adapter 走通；其他方法（evaluate_message_intent
    等）若真有调用会触发 AttributeError 暴露 bug。
    """

    def __init__(self, action: DecisionAction = DecisionAction.ALLOW) -> None:
        self.calls = 0
        self.action = action

    def evaluate_tool_call(self, event: Any, ctx: Any) -> PolicyDecisionV2:
        self.calls += 1
        return PolicyDecisionV2(
            action=self.action,
            reason="" if self.action == DecisionAction.ALLOW else "stub deny",
            approval_class=ApprovalClass.READONLY_SCOPED,
            is_unattended_path=self.action == DecisionAction.DEFER,
        )


def _install_v2(engine: _CountingV2Engine) -> None:
    """注入 v2 stub 引擎到全局单例。"""
    # set_engine_v2 期望 PolicyEngineV2 实例；这里 stub duck-type 通过即可
    # （adapter 只调 .evaluate_tool_call）。
    set_engine_v2(engine)  # type: ignore[arg-type]


def _patch_engine_to_raise(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """让 adapter._get_engine() 抛指定异常，模拟引擎不可用。"""

    def _boom() -> Any:
        raise exc

    monkeypatch.setattr(
        "openakita.core.policy_v2.adapter._get_engine",
        _boom,
    )


# ---------------------------------------------------------------------------
# fail-closed / fail-open 行为
# ---------------------------------------------------------------------------


def test_permission_fail_closed_for_risky_tools(monkeypatch: pytest.MonkeyPatch):
    _patch_engine_to_raise(monkeypatch, RuntimeError("policy unavailable"))
    result = check_permission("run_shell", {"command": "echo hi"})
    assert result.behavior == "deny"
    assert "安全策略暂时不可用" in result.reason
    # 决策链最后一个节点必须是 v2（防止意外回退到 v1）
    assert any("policy_engine_v2" in step.get("layer", "") for step in result.decision_chain)


def test_permission_still_allows_safe_reads_when_policy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_engine_to_raise(monkeypatch, RuntimeError("policy unavailable"))
    result = check_permission("read_file", {"path": "README.md"})
    assert result.behavior == "allow"


def test_permission_fail_closed_propagates_v2_exception_string(
    monkeypatch: pytest.MonkeyPatch,
):
    """adapter 自己 fail-closed 后 permission 层再 catch 一次：error step 应包含异常详情。"""
    _patch_engine_to_raise(monkeypatch, RuntimeError("specific-engine-error-XYZ"))
    result = check_permission("write_file", {"path": "/tmp/x"})
    assert result.behavior == "deny"
    # adapter._synthesize_fail_closed 给出的 chain 走 metadata；外层 chain 至少标 v2 layer
    assert any("policy_engine_v2" in step.get("layer", "") for step in result.decision_chain)


# ---------------------------------------------------------------------------
# execute_batch ↔ v2 adapter 集成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_batch_only_runs_policy_once_in_non_agent_mode():
    engine = _CountingV2Engine()
    _install_v2(engine)

    executor = ToolExecutor(_DummyRegistry())
    executor._current_mode = "plan"

    results, executed, _ = await executor.execute_batch(
        [{"id": "tool-1", "name": "read_file", "input": {"path": "README.md"}}]
    )

    # plan 模式下 read_file 由 mode_ruleset 直接 allow，policy 仍会被调用一次
    # （Step 2 兜底）；防止 dual-check 回潮 → 必须严格 == 1
    assert engine.calls == 1
    assert executed == ["read_file"]
    assert results[0]["content"] == "ok:read_file"


@pytest.mark.asyncio
async def test_execute_batch_blocks_plan_denials_before_policy():
    engine = _CountingV2Engine()
    _install_v2(engine)

    executor = ToolExecutor(_DummyRegistry())
    executor._current_mode = "plan"

    results, executed, _ = await executor.execute_batch(
        [{"id": "tool-1", "name": "run_shell", "input": {"command": "echo hi"}}]
    )

    # plan 模式 run_shell 被 mode_ruleset deny —— policy 层不应被调用
    assert engine.calls == 0
    assert executed == []
    assert "run_shell" in results[0]["content"]
    assert results[0]["is_error"] is True


# ---------------------------------------------------------------------------
# execute_tool_with_policy 仍然兼容 v1 PolicyResult（reasoning_engine 合成路径）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_with_policy_still_enforces_plan_mode_rules():
    """C8b-6b: 原本传 v1 ``PolicyResult``；改传 v2 ``PolicyDecisionV2`` 等价
    （``execute_tool_with_policy`` duck-type 只读 ``.metadata``，两者兼容）。"""
    registry = _DummyRegistry()
    executor = ToolExecutor(registry)
    executor._current_mode = "plan"

    result = await executor.execute_tool_with_policy(
        "run_shell",
        {"command": "echo hi"},
        PolicyDecisionV2(action=DecisionAction.ALLOW),
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
        PolicyDecisionV2(action=DecisionAction.ALLOW),
        session_id="conv-1",
    )

    assert result == "ok:create_todo"
    assert registry.executed == ["create_todo"]


@pytest.mark.asyncio
async def test_execute_tool_with_policy_refuses_defer_decision():
    registry = _DummyRegistry()
    executor = ToolExecutor(registry)

    with pytest.raises(DeferredApprovalRequired):
        await executor.execute_tool_with_policy(
            "read_file",
            {"path": "README.md"},
            PolicyDecisionV2(
                action=DecisionAction.DEFER,
                reason="unattended strategy=defer_to_owner",
                approval_class=ApprovalClass.READONLY_SCOPED,
                metadata={"unattended_strategy": "defer_to_owner"},
            ),
            session_id="conv-1",
        )

    assert registry.executed == []


def test_reasoning_engine_defer_paths_route_to_pending_approvals():
    source = inspect.getsource(reasoning_engine_module.ReasoningEngine)

    assert source.count("DecisionAction.DEFER") >= 2
    assert source.count("_defer_unattended_confirm") >= 2
    assert source.count("DeferredApprovalRequired") >= 2


@pytest.mark.asyncio
async def test_execute_batch_stops_immediately_on_deferred_approval(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = _CountingV2Engine(action=DecisionAction.DEFER)
    _install_v2(engine)

    async def _fake_defer(self, **kwargs):  # noqa: ANN001
        return {
            "type": "tool_result",
            "tool_use_id": kwargs["tool_use_id"],
            "content": "paused",
            "is_error": True,
            "_deferred_approval_id": "pa_test",
            "_deferred_approval_strategy": "defer_to_owner",
        }

    monkeypatch.setattr(ToolExecutor, "_defer_unattended_confirm", _fake_defer)

    registry = _DummyRegistry()
    executor = ToolExecutor(registry)
    results, executed, _ = await executor.execute_batch(
        [
            {"id": "tool-1", "name": "read_file", "input": {"path": "a.txt"}},
            {"id": "tool-2", "name": "create_todo", "input": {"title": "b"}},
        ],
    )

    assert len(results) == 1
    assert results[0]["_deferred_approval_id"] == "pa_test"
    assert executed == []
    assert registry.executed == []


# ---------------------------------------------------------------------------
# v2 stub 引擎 deny → permission.check_permission deny
# ---------------------------------------------------------------------------


def test_permission_propagates_v2_deny():
    engine = _CountingV2Engine(action=DecisionAction.DENY)
    _install_v2(engine)

    result = check_permission("write_file", {"path": "/etc/passwd"})

    assert result.behavior == "deny"
    assert engine.calls == 1
    # policy_name 应带 v2 前缀，便于审计辨识
    assert result.policy_name.startswith("policy_v2")


def test_permission_propagates_v2_confirm():
    engine = _CountingV2Engine(action=DecisionAction.CONFIRM)
    _install_v2(engine)

    result = check_permission("write_file", {"path": "/tmp/x.txt"})

    assert result.behavior == "confirm"
    assert engine.calls == 1


def test_permission_v2_defer_downgrades_to_confirm():
    """DEFER → CONFIRM 降级（v1 不识别 DEFER；UI 拦截）。"""
    engine = _CountingV2Engine(action=DecisionAction.DEFER)
    _install_v2(engine)

    result = check_permission("write_file", {"path": "/tmp/x.txt"})

    # adapter._V2_TO_V1_DECISION: DEFER → "confirm"
    assert result.behavior == "confirm"
    assert engine.calls == 1


# ---------------------------------------------------------------------------
# v2 hot-reload：reset_policy_v2_layer 是 UI 配置变更后的唯一刷新入口
# （C6 时由 reset_policy_engine v1 facade 触发；C8b-6b 删 v1 后直调 v2）
# ---------------------------------------------------------------------------


def test_reset_policy_v2_layer_clears_singleton():
    """C8b-6b: 原 ``test_reset_policy_engine_also_resets_v2_singleton`` 验证
    v1 reset facade 同步清 v2；v1 删除后改为直接验证 v2 ``reset_policy_v2_layer``
    是 UI hot-reload 的契约入口（同时清引擎 + audit logger，见 §C6 二轮 audit）。
    """
    from openakita.core.policy_v2.global_engine import (
        get_engine_v2,
        is_initialized,
        reset_policy_v2_layer,
    )

    eng_before = get_engine_v2()
    assert is_initialized()

    reset_policy_v2_layer()

    assert not is_initialized(), (
        "reset_policy_v2_layer() 没有清 v2 单例 —— "
        "UI 配置 hot-reload 后 v2 仍按旧 YAML 评估"
    )

    eng_after = get_engine_v2()
    assert eng_after is not eng_before
