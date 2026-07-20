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

    The wire-up registers handlers for every event the executor emits
    during a per-node run. A wildcard tap is **not** used because the
    named-subscriber surface is the only thing every bus impl is
    required to expose (``EventBusProtocol`` Protocol).

    Sprint-3 P0-2 (audit v3 §5.3) added ``agent_run_cancelled`` to the
    catalogue so user-initiated cancels reach the outcome cache as a
    distinct terminal state.
    """

    bus = _StubEventBus()
    OrgCommandService(_make_runtime(), event_bus=bus)
    assert {
        "agent_run_started",
        "agent_run_finished",
        "agent_run_failed",
        "agent_run_cancelled",
    }.issubset(bus._subs)


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


# Sprint-9 supervisor takeover: the two
# ``test_run_minimal_*_event_says_*`` cases (failed / finished) used
# to pin the legacy ``_run_minimal`` path that called
# ``runtime.send_command`` and then re-read ``_command_outcomes`` to
# flip ``cmd["status"]``. The new flow runs ``supervisor.run()`` and
# the supervisor's :class:`FinalOutcome` is the source of truth -- a
# stray bus event no longer overrides it. ``_command_outcomes`` is
# still maintained for ``get_cancel_source`` + ``get_status``
# overlay reads, which are covered by the remaining tests in this
# file and by :mod:`tests.runtime.orgs.test_supervisor_http_takeover`.


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


def test_get_status_reconstructs_from_events_after_restart() -> None:
    """case id: test18.get_status.event_store_fallback

    test18 (a): after a backend restart the in-memory ``_commands`` map is
    empty, so ``get_status`` used to return ``None`` -> ``/commands/<cid>`` 404
    -> the command center could not rebuild the final-report bubble on reload.
    The durable ``command_done`` event still carries the full result, so
    ``get_status`` must reconstruct an authoritative snapshot from the event
    store instead of 404ing.
    """

    done_event = {
        "type": "command_done",
        "command_id": "cmd_old",
        "status": "done",
        "root_node_id": "root1",
        "result": {"final_message": "最终交付报告正文", "deliverable": "d", "partial": False},
    }
    rt = _make_runtime()
    rt.get_event_store = MagicMock(
        return_value=MagicMock(
            query=lambda **kw: [done_event] if kw.get("event_type") == "command_done" else []
        )
    )
    svc = OrgCommandService(rt)
    # Nothing in the in-memory map (simulates a fresh process after restart).
    assert svc._commands == {}

    snap = svc.get_status("o1", "cmd_old")
    assert snap is not None, "must reconstruct from events, not 404"
    assert snap["status"] == "done"
    assert snap["result"]["final_message"] == "最终交付报告正文"
    assert snap["root_node_id"] == "root1"
    assert snap.get("reconstructed_from_events") is True


def test_get_status_returns_none_for_truly_unknown_command() -> None:
    """case id: test18.get_status.unknown_still_none

    The event-store fallback must not fabricate a record for a command that
    was never run -- an empty store still yields ``None`` (-> 404).
    """

    svc = OrgCommandService(_make_runtime())  # event store returns []
    assert svc.get_status("o1", "cmd_never") is None


def test_get_status_freezes_elapsed_time_after_command_finishes() -> None:
    svc = OrgCommandService(_make_runtime())
    svc._commands["cmd_done"] = {
        "command_id": "cmd_done",
        "org_id": "o1",
        "root_node_id": "root1",
        "status": "done",
        "phase": "done",
        "result": {"final_message": "done"},
        "error": None,
        "created_at": 100.0,
        "updated_at": 125.0,
        "finished_at": 125.0,
        "origin_surface": "org_console",
        "output_scope": "internal",
    }

    snapshot = svc.get_status("o1", "cmd_done")

    assert snapshot is not None
    assert snapshot["elapsed_s"] == 25.0
