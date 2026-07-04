"""Tests for :mod:`openakita.runtime.stall_detector`.

Phase 3 commit 2. Asserts the regen-stall pattern that ADR-0004
adopts from AutoGen's Magentic-One orchestrator:

* PROCEED when both progress and not-in-loop;
* +1 stall on either no-progress or in-loop;
* regen on real progress (n_stalls reduced by regen_step, never
  below 0);
* SUSPECT once n_stalls > 0 but below threshold;
* REPLAN exactly when n_stalls reaches max_stalls;
* DONE wins over everything when request_satisfied;
* OUT_OF_TURNS at the hard cap regardless of stall state;
* reset_after_replan clears stalls but keeps n_turns;
* reset_all clears everything.

The legacy duplicate-storyboard regression class is encoded as a
specific regression test below: a sequence of long-but-progressing
turns must NOT trigger a REPLAN, even though they each occupy the
real wall-clock budget that legacy max_task_seconds would have
tripped.
"""

from __future__ import annotations

from openakita.runtime.ledger import ProgressLedger, ProgressLedgerEntry
from openakita.runtime.stall_detector import (
    DEFAULT_MAX_STALLS,
    DEFAULT_MAX_TURNS,
    StallDecision,
    StallDetector,
    StallVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger(
    *,
    satisfied: bool = False,
    progress: bool = True,
    loop: bool = False,
    turn: int = 0,
    speaker: str = "art_director",
    instruction: str = "do the next thing",
) -> ProgressLedger:
    return ProgressLedger(
        turn_id=turn,
        is_request_satisfied=ProgressLedgerEntry(answer=satisfied, reason="r"),
        is_progress_being_made=ProgressLedgerEntry(answer=progress, reason="r"),
        is_in_loop=ProgressLedgerEntry(answer=loop, reason="r"),
        instruction_or_question=ProgressLedgerEntry(answer=instruction, reason="r"),
        next_speaker=ProgressLedgerEntry(answer=speaker, reason="r"),
        raw_json="{}",
    )


# ---------------------------------------------------------------------------
# Defaults from ADR-0004
# ---------------------------------------------------------------------------


def test_default_caps_match_adr_0004() -> None:
    assert DEFAULT_MAX_STALLS == 3
    assert DEFAULT_MAX_TURNS == 30
    d = StallDetector()
    assert d.max_stalls == 3
    assert d.max_turns == 30
    assert d.regen_step == 1


# ---------------------------------------------------------------------------
# PROCEED happy path
# ---------------------------------------------------------------------------


def test_progress_always_proceeds() -> None:
    d = StallDetector()
    for i in range(5):
        decision = d.evaluate(_ledger(progress=True, loop=False, turn=i))
        assert decision.verdict == StallVerdict.PROCEED
        assert decision.n_stalls == 0
    assert d.n_turns == 5


# ---------------------------------------------------------------------------
# Stall accumulation
# ---------------------------------------------------------------------------


def test_no_progress_increments_stall() -> None:
    d = StallDetector(max_stalls=3)
    decision = d.evaluate(_ledger(progress=False))
    assert decision.verdict == StallVerdict.SUSPECT
    assert decision.n_stalls == 1


def test_in_loop_increments_stall() -> None:
    d = StallDetector(max_stalls=3)
    decision = d.evaluate(_ledger(progress=True, loop=True))
    assert decision.verdict == StallVerdict.SUSPECT
    assert decision.n_stalls == 1


def test_no_progress_and_in_loop_only_count_once_per_turn() -> None:
    """Per ADR-0004, the stall counter increments by ONE per turn even
    when both signals are flagged. A turn is one stall, not two."""
    d = StallDetector(max_stalls=3)
    decision = d.evaluate(_ledger(progress=False, loop=True))
    assert decision.verdict == StallVerdict.SUSPECT
    assert decision.n_stalls == 1


# ---------------------------------------------------------------------------
# Regen
# ---------------------------------------------------------------------------


def test_real_progress_regens_stall_counter() -> None:
    d = StallDetector(max_stalls=3)
    d.evaluate(_ledger(progress=False))  # n_stalls=1
    d.evaluate(_ledger(progress=False))  # n_stalls=2
    decision = d.evaluate(_ledger(progress=True, loop=False))
    assert decision.verdict == StallVerdict.SUSPECT
    assert decision.n_stalls == 1


def test_regen_clamps_to_zero() -> None:
    d = StallDetector(max_stalls=3)
    d.evaluate(_ledger(progress=False))  # n_stalls=1
    d.evaluate(_ledger(progress=True))   # regen back to 0
    d.evaluate(_ledger(progress=True))   # already 0
    assert d.n_stalls == 0


# ---------------------------------------------------------------------------
# REPLAN at threshold
# ---------------------------------------------------------------------------


def test_replan_fires_exactly_at_max_stalls() -> None:
    d = StallDetector(max_stalls=3)
    decisions: list[StallDecision] = []
    for _ in range(3):
        decisions.append(d.evaluate(_ledger(progress=False)))
    assert decisions[0].verdict == StallVerdict.SUSPECT
    assert decisions[1].verdict == StallVerdict.SUSPECT
    assert decisions[2].verdict == StallVerdict.REPLAN
    assert decisions[2].n_stalls == 3


# ---------------------------------------------------------------------------
# DONE wins
# ---------------------------------------------------------------------------


def test_done_wins_even_when_stalls_at_threshold() -> None:
    d = StallDetector(max_stalls=2)
    d.evaluate(_ledger(progress=False))
    d.evaluate(_ledger(progress=False))  # would be REPLAN next time
    decision = d.evaluate(_ledger(satisfied=True))
    assert decision.verdict == StallVerdict.DONE


# ---------------------------------------------------------------------------
# OUT_OF_TURNS
# ---------------------------------------------------------------------------


def test_out_of_turns_fires_at_cap() -> None:
    d = StallDetector(max_turns=3)
    for _ in range(2):
        d.evaluate(_ledger(progress=True))
    decision = d.evaluate(_ledger(progress=True))
    assert decision.verdict == StallVerdict.OUT_OF_TURNS
    assert decision.n_turns == 3


def test_out_of_turns_takes_precedence_over_replan_threshold() -> None:
    d = StallDetector(max_stalls=10, max_turns=2)
    d.evaluate(_ledger(progress=False))
    decision = d.evaluate(_ledger(progress=False))
    assert decision.verdict == StallVerdict.OUT_OF_TURNS


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_after_replan_clears_stalls_keeps_turns() -> None:
    d = StallDetector(max_stalls=3, max_turns=20)
    for _ in range(3):
        d.evaluate(_ledger(progress=False))
    assert d.n_stalls == 3
    assert d.n_turns == 3
    d.reset_after_replan()
    assert d.n_stalls == 0
    assert d.n_turns == 3


def test_reset_all_clears_everything() -> None:
    d = StallDetector()
    d.evaluate(_ledger(progress=False))
    d.evaluate(_ledger(progress=False))
    d.reset_all()
    assert d.n_stalls == 0
    assert d.n_turns == 0
    assert d.history == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_caps_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        StallDetector(max_stalls=0)
    with pytest.raises(ValueError):
        StallDetector(max_turns=0)
    with pytest.raises(ValueError):
        StallDetector(regen_step=0)


# ---------------------------------------------------------------------------
# Regression: long-but-progressing storyboard does not trip REPLAN
# ---------------------------------------------------------------------------


def test_regression_long_progressing_storyboard_does_not_replan() -> None:
    """The duplicate-storyboard cascade described in ADR-0001 came from
    legacy max_task_seconds tripping on long-but-progressing tool calls.

    With the v2 detector the same scenario looks like a sequence of
    PROCEED verdicts, never a REPLAN, because the LLM is reporting
    progress on every turn — the wall-clock is no longer in the
    decision."""
    d = StallDetector(max_stalls=3, max_turns=40)
    # Simulate ten turns of long-but-progressing storyboard work.
    for i in range(10):
        decision = d.evaluate(_ledger(progress=True, loop=False, turn=i))
        assert decision.verdict == StallVerdict.PROCEED
    # And the final turn that finishes.
    final = d.evaluate(_ledger(satisfied=True, turn=10))
    assert final.verdict == StallVerdict.DONE


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def test_to_jsonable_has_history() -> None:
    d = StallDetector()
    d.evaluate(_ledger(progress=True))
    payload = d.to_jsonable()
    assert payload["n_turns"] == 1
    assert payload["max_stalls"] == 3
    assert len(payload["history"]) == 1
    assert payload["history"][0]["verdict"] == "proceed"


def test_decision_to_jsonable() -> None:
    d = StallDetector()
    decision = d.evaluate(_ledger(progress=False))
    j = decision.to_jsonable()
    assert j["verdict"] == "suspect"
    assert j["n_stalls"] == 1
    assert "no progress" in j["reason"]
