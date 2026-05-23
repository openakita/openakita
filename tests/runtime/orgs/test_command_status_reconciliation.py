"""Sprint-2 P0-2 regression: ``OrgCommandService`` reconciles status with events.

The v13 business-capability audit (``_orgs_business_capability_audit_v2.md``
§5 / §6 Top-1) found ``GET /api/v2/orgs/{id}/commands/{cid}`` returning
``phase=done, error=null`` while ``events.jsonl`` showed
``agent_run_failed`` -- the UI displayed "task complete" while the node
had crashed. This file pins the reconciliation:

* When the service is wired with an event bus, ``agent_run_failed``
  events flip the command's status to ``error`` and surface the
  reason / error string.
* When the service is wired with an event bus, ``agent_run_finished``
  events leave the command at ``done`` (the legacy pre-Sprint-2
  behaviour) but tag the snapshot with ``event_ref``.
* When no event bus is provided, the service still constructs and
  works (back-compat with the existing P9.4 contract suite).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node("root1")]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _StubEventBus:
    """Sync subscribe/emit bus matching ``EventBusProtocol``-ish surface."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Any]] = {}

    def subscribe(self, event: str, handler: Any) -> None:
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Any) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for h in list(self._subs.get(event, ())):
            res = h(payload)
            if asyncio.iscoroutine(res):
                await res


def _make_runtime(*, send_result: dict[str, Any] | None = None) -> MagicMock:
    rt = MagicMock()
    rt.get_org = MagicMock(return_value=_Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    rt.send_command = AsyncMock(return_value=send_result or {"status": "submitted"})
    rt.cancel_user_command = AsyncMock(return_value={"cancelled_roots": []})
    return rt


def test_service_constructs_without_event_bus_back_compat() -> None:
    """case id: p02.service.no_event_bus_back_compat

    Existing P9.4 contract / parity tests pass ``OrgCommandService``
    without an event_bus. They must keep working.
    """

    svc = OrgCommandService(_make_runtime())
    assert svc._event_bus is None
    assert svc._command_outcomes == {}


def test_service_subscribes_to_agent_run_events_when_bus_provided() -> None:
    """case id: p02.service.subscribes_to_agent_run_events

    The wire-up registers handlers for the three events the executor
    emits during a per-node run. A wildcard tap is **not** used
    because the named-subscriber surface is the only thing every
    bus impl is required to expose (``EventBusProtocol`` Protocol).
    """

    bus = _StubEventBus()
    OrgCommandService(_make_runtime(), event_bus=bus)
    assert set(bus._subs) == {
        "agent_run_started",
        "agent_run_finished",
        "agent_run_failed",
    }


def test_handle_agent_event_records_failed_outcome() -> None:
    """case id: p02.service.handler_records_failed_outcome"""

    bus = _StubEventBus()
    svc = OrgCommandService(_make_runtime(), event_bus=bus)
    asyncio.run(
        bus.emit(
            "agent_run_failed",
            {
                "org_id": "o1",
                "node_id": "n1",
                "command_id": "cmd_x",
                "reason": "agent_build_failed",
                "error": "AgentBuilderProtocol not wired",
            },
        )
    )
    assert svc._command_outcomes["cmd_x"]["event"] == "agent_run_failed"
    assert svc._command_outcomes["cmd_x"]["reason"] == "agent_build_failed"


def test_handle_agent_event_records_finished_outcome() -> None:
    """case id: p02.service.handler_records_finished_outcome"""

    bus = _StubEventBus()
    svc = OrgCommandService(_make_runtime(), event_bus=bus)
    asyncio.run(
        bus.emit(
            "agent_run_finished",
            {
                "org_id": "o1",
                "node_id": "n1",
                "command_id": "cmd_y",
                "output_len": 42,
            },
        )
    )
    assert svc._command_outcomes["cmd_y"]["event"] == "agent_run_finished"
    assert svc._command_outcomes["cmd_y"]["output_len"] == 42


def test_handle_agent_event_skips_payload_without_command_id() -> None:
    """case id: p02.service.handler_requires_command_id

    Synthetic / aggregate events (e.g. an org-wide health probe) may
    not have ``command_id``. The handler must skip those silently
    instead of registering a ``""`` outcome that overwrites real ones.
    """

    bus = _StubEventBus()
    svc = OrgCommandService(_make_runtime(), event_bus=bus)
    asyncio.run(bus.emit("agent_run_started", {"org_id": "o1"}))
    assert svc._command_outcomes == {}


@pytest.mark.asyncio
async def test_run_minimal_flips_status_to_error_when_event_says_failed() -> None:
    """case id: p02.run_minimal.failed_event_overrides_done

    Pre-Sprint-2 ``_run_minimal`` finished with ``status=done,
    phase=done, error=null`` regardless of the event-bus signal.
    With the bus wired, ``agent_run_failed`` flips the public
    snapshot to ``error`` so ``GET /commands/{cid}`` no longer lies.
    """

    bus = _StubEventBus()
    rt = _make_runtime()

    async def fake_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Simulate the dispatch sibling: emit the failure event,
        # then return the legacy ``submitted`` envelope.
        await bus.emit(
            "agent_run_failed",
            {
                "org_id": args[0],
                "node_id": args[1],
                "command_id": kwargs["command_id"],
                "reason": "agent_build_failed",
                "error": "AgentBuilderProtocol not wired",
            },
        )
        return {"status": "submitted", "command_id": kwargs["command_id"]}

    rt.send_command = AsyncMock(side_effect=fake_send)
    svc = OrgCommandService(rt, event_bus=bus)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    # Yield once so the background ``_run_minimal`` task progresses.
    await asyncio.sleep(0.05)
    cmd = svc.commands[res["command_id"]]
    assert cmd["status"] == "error"
    assert cmd["phase"] == "error"
    assert "agent_build_failed" in (cmd.get("error") or "")
    assert cmd.get("event_ref") == "agent_run_failed"


@pytest.mark.asyncio
async def test_run_minimal_keeps_done_when_event_says_finished() -> None:
    """case id: p02.run_minimal.finished_event_keeps_done"""

    bus = _StubEventBus()
    rt = _make_runtime()

    async def fake_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await bus.emit(
            "agent_run_finished",
            {
                "org_id": args[0],
                "node_id": args[1],
                "command_id": kwargs["command_id"],
                "output_len": 7,
            },
        )
        return {"status": "submitted", "command_id": kwargs["command_id"]}

    rt.send_command = AsyncMock(side_effect=fake_send)
    svc = OrgCommandService(rt, event_bus=bus)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    await asyncio.sleep(0.05)
    cmd = svc.commands[res["command_id"]]
    assert cmd["status"] == "done"
    assert cmd["phase"] == "done"
    assert cmd.get("error") is None
    assert cmd.get("event_ref") == "agent_run_finished"


@pytest.mark.asyncio
async def test_get_status_overlays_event_ref_and_error_during_running_window() -> None:
    """case id: p02.get_status.live_overlay_from_outcomes

    A frontend may poll ``GET /commands/{cid}`` while the background
    finaliser is between ``send_command`` returning and
    ``_update_command_state`` flipping the dict. During that window
    the outcomes cache is the only signal of failure -- ``get_status``
    must surface it so the user does not see "running" forever.
    """

    bus = _StubEventBus()
    svc = OrgCommandService(_make_runtime(), event_bus=bus)
    # Simulate a command record in ``running`` state (the submit-side
    # set this up; we shortcut for the unit test).
    cid = "cmd_live"
    svc._commands[cid] = {
        "command_id": cid,
        "org_id": "o1",
        "root_node_id": "root1",
        "status": "running",
        "phase": "running",
        "result": None,
        "error": None,
        "created_at": 1.0,
        "updated_at": 1.0,
        "finished_at": None,
        "origin_surface": "org_console",
        "output_scope": "internal",
    }
    await bus.emit(
        "agent_run_failed",
        {
            "org_id": "o1",
            "command_id": cid,
            "reason": "agent_build_failed",
            "error": "x",
        },
    )
    snap = svc.get_status("o1", cid)
    assert snap is not None
    assert snap.get("event_ref") == "agent_run_failed"
    assert "agent_build_failed" in (snap.get("error") or "")
