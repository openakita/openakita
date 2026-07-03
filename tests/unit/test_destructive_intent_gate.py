"""RiskGate must be driven by structured tool calls, not user-message heuristics."""

from __future__ import annotations

import time


def test_tool_commit_policy_requests_confirmation_without_replay():
    from openakita.core.policy_v2 import (
        DecisionAction,
        ToolPolicy,
        build_policy_context,
        evaluate_via_v2,
        reset_current_context,
        set_current_context,
    )

    ctx = build_policy_context(
        session=None,
        mode="agent",
        user_message="用户怎么说不重要，安全边界在工具参数",
        tool_policies={
            "declared_delete_tool": ToolPolicy(
                preview_param="dry_run",
                preview_default=True,
                commit_requires_riskgate=True,
                riskgate_operation="memory_delete",
                riskgate_scope_params=("query",),
                riskgate_scope_required_any=("query",),
                riskgate_scope_text_params=("query",),
                commit_step_name="tool_commit_requires_riskgate",
            )
        },
    )
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2(
            "declared_delete_tool",
            {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        )
    finally:
        reset_current_context(token)

    assert decision.action == DecisionAction.CONFIRM
    assert decision.metadata["riskgate_required"] is True
    assert decision.metadata["riskgate_operation"] == "memory_delete"
    assert decision.metadata["riskgate_tool_name"] == "declared_delete_tool"
    assert decision.metadata["riskgate_scope"]["query"] == "OPENAKITA_RISKGATE_689_REPRO_TEST"


def test_tool_commit_policy_does_not_accept_backend_replay_as_commit_authorization():
    from openakita.core.policy_v2 import (
        DecisionAction,
        ToolPolicy,
        build_policy_context,
        evaluate_via_v2,
        reset_current_context,
        set_current_context,
    )

    ctx = build_policy_context(
        session=None,
        mode="agent",
        user_message="original user message",
        tool_policies={
            "declared_delete_tool": ToolPolicy(
                preview_param="dry_run",
                preview_default=True,
                commit_requires_riskgate=True,
                riskgate_operation="memory_delete",
                riskgate_scope_params=("query",),
                riskgate_scope_required_any=("query",),
                riskgate_scope_text_params=("query",),
                commit_step_name="tool_commit_requires_riskgate",
            )
        },
        replay_authorizations=[
            {
                "expires_at": time.time() + 3600.0,
                "original_message": "original user message",
                "confirmation_id": "risk-tool-confirm",
                "operation": "delete",
                "tool_names": ["declared_delete_tool"],
            }
        ],
    )
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2(
            "declared_delete_tool",
            {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        )
    finally:
        reset_current_context(token)

    assert decision.action == DecisionAction.CONFIRM
    assert any(step.name == "tool_commit_requires_riskgate" for step in decision.chain)
    assert not any(step.name == "replay" for step in decision.chain)
    assert decision.metadata["riskgate_required"] is True


async def test_plain_tool_confirmation_does_not_inject_riskgate_authorization():
    from openakita.core.policy_v2 import DecisionAction
    from openakita.core.policy_v2.models import PolicyDecisionV2

    captured = {}

    class _Executor:
        async def execute_tool_with_policy(self, **kwargs):
            ctx = kwargs.get("execution_context")
            captured["ctx"] = ctx
            captured["auth"] = getattr(ctx, "risk_authorization", None)
            captured["kwargs"] = kwargs
            return "ok", None

    result = await _Executor().execute_tool_with_policy(
        tool_name="declared_delete_tool",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        policy_result=PolicyDecisionV2(action=DecisionAction.ALLOW),
        session_id="conv-tool-auth",
    )

    assert result == ("ok", None)
    assert captured["ctx"] is None
    assert captured["auth"] is None


async def test_confirmed_riskgate_tool_record_injects_turn_authorization():
    from openakita.core.confirmation_state import ApprovedRiskGateToolCall
    from openakita.core.policy_v2 import DecisionAction
    from openakita.core.policy_v2.models import PolicyDecisionV2
    from openakita.core.risk_gate_tools import (
        execute_with_confirmed_riskgate_tool_authorization,
    )

    captured = {}

    class _Executor:
        async def execute_tool_with_policy(self, **kwargs):
            ctx = kwargs.get("execution_context")
            captured["ctx"] = ctx
            captured["auth"] = getattr(ctx, "risk_authorization", None)
            captured["kwargs"] = kwargs
            return "ok", None

    approved = ApprovedRiskGateToolCall(
        confirmation_id="risk-tool-auth",
        conversation_id="conv-tool-auth",
        tool_name="declared_delete_tool",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        classification={
            "kind": "tool_call",
            "operation": "memory_delete",
            "tool_name": "declared_delete_tool",
            "tool_input": {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
            "riskgate_scope": {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST"},
        },
        decision="allow_once",
    )

    result = await execute_with_confirmed_riskgate_tool_authorization(
        _Executor(),
        tool_name="declared_delete_tool",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        policy_result=PolicyDecisionV2(action=DecisionAction.ALLOW),
        session_id="conv-tool-auth",
        approved_tool_call=approved,
    )

    assert result == ("ok", None)
    auth = captured["auth"]
    assert auth is not None
    assert auth.confirmation_id == "risk-tool-auth"
    assert auth.authorized_intent["operation"] == "memory_delete"
    assert auth.authorized_intent["tool_names"] == ["declared_delete_tool"]
    assert auth.authorized_intent["scope"]["query"] == "OPENAKITA_RISKGATE_689_REPRO_TEST"
    assert captured["kwargs"]["execution_context"] is captured["ctx"]


async def test_confirmed_riskgate_tool_record_without_operation_fails_closed():
    from openakita.core.confirmation_state import ApprovedRiskGateToolCall
    from openakita.core.policy_v2 import DecisionAction
    from openakita.core.policy_v2.models import PolicyDecisionV2
    from openakita.core.risk_gate_tools import (
        execute_with_confirmed_riskgate_tool_authorization,
    )

    executed = False

    class _Executor:
        async def execute_tool_with_policy(self, **_kwargs):
            nonlocal executed
            executed = True
            return "ok", None

    approved = ApprovedRiskGateToolCall(
        confirmation_id="risk-tool-auth",
        conversation_id="conv-tool-auth",
        tool_name="declared_delete_tool",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
        classification={
            "kind": "tool_call",
            "tool_name": "declared_delete_tool",
            "tool_input": {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
            "riskgate_scope": {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST"},
        },
        decision="allow_once",
    )

    try:
        await execute_with_confirmed_riskgate_tool_authorization(
            _Executor(),
            tool_name="declared_delete_tool",
            tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
            policy_result=PolicyDecisionV2(action=DecisionAction.ALLOW),
            session_id="conv-tool-auth",
            approved_tool_call=approved,
        )
    except RuntimeError as exc:
        assert "does not contain executable scope" in str(exc)
    else:
        raise AssertionError("RiskGate tool authorization without operation must fail closed")

    assert executed is False
