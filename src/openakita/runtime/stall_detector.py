"""Stall detection driven by ProgressLedger signals.

Implements the ``n_stalls`` counter with regen logic that ADR-0004
calls for. A counter that *only* counts up cannot tolerate intermittent
plateaus and would fire false positives on a single slow turn; a
counter that *only* counts in-loop signals cannot detect long
no-progress stretches. The AutoGen Magentic-One pattern is the
combination:

* ``not is_progress_being_made``         => ``n_stalls += 1``
* ``is_in_loop``                         => ``n_stalls += 1``
* otherwise (real progress)              => ``n_stalls = max(0, n_stalls - 1)``

The detector also tracks ``n_turns`` (a hard cap, replaces
``org_command_max_seconds``) and exposes a single :meth:`evaluate`
method whose return type tells the supervisor what to do next.

This module is intentionally pure-logic — it has no side effects, no
I/O, no async. Tests can drive it deterministically by feeding hand-
crafted ProgressLedger instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .ledger import ProgressLedger

__all__ = [
    "StallDetector",
    "StallDecision",
    "StallVerdict",
    "DEFAULT_MAX_STALLS",
    "DEFAULT_MAX_TURNS",
]


DEFAULT_MAX_STALLS = 3
DEFAULT_MAX_TURNS = 30


class StallVerdict(StrEnum):
    """What the detector recommends after the latest ProgressLedger."""

    PROCEED = "proceed"  # advance to next_speaker as planned
    SUSPECT = "suspect"  # stalls observed but threshold not yet hit
    REPLAN = "replan"  # threshold hit; outer loop should replan
    DONE = "done"  # request satisfied
    OUT_OF_TURNS = "out_of_turns"  # max_turns hit before satisfaction


@dataclass(frozen=True)
class StallDecision:
    """The detector's verdict for one inner-loop turn."""

    verdict: StallVerdict
    n_stalls: int
    n_turns: int
    max_stalls: int
    max_turns: int
    reason: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "n_stalls": self.n_stalls,
            "n_turns": self.n_turns,
            "max_stalls": self.max_stalls,
            "max_turns": self.max_turns,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@dataclass
class StallDetector:
    """Stateful counter with regen, gated by a hard turn cap.

    Args:
        max_stalls: number of *cumulative* stall signals (after regen)
            that triggers a REPLAN. Default 3 per ADR-0004.
        max_turns: hard cap on inner-loop turns. Default 30. When the
            cap is hit, the detector returns OUT_OF_TURNS regardless of
            stall state, so a runaway outer loop with healthy progress
            signals still terminates.
        regen_step: how many stalls a real-progress turn forgives.
            Default 1, matching the Magentic-One pattern.
    """

    max_stalls: int = DEFAULT_MAX_STALLS
    max_turns: int = DEFAULT_MAX_TURNS
    regen_step: int = 1
    n_stalls: int = 0
    n_turns: int = 0
    history: list[StallDecision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_stalls < 1:
            raise ValueError("max_stalls must be >= 1")
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if self.regen_step < 1:
            raise ValueError("regen_step must be >= 1")

    # ------------------------------------------------------------------
    # Outer-loop reset
    # ------------------------------------------------------------------

    def reset_after_replan(self) -> None:
        """Called by the supervisor after a successful outer-loop replan.

        ``n_stalls`` clears (we just produced a new plan, give it a
        fresh budget). ``n_turns`` keeps accumulating because it is the
        hard cap on the *whole command*, not per-plan.
        """
        self.n_stalls = 0

    def reset_all(self) -> None:
        self.n_stalls = 0
        self.n_turns = 0
        self.history.clear()

    # ------------------------------------------------------------------
    # Per-turn evaluation
    # ------------------------------------------------------------------

    def evaluate(self, progress: ProgressLedger) -> StallDecision:
        """Update counters and return the resulting decision.

        Order of checks matters:
          1. request_satisfied wins over everything (DONE).
          2. then n_turns cap (OUT_OF_TURNS).
          3. then stall accounting + max_stalls (REPLAN if hit).
          4. otherwise PROCEED or SUSPECT depending on n_stalls.

        Side effects: increments ``n_turns`` by 1 and updates
        ``n_stalls`` per the regen rules described in the module
        docstring.
        """
        self.n_turns += 1

        # 1) Did the LLM say we are done?
        if progress.request_satisfied:
            decision = StallDecision(
                verdict=StallVerdict.DONE,
                n_stalls=self.n_stalls,
                n_turns=self.n_turns,
                max_stalls=self.max_stalls,
                max_turns=self.max_turns,
                reason=str(progress.is_request_satisfied.reason),
            )
            self.history.append(decision)
            return decision

        # 2) Hard turn cap.
        if self.n_turns >= self.max_turns:
            decision = StallDecision(
                verdict=StallVerdict.OUT_OF_TURNS,
                n_stalls=self.n_stalls,
                n_turns=self.n_turns,
                max_stalls=self.max_stalls,
                max_turns=self.max_turns,
                reason=(
                    f"reached max_turns={self.max_turns} without "
                    f"request_satisfied"
                ),
            )
            self.history.append(decision)
            return decision

        # 3) Stall accounting.
        stalled_this_turn = (
            (not progress.progress_being_made) or progress.in_loop
        )
        if stalled_this_turn:
            self.n_stalls += 1
            stall_reason_parts: list[str] = []
            if not progress.progress_being_made:
                stall_reason_parts.append(
                    f"no progress: {progress.is_progress_being_made.reason}"
                )
            if progress.in_loop:
                stall_reason_parts.append(
                    f"in loop: {progress.is_in_loop.reason}"
                )
            stall_reason = "; ".join(stall_reason_parts)
        else:
            self.n_stalls = max(0, self.n_stalls - self.regen_step)
            stall_reason = ""

        if self.n_stalls >= self.max_stalls:
            decision = StallDecision(
                verdict=StallVerdict.REPLAN,
                n_stalls=self.n_stalls,
                n_turns=self.n_turns,
                max_stalls=self.max_stalls,
                max_turns=self.max_turns,
                reason=(
                    f"n_stalls={self.n_stalls} >= max_stalls="
                    f"{self.max_stalls}: {stall_reason}"
                ),
            )
            self.history.append(decision)
            return decision

        if self.n_stalls > 0:
            verdict = StallVerdict.SUSPECT
            reason = (
                f"n_stalls={self.n_stalls} (max={self.max_stalls}); "
                + stall_reason
            )
        else:
            verdict = StallVerdict.PROCEED
            reason = ""

        decision = StallDecision(
            verdict=verdict,
            n_stalls=self.n_stalls,
            n_turns=self.n_turns,
            max_stalls=self.max_stalls,
            max_turns=self.max_turns,
            reason=reason,
        )
        self.history.append(decision)
        return decision

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "max_stalls": self.max_stalls,
            "max_turns": self.max_turns,
            "regen_step": self.regen_step,
            "n_stalls": self.n_stalls,
            "n_turns": self.n_turns,
            "history": [d.to_jsonable() for d in self.history],
        }
