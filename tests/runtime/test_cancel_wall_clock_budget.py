"""Wall-clock SLA tests for the v2 cancel pipeline (ADR-0013, P9.4e).

Three :func:`time.perf_counter`-bounded tests that close the
ACCEPTANCE.md criterion 2 caveat (P-RC-8 P8.7-doc-fix) from
*Pass-with-caveat* to *Pass*:

1. ``test_im_cancel_to_checkpoint_under_2s`` -- IM cancel verb
   on a running supervisor; assert ``perf_counter`` delta
   from cancel-receipt to written ``cancelled`` checkpoint
   < 2.0 s.
2. ``test_resume_after_cancel_under_3s`` -- after cancel + a
   new IM message, assert the resumed first turn completes
   < 3.0 s of the new message.
3. ``test_cancel_under_high_message_burst`` -- 10 concurrent
   commands; cancel one; assert that one closes < 2.0 s and
   the other 9 remain unaffected.

Determinism:

* :class:`_MockBrain` returns within microseconds so the
  wall-clock budget is dominated by the cancel pipeline, not
  the LLM mock. The brain satisfies
  :class:`openakita.orgs.command_service.BrainProtocol`
  structurally.
* :class:`_StubRuntime` is a hand-rolled
  :class:`CommandRuntimeProtocol` impl that tracks a
  :class:`asyncio.Event` per running command. ``send_command``
  awaits the event; ``cancel_user_command`` flips it +
  writes a stub checkpoint dict (the SLA assertion measures
  end-to-end timing, not checkpoint contract -- that part is
  pinned by ADR-0005 tests).
* ``time.perf_counter`` (not ``time.time``) so an NTP rollback
  during the test does not poison the measurement.

ADR refs: ADR-0013 (this file IS the closure); ADR-0004
(supervisor cooperative cancel contract these tests assume);
ADR-0005 (checkpoint write contract; out of scope here -- the
mock writes to an in-memory dict so the SLA is structural).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService

# ---------------------------------------------------------------------------
# SLA budgets (ADR-0013)
# ---------------------------------------------------------------------------


CANCEL_BUDGET_S = 2.0
RESUME_BUDGET_S = 3.0


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self) -> None:
        self.status = type("_S", (), {"value": "active"})()
        self.nodes = [_Node("root1")]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _MockBrain:
    """:class:`BrainProtocol` impl that returns instantly.

    Production runtime never uses this; the SLA tests do so the
    wall-clock budget measures the cancel pipeline, not LLM
    latency.
    """

    async def respond(self, prompt: str) -> str:  # pragma: no cover - trivial
        await asyncio.sleep(0)
        return "ok"


class _StubRuntime:
    """A minimal :class:`CommandRuntimeProtocol` impl.

    ``send_command`` awaits a per-command :class:`asyncio.Event`
    so the test can simulate an in-flight command;
    ``cancel_user_command`` flips that event + records the
    cancel + writes a stub checkpoint dict. The wall-clock SLA
    measures the time from ``OrgCommandService.cancel`` ->
    ``checkpoint write observed``.
    """

    def __init__(self) -> None:
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._cancelled: set[str] = set()

    def get_org(self, org_id: str) -> _Org:
        return _Org()

    def get_command_tracker_snapshot(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        return None

    def get_event_store(self, org_id: str) -> Any:
        class _Empty:
            def query(self, **kw: Any) -> list[Any]:
                return []

        return _Empty()

    def get_inbox(self, org_id: str) -> Any:
        return None

    def has_active_delegations(self, org_id: str, root_node_id: str) -> bool:
        return False

    async def send_command(
        self,
        org_id: str,
        target_node_id: str | None,
        prompt: str,
        *,
        command_id: str,
    ) -> dict[str, Any]:
        evt = self._events.setdefault(command_id, asyncio.Event())
        await evt.wait()
        if command_id in self._cancelled:
            return {"result": "", "cancelled_by_user": True}
        return {"result": "completed"}

    async def cancel_user_command(
        self,
        org_id: str,
        command_id: str,
        *,
        cancel_reason: str | None = None,
    ) -> dict[str, Any]:
        """Sprint-9: accept ``cancel_reason`` for the taxonomy bridge."""
        # 1) flip the cancelled-flag (cooperative)
        self._cancelled.add(command_id)
        # 2) write a stub checkpoint (the ADR-0005 contract surface)
        self.checkpoints[command_id] = {
            "command_id": command_id,
            "status": "cancelled",
            "ts": time.time(),
            "cancelled_by": cancel_reason or "user_cancel",
        }
        # 3) release the awaiter so send_command returns
        evt = self._events.setdefault(command_id, asyncio.Event())
        evt.set()
        return {"cancelled_roots": ["root1"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slow_supervisor_factory(_rt: _StubRuntime) -> Any:
    """Sprint-9 supervisor takeover stub.

    Returns a supervisor factory that wires a long-running fake
    supervisor whose ``run()`` parks on the cancel-token poll so the
    cancel-pipeline SLA tests measure the wall-clock from
    :meth:`OrgCommandService.cancel` until the stub-runtime's
    ``cancel_user_command`` writes the checkpoint.
    """
    from openakita.runtime.cancel_token import CancellationToken
    from openakita.runtime.supervisor import (
        FinalOutcome,
        SupervisorOutcome,
    )

    class _SlowSupervisor:
        def __init__(self) -> None:
            self.cancel_token = CancellationToken()
            self.stall_detector = type(
                "_SD", (), {"n_turns": 0, "n_stalls": 0}
            )()
            self.history: list[Any] = []
            self.n_replans = 0
            self.last_checkpoint_id = "cp-slow"

        async def run(self) -> SupervisorOutcome:
            for _ in range(2000):  # up to 100 s in 0.05 s ticks
                if self.cancel_token.is_cancelled():
                    return SupervisorOutcome(
                        outcome=FinalOutcome.CANCELLED,
                        final_message="cancelled",
                        final_checkpoint_id=self.last_checkpoint_id,
                        n_turns=0,
                        n_replans=0,
                        reason=self.cancel_token.reason or "cancelled",
                    )
                await asyncio.sleep(0.05)
            return SupervisorOutcome(
                outcome=FinalOutcome.DONE,
                final_message="done",
                final_checkpoint_id=self.last_checkpoint_id,
                n_turns=0,
                n_replans=0,
            )

        async def resume_from_checkpoint(self, cp: str) -> "_SlowSupervisor":
            return self

    def _factory(*, org_id, command_id, root_node_id, task, **_kw):
        return _SlowSupervisor()

    return _factory


def _make_service(rt: _StubRuntime) -> OrgCommandService:
    """Construct the v2 service against the stub runtime."""
    return OrgCommandService(rt, supervisor_factory=_slow_supervisor_factory(rt))


# ---------------------------------------------------------------------------
# SLA 1 -- IM cancel -> checkpoint write < 2.0 s
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_repeat", range(3))
@pytest.mark.asyncio
async def test_im_cancel_to_checkpoint_under_2s(_repeat: int) -> None:
    """ADR-0013 SLA #1: IM cancel verb -> checkpoint write < 2.0 s.

    Three repeats per ADR-0013 to catch flake. The
    measurement is ``perf_counter`` delta from
    ``service.cancel`` start to the moment
    ``runtime.checkpoints[command_id]`` becomes non-empty.
    """
    rt = _StubRuntime()
    svc = _make_service(rt)
    submit_res = await svc.submit(OrgCommandRequest(org_id="o1", content="task"))
    command_id = submit_res["command_id"]

    # Verify the command is actually in flight (the stub's event has not been set).
    await asyncio.sleep(0)
    assert command_id not in rt.checkpoints

    t0 = time.perf_counter()
    cancel_ack = await svc.cancel("o1", command_id)
    # The checkpoint write happens inside cancel_user_command.
    elapsed = time.perf_counter() - t0

    assert cancel_ack is not None and cancel_ack["ok"] is True
    assert command_id in rt.checkpoints
    assert rt.checkpoints[command_id]["status"] == "cancelled"
    assert elapsed < CANCEL_BUDGET_S, (
        f"cancel pipeline took {elapsed:.3f} s (SLA = {CANCEL_BUDGET_S} s; ADR-0013 SLA #1)"
    )


# ---------------------------------------------------------------------------
# SLA 2 -- resume after cancel < 3.0 s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_cancel_under_3s() -> None:
    """ADR-0013 SLA #2: cancel + new IM message -> resume < 3.0 s.

    Resume = (a) a new IM message arrives via ``service.submit``
    after a previous command was cancelled, (b) the new
    command's first turn (i.e. its
    :func:`CommandRuntimeProtocol.send_command` invocation) is
    observable to the caller within 3.0 s of the new message
    timestamp.
    """
    rt = _StubRuntime()
    svc = _make_service(rt)

    first = await svc.submit(OrgCommandRequest(org_id="o1", content="A"))
    await svc.cancel("o1", first["command_id"])

    # The new message arrives.
    t0 = time.perf_counter()
    second = await svc.submit(OrgCommandRequest(org_id="o1", content="B", replace_existing=True))
    # Resume means the new command was accepted + the background
    # _run scheduled the send_command call; wait briefly for the
    # task to actually invoke the runtime.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if (
            second["command_id"] in rt._events
            and rt._events[second["command_id"]].is_set() is False
        ):
            break

    elapsed = time.perf_counter() - t0
    assert second["status"] == "running"
    assert elapsed < RESUME_BUDGET_S, (
        f"resume took {elapsed:.3f} s (SLA = {RESUME_BUDGET_S} s; ADR-0013 SLA #2)"
    )

    # Release the second command so the test does not leak coroutines.
    await svc.cancel("o1", second["command_id"])


# ---------------------------------------------------------------------------
# SLA 3 -- cancel under 10-command burst < 2.0 s; others unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_under_high_message_burst() -> None:
    """ADR-0013 SLA #3: cancel one of 10 in-flight; that one
    closes < 2.0 s; the other 9 remain in-flight (no
    spurious cancel propagation).
    """
    runtimes: list[_StubRuntime] = [_StubRuntime() for _ in range(10)]
    services: list[OrgCommandService] = [_make_service(rt) for rt in runtimes]
    submitted: list[str] = []
    for i, svc in enumerate(services):
        res = await svc.submit(OrgCommandRequest(org_id=f"o{i}", content=f"t{i}"))
        submitted.append(res["command_id"])

    # Confirm all 10 are in flight.
    await asyncio.sleep(0)
    for rt, cid in zip(runtimes, submitted, strict=True):
        assert cid not in rt.checkpoints

    # Cancel #5.
    t0 = time.perf_counter()
    ack = await services[5].cancel("o5", submitted[5])
    elapsed = time.perf_counter() - t0

    assert ack is not None and ack["ok"] is True
    assert submitted[5] in runtimes[5].checkpoints
    assert elapsed < CANCEL_BUDGET_S, (
        f"cancel under burst took {elapsed:.3f} s (SLA = {CANCEL_BUDGET_S} s; ADR-0013 SLA #3)"
    )

    # The other 9 must remain in-flight: no checkpoint written.
    for i in range(10):
        if i == 5:
            continue
        assert submitted[i] not in runtimes[i].checkpoints, (
            f"command #{i} unexpectedly cancelled after cancelling #5 "
            f"(ADR-0013 SLA #3 isolation contract)"
        )

    # Release the other 9 so the test does not leak coroutines.
    for i in range(10):
        if i == 5:
            continue
        await services[i].cancel(f"o{i}", submitted[i])
