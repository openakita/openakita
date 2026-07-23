"""Unified backend channel for security confirmation UI events."""

from __future__ import annotations

from typing import Any

from .policy_v2.display import security_confirm_display

SECURITY_CONFIRM_DECISIONS = frozenset(
    {"allow_once", "allow_session", "allow_always", "deny", "sandbox", "timeout"}
)
SECURITY_CONFIRM_BATCH_DECISIONS = frozenset(
    {"allow_once", "allow_session", "allow_always", "deny", "sandbox"}
)
ALLOW_SECURITY_CONFIRM_DECISIONS = frozenset(
    {"allow_once", "allow_session", "allow_always", "sandbox"}
)
SECURITY_CONFIRM_TIMEOUT_DEFAULTS = frozenset({"allow_once", "deny"})


def require_security_confirm_decision(
    decision: str,
    *,
    allow_timeout: bool = True,
) -> str:
    """Return a protocol decision or raise for non-structured values."""
    decision = (decision or "").strip()
    allowed = SECURITY_CONFIRM_DECISIONS if allow_timeout else SECURITY_CONFIRM_BATCH_DECISIONS
    if decision in allowed:
        return decision
    raise ValueError(f"unsupported security confirmation decision: {decision!r}")


def require_security_confirm_timeout_default(default_on_timeout: str) -> str:
    """Return a protocol timeout default or raise for non-structured values."""
    value = (default_on_timeout or "").strip()
    if value in SECURITY_CONFIRM_TIMEOUT_DEFAULTS:
        return value
    raise ValueError(f"unsupported security confirmation timeout default: {value!r}")


def register_policy_confirm(
    *,
    confirm_id: str,
    conversation_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    reason: str,
    risk_level: str,
    needs_sandbox: bool,
    timeout_seconds: float,
    default_on_timeout: str,
    channel: str,
    approval_class: str | None,
    policy_version: int,
    decision_chain: list[dict[str, Any]],
    delegate_chain: list[str],
    root_user_id: str | None,
    policy_metadata: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> dict[str, Any]:
    """Build, register, and return an ordinary PolicyV2 security confirm event."""
    event = build_security_confirm_event(
        source="policy_v2",
        confirm_id=confirm_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        display_tool_name=tool_name,
        tool_args=tool_args,
        reason=reason,
        risk_level=risk_level,
        needs_sandbox=needs_sandbox,
        timeout_seconds=timeout_seconds,
        default_on_timeout=default_on_timeout,
        channel=channel,
        approval_class=approval_class,
        policy_version=policy_version,
        risk_intent={},
        decision_chain=decision_chain,
        delegate_chain=delegate_chain,
        root_user_id=root_user_id,
        options=[
            "allow_once",
            "allow_session",
            "allow_always",
            "deny",
            *(["sandbox"] if needs_sandbox else []),
        ],
        policy_metadata=policy_metadata,
    )

    from openakita.agent.ui_confirm_bus import get_ui_confirm_bus

    bus = get_ui_confirm_bus()
    event.update(
        bus.store_pending(
            confirm_id,
            tool_name,
            tool_args,
            session_id=conversation_id,
            needs_sandbox=needs_sandbox,
            dedup_key=dedup_key,
            confirm_event=event,
        )
    )
    bus.prepare(confirm_id)
    return event


def build_security_confirm_event(
    *,
    source: str,
    confirm_id: str,
    conversation_id: str,
    tool_name: str,
    display_tool_name: str,
    tool_args: dict[str, Any],
    reason: str,
    risk_level: str,
    needs_sandbox: bool,
    timeout_seconds: float,
    default_on_timeout: str,
    channel: str,
    approval_class: str | None,
    policy_version: int,
    risk_intent: dict[str, Any],
    decision_chain: list[dict[str, Any]],
    delegate_chain: list[str],
    root_user_id: str | None,
    options: list[str],
    policy_metadata: dict[str, Any] | None,
    kind: str | None = None,
) -> dict[str, Any]:
    default_on_timeout = require_security_confirm_timeout_default(default_on_timeout)
    options = [require_security_confirm_decision(option, allow_timeout=False) for option in options]
    event: dict[str, Any] = {
        "type": "security_confirm",
        "source": source,
        "tool": tool_name,
        "args": dict(tool_args or {}),
        "id": confirm_id,
        "confirm_id": confirm_id,
        "conversation_id": conversation_id,
        "reason": reason,
        "risk_level": risk_level,
        "needs_sandbox": needs_sandbox,
        "timeout_seconds": timeout_seconds,
        "default_on_timeout": default_on_timeout,
        "channel": channel,
        "approval_class": approval_class,
        "policy_version": policy_version,
        "risk_intent": dict(risk_intent or {}),
        "delegate_chain": list(delegate_chain or []),
        "root_user_id": root_user_id,
        "decision_chain": list(decision_chain or []),
        "display": security_confirm_display(
            source=source,
            tool_name=display_tool_name,
            args=tool_args,
            reason=reason,
            risk_level=risk_level,
            approval_class=approval_class,
            channel=channel,
            policy_metadata=policy_metadata,
        ),
        "options": list(options),
    }
    if kind:
        event["kind"] = kind
    return event
