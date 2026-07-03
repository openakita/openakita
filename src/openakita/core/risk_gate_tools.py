"""Structured RiskGate tool-call authorization helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..tools.tool_hints import ConfigHint
from .risk_gate_workflow import RiskGateToolPrompt
from .risk_intent import TurnRiskAuthorization
from .tool_execution_context import ToolExecutionContext


@dataclass(slots=True)
class RiskGateToolExecutionOutcome:
    result_text: str
    hint: ConfigHint | None
    is_error: bool
    metadata: dict[str, Any] | None = None


def riskgate_tool_classification(
    *,
    policy_metadata: dict[str, Any] | None,
    tool_name: str,
    tool_args: dict[str, Any],
) -> dict[str, Any]:
    """Create RiskGate metadata from structured PolicyV2 tool metadata."""
    meta = policy_metadata if isinstance(policy_metadata, dict) else {}
    if meta.get("riskgate_required") is not True:
        raise ValueError("RiskGate tool confirmation requires policy riskgate_required metadata")

    operation = str(meta.get("riskgate_operation") or "").strip()
    if not operation:
        raise ValueError("RiskGate tool policy must declare riskgate_operation")

    scope = meta.get("riskgate_scope")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("RiskGate tool policy must provide a non-empty riskgate_scope")

    classification = {
        "kind": "tool_call",
        "risk_level": str(meta.get("risk_level") or "high"),
        "operation_kind": operation,
        "operation": operation,
        "target_kind": str(meta.get("riskgate_target_kind") or "tool"),
        "requires_confirmation": True,
        "action": None,
        "tool_name": tool_name,
        "tool_input": dict(tool_args or {}),
        "riskgate_scope": dict(scope),
        "reason": str(meta.get("riskgate_reason") or "tool commit requires RiskGate"),
        "parameters": {
            "tool_name": tool_name,
            "tool_input": dict(tool_args or {}),
        },
    }
    tool_display = meta.get("tool_display")
    if isinstance(tool_display, dict) and tool_display:
        classification["tool_display"] = dict(tool_display)
    return classification


def prepare_riskgate_tool_prompt(
    *,
    conversation_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    policy_result: Any,
    request_id: str,
    timeout_seconds: float,
    channel: str,
    delegate_chain: list[str],
    root_user_id: str | None,
) -> RiskGateToolPrompt:
    """Create a structured RiskGate prompt and register its UI event."""
    from .risk_gate_workflow import get_risk_gate_workflow

    return get_risk_gate_workflow().open_tool_confirmation(
        conversation_id=conversation_id,
        original_message=f"tool:{tool_name}",
        classification=riskgate_tool_classification(
            policy_metadata=dict(getattr(policy_result, "metadata", {}) or {}),
            tool_name=tool_name,
            tool_args=tool_input,
        ),
        request_id=request_id,
        tool_name=tool_name,
        tool_args=tool_input,
        reason=str(getattr(policy_result, "reason", "") or ""),
        timeout_seconds=timeout_seconds,
        channel=channel,
        approval_class=getattr(getattr(policy_result, "approval_class", None), "value", None),
        policy_version=2,
        decision_chain=policy_result.to_ui_chain(),
        delegate_chain=delegate_chain,
        root_user_id=root_user_id,
    )


async def resolve_riskgate_tool_decision(
    *,
    confirmation_id: str,
    timeout_seconds: float,
) -> Any | None:
    """Wait for a RiskGate tool confirmation and return its one-shot grant."""
    from .risk_gate_workflow import get_risk_gate_workflow

    return await get_risk_gate_workflow().wait_for_tool_grant(
        confirmation_id=confirmation_id,
        timeout_seconds=timeout_seconds,
    )


def tool_risk_authorization_from_approved(
    approved_tool_call: Any,
) -> TurnRiskAuthorization | None:
    """Create execution authorization only from a confirmed RiskGate record."""
    classification = (
        dict(approved_tool_call.classification or {})
        if hasattr(approved_tool_call, "classification")
        else {}
    )
    operation = str(
        classification.get("operation")
        or classification.get("operation_kind")
        or ""
    ).strip()
    if not operation:
        return None
    scope = classification.get("riskgate_scope")
    if not isinstance(scope, dict):
        scope = {}
    authorized_intent = {
        "operation": operation,
        "target_kind": str(classification.get("target_kind") or "tool"),
        "scope": dict(scope),
        "tool_names": [str(classification.get("tool_name") or approved_tool_call.tool_name)],
        "confirmation_id": approved_tool_call.confirmation_id,
        "issued_at": time.time(),
        "turn_scoped": True,
        "version": 2,
    }
    return TurnRiskAuthorization(
        original_message=f"tool:{approved_tool_call.tool_name}:{approved_tool_call.confirmation_id}",
        confirmation_id=approved_tool_call.confirmation_id,
        authorized_intent=authorized_intent,
    )


async def execute_with_confirmed_riskgate_tool_authorization(
    executor: Any,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    policy_result: Any,
    session_id: str | None,
    approved_tool_call: Any,
) -> Any:
    """Execute a confirmed RiskGate tool call with scoped authorization."""
    auth = tool_risk_authorization_from_approved(approved_tool_call)
    if auth is None:
        raise RuntimeError("confirmed RiskGate tool record does not contain executable scope")
    return await executor.execute_tool_with_policy(
        tool_name=tool_name,
        tool_input=tool_input,
        policy_result=policy_result,
        session_id=session_id,
        execution_context=ToolExecutionContext(risk_authorization=auth),
    )


async def execute_after_riskgate_tool_prompt(
    executor: Any,
    *,
    confirmation_id: str,
    timeout_seconds: float,
    tool_name: str,
    tool_input: dict[str, Any],
    session_id: str | None,
    detect_result_errors: bool,
    unpack_tool_result: Any,
    tool_result_looks_error: Any,
) -> RiskGateToolExecutionOutcome:
    """Wait for RiskGate confirmation and execute the approved tool once."""
    approved_tool_call = await resolve_riskgate_tool_decision(
        confirmation_id=confirmation_id,
        timeout_seconds=timeout_seconds,
    )
    if approved_tool_call is None:
        return RiskGateToolExecutionOutcome(
            result_text="用户已拒绝 RiskGate 确认或确认已超时。不要再执行该高风险操作。",
            hint=None,
            is_error=True,
        )

    try:
        from .policy_v2.enums import DecisionAction
        from .policy_v2.models import PolicyDecisionV2

        raw_result = await execute_with_confirmed_riskgate_tool_authorization(
            executor,
            tool_name=tool_name,
            tool_input=tool_input,
            policy_result=PolicyDecisionV2(
                action=DecisionAction.ALLOW,
                reason="用户已通过 RiskGate 确认",
                metadata={"confirmed_bypass": True},
            ),
            session_id=session_id,
            approved_tool_call=approved_tool_call,
        )
        unpacked = unpack_tool_result(raw_result)
        if isinstance(unpacked, tuple) and len(unpacked) == 3:
            result_text, hint, metadata = unpacked
        else:
            result_text, hint = unpacked
            metadata = None
        return RiskGateToolExecutionOutcome(
            result_text=result_text,
            hint=hint,
            is_error=tool_result_looks_error(result_text) if detect_result_errors else False,
            metadata=dict(metadata or {}),
        )
    except Exception as exc:
        return RiskGateToolExecutionOutcome(
            result_text=f"Tool error after RiskGate confirmation: {exc}",
            hint=None,
            is_error=True,
        )
