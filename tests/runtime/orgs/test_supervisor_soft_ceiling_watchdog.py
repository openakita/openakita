"""P2 soft-landing watchdog for ``OrgCommandService``.

Exploratory test report (Domain 2, 问题 2): the cooperative soft budget
(``supervisor_hard_ceiling_s * orgs_supervisor_soft_ceiling_ratio``) is only
re-checked at *turn boundaries* inside ``Supervisor._inner_loop``. When a single
orchestration / node brain call is wedged (a provider 403 -> failover-retry
storm with no cooperative cancel point), no turn boundary is ever reached, so the
graceful ``OUT_OF_TURNS`` landing never fires and the run drifts all the way to
the hard ceiling, where it is force-cancelled -- frequently into a bare,
output-less ``error``.

:meth:`OrgCommandService._run_supervisor_with_hard_ceiling` now starts a
wall-clock soft-landing watchdog *decoupled* from turn boundaries. Once the soft
budget elapses it interrupts the wedged run and salvages a best-effort
deliverable, so the soft landing lands BEFORE the hard ceiling. This file pins
that contract.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
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
    n_turns = 0
    n_stalls = 0


class _WedgedSupervisor:
    """``run()`` wedged inside a non-cooperative await (mirrors a hung provider
    call: no turn boundary is ever reached).

    Like the real :class:`Supervisor`, it catches ``asyncio.CancelledError`` and,
    when the cooperative token was fired first, unwinds to a graceful CANCELLED
    outcome. Crucially -- matching :meth:`Supervisor._terminate` for CANCELLED --
    that outcome carries NO deliverable; salvaging the best-effort output is the
    watchdog path's job.
    """

    def __init__(self, *, deliverable: str = "") -> None:
        self.cancel_token = CancellationToken()
        self.stall_detector = _FakeStallDetector()
        self.stall_detector.n_turns = 4
        self.history: list[Any] = []
        self.n_replans = 1
        self.last_checkpoint_id: str | None = "cp-wedged"
        self.resume_calls: list[str] = []
        self.run_started = asyncio.Event()
        self._deliverable = deliverable

    async def run(self) -> SupervisorOutcome:
        self.run_started.set()
        try:
            await asyncio.sleep(3600)  # wedged: no cooperative check reachable
        except asyncio.CancelledError:
            if self.cancel_token.is_cancelled():
                return SupervisorOutcome(
                    outcome=FinalOutcome.CANCELLED,
                    final_message="cancelled by token",
                    final_checkpoint_id=self.last_checkpoint_id,
                    n_turns=self.stall_detector.n_turns,
                    n_replans=self.n_replans,
                    reason=self.cancel_token.reason or "",
                    deliverable="",  # CANCELLED never salvages in _terminate
                )
            raise
        raise AssertionError("wedged supervisor should never return naturally")

    def best_effort_deliverable(self) -> str:
        return self._deliverable

    async def resume_from_checkpoint(self, checkpoint_id: str) -> _WedgedSupervisor:
        self.resume_calls.append(checkpoint_id)
        return self


class _QuickDoneSupervisor:
    """Finishes DONE almost immediately -- exercises the no-false-trigger path."""

    def __init__(self) -> None:
        self.cancel_token = CancellationToken()
        self.stall_detector = _FakeStallDetector()
        self.history: list[Any] = []
        self.n_replans = 0
        self.last_checkpoint_id = "cp-quick"
        self.resume_calls: list[str] = []

    async def run(self) -> SupervisorOutcome:
        await asyncio.sleep(0)
        return SupervisorOutcome(
            outcome=FinalOutcome.DONE,
            final_message="all done",
            final_checkpoint_id=self.last_checkpoint_id,
            n_turns=1,
            n_replans=0,
            reason="",
            deliverable="the finished integrated report body",
        )

    def best_effort_deliverable(self) -> str:
        return "the finished integrated report body"

    async def resume_from_checkpoint(self, checkpoint_id: str) -> Any:
        self.resume_calls.append(checkpoint_id)
        return self


def _make_runtime(*, org: _Org | None = None) -> MagicMock:
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


def _make_service(*, supervisor: Any) -> OrgCommandService:
    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        return supervisor

    return OrgCommandService(_make_runtime(), supervisor_factory=_factory)


def _enable_fast_soft_landing(monkeypatch, *, ceiling: int = 3) -> None:
    """Configure a soft landing that fires well before the hard ceiling.

    Only the real Settings fields ``supervisor_hard_ceiling_s`` and
    ``orgs_supervisor_soft_ceiling_ratio`` are set; the watchdog grace fraction
    uses its built-in default (0.5). With ``ceiling=3`` and ``ratio=0.1`` the
    soft budget is 0.3s and the watchdog fires at ~1.65s -- comfortably before
    the 3s hard ceiling, so tests exercise the soft path without long waits.
    """
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", ceiling, raising=False)
    monkeypatch.setattr(
        settings, "orgs_supervisor_soft_ceiling_ratio", 0.1, raising=False
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_landing_fires_before_hard_ceiling_and_salvages_partial(
    monkeypatch,
) -> None:
    """A wedged run past the soft budget -> soft landing -> ``partial`` with output.

    The soft watchdog (fires ~1s) interrupts the wedged run long before the hard
    ceiling (10s) would. Because a usable best-effort deliverable survived, the
    command degrades to ``partial`` (NOT ``error``), attributed to the soft
    landing rather than the hard ceiling.
    """
    _enable_fast_soft_landing(monkeypatch, ceiling=3)
    salvage = "阶段性市场调研摘要：" + ("关键结论与数据支撑。" * 40)
    supervisor = _WedgedSupervisor(deliverable=salvage)
    svc = _make_service(supervisor=supervisor)
    # No clean root-integration file on disk -> best-effort deliverable path.
    monkeypatch.setattr(svc, "_root_disk_deliverable", lambda _c: None)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="long task"))
    cid = res["command_id"]
    await asyncio.wait_for(supervisor.run_started.wait(), timeout=1.0)

    task = svc._inflight_tasks.get(cid)
    assert task is not None
    # Soft fires ~1s; give generous slack but stay well under the 10s ceiling so
    # a regression that waits for the hard ceiling would still be caught by the
    # test-level timeout.
    await asyncio.wait_for(task, timeout=6.0)

    # Soft landing attribution -- NOT hard ceiling.
    assert supervisor.cancel_token.is_cancelled()
    assert supervisor.cancel_token.reason == "soft_ceiling"
    oc = svc._command_outcomes.get(cid) or {}
    assert oc.get("cancelled_by") == "soft_ceiling"
    assert oc.get("reason") == "supervisor_soft_ceiling_soft_landing"

    cmd = svc._commands[cid]
    assert cmd["status"] == "partial", "delivered soft landing must not be error"
    assert cmd["status"] != "cancelled"
    assert cmd["result"]["partial"] is True
    assert cmd["result"]["outcome"] == "partial_delivery"
    assert cmd["result"]["degraded_reason"] == "wall_clock_soft_landing"
    assert salvage in cmd["result"]["deliverable"]
    assert cmd["error"] is None

    # No slot / task leak.
    assert ("o1", "root1") not in svc._running_by_root
    assert cid not in svc._active_supervisors
    assert cid not in svc._inflight_tasks


@pytest.mark.asyncio
async def test_soft_landing_with_no_output_stays_error(monkeypatch) -> None:
    """Soft landing must not fabricate output: an empty salvage -> honest ``error``."""
    _enable_fast_soft_landing(monkeypatch, ceiling=3)
    supervisor = _WedgedSupervisor(deliverable="")
    svc = _make_service(supervisor=supervisor)
    monkeypatch.setattr(svc, "_root_disk_deliverable", lambda _c: None)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="long task"))
    cid = res["command_id"]
    await asyncio.wait_for(supervisor.run_started.wait(), timeout=1.0)
    task = svc._inflight_tasks.get(cid)
    assert task is not None
    await asyncio.wait_for(task, timeout=6.0)

    assert supervisor.cancel_token.reason == "soft_ceiling"
    cmd = svc._commands[cid]
    assert cmd["status"] == "error"
    assert cmd["result"]["partial"] is False


@pytest.mark.asyncio
async def test_soft_landing_not_triggered_for_fast_task(monkeypatch) -> None:
    """A task that finishes within the soft budget is untouched (no false trigger)."""
    _enable_fast_soft_landing(monkeypatch, ceiling=3)
    supervisor = _QuickDoneSupervisor()
    svc = _make_service(supervisor=supervisor)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="quick task"))
    cid = res["command_id"]
    task = svc._inflight_tasks.get(cid)
    assert task is not None
    await asyncio.wait_for(task, timeout=2.0)

    # Happy path: watchdog never fired -> token untouched, status done.
    assert not supervisor.cancel_token.is_cancelled()
    status = svc.get_status("o1", cid)
    assert status is not None
    assert status["status"] == "done"
    oc = svc._command_outcomes.get(cid) or {}
    assert oc.get("cancelled_by") != "soft_ceiling"

    # No dangling watchdog timer: give the event loop a tick, then confirm no
    # soft-watchdog task is still pending.
    await asyncio.sleep(0)
    lingering = [
        t
        for t in asyncio.all_tasks()
        if "soft-watchdog" in (t.get_name() or "") and not t.done()
    ]
    assert not lingering, f"soft-landing watchdog leaked: {lingering}"


@pytest.mark.asyncio
async def test_soft_landing_disabled_when_ratio_zero(monkeypatch) -> None:
    """``orgs_supervisor_soft_ceiling_ratio = 0`` disables the watchdog entirely.

    With soft landing off, a wedged run must fall through to the hard ceiling
    (the pre-existing backstop), attributed to ``hard_ceiling``.
    """
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", 2, raising=False)
    monkeypatch.setattr(
        settings, "orgs_supervisor_soft_ceiling_ratio", 0.0, raising=False
    )
    supervisor = _WedgedSupervisor(deliverable="salvage body long enough" * 10)
    svc = _make_service(supervisor=supervisor)
    monkeypatch.setattr(svc, "_root_disk_deliverable", lambda _c: None)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="long task"))
    cid = res["command_id"]
    await asyncio.wait_for(supervisor.run_started.wait(), timeout=1.0)
    task = svc._inflight_tasks.get(cid)
    assert task is not None
    await asyncio.wait_for(task, timeout=6.0)

    # Hard ceiling governed (no soft landing): attribution is hard_ceiling.
    oc = svc._command_outcomes.get(cid) or {}
    assert oc.get("cancelled_by") == "hard_ceiling"
    assert supervisor.cancel_token.reason == "hard_ceiling"
    # Slot released.
    assert cid not in svc._inflight_tasks
