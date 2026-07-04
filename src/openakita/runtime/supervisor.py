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

import asyncio
import logging
import time
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
    # RC-conv: the best-effort concrete deliverable assembled from the real
    # node outputs (``delegation_history``). On DONE this is the produced
    # content; on the graceful-degradation terminals (OUT_OF_TURNS /
    # REPLAN_BUDGET_EXHAUSTED) it is the best partial result so the command
    # surfaces something useful instead of a bare "ran out of budget" reason.
    deliverable: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "final_message": self.final_message,
            "final_checkpoint_id": self.final_checkpoint_id,
            "n_turns": self.n_turns,
            "n_replans": self.n_replans,
            "reason": self.reason,
            "deliverable": self.deliverable,
        }


class SupervisorTimeout(Exception):
    """Coarse last-resort guardrail; only raised by an external watchdog
    when a supervisor itself hangs (e.g. infinite tool loop inside a
    node). Documented in ADR-0004 as `org_command_max_seconds`."""


# Machine-emitted markers that only appear in a root KICKOFF / 派单 aggregation
# (the root splitting work + the executor concatenating the raw child replies),
# never in a genuine integrated final report. Used to keep the kickoff out of the
# best-effort deliverable when any real output exists (test13 RCA).
_KICKOFF_MARKERS = (
    "[dispatched to ",
    "[dispatch to `",
    "[from node `",
    "项目启动指令",
    "项目正式启动",
)


def _looks_like_kickoff(text: str) -> bool:
    """Heuristically detect a root kickoff / 派单 dump (not a final deliverable).

    Deterministic and conservative: a message is treated as a kickoff only when
    it carries the executor's machine dispatch scaffolding (``[dispatched to …]``
    / ``[from node …]``) or an unmistakable kickoff heading. A real integrated
    report that merely mentions a node name in prose does not match.
    """
    if not text:
        return False
    hits = sum(1 for m in _KICKOFF_MARKERS if m in text)
    return hits >= 1


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

    v22 RCA RC-4: each method accepts an optional ``cancel_event``
    that the supervisor bridges from its
    :class:`~openakita.runtime.cancel_token.CancellationToken`.
    Implementations forward the event to the underlying LLM client
    (e.g. ``Brain.messages_create_async(cancel_event=...)``) so an
    in-flight ``httpx`` request can be aborted the instant
    ``cancel_token.cancel()`` fires -- without the historical 5s
    drain timeout. Stub / pass-through brains may ignore the
    argument; the default ``None`` keeps the protocol backward
    compatible with existing implementations.

    RC-5 S1 (gap⑤): ``emit_progress_ledger`` additionally accepts an
    optional ``recent_outputs`` -- the most recent
    :class:`DelegationResult` records the supervisor collected from the
    ``deliver`` callable. Only the LLM orchestration brain consumes them
    (to render the *actual* node deliverables into its convergence
    prompt); scaffold / pass-through brains ignore the argument. As with
    ``cancel_event``, the default ``None`` keeps the protocol backward
    compatible.
    """

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str: ...
    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str: ...
    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        recent_outputs: list[DelegationResult] | None = None,
        cancel_event: asyncio.Event | None = None,
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
        cancel_event: asyncio.Event | None = None,
        max_stalls: int = 3,
        max_turns: int = 30,
        max_replans: int = 5,
        progress_ledger_max_retries: int = 10,
        wall_clock_soft_budget_s: float = 0.0,
        deliver_includes_recent_outputs: bool = True,
        recent_output_window: int = 4,
        recent_output_char_cap: int = 2400,
        force_root_finalization: bool = False,
        root_finalization_min_chars: int = 200,
        root_finalization_char_cap: int = 6000,
        wall_clock_hard_ceiling_s: float = 0.0,
        root_finalization_min_budget_s: float = 150.0,
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
        # v22 RCA RC-4: bridge the (thread-safe) ``CancellationToken``
        # onto an ``asyncio.Event`` so brain implementations can race
        # the event against an in-flight ``httpx`` request and abort
        # the moment ``cancel_token.cancel()`` fires. When the caller
        # (production: :func:`supervisor_factory.build_supervisor_for_command`)
        # already wired the bridge we honour it as-is; otherwise we
        # mint one here and attach the callback so a fresh
        # ``Supervisor()`` is functional in tests too.
        if cancel_event is None:
            cancel_event = asyncio.Event()
            self.cancel_token.add_callback(cancel_event.set)
        self._cancel_event = cancel_event
        # RC-5 S0: clamp the turn budget UP so the graceful replan path is
        # always reachable. StallDetector evaluates DONE -> OUT_OF_TURNS ->
        # REPLAN, so if ``max_turns`` is smaller than what it takes to burn
        # the whole replan budget (each replan needs ``max_stalls`` stalls,
        # for ``max_replans + 1`` segments, plus a facts/plan + finish
        # buffer), a contradictory task hits the hard turn cap before it can
        # terminate gracefully via ``replan_budget_exhausted``. We never
        # raise here -- breaking submit is worse than a slightly larger cap;
        # we clamp UP and warn. See ``_rc5_biz/sprint_plan/
        # _prereq_convergence_params.md`` §4.2 (first layer).
        min_turns = max_stalls * (max_replans + 2)
        if max_turns < min_turns:
            logger.warning(
                "Supervisor(command_id=%s): max_turns=%d < "
                "max_stalls*(max_replans+2)=%d; clamping max_turns up to %d "
                "to keep the replan budget reachable (else the hard turn cap "
                "pre-empts graceful replan termination).",
                command_id,
                max_turns,
                min_turns,
                min_turns,
            )
            max_turns = min_turns
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
        # RC-5 S1 (gap⑤): the real node deliverables, fed back to the brain's
        # progress ledger so it can judge satisfaction/progress from concrete
        # outputs instead of being "blind" to what nodes actually produced.
        # Intentionally NOT persisted in checkpoints (the outputs already live
        # on the stream/artefact path, and the restored ``history`` carries
        # enough context); resume starts this empty by design -- see
        # ``_rc5_biz/sprint_plan/sprint_implementation_plan.md`` S1 risk note.
        self.delegation_history: list[DelegationResult] = []
        self.n_replans: int = 0
        self.last_checkpoint_id: str | None = None
        # Sprint-9: set to True by :meth:`resume_from_checkpoint` so
        # :meth:`run` skips the outer-loop setup and dives straight
        # into the inner loop with restored history.
        self._resumed: bool = False
        # RC-conv (graceful degradation): self-imposed wall-clock budget that
        # fires *before* the external ``supervisor_hard_ceiling_s`` so the
        # supervisor terminates itself gracefully (with a best-effort
        # deliverable) instead of being force-cancelled into a bare
        # ``status=error``. <= 0 disables (tests + opt-out keep old behaviour).
        self._wall_clock_soft_budget_s = float(wall_clock_soft_budget_s or 0.0)
        self._start_monotonic: float | None = None
        # RC-conv (context回灌给节点): whether the delegated ``content`` carries
        # an inline copy of the most-recent peer node outputs. The brain feeds
        # outputs into its own convergence prompt (gap⑤), but a node is
        # activated in a *fresh* conversation -- without this it never sees the
        # "Output N above" the brain's instruction references, so it
        # hallucinates "missing file / paste the data" and the org spins until
        # the wall clock. See the v* convergence RCA.
        self._deliver_includes_recent_outputs = bool(deliver_includes_recent_outputs)
        self._recent_output_window = max(1, int(recent_output_window))
        self._recent_output_char_cap = max(200, int(recent_output_char_cap))
        # Deterministic root finalization (task A): when the loop converges but
        # the root/主编 has not itself produced the final integrated deliverable,
        # force ONE closing delegation to the root so it integrates all upstream
        # outputs into a user-facing report. This guarantees the final
        # deliverable / PDF comes from the root's integration, never from a
        # report node's output or the root's initial kickoff. Off by default so
        # direct-construction unit tests keep their exact turn/deliverable
        # semantics; the production LLM-orchestration path opts in via the
        # factory. See ``supervisor_factory.build_supervisor_for_command``.
        self._force_root_finalization = bool(force_root_finalization)
        self._root_finalization_min_chars = max(1, int(root_finalization_min_chars))
        self._root_finalization_char_cap = max(400, int(root_finalization_char_cap))
        self._root_finalized = False
        # test13 RCA: the forced finalization is a full extra root LLM turn that
        # can take several minutes. When the run has already burned most of the
        # outer hard ceiling (e.g. a leaf hung to its node timeout), starting it
        # anyway guaranteed it would be killed mid-flight by the ceiling and the
        # deliverable would fall back to the kickoff dump. We therefore:
        #  * skip the finalization when the remaining hard-ceiling budget is too
        #    small to plausibly finish it, and
        #  * time-box the finalization deliver to the remaining budget so it can
        #    NEVER trip the outer ``asyncio.wait_for`` hard ceiling (which would
        #    force-kill the whole command with a "hard ceiling exceeded" state).
        self._wall_clock_hard_ceiling_s = float(wall_clock_hard_ceiling_s or 0.0)
        self._root_finalization_min_budget_s = max(0.0, float(root_finalization_min_budget_s))

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
        # RC-5 S5: imported lazily inside ``run`` to avoid an import-time
        # cycle. ``supervisor`` is imported very early (the agent package init
        # pulls in ``runtime.state_graph`` which imports ``DelegationResult``
        # from here); a top-level ``from ..agent.errors import ...`` would
        # re-enter the half-initialised module. By the time ``run`` executes
        # this module is fully loaded, so the import resolves cleanly.
        from ..agent.errors import UserCancelledError

        self._start_monotonic = time.monotonic()
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
        except UserCancelledError as exc:
            # RC-5 S5: ``UserCancelledError`` is a plain ``Exception``
            # subclass (``openakita.agent.errors``), so neither the
            # cooperative ``CancelledByToken`` arm above nor the
            # ``asyncio.CancelledError`` arm below catches it. It bubbles up
            # from the deep LLM path (``LLMClient.chat(cancel_event=...)``
            # raising on a user "stop") through ``emit_progress_ledger`` /
            # ``deliver``. Absorb it into a clean ``cancelled`` terminal
            # checkpoint so the command stays resumable instead of crashing
            # with an uncaught exception.
            return await self._terminate(
                FinalOutcome.CANCELLED, exc.reason or "cancelled"
            )
        except asyncio.CancelledError:
            # v23 RC-4 fix: the d1275851 ``cancel_event`` bridge only
            # reaches ``SupervisorBrain`` (production
            # ``PassThroughSupervisorBrain`` ignores it). The real LLM
            # call lives deeper, inside ``self.deliver ->
            # executor.activate_and_run -> agent.run ->
            # Brain.messages_create_async``, where ``cancel_event`` is
            # never plumbed (audit ``_v23_biz/_rc4_debug_notes.md``).
            # The defensive cancel path therefore fires
            # ``task.cancel()`` from
            # :meth:`OrgCommandService._cooperative_cancel` after
            # ``cancel_token.cancel()``; the resulting
            # ``CancelledError`` unwinds through ``httpx`` into here.
            # When the token has been cancelled we still want to
            # write the final ``cancelled`` checkpoint so the command
            # stays resumable -- mirroring the cooperative
            # :class:`CancelledByToken` branch above. We absorb the
            # single cancellation and run ``_terminate`` normally:
            # only one cancellation is requested in this flow (by
            # ``wait_for``'s ``_cancel_and_wait`` inside
            # :meth:`_run_supervisor_with_hard_ceiling`), so no
            # further ``CancelledError`` will preempt the
            # checkpoint write.
            if self.cancel_token.is_cancelled():
                reason = self.cancel_token.reason or "cancelled"
                return await self._terminate(
                    FinalOutcome.CANCELLED, reason
                )
            raise

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
        facts = await self.brain.extract_facts(
            task=self.task_ledger.task,
            cancel_event=self._cancel_event,
        )
        self.cancel_token.raise_if_cancelled()
        plan = await self.brain.draft_plan(
            task=self.task_ledger.task,
            facts=facts,
            cancel_event=self._cancel_event,
        )
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
        new_facts = await self.brain.extract_facts(
            task=self.task_ledger.task,
            cancel_event=self._cancel_event,
        )
        self.cancel_token.raise_if_cancelled()
        new_plan = await self.brain.draft_plan(
            task=self.task_ledger.task,
            facts=new_facts,
            cancel_event=self._cancel_event,
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

            # RC-conv: graceful self-termination before the external hard
            # ceiling. We check *before* starting another (expensive) turn so
            # the supervisor unwinds with a best-effort deliverable instead of
            # being force-cancelled into ``status=error`` at the wall clock.
            if self._soft_budget_exceeded():
                elapsed = self._elapsed_s()
                # User-facing note prepended to the final report — keep it in
                # friendly Simplified Chinese (release-notes tone) instead of the
                # raw internal English budget string (test18 item #4). The
                # machine-readable outcome stays OUT_OF_TURNS; only this display
                # text changed.
                return await self._terminate(
                    FinalOutcome.OUT_OF_TURNS,
                    (
                        f"本次任务已达到预设的时间预算（约 {elapsed:.0f} 秒、"
                        f"共 {self.stall_detector.n_turns} 轮），已在时限内尽力完成"
                        f"并交付当前阶段的最佳结果。"
                    ),
                )

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
                    await self._maybe_force_root_finalization(progress)
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
                    progress.next_speaker_name,
                    self._compose_delegated_content(progress.instruction),
                    progress,
                )
            except CancelledByToken:
                raise
            # RC-5 S1 (gap⑤): retain the real node deliverable so the next
            # turn's progress ledger can be judged from concrete outputs.
            self.delegation_history.append(result)
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
    # RC-conv: wall-clock soft budget + node context injection helpers
    # ------------------------------------------------------------------

    def _elapsed_s(self) -> float:
        if self._start_monotonic is None:
            return 0.0
        return time.monotonic() - self._start_monotonic

    def _root_finalization_budget_s(self) -> float | None:
        """Remaining wall-clock budget the forced finalization may consume.

        Returns ``None`` when no outer hard ceiling is configured (unbounded —
        keep the pre-test13 behaviour). Otherwise returns the seconds left before
        the outer ``asyncio.wait_for`` hard ceiling would fire, minus a small
        safety margin, floored at 0. The caller skips the finalization when this
        is below ``_root_finalization_min_budget_s`` and otherwise time-boxes the
        deliver to this value so it can never trip the outer ceiling.
        """
        if self._wall_clock_hard_ceiling_s <= 0:
            return None
        margin = 20.0
        remaining = self._wall_clock_hard_ceiling_s - self._elapsed_s() - margin
        return max(0.0, remaining)

    def _soft_budget_exceeded(self) -> bool:
        if self._wall_clock_soft_budget_s <= 0:
            return False
        # Never pre-empt before at least one real delegation has produced
        # something; otherwise a misconfigured tiny budget would return an
        # empty deliverable on turn 1.
        if not self.delegation_history:
            return False
        return self._elapsed_s() >= self._wall_clock_soft_budget_s

    def _compose_delegated_content(self, instruction: str) -> str:
        """Append the most-recent peer node outputs to the delegated content.

        RC-conv (context回灌给节点): the orchestration brain writes instructions
        that reference prior deliverables ("use the report in Output 3
        above"), but each node is activated in a *fresh* conversation and only
        receives this ``content``. Without the actual outputs inlined the node
        is blind to what its peers produced and hallucinates missing context.
        We render the same bounded window of real ``DelegationResult`` records
        the brain sees into the content so the node can build on them.
        """
        if not self._deliver_includes_recent_outputs or not self.delegation_history:
            return instruction
        recent = self.delegation_history[-self._recent_output_window :]
        blocks: list[str] = []
        for i, r in enumerate(recent, start=1):
            if not getattr(r, "success", False):
                continue
            body = str(getattr(r, "message", "") or "").strip()
            if not body:
                continue
            if len(body) > self._recent_output_char_cap:
                body = body[: self._recent_output_char_cap] + "\n…（已截断）"
            blocks.append(f"[Output {i}] 来自节点 {r.speaker!r} 的产出：\n{body}")
        if not blocks:
            return instruction
        joined = "\n\n".join(blocks)
        return (
            f"{instruction}\n\n"
            "=== 上游节点已产出的真实内容（请直接基于这些内容工作，"
            "不要假设缺失、不要模拟搜索文件、不要要求用户重新粘贴） ===\n"
            f"{joined}\n"
            "=== 以上为可直接使用的上游产出 ==="
        )

    def _best_effort_deliverable(self) -> str:
        """Assemble the best concrete deliverable from real node outputs.

        Used to surface something useful on every terminal: the produced
        content on DONE, and the best partial result on the graceful
        degradation terminals. Prefers the most-recent *successful* output
        (the org's pipeline funnels the synthesised result to the last
        speaker); falls back to the longest successful output, then to the
        last output of any kind.
        """
        if not self.delegation_history:
            return ""
        successes = [
            r for r in self.delegation_history
            if getattr(r, "success", False) and str(getattr(r, "message", "") or "").strip()
        ]
        # test13 RCA: the root's turn-1 output is usually a KICKOFF / 派单 dump
        # ("# 项目启动指令 … [dispatched to …] … [from node …]"), not a finished
        # deliverable. When the hard ceiling killed the forced finalization, this
        # kickoff (being the longest success) became the final PDF + chat bubble.
        # Prefer any NON-kickoff success; only fall back to a kickoff when it is
        # the only content we have (so we never return an empty deliverable).
        non_kickoff = [
            r for r in successes if not _looks_like_kickoff(str(r.message or ""))
        ]
        if non_kickoff:
            successes = non_kickoff
        chosen: DelegationResult | None = None
        if successes:
            last = successes[-1]
            longest = max(successes, key=lambda r: len(str(r.message or "")))
            # The last successful output is usually the synthesised final
            # answer; only prefer a much longer earlier output when the last
            # one is conspicuously thin (a one-liner ack).
            chosen = last
            if len(str(last.message or "")) < 200 <= len(str(longest.message or "")):
                chosen = longest
        elif self.delegation_history:
            chosen = self.delegation_history[-1]
        if chosen is None:
            return ""
        return str(getattr(chosen, "message", "") or "").strip()

    def best_effort_deliverable(self) -> str:
        """Public accessor for the hard-ceiling fallback in command_service."""
        return self._best_effort_deliverable()

    # ------------------------------------------------------------------
    # Deterministic root finalization (task A)
    # ------------------------------------------------------------------

    def _root_already_finalized(self) -> bool:
        """True when the root has already produced the final integrated result.

        The most-recent *successful* delegation being the root node with a
        substantial body means the root itself produced the closing deliverable
        (PassThrough single-shot, or an LLM brain that correctly routed the
        integration to the root). In that case forcing another root turn would
        be redundant, so we skip it. Any other shape -- the last speaker is a
        report node, or the root only ever emitted a short kickoff -- means the
        integrated deliverable is NOT owned by the root yet.
        """
        root = self.task_ledger.root_node_id
        if not root:
            return True  # no addressable root -> nothing to force
        successes = [
            r
            for r in self.delegation_history
            if getattr(r, "success", False) and str(getattr(r, "message", "") or "").strip()
        ]
        if not successes:
            return False
        last = successes[-1]
        return (
            last.speaker == root
            and len(str(last.message or "")) >= self._root_finalization_min_chars
        )

    def _compose_root_finalization_instruction(self) -> str:
        """Build the closing "integrate + report" instruction for the root.

        Inlines every successful upstream output (bounded per item) so the root
        integrates the real produced content instead of re-delegating or
        hallucinating missing context. Explicitly forbids further delegation --
        this is the terminal synthesis step.
        """
        blocks: list[str] = []
        idx = 0
        for r in self.delegation_history:
            if not getattr(r, "success", False):
                continue
            body = str(getattr(r, "message", "") or "").strip()
            if not body:
                continue
            idx += 1
            if len(body) > self._root_finalization_char_cap:
                body = body[: self._root_finalization_char_cap] + "\n…（已截断）"
            blocks.append(f"[产出 {idx}] 来自节点 {r.speaker!r}：\n{body}")
        joined = "\n\n".join(blocks) if blocks else "（无上游产出记录）"
        return (
            "【最终整合与交付 · 由主编（根节点）亲自完成】\n"
            "所有下游节点均已完成并交付各自产出（见下方）。现在请你作为主编/根节点，"
            "亲自完成本次任务的最终整合与面向用户的总结汇报：\n"
            "1. 通读并整合下方全部上游产出，形成一份完整、连贯、可直接交付给用户的最终成果；\n"
            "2. 用 write_file 将该最终成果写入一个 .md 文件，并用 deliver_artifacts 交付，"
            "使其在前端可下载/预览；\n"
            "3. 在你本次回复的正文中，直接给出这份完整的最终成果与总结汇报"
            "（包含关键结论、决策摘要与交付物清单），作为交付给用户的最终报告。\n"
            "注意：这是收尾步骤，不要再向下派发任务（不要 dispatch / delegate），"
            "直接基于下方已产出的内容整合成稿。\n\n"
            "=== 上游节点已产出的真实内容（请直接基于这些内容整合，不要假设缺失） ===\n"
            f"{joined}\n"
            "=== 以上为可直接使用的上游产出 ==="
        )

    async def _maybe_force_root_finalization(self, progress: ProgressLedger) -> None:
        """Force one closing root delegation when the root has not integrated.

        Deterministic backstop for the "final delivery owned by the root"
        contract: independent of whether the brain routed the integration to
        the root, this guarantees the root produces the final integrated report
        exactly once before the command terminates DONE. No-op when disabled,
        already run, or the root already owns the final deliverable.
        """
        if not self._force_root_finalization or self._root_finalized:
            return
        if self._root_already_finalized():
            return
        root = self.task_ledger.root_node_id
        if not root:
            return
        # Budget gate (test13 RCA): only start the extra root turn when there is
        # enough remaining hard-ceiling budget for it to plausibly finish. If the
        # run already burned most of the ceiling, forcing a doomed turn wastes the
        # remaining time and gets killed mid-flight -> the deliverable falls back
        # to the kickoff dump. Skipping here lets the loop terminate cleanly with
        # the best real deliverable instead.
        finalize_timeout = self._root_finalization_budget_s()
        if finalize_timeout is not None and finalize_timeout <= self._root_finalization_min_budget_s:
            self._root_finalized = True  # do not retry within this run
            await self.stream.emit(
                "updates",
                "root_finalization_skipped",
                {
                    "speaker": root,
                    "reason": "insufficient_wall_clock_budget",
                    "remaining_budget_s": round(finalize_timeout, 1),
                    "min_budget_s": round(self._root_finalization_min_budget_s, 1),
                },
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
            )
            return
        self._root_finalized = True
        self.cancel_token.raise_if_cancelled()
        instruction = self._compose_root_finalization_instruction()
        await self.stream.emit(
            "tasks",
            "delegating",
            {
                "speaker": root,
                "instruction": "最终整合与交付（主编收尾）",
                "turn": self.stall_detector.n_turns,
                "root_finalization": True,
            },
            command_id=self.command_id,
            org_id=self.org_id,
            superstep=self.stall_detector.n_turns,
        )
        try:
            if finalize_timeout is not None:
                # Time-box the deliver to the remaining hard-ceiling budget so it
                # can never trip the outer ``asyncio.wait_for`` ceiling. On
                # timeout we degrade to the best-effort deliverable rather than
                # letting the whole command be force-killed as "hard ceiling
                # exceeded". A cooperative-token cancel (real user cancel /
                # stop_org) still propagates via CancelledByToken below.
                result = await asyncio.wait_for(
                    self.deliver(root, instruction, progress), timeout=finalize_timeout
                )
            else:
                result = await self.deliver(root, instruction, progress)
        except CancelledByToken:
            raise
        except TimeoutError:
            await self.stream.emit(
                "updates",
                "root_finalization_timeout",
                {
                    "speaker": root,
                    "reason": "finalization_exceeded_remaining_budget",
                    "budget_s": round(finalize_timeout or 0.0, 1),
                },
                command_id=self.command_id,
                org_id=self.org_id,
                superstep=self.stall_detector.n_turns,
            )
            return
        self.delegation_history.append(result)
        await self.stream.emit(
            "updates",
            "delegation_result",
            {
                "speaker": result.speaker,
                "success": result.success,
                "message": result.message,
                "root_finalization": True,
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
                recent_outputs=list(self.delegation_history),
                cancel_event=self._cancel_event,
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
        # RC-conv: surface the concrete produced content on the terminals
        # where a deliverable makes sense. DONE -> the produced answer;
        # OUT_OF_TURNS / REPLAN_BUDGET_EXHAUSTED -> the best partial result so
        # the command degrades gracefully into a "completed-with-output"
        # instead of a bare reason string. CANCELLED / FAILED keep their reason
        # unchanged.
        deliverable = ""
        if outcome in (
            FinalOutcome.DONE,
            FinalOutcome.OUT_OF_TURNS,
            FinalOutcome.REPLAN_BUDGET_EXHAUSTED,
        ):
            deliverable = self._best_effort_deliverable()
        final_message = reason
        if deliverable and outcome is FinalOutcome.DONE:
            final_message = deliverable
        elif deliverable:
            final_message = f"{reason}\n\n{deliverable}"
        return SupervisorOutcome(
            outcome=outcome,
            final_message=final_message,
            final_checkpoint_id=cp_id,
            n_turns=self.stall_detector.n_turns,
            n_replans=self.n_replans,
            reason=reason,
            deliverable=deliverable,
        )
