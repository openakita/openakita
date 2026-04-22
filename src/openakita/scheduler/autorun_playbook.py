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
from openakita.api.routes.websocket import broadcast_event
from openakita.utils.checkbox_md import count_checkboxes
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

    def _refresh_doc_snapshot(self, doc: PlaybookDocumentSpec, path: Path,
                              counts=None) -> None:
        counts = counts or count_checkboxes(path.read_text())
        prev_stalled = self._doc_snapshots.get(doc.filename, {}).get("stalled", False)
        self._doc_snapshots[doc.filename] = {
            "filename": doc.filename,
            "total": counts.checked + counts.unchecked,
            "checked": counts.checked,
            "stalled": prev_stalled,
        }

    def _refresh_all_doc_snapshots(self, loop_iter: int) -> None:
        for doc in self.cfg.documents:
            effective = Path(self._effective_path(doc, loop_iter))
            try:
                self._refresh_doc_snapshot(doc, effective)
            except OSError:
                self._doc_snapshots[doc.filename] = {
                    "filename": doc.filename, "total": 0, "checked": 0, "stalled": False,
                }

    def _docs_snapshot(self) -> list[dict]:
        """Full per-doc checkbox state included on every broadcast so the frontend
        reconstructs the run from the last payload alone (Maestro full-state-push
        pattern). Counts come from `_doc_snapshots`; broadcast does not re-scan."""
        return [dict(self._doc_snapshots.get(d.filename, {
            "filename": d.filename, "total": 0, "checked": 0, "stalled": False,
        })) for d in self.cfg.documents]

    async def _run_doc_pass(self, agent, loop_iter: int) -> bool:
        """Iterate over every doc; per doc, keep driving the agent until either
        all boxes are checked or MAX_CONSECUTIVE_NO_CHANGES consecutive turns
        failed to flip a box. Returns True iff any box was checked during the
        pass (used by execute() as the outer-loop "made progress" signal)."""
        any_progress = False
        for doc in self.cfg.documents:
            path = Path(self._effective_path(doc, loop_iter))
            no_change_streak = 0
            content = path.read_text()
            counts = count_checkboxes(content)
            self._refresh_doc_snapshot(doc, path, counts)
            while counts.unchecked > 0 and no_change_streak < MAX_CONSECUTIVE_NO_CHANGES:
                if self._stopping:
                    return any_progress
                before = counts.checked
                await agent.execute_task_from_message(self.cfg.prompt)
                content = path.read_text()
                counts = count_checkboxes(content)
                self._refresh_doc_snapshot(doc, path, counts)
                after = counts.checked
                if after > before:
                    no_change_streak = 0
                    any_progress = True
                    await self._broadcast(active_doc=doc.filename, delta=after - before)
                    continue
                no_change_streak += 1
                stalled = no_change_streak >= MAX_CONSECUTIVE_NO_CHANGES
                if stalled:
                    self._doc_snapshots[doc.filename]["stalled"] = True
                await self._broadcast(active_doc=doc.filename, stalled=stalled)
        return any_progress

    async def _broadcast(self, **extra) -> None:
        """One full-state push per reducer step (Maestro parity). Extra kwargs
        land on the event payload verbatim so callers can attach loop_iter,
        stalled, etc. without widening this method's surface."""
        await broadcast_event("autorun:state", {
            "run_id": self.run_id,
            "task_id": self.task.task_id,
            "state": self.state.value,
            "docs": self._docs_snapshot(),
            "active_doc": extra.pop("active_doc", None),
            "delta": extra.pop("delta", None),
            "error": extra.pop("error", None),
            **extra,
        })


__all__ = [
    "MAX_CONSECUTIVE_NO_CHANGES",
    "PlaybookDocumentSpec",
    "PlaybookWorktreeSpec",
    "PlaybookSpec",
    "PlaybookState",
    "PlaybookRun",
]
