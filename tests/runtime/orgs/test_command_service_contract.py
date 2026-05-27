"""Contract suite for v2 OrgCommandService (P-RC-9 P9.4d).

The contract pins the public surface of
``openakita.orgs.command_service.OrgCommandService``
against a single in-memory test-double backend
(``OrgCommandService`` has no JSON/SQLite split -- the
service is volatile orchestration on top of three injected
Protocols).

Per P-RC-9-PLAN section 4 P9.4 charter: 20 cases; section
5.2: parity gate (closed in P9.4c). This file ships
**16 cases** -- enough to cover the public surface without
over-fitting the v1 behavioural quirks that the parity gate
already enforces.

Case axes (same pattern as P9.1d / P9.2e / P9.3d):

* dispatch / CommandDispatcher boundary (1 case)
* submit -- happy path + 3 error gates (4 cases)
* submit -- replace_existing conflict semantics (1 case)
* get_status -- happy + missing + wrong-org + live overlay (4 cases)
* cancel -- terminal + running + missing (3 cases)
* fan-out -- subscribe / publish / late-subscriber replay (2 cases)
* find_command_for_event (1 case)

No backend parametrisation -- the service is single-impl. The
P9.3 NodeScheduler / P9.2 ProjectStore "two backends" pattern
does not apply: persistence is delegated through the injected
:class:`CommandRuntimeProtocol`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.command_models import (
    OrgCommandConflict,
    OrgCommandError,
    OrgCommandRequest,
    OrgCommandSurface,
    OrgOutputScope,
)
from openakita.orgs.command_service import OrgCommandService

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    """Minimal :class:`OrgLookupProtocol`-shaped object."""

    def __init__(self, status: str = "active", root_ids: list[str] | None = None) -> None:
        self.status = type("_Status", (), {"value": status})()
        self.nodes = [_Node(nid) for nid in (root_ids or ["root1"])]

    def get_node(self, nid: str):
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self):
        return list(self.nodes)


class _EvtStore:
    """``CommandRuntimeProtocol.get_event_store`` test double."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self._events = events or []

    def query(self, *, event_type: str, limit: int = 20):
        return [e for e in self._events if e.get("event_type") == event_type][:limit]


def _make_runtime(
    *,
    org: _Org | None = None,
    tracker_snapshot: dict[str, Any] | None = None,
    event_store: _EvtStore | None = None,
    send_hang: bool = False,
) -> MagicMock:
    """Build a fully-stubbed :class:`CommandRuntimeProtocol`."""
    rt = MagicMock()
    rt.get_org = MagicMock(return_value=org if org is not None else _Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=tracker_snapshot)
    rt.get_event_store = MagicMock(return_value=event_store or _EvtStore())
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    if send_hang:

        async def _hang(*_a, **_kw):
            await asyncio.sleep(60)

        rt.send_command = AsyncMock(side_effect=_hang)
    else:
        rt.send_command = AsyncMock(return_value={"result": "ok"})
    rt.cancel_user_command = AsyncMock(return_value={"cancelled_roots": ["root1"]})
    return rt


def _make_service(rt: MagicMock | None = None, **kw: Any) -> OrgCommandService:
    return OrgCommandService(rt if rt is not None else _make_runtime(), **kw)


# ---------------------------------------------------------------------------
# 1. dispatch (CommandDispatcher boundary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_passes_through_to_runtime_send_command() -> None:
    rt = _make_runtime()
    svc = _make_service(rt)
    result = await svc.dispatch("o1", "n1", "go")
    assert result == {"result": "ok"}
    rt.send_command.assert_awaited_once()
    args, kwargs = rt.send_command.await_args
    assert args[0] == "o1" and args[1] == "n1" and args[2] == "go"
    # A fresh command_id is minted; signature matches v1.
    assert kwargs["command_id"].startswith("cmd_")


# ---------------------------------------------------------------------------
# 2-5. submit -- happy path + error gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_happy_path_returns_running_dict() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="task"))
    # Sprint-9 supervisor takeover: the submit envelope adds a
    # ``resumed_from`` slot (carries the checkpoint id when
    # continue_previous=True succeeded; ``None`` on a fresh submit).
    assert res["command_id"].startswith("cmd_")
    assert res["status"] == "running"
    assert res["root_node_id"] == "root1"
    assert res["resumed_from"] is None
    cmd = svc.commands[res["command_id"]]
    assert cmd["status"] == "running" and cmd["origin_surface"] == "org_console"


@pytest.mark.asyncio
async def test_submit_rejects_empty_content() -> None:
    svc = _make_service()
    with pytest.raises(OrgCommandError, match="content is required"):
        await svc.submit(OrgCommandRequest(org_id="o1", content="   "))


@pytest.mark.asyncio
async def test_submit_rejects_missing_target_node() -> None:
    svc = _make_service(_make_runtime(org=_Org(root_ids=["root1"])))
    with pytest.raises(OrgCommandError, match="Node not found"):
        await svc.submit(OrgCommandRequest(org_id="o1", content="x", target_node_id="nope"))


@pytest.mark.asyncio
async def test_submit_rejects_paused_org() -> None:
    svc = _make_service(_make_runtime(org=_Org(status="paused")))
    with pytest.raises(OrgCommandConflict):
        await svc.submit(OrgCommandRequest(org_id="o1", content="x"))


# ---------------------------------------------------------------------------
# 6. submit replace_existing conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_second_command_conflicts_without_replace() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    first = await svc.submit(OrgCommandRequest(org_id="o1", content="A"))
    with pytest.raises(OrgCommandConflict) as exc:
        await svc.submit(OrgCommandRequest(org_id="o1", content="B"))
    assert exc.value.command_id == first["command_id"]


# ---------------------------------------------------------------------------
# 7-10. get_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_returns_none_for_missing_command() -> None:
    svc = _make_service()
    assert svc.get_status("o1", "missing_id") is None


@pytest.mark.asyncio
async def test_get_status_returns_none_for_wrong_org() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    assert svc.get_status("o_other", res["command_id"]) is None


@pytest.mark.asyncio
async def test_get_status_returns_snapshot_for_running_command() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(
        OrgCommandRequest(
            org_id="o1",
            content="x",
            origin_surface=OrgCommandSurface.DESKTOP_CHAT,
            output_scope=OrgOutputScope.CHAT_SUMMARY,
        )
    )
    st = svc.get_status("o1", res["command_id"])
    assert st is not None
    assert st["status"] == "running"
    assert st["origin_surface"] == "desktop_chat"
    assert st["output_scope"] == "chat_summary"


@pytest.mark.asyncio
async def test_get_status_overlays_live_tracker_snapshot() -> None:
    snapshot = {
        "phase": "deepening",
        "tracker_state": "busy",
        "open_chains": ["chain_1"],
    }
    rt = _make_runtime(send_hang=True, tracker_snapshot=snapshot)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    st = svc.get_status("o1", res["command_id"])
    assert st is not None
    assert st["phase"] == "deepening"
    assert st["tracker_state"] == "busy"
    assert st["open_chains"] == ["chain_1"]


# ---------------------------------------------------------------------------
# 11-13. cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_returns_none_for_missing_command() -> None:
    svc = _make_service()
    assert await svc.cancel("o1", "missing") is None


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_terminal_command() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    svc.commands[res["command_id"]]["status"] = "done"
    ack = await svc.cancel("o1", res["command_id"])
    assert ack == {"ok": True, "command_id": res["command_id"], "already_done": True}


@pytest.mark.asyncio
async def test_cancel_running_calls_runtime_and_emitter() -> None:
    rt = _make_runtime(send_hang=True)
    emitter = MagicMock()
    emitter.broadcast = AsyncMock()
    svc = _make_service(rt, emitter=emitter)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    ack = await svc.cancel("o1", res["command_id"])
    assert ack is not None and ack["ok"] is True
    # Sprint-9 supervisor takeover: cancel now propagates a
    # ``cancel_reason`` kwarg so the dispatch tracker can route the
    # taxonomy (user_cancel / stop_org / replaced) through to
    # events.jsonl alongside the supervisor's own cancelled
    # lifecycle event.
    rt.cancel_user_command.assert_awaited_once_with(
        "o1", res["command_id"], cancel_reason="user_cancel"
    )
    emitter.broadcast.assert_awaited_once()
    event, payload = emitter.broadcast.await_args[0]
    assert event == "org:command_cancelled"
    assert payload["by"] == "user"


# ---------------------------------------------------------------------------
# 14-15. fan-out (subscribe / publish / late subscriber)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_then_publish_delivers_event_and_records_target() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    q = svc.subscribe_summary(res["command_id"], surface="im", target="chat_1")
    await svc.publish_summary(res["command_id"], {"type": "org_progress"})
    event = await asyncio.wait_for(q.get(), timeout=0.1)
    assert event["type"] == "org_progress"
    delivered = svc.commands[res["command_id"]]["delivered_to"]
    assert delivered[-1] == {
        "surface": "im",
        "target": "chat_1",
        "event": "org_progress",
        "ts": delivered[-1]["ts"],
    }


@pytest.mark.asyncio
async def test_late_subscriber_receives_terminal_event_immediately() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    svc._update_command_state(
        res["command_id"],
        status="done",
        phase="done",
        result={"result": "done"},
    )
    q = svc.subscribe_summary(res["command_id"], surface="im", target="chat_x")
    event = await asyncio.wait_for(q.get(), timeout=0.1)
    assert event["type"] == "org_command_done"
    assert event["command_id"] == res["command_id"]


# ---------------------------------------------------------------------------
# 16. find_command_for_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_command_for_event_matches_by_id_then_lone_running() -> None:
    rt = _make_runtime(send_hang=True)
    svc = _make_service(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="x"))
    # Direct id wins.
    cmd = svc.find_command_for_event("o1", {"command_id": res["command_id"]})
    assert cmd is not None and cmd["command_id"] == res["command_id"]
    # No id + lone running -> the same command.
    cmd2 = svc.find_command_for_event("o1", {})
    assert cmd2 is not None and cmd2["command_id"] == res["command_id"]
    # Wrong org -> None.
    cmd3 = svc.find_command_for_event("o_other", {"command_id": res["command_id"]})
    assert cmd3 is None
