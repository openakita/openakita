"""Backend-owned RiskGate authorization data structures.

RiskGate authorization is not inferred from natural-language user messages.
Executable authorization is created only from structured policy/tool metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnRiskAuthorization:
    """RiskGate authorization bound to one server-side execution turn."""

    original_message: str
    confirmation_id: str
    authorized_intent: dict[str, Any] | None = None

    def matches(self, message: str) -> bool:
        return (self.original_message or "").strip() == (message or "").strip()

    def operation_for_policy(self) -> str:
        """Return the coarse operation used by PolicyV2 replay matching."""
        intent = self.authorized_intent if isinstance(self.authorized_intent, dict) else {}
        operation = str(intent.get("operation") or "").strip()
        if operation.endswith("_delete"):
            return "delete"
        if operation.endswith("_write"):
            return "write"
        if operation.endswith("_execute"):
            return "execute"
        return operation

    def tool_names_for_policy(self) -> tuple[str, ...]:
        """Return explicitly declared tools this authorization may relax."""
        intent = self.authorized_intent if isinstance(self.authorized_intent, dict) else {}
        raw = intent.get("tool_names") or intent.get("allowed_tools") or ()
        if isinstance(raw, str):
            return (raw,) if raw else ()
        if isinstance(raw, list | tuple):
            return tuple(str(name) for name in raw if str(name))
        return ()
