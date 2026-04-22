"""Maestro-faithful playbook runner (scheduler system action 'system:autorun_playbook').

Port of Maestro's useBatchProcessor (src/renderer/hooks/batch/useBatchProcessor.ts).
The agent flips checkboxes itself; this module re-reads the file per turn and
counts the `[x]` delta. State flows through `PlaybookState` whose string values
match Maestro's BatchState reducer byte-for-byte so the frontend payload is
identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MAX_CONSECUTIVE_NO_CHANGES = 2
_RUNS_DIRNAME = "Runs"


@dataclass(frozen=True)
class PlaybookDocumentSpec:
    filename: str
    reset_on_completion: bool = False


@dataclass(frozen=True)
class PlaybookWorktreeSpec:
    enabled: bool = False
    branch_name_template: str | None = None
    create_pr_on_completion: bool = False
    pr_target_branch: str = "main"
    keep_on_failure: bool = False
    project_root: str | None = None


@dataclass(frozen=True)
class PlaybookSpec:
    documents: tuple[PlaybookDocumentSpec, ...]
    prompt: str
    loop_enabled: bool = False
    max_loops: int | None = None
    worktree: PlaybookWorktreeSpec = field(default_factory=PlaybookWorktreeSpec)

    @classmethod
    def from_metadata(cls, metadata: dict) -> PlaybookSpec:
        raw = metadata["playbook"]
        return cls(
            documents=tuple(PlaybookDocumentSpec(**d) for d in raw["documents"]),
            prompt=raw["prompt"],
            loop_enabled=bool(raw.get("loop_enabled", False)),
            max_loops=raw.get("max_loops"),
            worktree=PlaybookWorktreeSpec(**(raw.get("worktree") or {})),
        )


class PlaybookState(StrEnum):
    """String values match Maestro BatchState reducer byte-for-byte."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETING = "completing"


__all__ = [
    "MAX_CONSECUTIVE_NO_CHANGES",
    "PlaybookDocumentSpec",
    "PlaybookWorktreeSpec",
    "PlaybookSpec",
    "PlaybookState",
]
