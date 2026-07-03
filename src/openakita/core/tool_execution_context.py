"""Per-tool execution context carried explicitly through ToolExecutor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .risk_scope import authorization_covers_tool_call


@dataclass(slots=True)
class _RiskAuthorizationState:
    authorization: Any | None = None
    consumed: bool = False


@dataclass(slots=True)
class ToolExecutionContext:
    """Backend-owned RiskGate authorization carried through ToolExecutor."""

    risk_authorization: Any | None = None
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    tool_policy: Any | None = None
    _risk_state: _RiskAuthorizationState = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._risk_state = _RiskAuthorizationState(self.risk_authorization)

    @property
    def risk_authorization_consumed(self) -> bool:
        return self._risk_state.consumed

    def for_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None,
        tool_policy: Any | None,
    ) -> ToolExecutionContext:
        """Return a tool-bound context sharing the parent authorization state."""
        child = ToolExecutionContext(
            risk_authorization=self._risk_state.authorization,
            tool_name=tool_name,
            tool_input=dict(tool_input or {}),
            tool_policy=tool_policy,
        )
        child._risk_state = self._risk_state
        return child

    def authorize_tool_commit(self, *, consume: bool = False) -> bool:
        """Return whether this context authorizes its bound tool commit."""
        if self._risk_state.consumed:
            return False
        if self._risk_state.authorization is None or self.tool_policy is None or not self.tool_name:
            return False
        allowed = authorization_covers_tool_call(
            self._risk_state.authorization,
            tool_name=self.tool_name,
            tool_input=self.tool_input or {},
            policy=self.tool_policy,
        )
        if allowed and consume:
            self._risk_state.consumed = True
        return allowed
