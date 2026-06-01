"""Item 2 + 图3 regression: ``command_done`` is a first-class bus event.

Pre-fix the v2 command path never emitted ``command_done`` onto the event
bus -- the command center learned a command had converged only by polling
``GET /commands/{id}``. The event store had no terminal row and the SSE /
WS surfaces never received a done signal.

``OrgRuntime.emit_command_done`` now routes the terminal state through the
event bus so all three taps fire exactly once:

* persist tap   -> appended to the per-org ``OrgEventStore``;
* stream tap    -> SSE ``lifecycle`` channel;
* ws tap        -> legacy ``org:command_done`` WS broadcast (status/result).

These tests pin persistence, idempotency, and the 图3 convergence safety
net (any still-busy node is flipped back to idle at command done).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.orgs.runtime import OrgRuntime


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_
        self.level = 0


class _Org:
    def __init__(self, org_id: str) -> None:
        self.id = org_id
        self.state = "active"
        self.nodes = [_Node("root1"), _Node("worker1")]

    def get_node(self, nid: str) -> Any:
        return next((n for n in self.nodes if n.id == nid), None)


class _Lookup:
    def get_org(self, org_id: str) -> Any:
        return _Org(org_id)


def _make_runtime() -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
    )


def test_command_done_is_persisted_to_event_store() -> None:
    """case id: item2.command_done.persisted"""

    rt = _make_runtime()
    asyncio.run(
        rt.emit_command_done(
            "o-done", "cmd-1", status="done", result={"final_message": "ok"}
        )
    )
    store = rt.get_event_store("o-done")
    assert store is not None
    events = store.query(limit=20)
    done = [e for e in events if (e.get("type") or e.get("event_type")) == "command_done"]
    assert len(done) == 1
    assert done[0].get("command_id") == "cmd-1"
    assert done[0].get("status") == "done"


def test_command_done_is_idempotent() -> None:
    """case id: item2.command_done.idempotent

    A second emit for the same command (retry / dual happy+synthetic path)
    must not append a duplicate terminal row.
    """

    rt = _make_runtime()

    async def main() -> None:
        await rt.emit_command_done("o-idem", "cmd-x", status="done")
        await rt.emit_command_done("o-idem", "cmd-x", status="done")

    asyncio.run(main())
    store = rt.get_event_store("o-idem")
    done = [
        e
        for e in store.query(limit=20)
        if (e.get("type") or e.get("event_type")) == "command_done"
    ]
    assert len(done) == 1


def test_command_done_forwards_to_stream_bus() -> None:
    """case id: item2.command_done.sse"""

    from openakita.runtime import stream_registry

    stream_registry.reset_org_stream_buses()
    rt = _make_runtime()
    bus = stream_registry.get_or_create_org_stream_bus("o-sse-done")
    sub = bus.make_subscription(("lifecycle",), drain_on_close=False)

    async def main() -> Any:
        await bus.register_subscription(sub)
        await rt.emit_command_done("o-sse-done", "cmd-sse", status="done")
        return await asyncio.wait_for(sub.queue.get(), timeout=1.0)

    event = asyncio.run(main())
    assert event.type == "command_done"
    assert event.org_id == "o-sse-done"
    stream_registry.reset_org_stream_buses()


def test_command_done_resets_busy_nodes_to_idle() -> None:
    """case id: graph3.command_done.converges_busy_nodes

    图3: a node left "busy" (dropped terminal event) must not stay 进行中
    after the command converges.
    """

    rt = _make_runtime()

    async def main() -> list[str | None]:
        # Mark both nodes busy as if a run was in flight.
        await rt._node_lifecycle.set_node_status("o-conv", "root1", "busy")
        await rt._node_lifecycle.set_node_status("o-conv", "worker1", "busy")
        await rt.emit_command_done("o-conv", "cmd-conv", status="done")
        return [
            rt._node_lifecycle.get_node_status("o-conv", "root1"),
            rt._node_lifecycle.get_node_status("o-conv", "worker1"),
        ]

    statuses = asyncio.run(main())
    assert all(str(s).lower() == "idle" for s in statuses), statuses


def test_command_done_skips_without_ids() -> None:
    """case id: item2.command_done.requires_ids"""

    rt = _make_runtime()
    asyncio.run(rt.emit_command_done("", "cmd", status="done"))
    asyncio.run(rt.emit_command_done("org", "", status="done"))
    assert rt._event_stores == {}
