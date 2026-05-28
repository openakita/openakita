"""Tests for :mod:`openakita.runtime.supervisor`.

Phase 3 commit 3. Covers the full outer/inner loop integration of
TaskLedger + ProgressLedger + StallDetector + Checkpointer + StreamBus
under deterministic fake brain inputs.

These tests are the gate G3 acceptance test for the dual-ledger
mechanism: every documented promise of ADR-0004 has at least one
assertion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import CheckpointStatus, MemoryCheckpointer
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
    SupervisorBrain,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_json(
    *,
    satisfied: bool = False,
    progress: bool = True,
    loop: bool = False,
    speaker: str = "art_director",
    instruction: str = "do the next thing",
) -> str:
    return json.dumps(
        {
            "is_request_satisfied":   {"answer": satisfied, "reason": "r"},
            "is_progress_being_made": {"answer": progress, "reason": "r"},
            "is_in_loop":             {"answer": loop, "reason": "r"},
            "instruction_or_question":{"answer": instruction, "reason": "r"},
            "next_speaker":           {"answer": speaker, "reason": "r"},
        }
    )


class FakeBrain(SupervisorBrain):
    """Scriptable LLM frontend used by the supervisor tests."""

    def __init__(
        self,
        *,
        facts: str | Callable[[int], str] = "fact-1",
        plan: str | Callable[[int], str] = "step-1; step-2",
        progress_responses: list[str] | None = None,
    ) -> None:
        self._facts = facts
        self._plan = plan
        self._progress = list(progress_responses or [])
        self.facts_calls = 0
        self.plan_calls = 0
        self.progress_calls = 0

    async def extract_facts(self, *, task: str, **_kwargs) -> str:
        self.facts_calls += 1
        if callable(self._facts):
            return self._facts(self.facts_calls)
        return self._facts

    async def draft_plan(self, *, task: str, facts: str, **_kwargs) -> str:
        self.plan_calls += 1
        if callable(self._plan):
            return self._plan(self.plan_calls)
        return self._plan

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        **_kwargs,
    ) -> str:
        self.progress_calls += 1
        if not self._progress:
            return _ledger_json(satisfied=True)
        return self._progress.pop(0)


def _make_deliver(
    success_default: bool = True,
) -> tuple[Callable[..., Awaitable[DelegationResult]], list[dict]]:
    log: list[dict] = []

    async def deliver(
        speaker: str, instruction: str, progress: ProgressLedger
    ) -> DelegationResult:
        log.append({"speaker": speaker, "instruction": instruction})
        return DelegationResult(
            success=success_default,
            speaker=speaker,
            message=f"{speaker} produced output",
        )

    return deliver, log


# ---------------------------------------------------------------------------
# Happy path — request satisfied on the first turn
# ---------------------------------------------------------------------------


async def test_supervisor_completes_when_request_satisfied_first_turn() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    brain = FakeBrain(progress_responses=[_ledger_json(satisfied=True)])
    deliver, log = _make_deliver()

    sup = Supervisor(
        command_id="cmd_1",
        org_id="org_1",
        root_node_id="node_root",
        task="hi",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE
    # Brain ran outer loop once and inner loop once.
    assert brain.facts_calls == 1
    assert brain.plan_calls == 1
    assert brain.progress_calls == 1
    # No delegation happened because we satisfied immediately.
    assert log == []
    # Final checkpoint exists.
    assert out.final_checkpoint_id is not None


# ---------------------------------------------------------------------------
# Replan path — three stalls trip a replan, then DONE
# ---------------------------------------------------------------------------


async def test_supervisor_replans_after_three_stalls_then_completes() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    brain = FakeBrain(
        progress_responses=[
            _ledger_json(progress=False),  # n_stalls=1 SUSPECT
            _ledger_json(progress=False),  # n_stalls=2 SUSPECT
            _ledger_json(progress=False),  # n_stalls=3 REPLAN
            _ledger_json(satisfied=True),  # after replan -> DONE
        ]
    )
    deliver, log = _make_deliver()

    sup = Supervisor(
        command_id="cmd_2",
        org_id="org_1",
        root_node_id="node_root",
        task="long task",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        max_stalls=3,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE
    assert out.n_replans == 1
    assert sup.task_ledger.revision == 1
    # extract_facts and draft_plan ran twice (initial + replan)
    assert brain.facts_calls == 2
    assert brain.plan_calls == 2


# ---------------------------------------------------------------------------
# Replan budget exhausted
# ---------------------------------------------------------------------------


async def test_supervisor_returns_replan_budget_exhausted() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    # One stall sequence is enough to trigger REPLAN if max_stalls=1.
    brain = FakeBrain(
        progress_responses=[
            _ledger_json(progress=False),  # REPLAN
            _ledger_json(progress=False),  # REPLAN again
        ]
        * 10  # plenty of bad ledgers
    )
    deliver, _ = _make_deliver()

    sup = Supervisor(
        command_id="cmd_3",
        org_id="org_1",
        root_node_id="node_root",
        task="hopeless",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        max_stalls=1,
        max_replans=2,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.REPLAN_BUDGET_EXHAUSTED
    assert out.n_replans == 2


# ---------------------------------------------------------------------------
# Out of turns
# ---------------------------------------------------------------------------


async def test_supervisor_out_of_turns_when_progressing_forever() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    # Always progressing, never satisfied -> hits max_turns.
    brain = FakeBrain(progress_responses=[_ledger_json(progress=True)] * 50)
    deliver, _ = _make_deliver()

    sup = Supervisor(
        command_id="cmd_4",
        org_id="org_1",
        root_node_id="node_root",
        task="endless",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        max_turns=5,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.OUT_OF_TURNS
    assert out.n_turns == 5


# ---------------------------------------------------------------------------
# Cooperative cancel
# ---------------------------------------------------------------------------


async def test_supervisor_cancels_cooperatively_writes_final_checkpoint() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    # Many turns of progress; we cancel mid-way.
    brain = FakeBrain(progress_responses=[_ledger_json(progress=True)] * 20)
    token = CancellationToken()

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        # On the second delegation, cancel the supervisor.
        if speaker == "art_director" and progress.turn_id >= 2:
            token.cancel("user pressed stop")
        return DelegationResult(success=True, speaker=speaker, message="ok")

    sup = Supervisor(
        command_id="cmd_5",
        org_id="org_1",
        root_node_id="node_root",
        task="cancel me",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        cancel_token=token,
        max_turns=20,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.CANCELLED
    assert "user pressed stop" in out.final_message
    fetched = await store.aget(out.final_checkpoint_id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.metadata.status == CheckpointStatus.CANCELLED


# ---------------------------------------------------------------------------
# Bad JSON retry, then success
# ---------------------------------------------------------------------------


async def test_supervisor_retries_bad_progress_json() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    brain = FakeBrain(
        progress_responses=[
            "totally not json",
            "still bad",
            _ledger_json(satisfied=True),
        ]
    )
    deliver, _ = _make_deliver()

    sup = Supervisor(
        command_id="cmd_6",
        org_id="org_1",
        root_node_id="node_root",
        task="t",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE
    # Brain emitted progress 3 times (2 bad + 1 good).
    assert brain.progress_calls == 3


# ---------------------------------------------------------------------------
# Stream channel coverage
# ---------------------------------------------------------------------------


async def test_supervisor_emits_progress_ledger_and_checkpoint_events() -> None:
    bus = StreamBus(strict=True)
    store = MemoryCheckpointer()
    brain = FakeBrain(
        progress_responses=[
            _ledger_json(progress=True),  # delegate, turn 1
            _ledger_json(satisfied=True),  # done, turn 2
        ]
    )
    deliver, _ = _make_deliver()

    seen_progress = []
    seen_checkpoints = []
    seen_lifecycle = []

    async def watch_progress() -> None:
        async for ev in bus.subscribe("progress_ledger"):
            seen_progress.append(ev.type)
            if len(seen_progress) >= 2:
                return

    async def watch_checkpoints() -> None:
        async for ev in bus.subscribe("checkpoints"):
            seen_checkpoints.append(ev.type)
            if len(seen_checkpoints) >= 2:
                return

    async def watch_lifecycle() -> None:
        async for ev in bus.subscribe("lifecycle"):
            seen_lifecycle.append(ev.type)
            if "done" in seen_lifecycle:
                return

    pwatch = asyncio.create_task(watch_progress())
    cwatch = asyncio.create_task(watch_checkpoints())
    lwatch = asyncio.create_task(watch_lifecycle())
    await asyncio.sleep(0.01)

    sup = Supervisor(
        command_id="cmd_7",
        org_id="org_1",
        root_node_id="node_root",
        task="t",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE
    await asyncio.wait_for(asyncio.gather(pwatch, cwatch, lwatch), timeout=2.0)

    assert seen_progress == ["ledger", "ledger"]
    assert seen_checkpoints[:2] == ["checkpoint_written", "checkpoint_written"]
    assert "started" in seen_lifecycle
    assert "task_ledger_published" in seen_lifecycle
    assert "done" in seen_lifecycle


# ---------------------------------------------------------------------------
# Hygiene: drain background tasks between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _drain_loop() -> object:
    yield
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    me = asyncio.current_task()
    for t in pending:
        if t is me:
            continue
        t.cancel()
        with contextlib.suppress(BaseException):
            await t
