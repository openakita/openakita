"""Pending confirmation state for high-risk user intents."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ConfirmationDecision(str, Enum):
    CONFIRM = "confirm_continue"
    INSPECT_ONLY = "inspect_only"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


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


class PendingRiskConfirmationStore:
    """In-memory pending confirmation store keyed by conversation_id."""

    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingRiskConfirmation] = {}

    def create(
        self,
        *,
        conversation_id: str,
        original_message: str,
        classification: dict[str, Any],
        request_id: str = "",
    ) -> PendingRiskConfirmation:
        now = time.time()
        action = classification.get("action")
        inspect_action = self._inspect_action(classification)
        allowed = [a for a in (action, inspect_action, "cancel") if a]
        pending = PendingRiskConfirmation(
            confirmation_id=f"risk_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            request_id=request_id or f"risk_{uuid.uuid4().hex[:8]}",
            original_message=original_message,
            classification=dict(classification),
            allowed_actions=allowed,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._pending[conversation_id] = pending
        return pending

    def get(self, conversation_id: str) -> PendingRiskConfirmation | None:
        pending = self._pending.get(conversation_id)
        if pending and pending.is_expired():
            self._pending.pop(conversation_id, None)
            return None
        return pending

    def consume(
        self,
        conversation_id: str,
        answer: str,
    ) -> tuple[ConfirmationDecision, PendingRiskConfirmation | None]:
        pending = self.get(conversation_id)
        if pending is None:
            return ConfirmationDecision.UNKNOWN, None
        decision = normalize_confirmation_answer(answer)
        if decision != ConfirmationDecision.UNKNOWN:
            self._pending.pop(conversation_id, None)
        return decision, pending

    def clear(self, conversation_id: str) -> None:
        self._pending.pop(conversation_id, None)

    @staticmethod
    def _inspect_action(classification: dict[str, Any]) -> str | None:
        target = classification.get("target_kind")
        if target == "security_user_allowlist":
            return "list_security_allowlist"
        if target == "skill_external_allowlist":
            return "list_skill_external_allowlist"
        return None


_CONFIRM_WORDS = {
    "confirm_continue", "确认继续", "继续", "确认", "继续吧", "继续执行",
    "好", "好的", "好滴", "好啊", "嗯", "嗯嗯",
    "是", "是的", "对", "对的",
    "行", "可以", "中", "ok", "okay", "yes", "y", "go", "gogogo",
    "同意", "批准", "通过", "执行", "开始", "开始吧", "做",
}
_INSPECT_WORDS = {
    "inspect_only", "只查看", "仅查看", "查看", "看看",
    "read_only", "inspect", "只读",
}
_CANCEL_WORDS = {
    "cancel", "取消", "停止", "停", "否", "不", "不要", "不用",
    "no", "n", "nope", "abort", "skip", "跳过", "算了",
}


def normalize_confirmation_answer(answer: str) -> ConfirmationDecision:
    normalized = (answer or "").strip().lower()
    if normalized in _CONFIRM_WORDS:
        return ConfirmationDecision.CONFIRM
    if normalized in _INSPECT_WORDS:
        return ConfirmationDecision.INSPECT_ONLY
    if normalized in _CANCEL_WORDS:
        return ConfirmationDecision.CANCEL
    return ConfirmationDecision.UNKNOWN


_store: PendingRiskConfirmationStore | None = None


def get_confirmation_store() -> PendingRiskConfirmationStore:
    global _store
    if _store is None:
        _store = PendingRiskConfirmationStore()
    return _store
