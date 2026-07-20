"""Sprint-9: ``OrgCommandService`` now drives ``Supervisor.run``.

The HTTP ``POST /api/v2/orgs/{id}/command`` path (and the IM canary)
both build a :class:`~openakita.runtime.supervisor.Supervisor` via
:func:`openakita.runtime.supervisor_factory.build_supervisor_for_command`
and let it run end-to-end. The wall-clock ``_watchdog_loop`` is gone;
stall detection lives in
:class:`~openakita.runtime.stall_detector.StallDetector` and is fed by
LLM-evaluated :class:`~openakita.runtime.ledger.ProgressLedger`
signals.

This file pins the five regression scenarios the audit / RCA list
flagged:

1. **happy path** -- submit -> supervisor.run() returns ``DONE`` ->
   command flips to ``status=done`` with the new observability
   fields (``n_turns`` / ``replan_count`` / ``last_checkpoint_id``).
2. **replace_existing=true** -- second submit on the same root
   cooperatively cancels the in-flight supervisor and only starts
   the new one after the cancel token fires.
3. **continue_previous=true** with no checkpoint -> falls back to
   the legacy content-concatenation path (does NOT 409).
4. **cancel(cid) -> idempotent** -- the dup_cancel audit case
   (v20 §3 B6.3): the second cancel call must return HTTP-OK with
   ``already_done=True`` instead of crashing.
5. **cancel_all_for_org** -- stop-org cancels every in-flight
   supervisor concurrently.

Each test patches :func:`build_supervisor_for_command` with a stub
that returns a controllable fake supervisor; that keeps the test
fast (no real LLM brain, no sqlite IO) and decoupled from the
``AgentPipelineExecutor`` plumbing whose own tests exercise it.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService
from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.supervisor import FinalOutcome, SupervisorOutcome

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, *, roots: tuple[str, ...] = ("root1",)) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _FakeStallDetector:
    n_turns = 3
    n_stalls = 1


class _FakeSupervisor:
    """Minimal stand-in for :class:`Supervisor`.

    Exposes the public surface the service touches:
    ``run()``, ``cancel_token``, ``stall_detector``, ``history``,
    ``n_replans``, ``last_checkpoint_id``, ``resume_from_checkpoint``.
    """

    def __init__(
        self,
        *,
        outcome: FinalOutcome = FinalOutcome.DONE,
        run_delay_s: float = 0.0,
        respect_cancel: bool = True,
    ) -> None:
        self.cancel_token = CancellationToken()
        self.stall_detector = _FakeStallDetector()
        self.history: list[Any] = []
        self.n_replans = 0
        self.last_checkpoint_id: str | None = "cp-test-abc"
        self._outcome = outcome
        self._run_delay_s = run_delay_s
        self._respect_cancel = respect_cancel
        self.resume_calls: list[str] = []

    async def run(self) -> SupervisorOutcome:
        if self._run_delay_s > 0:
            slept = 0.0
            tick = 0.05
            while slept < self._run_delay_s:
                if self._respect_cancel and self.cancel_token.is_cancelled():
                    return SupervisorOutcome(
                        outcome=FinalOutcome.CANCELLED,
                        final_message="cancelled by token",
                        final_checkpoint_id=self.last_checkpoint_id,
                        n_turns=int(self.stall_detector.n_turns),
                        n_replans=self.n_replans,
                        reason="cancelled by token",
                    )
                await asyncio.sleep(tick)
                slept += tick
        done = self._outcome is FinalOutcome.DONE
        return SupervisorOutcome(
            outcome=self._outcome,
            final_message=f"final:{self._outcome.value}",
            final_checkpoint_id=self.last_checkpoint_id,
            n_turns=int(self.stall_detector.n_turns),
            n_replans=self.n_replans,
            reason="",
            deliverable="completed result" if done else "",
            delivery_manifest=(
                {
                    "state": "complete",
                    "final": True,
                    "artifacts": [{"kind": "text", "status": "ready"}],
                }
                if done
                else None
            ),
        )

    async def resume_from_checkpoint(self, checkpoint_id: str) -> _FakeSupervisor:
        self.resume_calls.append(checkpoint_id)
        return self


def _make_runtime(*, org: _Org | None = None) -> MagicMock:
    """Minimal runtime mock satisfying the protocols the service touches."""

    rt = MagicMock()
    rt.get_org = MagicMock(return_value=org if org is not None else _Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())

    async def _async_cancel(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"cancelled_roots": ["root1"]}

    rt.cancel_user_command = _async_cancel
    return rt


def _make_service(
    *,
    org: _Org | None = None,
    supervisors: list[_FakeSupervisor] | None = None,
) -> tuple[OrgCommandService, list[_FakeSupervisor]]:
    """Build a service whose ``_supervisor_factory`` returns the next stub."""

    used: list[_FakeSupervisor] = []
    queue = list(supervisors or [_FakeSupervisor()])

    def _factory(
        *,
        org_id: str,
        command_id: str,
        root_node_id: str,
        task: str,
        executor: Any = None,
        brain: Any = None,
        stream: Any = None,
        checkpointer: Any = None,
        cancel_token: Any = None,
    ) -> Any:
        sup = queue.pop(0) if queue else _FakeSupervisor()
        used.append(sup)
        return sup

    svc = OrgCommandService(
        _make_runtime(org=org),
        supervisor_factory=_factory,
    )
    return svc, used


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_runs_supervisor_and_marks_done() -> None:
    svc, used = _make_service(supervisors=[_FakeSupervisor(outcome=FinalOutcome.DONE)])
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    cid = res["command_id"]
    assert res["status"] == "running"

    task = svc._inflight_tasks.get(cid)
    assert task is not None
    await asyncio.wait_for(task, timeout=1.0)

    status = svc.get_status("o1", cid)
    assert status is not None
    assert status["status"] == "done"
    assert status["phase"] == "done"
    assert status["n_turns"] == 3
    assert status["replan_count"] == 0
    assert status["last_checkpoint_id"] == "cp-test-abc"
    assert status["event_ref"] == "supervisor_done"
    assert len(used) == 1


# ---------------------------------------------------------------------------
# 2. replace_existing=true cooperative cancel + relaunch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_existing_cooperative_cancels_and_relaunches() -> None:
    slow = _FakeSupervisor(outcome=FinalOutcome.DONE, run_delay_s=5.0)
    fresh = _FakeSupervisor(outcome=FinalOutcome.DONE)
    svc, used = _make_service(supervisors=[slow, fresh])

    res1 = await svc.submit(OrgCommandRequest(org_id="o1", content="first"))
    cid1 = res1["command_id"]
    await asyncio.sleep(0.05)
    assert slow in used

    res2 = await svc.submit(OrgCommandRequest(org_id="o1", content="second", replace_existing=True))
    cid2 = res2["command_id"]
    assert cid2 != cid1
    assert res2["status"] == "running"

    assert slow.cancel_token.is_cancelled()

    t2 = svc._inflight_tasks.get(cid2)
    assert t2 is not None
    await asyncio.wait_for(t2, timeout=1.5)

    s2 = svc.get_status("o1", cid2)
    assert s2 is not None
    assert s2["status"] == "done"

    s1 = svc.get_status("o1", cid1)
    assert s1 is not None
    assert s1["status"] in {"cancelled", "done"}


# ---------------------------------------------------------------------------
# 3. continue_previous=true with no checkpoint -> content concat fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continue_previous_falls_back_to_content_concat_without_checkpoint() -> None:
    first = _FakeSupervisor(outcome=FinalOutcome.DONE)
    first.last_checkpoint_id = None
    second = _FakeSupervisor(outcome=FinalOutcome.DONE)
    svc, used = _make_service(supervisors=[first, second])

    res1 = await svc.submit(OrgCommandRequest(org_id="o1", content="first part"))
    cid1 = res1["command_id"]
    t1 = svc._inflight_tasks.get(cid1)
    assert t1 is not None
    await asyncio.wait_for(t1, timeout=1.0)

    res2 = await svc.submit(
        OrgCommandRequest(
            org_id="o1",
            content="second part",
            continue_previous=True,
        )
    )
    assert res2["status"] == "running"
    cid2 = res2["command_id"]
    t2 = svc._inflight_tasks.get(cid2)
    assert t2 is not None
    await asyncio.wait_for(t2, timeout=1.0)

    assert second.resume_calls == []
    assert len(used) == 2


# ---------------------------------------------------------------------------
# 4. dup_cancel is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_dup_cancel() -> None:
    slow = _FakeSupervisor(outcome=FinalOutcome.DONE, run_delay_s=10.0)
    svc, _used = _make_service(supervisors=[slow])

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="long"))
    cid = res["command_id"]
    await asyncio.sleep(0.05)

    out1 = await svc.cancel("o1", cid, reason="user_cancel")
    assert out1 is not None
    assert out1["ok"] is True
    assert out1.get("command_id") == cid

    out2 = await svc.cancel("o1", cid, reason="user_cancel")
    assert out2 is not None
    assert out2["ok"] is True
    assert out2.get("already_done") is True


# ---------------------------------------------------------------------------
# 5. cancel_all_for_org cancels every in-flight supervisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_for_org_cancels_every_supervisor() -> None:
    org = _Org(roots=("root1", "root2"))
    a = _FakeSupervisor(outcome=FinalOutcome.DONE, run_delay_s=10.0)
    b = _FakeSupervisor(outcome=FinalOutcome.DONE, run_delay_s=10.0)
    svc, _used = _make_service(org=org, supervisors=[a, b])

    r1 = await svc.submit(OrgCommandRequest(org_id="o1", target_node_id="root1", content="A"))
    r2 = await svc.submit(OrgCommandRequest(org_id="o1", target_node_id="root2", content="B"))
    await asyncio.sleep(0.05)
    cid1, cid2 = r1["command_id"], r2["command_id"]
    assert {cid1, cid2} <= set(svc._inflight_by_org.get("o1", set()))
    queues = {
        cid: svc.subscribe_summary(cid, surface="desktop_chat", target=f"chat-{cid}")
        for cid in (cid1, cid2)
    }

    cancelled = await svc.cancel_all_for_org("o1", reason="stop_org")
    assert set(cancelled) == {cid1, cid2}

    for sup in (a, b):
        assert sup.cancel_token.is_cancelled()

    for cid in (cid1, cid2):
        task = svc._inflight_tasks.get(cid)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=1.5)
            except (asyncio.CancelledError, TimeoutError):
                pass

    for cid in (cid1, cid2):
        outcome = svc._command_outcomes.get(cid) or {}
        assert outcome.get("cancelled_by") == "stop_org"
        terminal = await asyncio.wait_for(queues[cid].get(), timeout=0.5)
        assert terminal["type"] == "org_command_done"
        assert terminal["error"] == "组织已停止，当前任务已取消。"
