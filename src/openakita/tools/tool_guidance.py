"""Tool guidance metadata for planner/prompt construction.

This module is intentionally outside ``policy_v2``. Policy objects describe
authorization behavior; guidance objects describe how the planner should use
tools after a policy decision has already granted a narrow capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ToolGuidance:
    """Prompt-facing guidance declared by a tool handler."""

    riskgate_operation: str = ""
    """Structured RiskGate operation this guidance applies to."""

    riskgate_execution_hint: str = ""
    """Instruction shown to the model for a confirmed RiskGate operation."""


def coerce_tool_guidance(raw: Any) -> dict[str, ToolGuidance]:
    """Normalize handler ``TOOL_GUIDANCE`` declarations."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, ToolGuidance] = {}
    for name, guidance in raw.items():
        tool_name = str(name or "").strip()
        if not tool_name:
            continue
        if isinstance(guidance, ToolGuidance):
            out[tool_name] = guidance
            continue
        if not isinstance(guidance, dict):
            continue
        out[tool_name] = ToolGuidance(
            riskgate_operation=str(guidance.get("riskgate_operation") or ""),
            riskgate_execution_hint=str(guidance.get("riskgate_execution_hint") or ""),
        )
    return out
