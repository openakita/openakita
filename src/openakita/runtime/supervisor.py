"""Dual-ledger supervisor implementation.

Implements ADR-0004 end to end. The supervisor is the only component
that decides when work is done, when to replan, and when to give up.
It does not decide based on the wall clock; it decides based on
LLM-evaluated progress signals (:class:`ProgressLedger`) plus a hard
turn cap (delegated to :class:`StallDetector`).

The supervisor is intentionally split from the LLM integration: it
talks to a :class:`SupervisorBrain` protocol whose three async methods
the Phase 2 ``agent.brain`` will satisfy. Tests drive a fake brain
under deterministic inputs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .cancel_token import CancellationToken, CancelledByToken
from .checkpoint import (
    BaseCheckpointer,
    Checkpoint,
    CheckpointMetadata,
    CheckpointStatus,
    make_checkpoint_id,
)
from .ledger import (
    ProgressLedger,
    ProgressLedgerParseError,
    TaskLedger,
    parse_progress_ledger_json,
)
from .stall_detector import StallDecision, StallDetector, StallVerdict
from .stream import StreamBus

__all__ = [
    "Supervisor",
    "SupervisorBrain",
    "DelegationResult",
    "SupervisorOutcome",
    "FinalOutcome",
    "SupervisorTimeout",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome enumeration
# ---------------------------------------------------------------------------


class FinalOutcome(StrEnum):
    DONE = "done"
    OUT_OF_TURNS = "out_of_turns"
    REPLAN_BUDGET_EXHAUSTED = "replan_budget_exhausted"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class SupervisorOutcome:
    """Result of a single :meth:`Supervisor.run` invocation."""

    outcome: FinalOutcome
    final_message: str
    final_checkpoint_id: str | None
    n_turns: int
    n_replans: int
    reason: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "final_message": self.final_message,
            "final_checkpoint_id": self.final_checkpoint_id,
            "n_turns": self.n_turns,
            "n_replans": self.n_replans,
            "reason": self.reason,
        }


class SupervisorTimeout(Exception):
    """Coarse last-resort guardrail; only raised by an external watchdog
    when a supervisor itself hangs (e.g. infinite tool loop inside a
    node). Documented in ADR-0004 as `org_command_max_seconds`."""


# ---------------------------------------------------------------------------
# Delegation protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelegationResult:
    """The outcome of a single delegation to ``next_speaker``.

    Returned by the caller-supplied ``deliver`` callable. The supervisor
    only cares whether the delegation produced an acceptable
    deliverable; quality enforcement (guardrails) is the caller's
    responsibility, mirrored back through this record.
    """

    success: bool
    speaker: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


DeliverCallable = Callable[
    [str, str, ProgressLedger], Awaitable[DelegationResult]
]
"""``deliver(next_speaker, instruction, progress) -> DelegationResult``."""


# ---------------------------------------------------------------------------
# Brain protocol (LLM frontend)
# ---------------------------------------------------------------------------


class SupervisorBrain(Protocol):
    """The LLM-facing surface the supervisor needs.

    Three async methods. Implementations route to whichever provider /
    model the runtime configures; the supervisor never knows.
    """

    async def extract_facts(self, *, task: str) -> str: ...
    async def draft_plan(self, *, task: str, facts: str) -> str: ...
    async def emit_progress_ledger(
        self, *, task: str, facts: str, plan: str, history: list[ProgressLedger]
    ) -> str:  # raw JSON
        ...


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


@dataclass
class _SupervisorConfig:
    max_stalls: int = 3
    max_turns: int = 30
    max_replans: int = 5
    progress_ledger_max_retries: int = 10


class Supervisor:
    """Outer/inner loop orchestration with checkpointing.

    Args:
        command_id: identifier for the user command being served.
        org_id: organization the command belongs to.
        root_node_id: the initial speaker; usually the producer node.
        task: the user's verbatim instruction.
        brain: the LLM frontend. See :class:`SupervisorBrain`.
        deliver: callable that delegates ``next_speaker.instruction``
            to a node and returns a :class:`DelegationResult`.
        stream: live event bus.
        checkpointer: durable state store; one checkpoint per turn.
        cancel_token: cooperative cancel; checked at every safe point.
        max_stalls / max_turns: defaults from ADR-0004.
        max_replans: how many outer-loop replans we allow before
            giving up. Default 5.
    """

    def __init__(
        self,
        *,
        command_id: str,
        org_id: str,
        root_node_id: str,
        task: str,
        brain: SupervisorBrain,
        deliver: DeliverCallable,
        stream: StreamBus,
        checkpointer: BaseCheckpointer,
        cancel_token: CancellationToken | None = None,
        max_stalls: int = 3,
        max_turns: int = 30,
        max_replans: int = 5,
        progress_ledger_max_retries: int = 10,
    ) -> None:
        self.command_id = command_id
        self.org_id = org_id
        self.task_ledger = TaskLedger(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
        )
        self.brain = brain
        self.deliver = deliver
        self.stream = stream
        self.checkpointer = checkpointer
        self.cancel_token = cancel_token or CancellationToken()
        self.cfg = _SupervisorConfig(
            max_stalls=max_stalls,
            max_turns=max_turns,
            max_replans=max_replans,
            progress_ledger_max_retries=progress_ledger_max_retries,
        )
        self.stall_detector = StallDetector(
            max_stalls=max_stalls, max_turns=max_turns
        )
        self.history: list[ProgressLedger] = []
        self.n_replans: int = 0
        self.last_checkpoint_id: str | None = None
        # Sprint-9: set to True by :meth:`resume_from_checkpoint` so
        # :meth:`run` skips the outer-loop setup and dives straight
        # into the inner loop with restored history.
        self._resumed: bool = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> SupervisorOutcome:
        """Drive the dual-ledger loop until a terminal outcome.

        Two execution modes:

        * Fresh run (default): perform :meth:`_outer_loop_setup` to
          extract facts + draft a plan via the brain, then enter the
          inner loop.
        * Resumed run: when :meth:`resume_from_checkpoint` has already
          restored ``task_ledger.facts`` + ``task_ledger.plan`` from
          a checkpoint, skip the outer-loop setup and re-enter the
          inner loop. The brain's ``emit_progress_ledger`` receives
          the full restored ``history`` on the first turn so the
          decision-making continues exactly where it left off.
        """
        if self._resumed:
            await self._emit_lifecycle(
                "resumed",
                {
                    "task": self.task_ledger.task,
                    "n_turns": self.stall_detector.n_turns,
                    "n_replans": self.n_replans,
                    "resumed_from": self.last_checkpoint_id,
                },
            )
        else:
            await self._emit_lifecycle("started", {"task": self.task_ledger.task})
        try:
            if not self._resumed:
                await self._outer_loop_setup()
            return await self._inner_loop()
        except CancelledByToken as exc:
            return await self._terminate(
                FinalOutcome.CANCELLED, exc.reason or "cancelled"
            )

    # ------------------------------------------------------------------
    # Resume from checkpoint (Sprint-9 HTTP-takeover continue_previous)
    # ------------------------------------------------------------------

    async def resume_from_checkpoint(self, checkpoint_id: str) -> Supervisor:
        """Restore TaskLedger / history / stall counter from a stored checkpoint.

        Loads ``self.checkpointer.aget(checkpoint_id)`` and, when the
        checkpoint belongs to the same ``command_id`` (it must -- a
        checkpoint stamped against a different command is a caller
        bug, not a runtime recoverable condition), rehydrates:

        * ``task_ledger.facts`` / ``task_ledger.plan`` /
          ``task_ledger.revision``
        * ``history`` of :class:`ProgressLedger` snapshots
        * ``stall_detector`` counters (n_turns + n_stalls)
        * ``n_replans``

        Returns ``self`` so callers can chain ``await sup.resume_from_checkpoint(cid)``
        with the subsequent ``await sup.run()``. Raises ``LookupError``
        when the checkpoint does not exist, and ``ValueError`` when
        it belongs to a different command.

        Sprint-9 audit §9 item 5: when a caller asks for resume but
        the checkpoint id is unknown, the higher-level dispatcher
        (``OrgCommandService.submit`` ``continue_previous=true`` path)
        is responsible for falling back to a fresh run with the
        legacy ``_build_continue_content`` text concatenation. The
        method here is intentionally strict so the upstream caller
        sees the exact failure mode and decides the policy.
        """
        ck = await self.checkpointer.aget(checkpoint_id)
        if ck is None:
            raise LookupError(f"checkpoint {checkpoint_id!r} not found")
        if ck.metadata.command_id != self.command_id:
            raise ValueError(
                f"checkpoint {checkpoint_id!r} belongs to command "
                f"{ck.metadata.command_id!r}, not {self.command_id!r}"
            )
        state = ck.state or {}
        ledger_blob = state.get("task_ledger") or {}
        if isinstance(ledger_blob, dict):
            self.task_ledger.facts = str(ledger_blob.get("facts") or self.task_ledger.facts)
            self.task_ledger.plan = str(ledger_blob.get("plan") or self.task_ledger.plan)
            rev = ledger_blob.get("revision")
            if isinstance(rev, int):
                self.task_ledger.revision = rev
        history_blob = state.get("history") or []
        restored: list[ProgressLedger] = []
        for entry in history_blob:
            if not isinstance(entry, dict):
                continue
            try:
                # Round-trip through parse_progress_ledger_json so the
                # restored history goes through the same validation
                # path live progress ledgers do; if a stored entry is
                # malformed (would only happen if someone hand-edited
                # the sqlite file) we drop it rather than crashing.
                import json as _json

                restored.append(
                    parse_progress_ledger_json(
                        _json.dumps(entry, ensure_ascii=False),
                        turn_id=int(entry.get("turn_id") or len(restored) + 1),
                    )
                )
            except ProgressLedgerParseError:
                continue
        self.history = restored
        sd_blob = state.get("stall_detector") or {}
        if isinstance(sd_blob, dict):
            try:
                self.stall_detector.n_turns = int(sd_blob.get("n_turns") or 0)
                self.stall_detector.n_stalls = int(sd_blob.get("n_stalls") or 0)
            except (TypeError, ValueError):
                pass
        replans = state.get("n_replans")
        if isinstance(replans, int):
            self.n_replans = replans
        self.last_checkpoint_id = checkpoint_id
        self._resumed = True
        return self

    # ------------------------------------------------------------------
    # Outer loop — facts + plan
    # ------------------------------------------------------------------

    async def _outer_loop_setup(self) -> None:
        self.cancel_token.raise_if_cancelled()
        facts = await self.brain.extract_facts(task=self.task_ledger.task)
        self.cancel_token.raise_if_cancelled()
        plan = await self.brain.draft_plan(task=self.task_ledger.task, facts=facts)
        self.task_ledger.facts = facts
        self.task_ledger.plan = plan
        self.task_ledger.updated_at = datetime.now(UTC)
        await self._emit_lifecycle(
            "task_ledger_published",
            {"facts": facts, "plan": plan, "revision": self.task_ledger.revision},
        )

    async def _outer_loop_replan(self, reason: str) -> bool:
        """Re-extract facts and re-draft plan. Returns True on success.

        Returns False (and emits a lifecycle event) when we have hit
        ``max_replans``; the caller then closes out with
        REPLAN_BUDGET_EXHAUSTED.
        """
        if self.n_replans >= self.cfg.max_replans:
            return False
        self.n_replans += 1
        await self._emit_lifecycle("replanning", {"reason": reason, "n_replans": self.n_replans})
        self.cancel_token.raise_if_cancelled()
        new_facts = await self.brain.extract_facts(task=self.task_ledger.task)
        self.cancel_token.raise_if_cancelled()
        new_plan = await self.brain.draft_plan(
            task=self.task_ledger.task, facts=new_facts
        )
        self.task_ledger.revise(new_facts=new_facts, new_plan=new_plan)
        self.stall_detector.reset_after_replan()
        await self._emit_lifecycle(
            "task_ledger_published",
            {
                "facts": new_facts,
                "plan": new_plan,
                "revision": self.task_ledger.revision,
            },
        )
        return True

    # ------------------------------------------------------------------
    # Inner loop — progress ledger + delegation
    # ------------------------------------------------------------------

    async def _inner_loop(self) -> SupervisorOutcome:
        while True:
            self.cancel_token.raise_if_cancelled()

            progress = await self._emit_progress_ledger()
            self.history.append(progress)
            await self.stream.emit(
                "progress_ledger",
                "ledger",
                progress.to_jsonable(),
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
            )

            decision = self.stall_detector.evaluate(progress)
            await self._checkpoint(decision)

            match decision.verdict:
                case StallVerdict.DONE:
                    return await self._terminate(
                        FinalOutcome.DONE,
                        progress.is_request_satisfied.reason,
                    )
                case StallVerdict.OUT_OF_TURNS:
                    return await self._terminate(
                        FinalOutcome.OUT_OF_TURNS, decision.reason
                    )
                case StallVerdict.REPLAN:
                    replanned = await self._outer_loop_replan(decision.reason)
                    if not replanned:
                        return await self._terminate(
                            FinalOutcome.REPLAN_BUDGET_EXHAUSTED, decision.reason
                        )
                    continue
                case StallVerdict.SUSPECT:
                    await self.stream.emit(
                        "lifecycle",
                        "stall_warning",
                        {
                            "n_stalls": decision.n_stalls,
                            "max_stalls": decision.max_stalls,
                            "reason": decision.reason,
                        },
                        command_id=self.command_id,
                        org_id=self.org_id,
                        superstep=self.stall_detector.n_turns,
                    )
                case StallVerdict.PROCEED:
                    pass

            # Delegate to next_speaker.
            self.cancel_token.raise_if_cancelled()
            await self.stream.emit(
                "tasks",
                "delegating",
                {
                    "speaker": progress.next_speaker_name,
                    "instruction": progress.instruction,
                    "turn": self.stall_detector.n_turns,
                },
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
            )
            try:
                result = await self.deliver(
                    progress.next_speaker_name, progress.instruction, progress
                )
            except CancelledByToken:
                raise
            await self.stream.emit(
                "updates",
                "delegation_result",
                {
                    "speaker": result.speaker,
                    "success": result.success,
                    "message": result.message,
                },
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
            )

    # ------------------------------------------------------------------
    # Progress ledger acquisition with retry
    # ------------------------------------------------------------------

    async def _emit_progress_ledger(self) -> ProgressLedger:
        """Ask the brain for the next ProgressLedger, retrying on bad JSON."""
        last_error: ProgressLedgerParseError | None = None
        for attempt in range(self.cfg.progress_ledger_max_retries):
            self.cancel_token.raise_if_cancelled()
            raw = await self.brain.emit_progress_ledger(
                task=self.task_ledger.task,
                facts=self.task_ledger.facts,
                plan=self.task_ledger.plan,
                history=list(self.history),
            )
            try:
                return parse_progress_ledger_json(
                    raw, turn_id=self.stall_detector.n_turns + 1
                )
            except ProgressLedgerParseError as exc:
                last_error = exc
                logger.debug(
                    "Supervisor: bad progress ledger JSON on attempt %d: %s",
                    attempt + 1,
                    exc,
                )
                await self.stream.emit(
                    "debug",
                    "progress_ledger_parse_error",
                    {"attempt": attempt + 1, "error": str(exc), "raw": raw[:512]},
                    command_id=self.command_id,
                    org_id=self.org_id,
                    superstep=self.stall_detector.n_turns,
                )
        # Out of retries — promote to a hard supervisor failure.
        raise ProgressLedgerParseError(
            f"progress ledger could not be parsed after "
            f"{self.cfg.progress_ledger_max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Checkpoint + lifecycle helpers
    # ------------------------------------------------------------------

    async def _checkpoint(self, decision: StallDecision) -> CheckpointMetadata:
        """Persist a checkpoint after each inner-loop decision."""
        cp_id = make_checkpoint_id()
        status = (
            CheckpointStatus.RUNNING
            if decision.verdict in (StallVerdict.PROCEED, StallVerdict.SUSPECT)
            else CheckpointStatus(self._verdict_to_checkpoint_status(decision.verdict))
        )
        ck = Checkpoint(
            metadata=CheckpointMetadata(
                checkpoint_id=cp_id,
                parent_id=self.last_checkpoint_id,
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
                status=status,
                n_stalls=self.stall_detector.n_stalls,
                n_turns=self.stall_detector.n_turns,
                created_at=datetime.now(UTC),
            ),
            state={
                "task_ledger": self.task_ledger.to_jsonable(),
                "history": [p.to_jsonable() for p in self.history],
                "stall_detector": self.stall_detector.to_jsonable(),
                "n_replans": self.n_replans,
            },
        )
        meta = await self.checkpointer.aput(ck)
        self.last_checkpoint_id = meta.checkpoint_id
        await self.stream.emit(
            "checkpoints",
            "checkpoint_written",
            meta.to_jsonable(),
            command_id=self.command_id,
            org_id=self.org_id,
            superstep=self.stall_detector.n_turns,
        )
        return meta

    @staticmethod
    def _verdict_to_checkpoint_status(verdict: StallVerdict) -> str:
        return {
            StallVerdict.DONE: CheckpointStatus.DONE.value,
            StallVerdict.OUT_OF_TURNS: CheckpointStatus.OUT_OF_STEPS.value,
            StallVerdict.REPLAN: CheckpointStatus.RUNNING.value,
            StallVerdict.PROCEED: CheckpointStatus.RUNNING.value,
            StallVerdict.SUSPECT: CheckpointStatus.RUNNING.value,
        }[verdict]

    async def _emit_lifecycle(self, type_: str, payload: dict[str, Any]) -> None:
        await self.stream.emit(
            "lifecycle",
            type_,
            payload,
            command_id=self.command_id,
            org_id=self.org_id,
            superstep=self.stall_detector.n_turns,
        )

    async def _terminate(
        self, outcome: FinalOutcome, reason: str
    ) -> SupervisorOutcome:
        """Emit final lifecycle event and return the outcome record.

        Always writes a final cancelled / done checkpoint so resume from
        a terminated command lands somewhere consistent.
        """
        terminal_status = {
            FinalOutcome.DONE: CheckpointStatus.DONE,
            FinalOutcome.OUT_OF_TURNS: CheckpointStatus.OUT_OF_STEPS,
            FinalOutcome.REPLAN_BUDGET_EXHAUSTED: CheckpointStatus.FAILED,
            FinalOutcome.CANCELLED: CheckpointStatus.CANCELLED,
            FinalOutcome.FAILED: CheckpointStatus.FAILED,
        }[outcome]
        cp_id = make_checkpoint_id()
        ck = Checkpoint(
            metadata=CheckpointMetadata(
                checkpoint_id=cp_id,
                parent_id=self.last_checkpoint_id,
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
                status=terminal_status,
                n_stalls=self.stall_detector.n_stalls,
                n_turns=self.stall_detector.n_turns,
                created_at=datetime.now(UTC),
            ),
            state={
                "task_ledger": self.task_ledger.to_jsonable(),
                "history": [p.to_jsonable() for p in self.history],
                "stall_detector": self.stall_detector.to_jsonable(),
                "n_replans": self.n_replans,
                "final_reason": reason,
            },
        )
        await self.checkpointer.aput(ck)
        self.last_checkpoint_id = cp_id
        await self._emit_lifecycle(
            outcome.value, {"reason": reason, "n_turns": self.stall_detector.n_turns}
        )
        return SupervisorOutcome(
            outcome=outcome,
            final_message=reason,
            final_checkpoint_id=cp_id,
            n_turns=self.stall_detector.n_turns,
            n_replans=self.n_replans,
            reason=reason,
        )
