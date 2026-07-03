"""Backend-owned resolution for security confirmation decisions."""

from __future__ import annotations

from typing import Any

from .risk_gate_workflow import (
    get_risk_gate_workflow,
    riskgate_state_from_security_decision,
)
from .security_confirm_channel import require_security_confirm_decision


def resolve_security_confirmation(confirm_id: str, decision: str) -> dict[str, Any]:
    """Resolve a security confirmation through the backend state machine.

    RiskGate confirmations are routed through ``RiskGateWorkflow``. Ordinary
    PolicyV2 tool confirmations continue through ``apply_resolution`` so their
    allowlist side effects remain centralized.
    """
    normalized = require_security_confirm_decision(decision)
    riskgate_result = get_risk_gate_workflow().resolve_decision(confirm_id, normalized)
    if riskgate_result is not None:
        response = riskgate_result.to_response()
        _attach_resolution_ui_update(response, confirm_id)
        return response

    from .policy_v2 import apply_resolution

    found = apply_resolution(confirm_id, normalized)
    response = {
        "handled": bool(found),
        "status": "ok",
        "kind": "policy_v2",
        "confirm_id": confirm_id,
        "decision": normalized,
    }
    _attach_resolution_ui_update(response, confirm_id)
    return response


def _attach_resolution_ui_update(response: dict[str, Any], confirm_id: str) -> None:
    """Attach backend-owned presentation updates produced by UIConfirmBus."""
    try:
        from .ui_confirm_bus import get_ui_confirm_bus

        ui_update = get_ui_confirm_bus().consume_resolution_ui_update(confirm_id)
    except Exception:  # noqa: BLE001
        ui_update = None
    if not ui_update:
        return
    response["active_confirm_id"] = ui_update.get("active_confirm_id")
    response["queued_count"] = ui_update.get("queued_count", 0)
    response["pending_count"] = ui_update.get("pending_count", 0)
    next_confirm = ui_update.get("next_confirm")
    if isinstance(next_confirm, dict):
        response["next_confirm"] = next_confirm


__all__ = [
    "require_security_confirm_decision",
    "resolve_security_confirmation",
    "riskgate_state_from_security_decision",
]
