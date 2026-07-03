"""Storage records for RiskGate confirmations.

The lifecycle boundary for RiskGate confirmations is
``openakita.core.risk_gate_workflow.RiskGateWorkflow``. This module stores the
records, applies retention, and exposes read/clear helpers for diagnostics and
tests; it does not own UI sidecars, waiters, user decisions, or executable
grants.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RiskGateConfirmationState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class PendingRiskConfirmation:
    confirmation_id: str
    conversation_id: str
    request_id: str
    original_message: str
    classification: dict[str, Any]
    allowed_actions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskGateConfirmationRecord:
    confirmation_id: str
    conversation_id: str
    original_message: str
    classification: dict[str, Any]
    request_id: str = ""
    allowed_actions: list[str] = field(default_factory=list)
    state: RiskGateConfirmationState = RiskGateConfirmationState.PENDING
    decision: str = ""
    answer: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    retain_until: float = 0.0
    execution: dict[str, Any] = field(default_factory=dict)

    def pending_deadline_passed(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def retention_elapsed(self, now: float | None = None) -> bool:
        return self.retain_until > 0 and (now or time.time()) >= self.retain_until

    def is_pending(self, now: float | None = None) -> bool:
        return (
            self.state == RiskGateConfirmationState.PENDING
            and not self.pending_deadline_passed(now)
        )

    def to_pending(self) -> PendingRiskConfirmation:
        return PendingRiskConfirmation(
            confirmation_id=self.confirmation_id,
            conversation_id=self.conversation_id,
            request_id=self.request_id,
            original_message=self.original_message,
            classification=dict(self.classification or {}),
            allowed_actions=list(self.allowed_actions or []),
            created_at=self.created_at,
            expires_at=self.expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class ApprovedRiskGateToolCall:
    confirmation_id: str
    conversation_id: str
    tool_name: str
    tool_input: dict[str, Any]
    classification: dict[str, Any]
    decision: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PendingRiskConfirmationStore:
    """In-memory store for RiskGate confirmation records.

    ``ttl_seconds`` bounds how long a pending prompt can be accepted.
    ``terminal_retention_seconds`` bounds how long resolved or timed-out
    records stay available for late UI responses and diagnostics.
    """

    def __init__(
        self,
        ttl_seconds: float = 120.0,
        *,
        terminal_retention_seconds: float | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.terminal_retention_seconds = (
            ttl_seconds if terminal_retention_seconds is None else terminal_retention_seconds
        )
        self._records: dict[str, RiskGateConfirmationRecord] = {}
        self._conversation_pending: dict[str, list[str]] = {}

    def create_record(
        self,
        *,
        conversation_id: str,
        original_message: str,
        classification: dict[str, Any],
        request_id: str = "",
    ) -> PendingRiskConfirmation:
        now = time.time()
        self.sweep_expired(now=now)
        action = classification.get("action")
        inspect_action = self._inspect_action(classification)
        allowed = [a for a in (action, inspect_action, "cancel") if a]
        record = RiskGateConfirmationRecord(
            confirmation_id=f"risk_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            request_id=request_id or f"risk_{uuid.uuid4().hex[:8]}",
            original_message=original_message,
            classification=dict(classification),
            allowed_actions=allowed,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._records[record.confirmation_id] = record
        self._conversation_pending.setdefault(conversation_id, []).append(record.confirmation_id)
        return record.to_pending()

    def get(self, conversation_id: str) -> PendingRiskConfirmation | None:
        self.sweep_expired()
        for confirmation_id in reversed(self._conversation_pending.get(conversation_id, [])):
            record = self._records.get(confirmation_id)
            if record and record.is_pending():
                return record.to_pending()
        return None

    def get_record(self, confirmation_id: str) -> RiskGateConfirmationRecord | None:
        if not confirmation_id:
            return None
        self.sweep_expired()
        return self._records.get(confirmation_id)

    def list_records(self, conversation_id: str = "") -> list[RiskGateConfirmationRecord]:
        self.sweep_expired()
        if not conversation_id:
            return list(self._records.values())
        return [
            record for record in self._records.values() if record.conversation_id == conversation_id
        ]

    def transition_record(
        self,
        record: RiskGateConfirmationRecord,
        *,
        state: str | RiskGateConfirmationState,
        decision: str = "",
        answer: str = "",
        message: str = "",
        detail: str = "",
        updated_at: float | None = None,
    ) -> RiskGateConfirmationRecord | None:
        if record is None:
            return None
        next_state = self._require_state(state)
        record.state = next_state
        if decision:
            record.decision = decision
        if answer:
            record.answer = answer
        record.updated_at = time.time() if updated_at is None else updated_at
        if message or detail:
            execution = dict(record.execution or {})
            execution.update(
                {
                    "state": next_state.value,
                    "request_id": record.request_id or execution.get("request_id", ""),
                    "message": message,
                    "detail": detail,
                    "updated_at": record.updated_at,
                }
            )
            record.execution = execution
        if next_state != RiskGateConfirmationState.PENDING:
            self._remove_pending_index(record.conversation_id, record.confirmation_id)
            record.retain_until = record.updated_at + max(0.0, self.terminal_retention_seconds)
        else:
            record.retain_until = 0.0
        return record

    def clear(self, conversation_id: str = "") -> None:
        if not conversation_id:
            self._records.clear()
            self._conversation_pending.clear()
            return
        ids = set(self._conversation_pending.pop(conversation_id, []))
        for confirmation_id, record in list(self._records.items()):
            if record.conversation_id == conversation_id or confirmation_id in ids:
                self._records.pop(confirmation_id, None)

    def _remove_pending_index(self, conversation_id: str, confirmation_id: str) -> None:
        ids = self._conversation_pending.get(conversation_id)
        if not ids:
            return
        self._conversation_pending[conversation_id] = [cid for cid in ids if cid != confirmation_id]
        if not self._conversation_pending[conversation_id]:
            self._conversation_pending.pop(conversation_id, None)

    def sweep_expired(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        removed = 0
        for confirmation_id, record in list(self._records.items()):
            if (
                record.state == RiskGateConfirmationState.PENDING
                and record.pending_deadline_passed(now)
            ):
                self.transition_record(
                    record,
                    state=RiskGateConfirmationState.TIMEOUT,
                    decision="timeout",
                    answer="timeout",
                    message="RiskGate confirmation timed out.",
                    updated_at=now,
                )
            if record.state != RiskGateConfirmationState.PENDING and record.retention_elapsed(now):
                self._records.pop(confirmation_id, None)
                self._remove_pending_index(record.conversation_id, confirmation_id)
                removed += 1
        return removed

    @staticmethod
    def _require_state(state: str | RiskGateConfirmationState) -> RiskGateConfirmationState:
        try:
            return (
                state
                if isinstance(state, RiskGateConfirmationState)
                else RiskGateConfirmationState(str(state))
            )
        except ValueError as exc:
            raise ValueError(f"unsupported RiskGate confirmation state: {state!r}") from exc

    @staticmethod
    def _inspect_action(classification: dict[str, Any]) -> str | None:
        target = classification.get("target_kind")
        if target == "security_user_allowlist":
            return "list_security_allowlist"
        if target == "skill_external_allowlist":
            return "list_skill_external_allowlist"
        return None


_store: PendingRiskConfirmationStore | None = None


def get_confirmation_store() -> PendingRiskConfirmationStore:
    global _store
    if _store is None:
        _store = PendingRiskConfirmationStore()
    return _store
