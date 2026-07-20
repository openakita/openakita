"""Structured execution context shared by the supervisor and node runtime."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExecutionPhase(StrEnum):
    """Machine-owned phase for one supervisor-to-node activation."""

    PLANNING = "planning"
    EXECUTION = "execution"
    FINALIZATION = "finalization"


class ArtifactRole(StrEnum):
    """Machine-owned semantic role of an output artifact."""

    KICKOFF = "kickoff"
    INTERMEDIATE = "intermediate"
    FINAL = "final"


@dataclass(frozen=True)
class UpstreamContext:
    """Structured evidence supplied by completed dependency steps."""

    dependencies: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_present(self) -> bool:
        return bool(self.dependencies)

    def to_dict(self) -> dict[str, Any]:
        return {"dependencies": [dict(item) for item in self.dependencies]}

    @classmethod
    def from_value(cls, value: UpstreamContext | Mapping[str, Any] | None) -> UpstreamContext:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return cls()
        raw = value.get("dependencies")
        if not isinstance(raw, (list, tuple)):
            return cls()
        return cls(dependencies=tuple(dict(item) for item in raw if isinstance(item, Mapping)))


current_execution_phase_var: ContextVar[ExecutionPhase] = ContextVar(
    "openakita_execution_phase",
    default=ExecutionPhase.EXECUTION,
)

current_upstream_context_var: ContextVar[UpstreamContext | None] = ContextVar(
    "openakita_upstream_context",
    default=None,
)


def artifact_role_for_phase(phase: ExecutionPhase) -> ArtifactRole:
    if phase is ExecutionPhase.PLANNING:
        return ArtifactRole.KICKOFF
    return ArtifactRole.INTERMEDIATE
