"""v22 RCA RC-4 + v23 regression: cancel propagation contracts.

The original RCA documents how :meth:`OrgCommandService._cooperative_cancel`'s
5s drain window was *structurally guaranteed* to time out: cancel_token's
flag flip notified nothing else, while the supervisor sat in
``await provider.chat(...)`` with no checkpoints between the
supervisor-level ``raise_if_cancelled()`` and the underlying
``httpx.post``. Every user cancel ended in force-cancel, which in turn
aborted before ``_terminate`` could write a final cancelled checkpoint.

This file pins the cancel contract:

1. ``Supervisor`` mints / inherits an ``asyncio.Event`` bridged onto
   ``cancel_token`` and forwards it to every brain call so a real LLM
   client can race the in-flight ``httpx`` request (commit
   ``d1275851``).
2. A brain whose ``emit_progress_ledger`` simulates a 30s LLM call but
   races against ``cancel_event`` aborts in well under 1.5s after the
   cancel is issued.
3. Going through the full ``OrgCommandService`` submit / cancel cycle
   no longer logs ``"drain timed out"`` -- the supervisor task
   terminates naturally inside the drain budget.

v23 regression report (``_v23_biz/v23_regression_report.md``) caught
that the d1275851 bridge only reaches :class:`SupervisorBrain`; the
production :class:`PassThroughSupervisorBrain` ignores ``cancel_event``
and the real LLM call happens via ``deliver -> executor.activate_and_run
-> agent.run -> Brain.messages_create_async`` with ``cancel_event=None``
all the way down. Cancel therefore still fell back to the 8s force-cancel
path and lost the final checkpoint
(see ``_v23_biz/_rc4_debug_notes.md``). The "v23" tests below pin the
defensive fix:

4. With a production-shaped ``PassThroughSupervisorBrain`` + a
   long-running deliver that *ignores* ``cancel_event``, ``cancel``
   still terminates the supervisor in well under 1.5s by firing
   ``task.cancel()`` after ``cancel_token.cancel()`` -- and
   :meth:`Supervisor.run`'s new ``except CancelledError`` branch writes
   the final ``cancelled`` checkpoint so ``last_checkpoint_id`` is
   non-null afterwards.
5. ``OrgCommandService.cancel`` surfaces the active supervisor's root
   in ``cancelled_roots`` even when the runtime tracker is not
   registered (the supervisor-takeover path never calls
   ``runtime.send_command``).
6. The full submit / cancel cycle under the production brain shape
   does not log ``"drain timed out"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
        recent_outputs: list | None = None,
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
        recent_outputs: list | None = None,
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


# ---------------------------------------------------------------------------
# v23 RC-4 regression tests
# ---------------------------------------------------------------------------
#
# These three tests reproduce the production failure mode the v23
# regression report (``_v23_biz/v23_regression_report.md``) caught:
# the d1275851 bridge wired ``cancel_event`` to ``SupervisorBrain``
# only, but production uses ``PassThroughSupervisorBrain`` whose
# methods return canned JSON without consulting ``cancel_event``.
# The actual LLM call happens deeper, inside
# ``Supervisor.deliver -> executor.activate_and_run -> agent.run ->
# Brain.messages_create_async``, where ``cancel_event`` is never
# plumbed (audit ``_v23_biz/_rc4_debug_notes.md``). Pre-fix, cancel
# returned ``cancelled_roots=[]``, waited the full 8s drain budget,
# fell back to force-cancel, and ``last_checkpoint_id`` came back
# null. The tests below would have failed on ``d1275851`` and pass
# on the new defensive-cancel commit.


class _SlowDeliverIgnoringCancelEvent:
    """Production-shaped deliver: long-running, ignores ``cancel_event``.

    Mirrors ``supervisor_factory._make_executor_deliver`` ->
    ``executor.activate_and_run`` -> ``agent.run`` -> ``Brain.messages_create_async``
    -> ``httpx``. None of those layers receive ``cancel_event`` today,
    so a slow LLM call only aborts when ``asyncio.CancelledError`` is
    raised on the supervisor task (i.e. when
    :meth:`OrgCommandService._cooperative_cancel` calls
    ``task.cancel()``).
    """

    def __init__(self, *, slow_seconds: float = 30.0) -> None:
        self.slow_seconds = slow_seconds
        self.entered = asyncio.Event()
        self.exited_via_cancel = False

    async def __call__(self, speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        self.entered.set()
        try:
            await asyncio.sleep(self.slow_seconds)
        except asyncio.CancelledError:
            self.exited_via_cancel = True
            raise
        return DelegationResult(success=True, speaker=speaker, message="ok")


@pytest.mark.asyncio
async def test_cancel_aborts_long_deliver_under_production_brain(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """v23 RC-4: cancel under PassThroughSupervisorBrain + slow deliver.

    Reproduces the production failure mode the d1275851 cancel_event
    bridge does *not* cover, and pins the v23 fix:

    * cancel returns ``cancelled_roots`` containing the supervisor's
      root (non-empty);
    * ``cancel_to_terminal`` is well under 1.5s (NOT the 8s drain budget);
    * no ``"drain timed out"`` warning is logged;
    * ``last_checkpoint_id`` is non-null after cancel
      (``_terminate`` ran inside the supervisor's new ``except
      CancelledError`` branch).
    """
    from openakita.agent.supervisor_brain import PassThroughSupervisorBrain

    monkeypatch.setattr(settings, "orgs_cancel_drain_budget_s", 3, raising=False)
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", 60, raising=False)

    deliver = _SlowDeliverIgnoringCancelEvent(slow_seconds=30.0)

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        token = cancel_token or CancellationToken()
        return Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=PassThroughSupervisorBrain(root_node_id=root_node_id),
            deliver=deliver,
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
        )

    svc = OrgCommandService(_make_runtime(), supervisor_factory=_factory)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="long running"))
    cid = res["command_id"]
    assert res["status"] == "running"

    # Wait for the slow deliver to actually start; otherwise the cancel
    # might race the supervisor before _inner_loop reaches deliver.
    await asyncio.wait_for(deliver.entered.wait(), timeout=2.0)

    caplog.set_level(logging.WARNING, logger="openakita.orgs.command_service")
    started = time.monotonic()
    cancel_res = await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")
    elapsed = time.monotonic() - started

    assert cancel_res is not None and cancel_res["ok"] is True
    # v23 fix #1: response surfaces the supervisor's root rather than
    # the empty list the runtime tracker returns for the takeover path.
    assert cancel_res["cancelled_roots"], (
        f"expected non-empty cancelled_roots, got {cancel_res['cancelled_roots']!r}"
    )
    # v23 fix #2: cancel is no longer pinned to the drain budget.
    assert elapsed < 1.5, f"cancel took {elapsed:.2f}s; expected <1.5s via task.cancel bridge"
    # v23 fix #3: drain budget did NOT trip (the new task.cancel bridge
    # aborts the deliver via CancelledError, not via the drain timeout).
    drain_warnings = [
        r.getMessage()
        for r in caplog.records
        if "drain timed out" in r.getMessage()
    ]
    assert not drain_warnings, f"drain timed out warning logged: {drain_warnings}"
    # The slow deliver should have observed the cancellation directly
    # (proves task.cancel() actually propagated through deliver).
    assert deliver.exited_via_cancel, "deliver did not see CancelledError"

    # Let the background task settle so we can read the final state.
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass

    # v23 fix #4: final checkpoint was written by ``Supervisor.run``'s
    # new ``except CancelledError`` branch, mirrored onto the command
    # state by ``_run``'s except so ``get_status`` sees it.
    snapshot = svc.get_status("o1", cid)
    assert snapshot is not None
    assert snapshot["status"] == "cancelled"
    assert snapshot.get("last_checkpoint_id") is not None, (
        "last_checkpoint_id is still null; supervisor._terminate did not run"
    )


@pytest.mark.asyncio
async def test_cancel_response_includes_supervisor_root_when_no_runtime_tracker() -> None:
    """v23 RC-4 observability: response surfaces supervisor root when runtime tracker is absent.

    Pre-fix ``cancel`` always read ``cancelled_roots`` off
    ``runtime.cancel_user_command``. The supervisor-takeover HTTP path
    never registers a runtime tracker (it skips ``runtime.send_command``),
    so the runtime call returned ``None`` and the response shipped
    ``cancelled_roots: []`` -- which led v23 regression triage to
    incorrectly conclude the cancel never found a supervisor.
    """
    from openakita.agent.supervisor_brain import PassThroughSupervisorBrain

    # Build a runtime whose cancel_user_command returns the empty body
    # (mirroring the production "no tracker" path).
    rt = _make_runtime()

    async def _empty_cancel(*_a: Any, **_kw: Any) -> dict[str, Any] | None:
        return None

    rt.cancel_user_command = _empty_cancel

    sleep_started = asyncio.Event()

    async def _slow_deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        sleep_started.set()
        await asyncio.sleep(30.0)
        return DelegationResult(success=True, speaker=speaker, message="ok")

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        token = cancel_token or CancellationToken()
        return Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=PassThroughSupervisorBrain(root_node_id=root_node_id),
            deliver=_slow_deliver,
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
        )

    svc = OrgCommandService(rt, supervisor_factory=_factory)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="surface root"))
    cid = res["command_id"]
    await asyncio.wait_for(sleep_started.wait(), timeout=2.0)

    cancel_res = await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")
    assert cancel_res is not None
    assert cancel_res["cancelled_roots"] == ["root1"], (
        f"expected ['root1'] (supervisor root fallback), got "
        f"{cancel_res['cancelled_roots']!r}"
    )

    # Drain so pytest doesn't see an orphan task warning.
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_cancel_emits_terminal_command_done_event() -> None:
    """Exploratory v21 (2026-06): the cancel path must emit ``command_done``.

    A real multi-layer cancel left only ``agent_run_cancelled`` in
    events.jsonl — no ``command_done`` and no busy-node convergence reset —
    because the force-cancel fallback hard-cancels the supervisor task before
    ``_reflect_supervisor_outcome`` (which emits the terminal event) can run.
    Category 5 requires ALL terminal paths (done / error / cancel / timeout) to
    produce ``command_done`` + clean up hanging node_status. ``cancel`` now
    emits it as an idempotent fallback; here we pin that ``emit_command_done``
    is invoked with ``status="cancelled"``.
    """
    from openakita.agent.supervisor_brain import PassThroughSupervisorBrain

    rt = _make_runtime()
    rt.emit_command_done = AsyncMock(return_value=None)

    deliver = _SlowDeliverIgnoringCancelEvent(slow_seconds=30.0)

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        token = cancel_token or CancellationToken()
        return Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=PassThroughSupervisorBrain(root_node_id=root_node_id),
            deliver=deliver,
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
        )

    svc = OrgCommandService(rt, supervisor_factory=_factory)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="cancel done"))
    cid = res["command_id"]
    await asyncio.wait_for(deliver.entered.wait(), timeout=2.0)

    cancel_res = await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")
    assert cancel_res is not None and cancel_res["ok"] is True

    # The terminal command_done emit fired exactly once, with cancelled status.
    rt.emit_command_done.assert_awaited()
    call = rt.emit_command_done.await_args
    assert call.args[0] == "o1"
    assert call.args[1] == cid
    assert call.kwargs.get("status") == "cancelled"

    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_cancel_event_set_when_cancel_token_fired() -> None:
    """Mock a registered supervisor, cancel it, assert event + token state.

    Directly exercises :meth:`OrgCommandService._cooperative_cancel`'s
    contract: when a live supervisor sits in ``_active_supervisors``,
    the cancel fires its ``cancel_token`` and the bridged
    ``cancel_event`` is set as a side effect.
    """
    from openakita.agent.supervisor_brain import PassThroughSupervisorBrain

    finished = asyncio.Event()

    async def _slow_deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            finished.set()
            raise
        return DelegationResult(success=True, speaker=speaker, message="ok")

    captured: dict[str, Any] = {}

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        token = cancel_token or CancellationToken()
        sup = Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=PassThroughSupervisorBrain(root_node_id=root_node_id),
            deliver=_slow_deliver,
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
        )
        captured["supervisor"] = sup
        return sup

    svc = OrgCommandService(_make_runtime(), supervisor_factory=_factory)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="cancel me"))
    cid = res["command_id"]

    # Spin briefly so the background task built the supervisor and
    # registered it in _active_supervisors.
    for _ in range(50):
        if cid in svc._active_supervisors:
            break
        await asyncio.sleep(0.02)
    assert cid in svc._active_supervisors, "supervisor never registered"

    supervisor = captured["supervisor"]
    assert not supervisor.cancel_token.is_cancelled()
    assert not supervisor._cancel_event.is_set()

    await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")

    assert supervisor.cancel_token.is_cancelled(), "cancel_token was not fired"
    assert supervisor._cancel_event.is_set(), "cancel_event was not set via callback"
    # The slow deliver should have seen CancelledError (proves
    # task.cancel() actually reached deliver, not just the cancel_event
    # that PassThroughSupervisorBrain ignores).
    assert finished.is_set(), "deliver did not observe CancelledError"

    # Drain orphan task.
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass
