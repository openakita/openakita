"""v22 RCA RC-6: ``orgs_cancel_drain_budget_s`` settings hook.

Sprint-9 hardcoded ``_cooperative_cancel(timeout=5.0)`` so the graceful
drain window could not be tuned per deployment. v22 RCA RC-6 lifts it
into :attr:`Settings.orgs_cancel_drain_budget_s` (default 8s) and routes
``OrgCommandService._cooperative_cancel`` through
:meth:`OrgCommandService._cancel_drain_budget_s` so operators can shrink
the window on fast LLM stacks or grow it for slow ones without touching
code.

This file pins two contracts:

1. The setting default is 8 (the value the RCA recommended).
2. Calling ``OrgCommandService.cancel`` against a sleep-forever
   supervisor with ``orgs_cancel_drain_budget_s=1`` enters the
   force-cancel branch within ~1.5s -- proving the cancel path actually
   consulted the setting instead of holding onto a 5s constant.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService
from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.supervisor import FinalOutcome, SupervisorOutcome

# ---------------------------------------------------------------------------
# Test doubles -- mirror ``tests/runtime/orgs/test_supervisor_hard_ceiling.py``
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


class _SleepForeverSupervisor:
    """Stand-in supervisor that ignores ``cancel_token`` to force the
    drain-budget timeout branch.

    The hard-ceiling test cooperates with the token; here we want to
    pin the *opposite* path: even when the supervisor refuses to wind
    down, the cancel API must respect the configured drain budget and
    fall back to force-cancel.
    """

    def __init__(self) -> None:
        self.cancel_token = CancellationToken()
        self.stall_detector = _FakeStallDetector()
        self.history: list[Any] = []
        self.n_replans = 0
        self.last_checkpoint_id: str | None = "cp-sleep-forever"
        self.resume_calls: list[str] = []
        self.run_started = asyncio.Event()

    async def run(self) -> SupervisorOutcome:
        self.run_started.set()
        # Deliberately ignore cancel_token: we want the drain budget
        # to time out so the test can prove the budget value was read
        # from the setting.
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            # Force-cancel reached us; record a synthetic cancelled
            # outcome so the wrapping ``_schedule_run`` finally block
            # can clean up.
            return SupervisorOutcome(
                outcome=FinalOutcome.CANCELLED,
                final_message="force-cancelled",
                final_checkpoint_id=self.last_checkpoint_id,
                n_turns=0,
                n_replans=0,
                reason="force",
            )
        return SupervisorOutcome(
            outcome=FinalOutcome.DONE,
            final_message="impossible",
            final_checkpoint_id=self.last_checkpoint_id,
            n_turns=0,
            n_replans=0,
            reason="",
        )

    async def resume_from_checkpoint(self, checkpoint_id: str) -> _SleepForeverSupervisor:
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


def _make_service(*, supervisor: _SleepForeverSupervisor) -> OrgCommandService:
    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str,
                 executor: Any = None, brain: Any = None, stream: Any = None,
                 checkpointer: Any = None, cancel_token: Any = None) -> Any:
        return supervisor

    return OrgCommandService(_make_runtime(), supervisor_factory=_factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_orgs_cancel_drain_budget_is_eight() -> None:
    """v22 RCA RC-6: the default drain budget is 8 seconds."""
    assert settings.orgs_cancel_drain_budget_s == 8


def test_cancel_drain_budget_helper_reads_settings(monkeypatch) -> None:
    """``_cancel_drain_budget_s`` must defer to the settings field."""
    monkeypatch.setattr(settings, "orgs_cancel_drain_budget_s", 3, raising=False)

    assert OrgCommandService._cancel_drain_budget_s() == 3


@pytest.mark.asyncio
async def test_cancel_respects_drain_budget_setting(monkeypatch) -> None:
    """A 1s drain budget must enter force-cancel within ~1.5s."""
    # Keep the hard ceiling high so we are isolating the cancel path.
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", 60, raising=False)
    monkeypatch.setattr(settings, "orgs_cancel_drain_budget_s", 1, raising=False)

    supervisor = _SleepForeverSupervisor()
    svc = _make_service(supervisor=supervisor)

    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    cid = res["command_id"]
    assert res["status"] == "running"
    await asyncio.wait_for(supervisor.run_started.wait(), timeout=1.0)
    assert cid in svc._active_supervisors

    started = time.monotonic()
    await svc.cancel(org_id="o1", command_id=cid, reason="user_cancel")
    elapsed = time.monotonic() - started

    # The cancel must have returned within ~1.5s: ``effective_timeout``
    # = 1s + a small wait_for overhead. Anything longer means the
    # function did NOT read the setting (the regression we are
    # guarding against was a hardcoded 5.0s).
    assert elapsed <= 1.5, f"cancel returned after {elapsed:.2f}s; budget setting ignored"

    # ``cancel_token.cancel`` must have fired (cooperative path tried
    # first), and the task should be on its way out via force-cancel.
    assert supervisor.cancel_token.is_cancelled()
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        # Give the force-cancel a moment to complete bookkeeping.
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass
    assert ("o1", "root1") not in svc._running_by_root
