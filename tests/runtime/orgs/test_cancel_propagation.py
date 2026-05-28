"""v22 RCA RC-4: cancel_token -> brain -> LLM httpx propagation.

The RCA documents how :meth:`OrgCommandService._cooperative_cancel`'s 5s
drain window was *structurally guaranteed* to time out: cancel_token's
flag flip notified nothing else, while the supervisor sat in
``await provider.chat(...)`` with no checkpoints between the
supervisor-level ``raise_if_cancelled()`` and the underlying
``httpx.post``. Every user cancel ended in force-cancel, which in turn
aborted before ``_terminate`` could write a final cancelled checkpoint.

This file pins the post-RC-4 contract:

1. ``Supervisor`` mints / inherits an ``asyncio.Event`` bridged onto
   ``cancel_token`` and forwards it to every brain call so a real LLM
   client can race the in-flight ``httpx`` request.
2. A brain whose ``emit_progress_ledger`` simulates a 30s LLM call but
   races against ``cancel_event`` aborts in well under 1.5s after the
   cancel is issued.
3. Going through the full ``OrgCommandService`` submit / cancel cycle
   no longer logs ``"drain timed out"`` -- the supervisor task
   terminates naturally inside the drain budget.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService
from openakita.runtime.cancel_token import CancellationToken, CancelledByToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
    SupervisorBrain,
)

# ---------------------------------------------------------------------------
# Probe brains
# ---------------------------------------------------------------------------


class _CapturingBrain(SupervisorBrain):
    """Records ``cancel_event`` argument on every call; satisfies on turn 1."""

    def __init__(self) -> None:
        self.captured: list[tuple[str, asyncio.Event | None]] = []

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.captured.append(("extract_facts", cancel_event))
        return f"facts:{task[:20]}"

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.captured.append(("draft_plan", cancel_event))
        return "plan"

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.captured.append(("emit_progress_ledger", cancel_event))
        return json.dumps({
            "is_request_satisfied":    {"answer": True,  "reason": "done"},
            "is_progress_being_made":  {"answer": True,  "reason": "-"},
            "is_in_loop":              {"answer": False, "reason": "-"},
            "instruction_or_question": {"answer": "ok",  "reason": "-"},
            "next_speaker":            {"answer": "supervisor", "reason": "-"},
        })


class _SlowCancelAwareBrain(SupervisorBrain):
    """Simulates a slow LLM call that honours ``cancel_event``.

    Mirrors how a production brain wires
    :meth:`LLMClient._race_with_cancel`: the in-flight provider call is
    raced against ``cancel_event.wait()``; if the event fires first we
    surface a cooperative cancel as
    :class:`~openakita.runtime.cancel_token.CancelledByToken` so the
    supervisor's ``except CancelledByToken`` arm in :meth:`Supervisor.run`
    can run ``_terminate`` and write the final cancelled checkpoint.
    """

    def __init__(self, *, slow_seconds: float = 30.0) -> None:
        self.slow_seconds = slow_seconds
        self.entered_emit_progress = asyncio.Event()

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        return f"facts:{task[:20]}"

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        return "plan"

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.entered_emit_progress.set()
        if cancel_event is None:
            await asyncio.sleep(self.slow_seconds)
        else:
            slow = asyncio.ensure_future(asyncio.sleep(self.slow_seconds))
            waiter = asyncio.ensure_future(cancel_event.wait())
            try:
                done, pending = await asyncio.wait(
                    [slow, waiter],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()
                if waiter in done:
                    raise CancelledByToken("cancel_event fired")
            finally:
                for t in (slow, waiter):
                    if not t.done():
                        t.cancel()
        return json.dumps({
            "is_request_satisfied":    {"answer": True,  "reason": "done"},
            "is_progress_being_made":  {"answer": True,  "reason": "-"},
            "is_in_loop":              {"answer": False, "reason": "-"},
            "instruction_or_question": {"answer": "ok",  "reason": "-"},
            "next_speaker":            {"answer": "supervisor", "reason": "-"},
        })


async def _noop_deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
    return DelegationResult(success=True, speaker=speaker, message="ok")


# ---------------------------------------------------------------------------
# Test 1: cancel_event propagates through every brain method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_event_propagates_through_brain() -> None:
    """Supervisor must hand a live ``asyncio.Event`` to every brain call."""
    brain = _CapturingBrain()
    sup = Supervisor(
        command_id="cmd_propagate",
        org_id="org_propagate",
        root_node_id="root",
        task="hello",
        brain=brain,
        deliver=_noop_deliver,
        stream=StreamBus(strict=False),
        checkpointer=MemoryCheckpointer(),
    )

    out = await sup.run()
    assert out.outcome == FinalOutcome.DONE

    # All three brain methods should have been called and each should
    # have received a real ``asyncio.Event`` (the supervisor minted
    # one when no factory wired it).
    methods_seen = {name for name, _ev in brain.captured}
    assert "extract_facts" in methods_seen
    assert "draft_plan" in methods_seen
    assert "emit_progress_ledger" in methods_seen
    for name, ev in brain.captured:
        assert isinstance(ev, asyncio.Event), (
            f"{name} did not receive an asyncio.Event (got {type(ev).__name__})"
        )

    # And the event must be wired to ``cancel_token``: cancelling the
    # token should set the event.
    assert not sup._cancel_event.is_set()
    sup.cancel_token.cancel("test")
    # ``add_callback`` fires synchronously so the event is observable
    # immediately even before yielding to the loop.
    assert sup._cancel_event.is_set()


# ---------------------------------------------------------------------------
# Test 2: cancel aborts a long LLM call within 1s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_aborts_long_llm_call_within_1s() -> None:
    """Cancel during a slow brain call must terminate within ~1.5s."""
    brain = _SlowCancelAwareBrain(slow_seconds=30.0)
    sup = Supervisor(
        command_id="cmd_slow",
        org_id="org_slow",
        root_node_id="root",
        task="slow",
        brain=brain,
        deliver=_noop_deliver,
        stream=StreamBus(strict=False),
        checkpointer=MemoryCheckpointer(),
    )

    run_task = asyncio.create_task(sup.run())
    # Wait for the supervisor to actually enter the slow brain call;
    # otherwise the cancel could land before any await observed the
    # event and we would not be measuring the bridge.
    await asyncio.wait_for(brain.entered_emit_progress.wait(), timeout=1.0)

    started = time.monotonic()
    sup.cancel_token.cancel("user_cancel")

    out = await asyncio.wait_for(run_task, timeout=1.5)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"cancel propagation took {elapsed:.2f}s"
    assert out.outcome == FinalOutcome.CANCELLED, f"unexpected outcome {out}"
    # The supervisor's cooperative ``_terminate`` path must have
    # written the final checkpoint before returning -- the regression
    # we are guarding against was force-cancel preempting that write.
    assert out.final_checkpoint_id is not None


# ---------------------------------------------------------------------------
# Test 3: cooperative_cancel no longer trips ``drain timed out``
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, *, roots: tuple[str, ...] = ("root1",)) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]

    def get_node(self, nid: str) -> Any:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[Any]:
        return list(self.nodes)


def _make_runtime() -> MagicMock:
    rt = MagicMock()
    rt.get_org = MagicMock(return_value=_Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())

    async def _async_cancel(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"cancelled_roots": ["root1"]}

    rt.cancel_user_command = _async_cancel
    return rt


@pytest.mark.asyncio
async def test_cancel_drain_no_longer_times_out(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancel-aware brain + supervisor must not log 'drain timed out'."""
    monkeypatch.setattr(settings, "orgs_cancel_drain_budget_s", 3, raising=False)
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", 60, raising=False)

    brain = _SlowCancelAwareBrain(slow_seconds=10.0)

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        token = cancel_token or CancellationToken()
        return Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=globals()["_brain_for_factory"],
            deliver=_noop_deliver,
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
        )

    # Pin the brain instance the factory uses (one per submit).
    globals()["_brain_for_factory"] = brain
    svc = OrgCommandService(_make_runtime(), supervisor_factory=_factory)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="slow"))
    cid = res["command_id"]
    assert res["status"] == "running"

    await asyncio.wait_for(brain.entered_emit_progress.wait(), timeout=2.0)

    caplog.set_level(logging.WARNING, logger="openakita.orgs.command_service")
    started = time.monotonic()
    cancel_res = await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")
    elapsed = time.monotonic() - started

    assert cancel_res is not None and cancel_res["ok"] is True
    # The cancel should have completed well before the 3s drain
    # budget, proving the cancel_event bridge actually aborted the
    # in-flight brain call.
    assert elapsed < 2.0, f"cancel took {elapsed:.2f}s; expected fast cancel path"
    # And, critically, the warning must not appear.
    assert not any(
        "drain timed out" in record.getMessage() for record in caplog.records
    ), "drain timed out warning logged despite cancel_event bridge"
    # Slot must be released (verifies _schedule_run finally ran).
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass
    assert ("o1", "root1") not in svc._running_by_root
