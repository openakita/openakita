"""Maestro-faithful playbook runner (scheduler system action 'system:autorun_playbook').

Port of Maestro's useBatchProcessor (src/renderer/hooks/batch/useBatchProcessor.ts).
The agent flips checkboxes itself; this module re-reads the file per turn and
counts the `[x]` delta. State flows through `PlaybookState` whose string values
match Maestro's BatchState reducer byte-for-byte so the frontend payload is
identical.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from openakita.agents.factory import AgentFactory
from openakita.utils.worktree import WorktreeInfo

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


class PlaybookRun:
    """Backend port of Maestro useBatchProcessor. One instance per scheduled run."""

    def __init__(self, task, executor, *, profile_store,
                 agent_factory: AgentFactory | None = None):
        self.task = task
        self.executor = executor
        self.profile_store = profile_store
        self.agent_factory = agent_factory or AgentFactory()
        self.cfg = PlaybookSpec.from_metadata(task.metadata)
        self.profile_id = task.agent_profile_id
        self.run_id = f"run-{uuid4().hex[:8]}"
        self.state = PlaybookState.INITIALIZING
        self.wt_info: WorktreeInfo | None = None
        self._doc_snapshots: dict[str, dict] = {}
        self._loop_iter = 0

    @property
    def _stopping(self) -> bool:
        return self.state == PlaybookState.STOPPING

    def _effective_path(self, doc: PlaybookDocumentSpec, loop_iter: int) -> str:
        """Original path when reset_on_completion is False; otherwise a
        Runs/{task_id}-loop{n}/{name} working copy (Maestro's audit-trail pattern).
        Working copies are never mutated after creation — they're copied once per
        (doc, loop_iter) and then edited in place by the agent."""
        src = Path(doc.filename)
        if not doc.reset_on_completion:
            return str(src)
        runs_dir = src.parent / _RUNS_DIRNAME / f"{self.task.task_id}-loop{loop_iter}"
        runs_dir.mkdir(parents=True, exist_ok=True)
        wc = runs_dir / src.name
        if not wc.exists():
            shutil.copy2(src, wc)
        return str(wc)

    def _reset_docs(self) -> list[PlaybookDocumentSpec]:
        return [d for d in self.cfg.documents if d.reset_on_completion]


__all__ = [
    "MAX_CONSECUTIVE_NO_CHANGES",
    "PlaybookDocumentSpec",
    "PlaybookWorktreeSpec",
    "PlaybookSpec",
    "PlaybookState",
    "PlaybookRun",
]
