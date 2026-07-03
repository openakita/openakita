"""RiskGate confirmation lifecycle boundary."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .confirmation_state import (
    ApprovedRiskGateToolCall,
    PendingRiskConfirmation,
    PendingRiskConfirmationStore,
    RiskGateConfirmationRecord,
    RiskGateConfirmationState,
    get_confirmation_store,
)
from .security_confirm_channel import (
    build_security_confirm_event,
    require_security_confirm_decision,
)
from .ui_confirm_bus import UIConfirmBus, get_ui_confirm_bus

RISKGATE_SECURITY_CONFIRM_DECISIONS = frozenset({"allow_once", "deny", "timeout"})
ALLOW_RISKGATE_CONFIRM_DECISIONS = frozenset({"allow_once"})


def require_riskgate_security_decision(decision: str) -> str:
    value = require_security_confirm_decision(decision)
    if value in RISKGATE_SECURITY_CONFIRM_DECISIONS:
        return value
    raise ValueError(f"unsupported RiskGate confirmation decision: {value!r}")


def riskgate_state_from_security_decision(decision: str) -> RiskGateConfirmationState:
    value = require_riskgate_security_decision(decision)
    if value in ALLOW_RISKGATE_CONFIRM_DECISIONS:
        return RiskGateConfirmationState.CONFIRMED
    if value == "timeout":
        return RiskGateConfirmationState.TIMEOUT
    return RiskGateConfirmationState.CANCELLED


@dataclass(slots=True)
class RiskGateDecisionResult:
    """Result of resolving one RiskGate confirmation decision."""

    handled: bool
    status: str
    confirm_id: str
    decision: str
    riskgate_state: RiskGateConfirmationState
    conversation_id: str = ""
    original_message: str = ""
    approved_tool_call: ApprovedRiskGateToolCall | None = None

    def to_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "handled": self.handled,
            "status": self.status,
            "kind": "risk_gate",
            "confirm_id": self.confirm_id,
            "decision": self.decision,
            "riskgate_state": self.riskgate_state.value,
            "execution": {
                "state": self.riskgate_state.value,
                "backend_owned": True,
                "client_action": "none",
            },
        }
        if self.conversation_id:
            response["conversation_id"] = self.conversation_id
            response["execution"]["conversation_id"] = self.conversation_id
        if self.original_message:
            response["original_message"] = self.original_message
        if self.approved_tool_call is not None:
            response["tool"] = self.approved_tool_call.tool_name
            response["execution"]["confirmation_id"] = self.confirm_id
        return response


@dataclass(slots=True)
class RiskGateToolPrompt:
    """RiskGate record plus the UI event that represents it."""

    pending: PendingRiskConfirmation
    event: dict[str, Any]


class RiskGateWorkflow:
    """Own RiskGate records, UI sidecars, decisions, waiters, and grants."""

    def __init__(
        self,
        *,
        store: PendingRiskConfirmationStore | None = None,
        bus: UIConfirmBus | None = None,
    ) -> None:
        self._store = store or get_confirmation_store()
        self._bus = bus or get_ui_confirm_bus()

    def open_tool_confirmation(
        self,
        *,
        conversation_id: str,
        original_message: str,
        classification: dict[str, Any],
        request_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        reason: str,
        timeout_seconds: float,
        channel: str,
        approval_class: str | None,
        policy_version: int,
        decision_chain: list[dict[str, Any]],
        delegate_chain: list[str],
        root_user_id: str | None,
    ) -> RiskGateToolPrompt:
        """Create one RiskGate confirmation and all UI/waiter side effects."""
        if dict(classification or {}).get("kind") != "tool_call":
            raise ValueError("RiskGate workflow only opens structured tool-call confirmations")
        tool_name = str(tool_name or "").strip()
        if not tool_name:
            raise ValueError("RiskGate tool confirmation requires a tool name")

        pending = self._store.create_record(
            conversation_id=conversation_id,
            original_message=original_message,
            classification=classification,
            request_id=request_id,
        )
        self._bus.prepare(pending.confirmation_id)

        risk_level = str(classification.get("risk_level") or "high")
        approval = approval_class or "destructive"
        event_tool = f"risk_gate:{tool_name}"
        event = build_security_confirm_event(
            source="risk_gate",
            confirm_id=pending.confirmation_id,
            conversation_id=conversation_id,
            tool_name=event_tool,
            display_tool_name=tool_name,
            tool_args=tool_args,
            reason=reason,
            risk_level=risk_level,
            needs_sandbox=False,
            timeout_seconds=timeout_seconds,
            default_on_timeout="deny",
            channel=channel,
            approval_class=approval,
            policy_version=policy_version,
            risk_intent=classification,
            decision_chain=decision_chain,
            delegate_chain=delegate_chain,
            root_user_id=root_user_id,
            options=["allow_once", "deny"],
            policy_metadata=classification,
            kind="risk_gate",
        )
        event.update(
            self._bus.store_pending(
                pending.confirmation_id,
                event_tool,
                tool_args,
                session_id=conversation_id,
                needs_sandbox=False,
                confirm_event=event,
            )
        )
        return RiskGateToolPrompt(pending=pending, event=event)

    def resolve_decision(
        self,
        confirm_id: str,
        decision: str,
    ) -> RiskGateDecisionResult | None:
        """Apply a user decision to a RiskGate record.

        Only structured ``kind=tool_call`` RiskGate records can produce an
        executable grant. Non-tool-call records are not RiskGate workflow
        records and are ignored by this boundary.
        """
        normalized = require_riskgate_security_decision(decision)
        record = self._store.get_record(confirm_id)
        if record is None:
            return None

        if dict(record.classification or {}).get("kind") != "tool_call":
            return None

        if record.is_pending():
            return self._resolve_tool_call_record(record, normalized)

        return RiskGateDecisionResult(
            handled=True,
            status="ok",
            confirm_id=confirm_id,
            decision=normalized,
            conversation_id=record.conversation_id,
            riskgate_state=record.state,
            original_message=record.original_message,
        )

    async def wait_for_tool_grant(
        self,
        *,
        confirmation_id: str,
        timeout_seconds: float,
    ) -> ApprovedRiskGateToolCall | None:
        """Wait for a tool-call confirmation and return a one-shot grant."""
        decision = await self._bus.wait_for_resolution(confirmation_id, timeout_seconds)
        self._bus.cleanup(confirmation_id)
        if decision not in ALLOW_RISKGATE_CONFIRM_DECISIONS:
            record = self._store.get_record(confirmation_id)
            if record is not None and record.state == RiskGateConfirmationState.PENDING:
                self._store.transition_record(
                    record,
                    state=RiskGateConfirmationState.TIMEOUT,
                    decision="timeout",
                    answer="timeout",
                    message="RiskGate 确认已超时或被默认拒绝。",
                )
            return None
        record = self._store.get_record(confirmation_id)
        if record is None or record.state != RiskGateConfirmationState.CONFIRMED:
            return None
        return self._approved_tool_call_from_record(record, decision=decision)

    def _resolve_tool_call_record(
        self,
        record: RiskGateConfirmationRecord,
        decision: str,
    ) -> RiskGateDecisionResult:
        riskgate_state = riskgate_state_from_security_decision(decision)
        self._store.transition_record(
            record,
            state=riskgate_state,
            decision=decision,
            answer=decision,
        )
        self._bus.resolve(record.confirmation_id, decision)
        approved_tool_call = (
            self._approved_tool_call_from_record(record, decision=decision)
            if riskgate_state == RiskGateConfirmationState.CONFIRMED
            else None
        )
        return RiskGateDecisionResult(
            handled=True,
            status="ok",
            confirm_id=record.confirmation_id,
            decision=decision,
            conversation_id=record.conversation_id,
            riskgate_state=riskgate_state,
            original_message=record.original_message,
            approved_tool_call=approved_tool_call,
        )

    def _approved_tool_call_from_record(
        self,
        record: RiskGateConfirmationRecord,
        *,
        decision: str,
    ) -> ApprovedRiskGateToolCall:
        now = time.time()
        classification = dict(record.classification or {})
        return ApprovedRiskGateToolCall(
            confirmation_id=record.confirmation_id,
            conversation_id=record.conversation_id,
            tool_name=str(classification.get("tool_name") or ""),
            tool_input=dict(classification.get("tool_input") or {}),
            classification=classification,
            decision=decision,
            created_at=now,
            expires_at=now + self._store.ttl_seconds,
        )


def get_risk_gate_workflow() -> RiskGateWorkflow:
    return RiskGateWorkflow()
