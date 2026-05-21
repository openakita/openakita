"""Contract suite for v2 OrgRuntime (P-RC-9 P9.6gamma).

Pins the public surface of
:class:`openakita.runtime.orgs.runtime.OrgRuntime` and the
~10 Protocol contracts it composes against the seven
sibling managers shipped in P9.6alpha-beta. Mirror the
P9.5d :class:`OrgManager` contract suite layout (16
cases) but covers the larger OrgRuntime surface
(~25 cases per the P9.6gamma brief).

This file lands in two commits:
* gamma-2a (this commit): 13 cases -- the 6
  CommandRuntimeProtocol method cases (10) + the 4
  Protocol surface cases (4 here -- new Protocol set) +
  the 3 OrgRuntime composition smokes (3 here).
* gamma-2b (next commit): 12 cases -- 4 concurrency, 1
  AgentBuilderProtocol Protocol surface, 1 OrgRuntime +
  OrgCommandService integration, 2 wall-clock SLA
  (perf_counter per ADR-0013 NIT-I-1 lesson).

The cases are stateless: each test constructs a fresh
:class:`OrgRuntime` against an in-process test-double
:class:`_CmdService` + :class:`_Lookup` so the suite stays
isolated (no cross-test bleed; no real persistence /
network / IM).
"""

from __future__ import annotations

import asyncio
from typing import Any

from openakita.runtime.orgs._runtime_agent_pipeline import (
    AgentBuilderProtocol,
    AgentCache,
    AgentSpec,
)
from openakita.runtime.orgs._runtime_dispatch import (
    TRACKER_CANCELLED,
    TRACKER_RUNNING,
    CommandDispatchManager,
)
from openakita.runtime.orgs.command_service import CommandRuntimeProtocol
from openakita.runtime.orgs.runtime import (
    EventBusProtocol,
    NodeLifecycleProtocol,
    OrgRuntime,
    RuntimeStateProtocol,
    _InMemoryEventBus,
    _InMemoryNodeLifecycle,
    _InMemoryRuntimeState,
)

# ---------------------------------------------------------------------------
# Shared test doubles -- minimal duck-typed shims; mirrors the parity harness.
# ---------------------------------------------------------------------------


class _Org:
    def __init__(self, org_id: str, *, state: str = "active") -> None:
        self.id = org_id
        self.state = state
        self.workspace_dir = None
        self.nodes = {"n1": type("N", (), {"role": "eng", "persona": "engineer"})()}


class _Lookup:
    def __init__(self, *, present: bool = True) -> None:
        self._present = present

    def get_org(self, org_id: str) -> Any:
        return _Org(org_id) if self._present else None


class _CmdService:
    """Async submit + cancel stub (CommandRuntimeProtocol facing)."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, str, str]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def submit(self, *, org_id: str, target_node_id: str, content: str) -> dict[str, Any]:
        self.submitted.append((org_id, target_node_id, content))
        return {"command_id": f"cmd_{len(self.submitted)}", "status": "submitted"}

    async def cancel(self, org_id: str, command_id: str) -> None:
        self.cancelled.append((org_id, command_id))


def _make_runtime(
    *,
    lookup_present: bool = True,
    command_service: _CmdService | None = None,
    event_bus: _InMemoryEventBus | None = None,
) -> tuple[OrgRuntime, _CmdService, _InMemoryEventBus]:
    cs = command_service if command_service is not None else _CmdService()
    bus = event_bus if event_bus is not None else _InMemoryEventBus()
    rt = OrgRuntime(
        lookup=_Lookup(present=lookup_present),
        persistence=object(),
        lifecycle_emitter=object(),
        command_service=cs,
        event_bus=bus,
    )
    return rt, cs, bus


# ===========================================================================
# Group A -- CommandRuntimeProtocol method cases (10 cases)
# ===========================================================================


def test_contract_send_command_happy() -> None:
    """case id: send_command.happy"""
    rt, cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "do it"))
    assert r["status"] == "submitted"
    assert r["org_id"] == "o1"
    assert r["node_id"] == "n1"
    assert r["command_id"] == "cmd_1"
    assert cs.submitted == [("o1", "n1", "do it")]


def test_contract_send_command_org_not_found() -> None:
    """case id: send_command.org_not_found"""
    rt, _cs, _bus = _make_runtime(lookup_present=False)
    r = asyncio.run(rt.send_command("nope", "n1", "x"))
    # v1 parity: error dict with reason ``org_not_found``.
    assert r == {"status": "error", "reason": "org_not_found", "org_id": "nope"}


def test_contract_cancel_user_command_running() -> None:
    """case id: cancel_user_command.running -> cancelled"""
    rt, cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "task"))
    cid = r["command_id"]
    out = asyncio.run(rt.cancel_user_command("o1", cid))
    assert out == {"ok": True, "command_id": cid, "cancelled": True}
    assert cs.cancelled == [("o1", cid)]
    snap = rt.get_command_tracker_snapshot("o1", cid)
    assert snap is not None and snap["state"] == TRACKER_CANCELLED


def test_contract_cancel_user_command_missing() -> None:
    """case id: cancel_user_command.missing -> None"""
    rt, _cs, _bus = _make_runtime()
    out = asyncio.run(rt.cancel_user_command("o1", "no_such_cmd"))
    assert out is None  # v1 parity: unknown command_id returns None


def test_contract_cancel_user_command_idempotent() -> None:
    """case id: cancel_user_command.already_done"""
    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "task"))
    cid = r["command_id"]
    asyncio.run(rt.cancel_user_command("o1", cid))
    again = asyncio.run(rt.cancel_user_command("o1", cid))
    assert again is not None
    assert again["already_done"] is True
    assert again["state"] == TRACKER_CANCELLED


def test_contract_has_active_delegations_no_chains() -> None:
    """case id: has_active_delegations.no_chains"""
    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "task"))
    # No chains opened yet -> no active delegations.
    assert rt.has_active_delegations("o1", "n1") is False
    rt._dispatch.register_chain("o1", r["command_id"], "ch_a")
    assert rt.has_active_delegations("o1", "n1") is True


def test_contract_get_command_tracker_snapshot_running() -> None:
    """case id: get_command_tracker_snapshot.running"""
    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "ping"))
    snap = rt.get_command_tracker_snapshot("o1", r["command_id"])
    assert snap is not None
    assert snap["state"] == TRACKER_RUNNING
    assert snap["root_node_id"] == "n1"
    assert snap["chain_count"] == 0
    assert snap["accepted_chain_count"] == 0


def test_contract_get_command_tracker_snapshot_missing() -> None:
    """case id: get_command_tracker_snapshot.missing"""
    rt, _cs, _bus = _make_runtime()
    assert rt.get_command_tracker_snapshot("o1", "no_such") is None


def test_contract_get_event_store_default() -> None:
    """case id: get_event_store.default -> lazy-mint when lookup resolves."""
    rt, _cs, _bus = _make_runtime()
    # Post-d6484ae3 (#5 SSE event-bus fix): on first access we lazy-mint
    # + cache an OrgEventStore for any org id the OrgLookupProtocol can
    # resolve, so mint-runtime orgs (POST /api/v2/orgs/from-template) get
    # /events + /stream wired without a separate register step. The same
    # call still returns ``None`` for genuinely unknown ids -- preserves
    # the B45 404 contract (test_b45_events_404_when_no_store).
    store = rt.get_event_store("o1")
    assert store is not None
    assert rt.get_event_store("o1") is store  # cached, not re-minted
    rt2, _cs2, _bus2 = _make_runtime(lookup_present=False)
    assert rt2.get_event_store("nope") is None


def test_contract_get_inbox_default() -> None:
    """case id: get_inbox.default -> None"""
    rt, _cs, _bus = _make_runtime()
    assert rt.get_inbox("o1") is None


# ===========================================================================
# Group B -- new Protocol surface cases (3 of 4 here; Agent Builder rides 2b)
# ===========================================================================


def test_contract_runtime_state_protocol_transitions() -> None:
    """case id: RuntimeStateProtocol.transition_org_state + get_org_state."""
    s: RuntimeStateProtocol = _InMemoryRuntimeState()
    assert s.get_org_state("o1") is None
    assert asyncio.run(s.transition_org_state("o1", "ACTIVE")) is True
    assert s.get_org_state("o1") == "ACTIVE"
    assert s.is_org_active("o1") is True
    assert asyncio.run(s.transition_org_state("o1", "STOPPED")) is True
    assert s.is_org_active("o1") is False


def test_contract_node_lifecycle_protocol_register_set_get() -> None:
    """case id: NodeLifecycleProtocol.register + set + get round trip."""
    state = _InMemoryRuntimeState()
    nl: NodeLifecycleProtocol = _InMemoryNodeLifecycle(state)
    nl.register_node("o1", "n1")
    # register_node defaults to IDLE.
    assert nl.get_node_status("o1", "n1") == "IDLE"
    asyncio.run(nl.set_node_status("o1", "n1", "BUSY"))
    assert nl.get_node_status("o1", "n1") == "BUSY"
    nl.deregister_node("o1", "n1")
    assert nl.get_node_status("o1", "n1") is None


def test_contract_event_bus_protocol_pubsub() -> None:
    """case id: EventBusProtocol.subscribe + emit + unsubscribe."""
    bus: EventBusProtocol = _InMemoryEventBus()
    received: list[dict[str, Any]] = []

    def handler(payload: dict[str, Any]) -> None:
        received.append(payload)

    bus.subscribe("e", handler)
    asyncio.run(bus.emit("e", {"k": "v"}))
    assert received == [{"k": "v"}]
    bus.unsubscribe("e", handler)
    asyncio.run(bus.emit("e", {"k": "v2"}))
    # No new events received after unsubscribe.
    assert received == [{"k": "v"}]


# ===========================================================================
# Group C -- AgentBuilderProtocol contract (1 case)
# ===========================================================================


class _RecBuilder:
    """Records build() calls; teardown() decrements live count."""

    def __init__(self) -> None:
        self.built: list[AgentSpec] = []
        self.torn_down: list[Any] = []

    def build(self, spec: AgentSpec) -> Any:
        self.built.append(spec)
        return type("A", (), {"spec": spec})()

    def teardown(self, agent: Any) -> None:
        self.torn_down.append(agent)


def test_contract_agent_builder_protocol_cache_round_trip() -> None:
    """case id: AgentBuilderProtocol.build + cache + teardown."""
    builder = _RecBuilder()
    # Builder satisfies the AgentBuilderProtocol runtime-checkable contract.
    assert isinstance(builder, AgentBuilderProtocol)
    cache = AgentCache(builder=builder)
    spec = AgentSpec(org_id="o1", node_id="n1", role="eng")
    a1 = cache.get_or_create(spec)
    # Second call returns cached instance + builder still called once.
    a2 = cache.get_or_create(spec)
    assert a1 is a2
    assert len(builder.built) == 1
    # Evict drops the entry + invokes teardown.
    assert cache.evict("o1", "n1") is True
    assert builder.torn_down == [a1]
    # evict on already-evicted -> False (idempotent).
    assert cache.evict("o1", "n1") is False


# ===========================================================================
# Group D -- OrgRuntime composition smokes (3 cases)
# ===========================================================================


def test_contract_org_runtime_implements_command_runtime_protocol() -> None:
    """case id: composition.isinstance.CommandRuntimeProtocol"""
    rt, _cs, _bus = _make_runtime()
    # The whole point of P9.6: closes the P9.4 dependency loop.
    assert isinstance(rt, CommandRuntimeProtocol)


def test_contract_org_runtime_default_backends_fall_back() -> None:
    """case id: composition.default_backends.fall_back"""
    rt = OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
    )
    # Default in-memory backends are constructed when DI omits them.
    assert isinstance(rt._state, RuntimeStateProtocol)
    assert isinstance(rt._node_lifecycle, NodeLifecycleProtocol)
    assert isinstance(rt._event_bus, EventBusProtocol)
    # Dispatch manager is also constructed by default.
    assert isinstance(rt._dispatch, CommandDispatchManager)


def test_contract_org_runtime_sibling_dispatch_wired() -> None:
    """case id: composition.sibling.dispatch_wired"""
    rt, cs, bus = _make_runtime()
    # The injected event_bus + command_service are routed through
    # the dispatch manager (verified by emitting a submit and
    # observing event + service call).
    received: list[dict[str, Any]] = []
    bus.subscribe("user_command_submitted", lambda p: received.append(p))
    asyncio.run(rt.send_command("o1", "n1", "x"))
    assert received and received[0]["org_id"] == "o1"
    assert cs.submitted and cs.submitted[0][0] == "o1"


# ===========================================================================
# Group E -- concurrency (4 cases)
# ===========================================================================


async def _dispatch_burst(rt: OrgRuntime, *, org_id: str, count: int) -> list[str]:
    """Fire ``count`` send_command calls concurrently; return command_ids."""

    async def one(i: int) -> str:
        r = await rt.send_command(org_id, f"n{i % 4}", f"task-{i}")
        return r["command_id"]

    return await asyncio.gather(*(one(i) for i in range(count)))


def test_contract_dispatch_4x25_unique_command_ids() -> None:
    """case id: concurrency.dispatch.100_unique_command_ids"""
    rt, _cs, _bus = _make_runtime()
    ids = asyncio.run(_dispatch_burst(rt, org_id="o1", count=100))
    # CommandRuntimeProtocol contract: every submit yields a
    # distinct command_id (v1 parity).
    assert len(ids) == 100
    assert len(set(ids)) == 100


def test_contract_concurrent_cancel_race_safe() -> None:
    """case id: concurrency.cancel.race_safe"""
    rt, _cs, _bus = _make_runtime()

    async def main() -> None:
        r = await rt.send_command("o1", "n1", "task")
        cid = r["command_id"]
        # Fire 8 concurrent cancels for the same id; only one
        # should yield ``cancelled=True`` -- the others see
        # ``already_done=True``.
        outs = await asyncio.gather(*(rt.cancel_user_command("o1", cid) for _ in range(8)))
        cancelled_count = sum(1 for o in outs if o and o.get("cancelled"))
        already_done = sum(1 for o in outs if o and o.get("already_done"))
        assert cancelled_count == 1
        assert cancelled_count + already_done == 8

    asyncio.run(main())


def test_contract_concurrent_chain_registration() -> None:
    """case id: concurrency.tracker.chain_registration"""
    rt, _cs, _bus = _make_runtime()

    async def main() -> None:
        r = await rt.send_command("o1", "n1", "task")
        cid = r["command_id"]

        async def reg(i: int) -> None:
            rt._dispatch.register_chain("o1", cid, f"ch_{i}")

        await asyncio.gather(*(reg(i) for i in range(50)))
        snap = rt.get_command_tracker_snapshot("o1", cid)
        assert snap is not None
        assert snap["chain_count"] == 50

    asyncio.run(main())


def test_contract_event_bus_concurrent_emit() -> None:
    """case id: concurrency.event_bus.broadcast"""
    bus = _InMemoryEventBus()
    received: list[int] = []
    bus.subscribe("tick", lambda p: received.append(p["i"]))

    async def main() -> None:
        await asyncio.gather(*(bus.emit("tick", {"i": i}) for i in range(40)))

    asyncio.run(main())
    assert sorted(received) == list(range(40))


# ===========================================================================
# Group F -- integration (1 case)
# ===========================================================================


def test_contract_integration_dispatch_then_cancel_end_to_end() -> None:
    """case id: integration.dispatch.then_cancel"""
    rt, cs, bus = _make_runtime()
    events: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe("user_command_submitted", lambda p: events.append(("submitted", p)))
    bus.subscribe("user_command_cancelled", lambda p: events.append(("cancelled", p)))

    async def main() -> None:
        r = await rt.send_command("o1", "n1", "do it")
        await rt.cancel_user_command("o1", r["command_id"])
        return r["command_id"]

    cid = asyncio.run(main())
    # Sequence: submitted then cancelled.
    names = [n for n, _ in events]
    assert names == ["submitted", "cancelled"]
    # Service saw a matching submit + cancel pair.
    assert cs.submitted == [("o1", "n1", "do it")]
    assert cs.cancelled == [("o1", cid)]
    # Final tracker snapshot is CANCELLED.
    snap = rt.get_command_tracker_snapshot("o1", cid)
    assert snap is not None and snap["state"] == TRACKER_CANCELLED


# ===========================================================================
# Group G -- wall-clock SLA via internal time.perf_counter
# (per G-RC-9.4 NIT-I-1 lesson: do NOT rely on pytest wall-clock)
# ===========================================================================


def test_contract_sla_send_command_under_50ms() -> None:
    """case id: sla.send_command.under_50ms (perf_counter)"""
    import time as _time

    rt, _cs, _bus = _make_runtime()

    async def main() -> float:
        t0 = _time.perf_counter()
        await rt.send_command("o1", "n1", "x")
        return _time.perf_counter() - t0

    elapsed = asyncio.run(main())
    # In-process happy path must stay well under 50 ms.
    assert elapsed < 0.05, f"send_command took {elapsed * 1000:.2f} ms"


def test_contract_sla_cancel_under_50ms() -> None:
    """case id: sla.cancel.under_50ms (perf_counter)"""
    import time as _time

    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "task"))
    cid = r["command_id"]

    async def main() -> float:
        t0 = _time.perf_counter()
        await rt.cancel_user_command("o1", cid)
        return _time.perf_counter() - t0

    elapsed = asyncio.run(main())
    assert elapsed < 0.05, f"cancel_user_command took {elapsed * 1000:.2f} ms"


# ===========================================================================
# Group H -- ledger smoke (1 case): tracker for_org snapshot consistency
# ===========================================================================


def test_contract_active_root_intent_most_recent_running() -> None:
    """case id: get_active_root_intent.most_recent_wins"""
    rt, _cs, _bus = _make_runtime()
    asyncio.run(rt.send_command("o1", "n1", "first"))
    asyncio.run(rt.send_command("o1", "n2", "second"))
    intent = rt._dispatch.get_active_root_intent("o1")
    assert intent in {"first", "second"}  # most-recent wins; ties OK


# ===========================================================================
# Group I -- smoke-B5 lifecycle wire-up (3 cases)
# ===========================================================================


def test_contract_lifecycle_methods_present() -> None:
    """case id: smoke_b5.lifecycle.methods_present

    Regression for smoke-B5: ``getattr(OrgRuntime, 'start_org')`` must
    resolve to a callable; otherwise the dispatch route at
    ``orgs_v2_runtime_dispatch._call_lifecycle`` returns
    ``503 OrgRuntime.start_org not wired``.
    """
    rt, _cs, _bus = _make_runtime()
    for verb in ("start_org", "stop_org", "pause_org", "resume_org"):
        method = getattr(rt, verb, None)
        assert callable(method), f"OrgRuntime.{verb} missing or not callable"


def test_contract_start_org_transitions_state() -> None:
    """case id: smoke_b5.start_org.state_transition"""
    rt, _cs, _bus = _make_runtime()
    out = asyncio.run(rt.start_org("o-smoke"))
    assert out["ok"] is True
    assert out["status"].upper() == "ACTIVE"


def test_contract_start_then_stop_org_lifecycle() -> None:
    """case id: smoke_b5.start_then_stop.full_cycle"""
    rt, _cs, _bus = _make_runtime()
    started = asyncio.run(rt.start_org("o-cycle"))
    assert started["ok"] is True
    stopped = asyncio.run(rt.stop_org("o-cycle"))
    assert stopped["ok"] is True
    assert stopped["status"].upper() == "STOPPED"
