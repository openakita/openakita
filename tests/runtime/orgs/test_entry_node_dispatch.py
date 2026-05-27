"""Sprint-3 P0-1 regression: entry-node dispatch wires real node_id.

The v14 audit (``_orgs_business_capability_audit_v3.md`` §5.2 + §4.5)
found 68/79 ``data/llm_debug/*.json`` files with ``context.node_id =
null`` and zero ``subtask_assigned`` events in ``events.jsonl`` for
50+ orgs_v2 commands -- the orchestrator looked like it was multi-node
coordinating, but really the root LLM was cosplaying every role in a
single ``messages_create_async`` call.

The two upstream bugs:

1. ``OrgCommandService._run_minimal`` forwarded the original
   ``request.target_node_id`` (often ``None``) to
   ``runtime.send_command`` instead of the resolved ``root_node_id``,
   so the executor's ``ProfileResolver.resolve(node_id=None)`` matched
   no ``OrgNode`` and the system prompt collapsed to
   ``"node `None` (role: worker)"``.
2. ``CommandDispatchManager.send_command`` emitted
   ``user_command_submitted`` but never ``subtask_assigned``, so
   exploratory testing had no signal that the dispatcher had selected
   a node.

This file pins both fixes plus the JSONL delegation-log side effect
(``data/delegation_logs/YYYYMMDD.jsonl``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs import _runtime_dispatch as dispatch_mod
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService
from openakita.orgs.runtime import OrgRuntime


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, root_id: str = "producer") -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.state = "active"
        self.nodes = [_Node(root_id)]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _Lookup:
    def __init__(self, root_id: str = "producer") -> None:
        self._org = _Org(root_id)

    def get_org(self, org_id: str) -> Any:
        return self._org


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


@pytest.mark.asyncio
async def test_submit_records_resolved_root_when_target_is_none() -> None:
    """case id: p0_1.submit.records_resolved_root

    Sprint-9 supervisor takeover replacement for the legacy
    ``test_run_minimal_forwards_resolved_root_when_target_is_none``:
    instead of poking ``runtime.send_command.await_args`` (the new
    flow does not call ``send_command`` at all -- it routes through
    the supervisor + executor.activate_and_run) we assert that the
    command bookkeeping captured the resolved root id. This still
    pins the v14 audit fix (root_id must not be ``None``); the
    downstream "resolved root reaches the executor" guarantee is
    covered by :mod:`tests.runtime.orgs.test_supervisor_http_takeover`
    which patches the supervisor factory and asserts the executor
    receives ``node_id=root_node_id``.
    """

    rt = _make_runtime()
    svc = OrgCommandService(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    cmd = svc.commands[res["command_id"]]
    assert cmd["root_node_id"] == "producer"
    assert res["root_node_id"] == "producer"


@pytest.mark.asyncio
async def test_submit_respects_explicit_target_node() -> None:
    """case id: p0_1.submit.explicit_target_wins"""

    rt = _make_runtime()
    svc = OrgCommandService(rt)
    res = await svc.submit(
        OrgCommandRequest(org_id="o1", content="hi", target_node_id="producer")
    )
    cmd = svc.commands[res["command_id"]]
    assert cmd["target_node_id"] == "producer"
    assert cmd["root_node_id"] == "producer"


def test_dispatch_emits_subtask_assigned_event(tmp_path: Path, monkeypatch) -> None:
    """case id: p0_1.dispatch.subtask_assigned_event_emitted

    The dispatch sibling now emits ``subtask_assigned`` for every entry
    dispatch so events.jsonl carries verifiable evidence that the
    orchestrator selected a real node. Pre-fix it only emitted
    ``user_command_submitted`` -- the v14 audit could not distinguish
    "dispatched to producer" from "cosplayed by root LLM".
    """

    from openakita.orgs._runtime_dispatch import CommandDispatchManager

    emitted: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        async def emit(self, ev: str, payload: dict[str, Any]) -> None:
            emitted.append((ev, dict(payload)))

        async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
            return None

        def subscribe(self, event: str, handler: Any) -> None:
            pass

        def unsubscribe(self, event: str, handler: Any) -> None:
            pass

    # Redirect delegation-log writes into a tmp dir so the test does
    # not pollute the project ``data/`` tree.
    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    mgr = CommandDispatchManager(
        command_service=None,
        lookup=_Lookup("producer"),
        event_bus=_Bus(),
    )
    asyncio.run(
        mgr.send_command("o1", "producer", "策划 5 分钟视频", command_id="cmd_1")
    )
    event_names = [name for name, _ in emitted]
    assert "user_command_submitted" in event_names
    assert "subtask_assigned" in event_names
    subtask_payload = next(p for n, p in emitted if n == "subtask_assigned")
    assert subtask_payload["node_id"] == "producer"
    assert subtask_payload["child_node_id"] == "producer"
    assert subtask_payload["content_preview"].startswith("策划 5 分钟")
    assert subtask_payload["command_id"] == "cmd_1"


def test_dispatch_writes_delegation_log_jsonl_line(tmp_path: Path, monkeypatch) -> None:
    """case id: p0_1.dispatch.delegation_log_jsonl_appended

    Each dispatch appends a single JSONL line to
    ``data/delegation_logs/YYYYMMDD.jsonl`` so the v13/v14 finding
    "delegation_logs increment = 0" stops being the pessimistic
    default. We redirect the log dir into a tmp_path so the test is
    hermetic.
    """

    from openakita.orgs._runtime_dispatch import CommandDispatchManager

    class _Bus:
        async def emit(self, ev: str, payload: dict[str, Any]) -> None:
            return None

        async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
            return None

        def subscribe(self, event: str, handler: Any) -> None:
            pass

        def unsubscribe(self, event: str, handler: Any) -> None:
            pass

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    mgr = CommandDispatchManager(
        command_service=None,
        lookup=_Lookup("producer"),
        event_bus=_Bus(),
    )
    asyncio.run(mgr.send_command("o1", "producer", "hi", command_id="cmd_log"))

    today = datetime.now().strftime("%Y%m%d")
    log_path = tmp_path / f"{today}.jsonl"
    assert log_path.is_file()
    lines = [
        json.loads(line)
        for line in log_path.read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    record = lines[0]
    assert record["command_id"] == "cmd_log"
    assert record["org_id"] == "o1"
    assert record["child_node"] == "producer"
    assert record["parent_node"] is None
    assert record["kind"] == "entry_dispatch"


def test_send_command_org_missing_returns_error_without_log_write(
    tmp_path: Path, monkeypatch
) -> None:
    """case id: p0_1.dispatch.org_missing_no_log_side_effect

    When the org lookup fails we return ``error`` and must not write a
    delegation-log line: the log file is meant to record real
    dispatches, not bogus org ids.
    """

    from openakita.orgs._runtime_dispatch import CommandDispatchManager

    class _MissingLookup:
        def get_org(self, org_id: str) -> Any:
            return None

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    class _Bus:
        async def emit(self, ev: str, payload: dict[str, Any]) -> None:
            return None

        async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
            return None

        def subscribe(self, event: str, handler: Any) -> None:
            pass

        def unsubscribe(self, event: str, handler: Any) -> None:
            pass

    mgr = CommandDispatchManager(
        command_service=None,
        lookup=_MissingLookup(),
        event_bus=_Bus(),
    )
    out = asyncio.run(mgr.send_command("unknown_org", "n1", "hi"))
    assert out.get("status") == "error"
    # No JSONL file should have been created.
    today = datetime.now().strftime("%Y%m%d")
    assert not (tmp_path / f"{today}.jsonl").exists()


def test_cancel_user_command_populates_cancelled_roots() -> None:
    """case id: p0_2.dispatch.cancel_populates_cancelled_roots

    Pre-Sprint-3 the dispatch cancel returned
    ``{"ok": True, "command_id": cid, "cancelled": True}`` -- no
    ``cancelled_roots`` field -- so the service layer fell back to
    ``[]`` and the v14 audit observed "cancel HTTP 200 + cancelled_
    roots:[]" as a smell. We now populate the field with the tracker's
    root id.
    """

    from openakita.orgs._runtime_dispatch import CommandDispatchManager

    class _Bus:
        async def emit(self, ev: str, payload: dict[str, Any]) -> None:
            return None

        async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
            return None

        def subscribe(self, event: str, handler: Any) -> None:
            pass

        def unsubscribe(self, event: str, handler: Any) -> None:
            pass

    mgr = CommandDispatchManager(
        command_service=None,
        lookup=_Lookup("producer"),
        event_bus=_Bus(),
    )
    asyncio.run(mgr.send_command("o1", "producer", "hi", command_id="cmd_c"))
    resp = asyncio.run(mgr.cancel_user_command("o1", "cmd_c"))
    assert resp is not None
    assert resp.get("cancelled_roots") == ["producer"]


def test_org_runtime_send_command_uses_real_target_node() -> None:
    """case id: p0_1.runtime.target_node_threads_through

    End-to-end through ``OrgRuntime.send_command`` -> dispatch sibling
    -> ``agent_dispatch`` callback: the node id we pass at the outer
    edge is what the executor receives. Sprint-3 fixes the upstream
    caller; this test pins the contract that downstream code does not
    silently drop or rewrite it.
    """

    captured: dict[str, Any] = {}

    async def fake_agent_dispatch(
        org_id: str, node_id: str, command_id: str, content: str
    ) -> dict[str, Any]:
        captured["node_id"] = node_id
        return {"status": "ok"}

    rt = OrgRuntime(
        lookup=_Lookup("producer"),
        persistence=object(),
        lifecycle_emitter=object(),
        agent_dispatch=fake_agent_dispatch,
    )
    asyncio.run(rt.send_command("o1", "producer", "hi", command_id="cmd_n"))
    assert captured.get("node_id") == "producer"
