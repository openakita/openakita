"""Sprint-6 P0-2 regression: ``cancelled_by`` survives to events.jsonl.

Pins the RCA ``_v17_p1_rca.md`` §2.5 anti-pattern fix: the Sprint-5
commit only wrote ``cancelled_by`` to the in-memory outcome cache
``_command_outcomes[cid]``. The events.jsonl writes in
``_runtime_dispatch.cancel_user_command`` (line 368-371) and
``_runtime_agent_pipeline_executor._invoke_agent except
CancelledError`` (line 252-260) hard-coded ``reason="user_cancel"``
and never read the cache, so the v17 audit saw 0/5 stop-org
``cancelled_by=stop_org`` cases on disk despite the in-memory marker
being present.

This module exercises the **disk side** of the bridge:

* ``test_dispatch_cancel_user_command_emits_cancelled_by_stop_org``
  -- routes ``cancel_reason="stop_org"`` through
  :meth:`CommandDispatchManager.cancel_user_command` and asserts the
  ``user_command_cancelled`` payload that lands on the bus carries
  the source verbatim (not the legacy ``user_cancel`` constant).
* ``test_executor_cancel_consults_cancel_source_provider`` -- forces
  a ``CancelledError`` through
  :meth:`AgentPipelineExecutor.activate_and_run` with a provider
  that reports ``stop_org``, and asserts the emitted
  ``agent_run_cancelled`` payload reflects the source.
* ``test_executor_cancel_falls_back_to_user_cancel_when_provider_returns_none``
  -- user-initiated cancel path stays backwards compatible: no
  source -> ``reason="user_cancel"`` + ``cancelled_by="user_cancel"``
  (the latter is new but the value matches the legacy reason so
  readers that prefer the new field still get a deterministic
  value).
* ``test_watchdog_kill_emits_cancelled_by_watchdog_to_disk`` --
  Sprint-6 P0-2 watchdog parity: the emit-to-events.jsonl payload
  must carry ``cancelled_by=watchdog`` so an external auditor can
  attribute the kill before the follow-on ``agent_run_cancelled``
  arrives.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs._runtime_dispatch import (
    CommandDispatchManager,
    _CommandTracker,
)
from openakita.orgs._runtime_event_store import OrgEventStore
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService


class _DiskWiredEventBus:
    """In-process bus that also persists every emit to a JSONL file.

    Mirrors the production composition: ``OrgRuntime`` taps the
    in-memory bus and forwards every event to
    :class:`OrgEventStore`. Re-implementing the tap inline here keeps
    the test independent of the runtime composition root (which
    pulls a much larger module graph than we need).
    """

    def __init__(self, store: OrgEventStore) -> None:
        self._store = store
        self._subs: dict[str, list[Any]] = {}
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, event: str, handler: Any) -> None:
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Any) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.emitted.append((event, dict(payload)))
        record = dict(payload)
        record.setdefault("type", event)
        self._store.append(record)
        for h in list(self._subs.get(event, ())):
            res = h(payload)
            if asyncio.iscoroutine(res):
                await res


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, *, roots: tuple[str, ...] = ("root1",)) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]
        self.watchdog_enabled = True
        self.watchdog_stuck_threshold_s = 0.5

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


def _read_events(jsonl: Path) -> list[dict[str, Any]]:
    if not jsonl.is_file():
        return []
    events: list[dict[str, Any]] = []
    for raw in jsonl.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ---------------------------------------------------------------------------
# P0-2 -- dispatch.cancel_user_command stamps cancelled_by on events.jsonl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_cancel_user_command_emits_cancelled_by_stop_org(
    tmp_path: Path,
) -> None:
    """case id: p06.cancelled_by.dispatch_writes_disk

    The Sprint-5 commit hard-coded ``reason="user_cancel"`` in the
    ``user_command_cancelled`` emit. v17 audit caught this: any
    stop-org cancel still showed up on disk as a user_cancel and the
    audit couldn't tell apart 5/5 cases. Sprint-6 P0-2 threads the
    explicit source through; this assertion is the regression guard.
    """

    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    bus = _DiskWiredEventBus(store)
    lookup = MagicMock()
    lookup.get_org = MagicMock(return_value=_Org())
    dispatch = CommandDispatchManager(
        command_service=None,
        lookup=lookup,
        event_bus=bus,
    )
    # Register a running tracker so cancel hits the real code path.
    tracker = _CommandTracker(
        org_id="org-int",
        command_id="cid-1",
        root_node_id="root1",
        root_intent="long task",
    )
    dispatch._registry.register(tracker)  # type: ignore[attr-defined]

    res = await dispatch.cancel_user_command(
        "org-int", "cid-1", cancel_reason="stop_org"
    )
    assert res is not None and res["cancelled"] is True
    assert tracker.cancel_reason == "stop_org"

    events = _read_events(jsonl)
    cancelled = next(e for e in events if e.get("type") == "user_command_cancelled")
    assert cancelled["reason"] == "stop_org"
    assert cancelled["cancelled_by"] == "stop_org"


@pytest.mark.asyncio
async def test_dispatch_cancel_user_command_defaults_user_cancel(
    tmp_path: Path,
) -> None:
    """case id: p06.cancelled_by.dispatch_default_user_cancel

    Backwards compatibility: when no cancel_reason is supplied the
    payload keeps the Sprint-3 ``user_cancel`` value so existing
    readers stay byte-for-byte compatible.
    """

    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    bus = _DiskWiredEventBus(store)
    lookup = MagicMock()
    lookup.get_org = MagicMock(return_value=_Org())
    dispatch = CommandDispatchManager(
        command_service=None,
        lookup=lookup,
        event_bus=bus,
    )
    tracker = _CommandTracker(
        org_id="org-int",
        command_id="cid-1",
        root_node_id="root1",
        root_intent="long task",
    )
    dispatch._registry.register(tracker)  # type: ignore[attr-defined]

    await dispatch.cancel_user_command("org-int", "cid-1")

    events = _read_events(jsonl)
    cancelled = next(e for e in events if e.get("type") == "user_command_cancelled")
    assert cancelled["reason"] == "user_cancel"
    assert cancelled["cancelled_by"] == "user_cancel"


# ---------------------------------------------------------------------------
# P0-2 -- end-to-end through OrgCommandService.cancel_all_for_org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_org_flow_writes_cancelled_by_to_disk(tmp_path: Path) -> None:
    """case id: p06.cancelled_by.stop_org_full_flow_to_disk

    End-to-end: submit a long-running command, call
    ``cancel_all_for_org(reason="stop_org")``, and assert the
    ``user_command_cancelled`` event landed on the JSONL file with
    ``cancelled_by="stop_org"``. This is the v17 audit signal that
    Sprint-6 must flip from 0/5 to >=4/5 in v18.
    """

    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    bus = _DiskWiredEventBus(store)
    lookup = MagicMock()
    lookup.get_org = MagicMock(return_value=_Org())

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    # Wire a real dispatch manager that the service forwards to.
    dispatch = CommandDispatchManager(
        command_service=None,
        lookup=lookup,
        event_bus=bus,
    )
    rt = MagicMock()
    rt.get_org = lookup.get_org
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    rt.send_command = AsyncMock(side_effect=slow_send)

    async def _cancel_user_command(
        org_id: str, command_id: str, *, cancel_reason: str | None = None
    ) -> dict[str, Any] | None:
        # Mint a tracker on demand so the dispatch path can flip it.
        tr = dispatch._registry.get(org_id, command_id)  # type: ignore[attr-defined]
        if tr is None:
            tr = _CommandTracker(
                org_id=org_id,
                command_id=command_id,
                root_node_id="root1",
                root_intent="task",
            )
            dispatch._registry.register(tr)  # type: ignore[attr-defined]
        return await dispatch.cancel_user_command(
            org_id, command_id, cancel_reason=cancel_reason
        )

    rt.cancel_user_command = AsyncMock(side_effect=_cancel_user_command)
    svc = OrgCommandService(rt, event_bus=bus)
    res = await svc.submit(OrgCommandRequest(org_id="org-int", content="task A"))
    cid = res["command_id"]
    # Give the background _schedule_run a tick.
    await asyncio.sleep(0.02)

    cancelled = await svc.cancel_all_for_org("org-int", reason="stop_org")
    assert cid in cancelled

    # Disk-side check: the audit-critical signal.
    events = _read_events(jsonl)
    user_cancelled = [
        e for e in events if e.get("type") == "user_command_cancelled"
    ]
    assert user_cancelled, "user_command_cancelled must land on events.jsonl"
    assert user_cancelled[0]["cancelled_by"] == "stop_org"
    assert user_cancelled[0]["reason"] == "stop_org"

    # And the cache stays consistent (Sprint-5 invariant preserved).
    outcome = svc._command_outcomes.get(cid)
    assert outcome is not None
    assert outcome["cancelled_by"] == "stop_org"


# ---------------------------------------------------------------------------
# P0-2 -- get_cancel_source bridge accessor (used by AgentPipelineExecutor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cancel_source_returns_stop_org_after_seeding() -> None:
    """case id: p06.cancelled_by.bridge_returns_source

    The executor cancel branch reads
    ``OrgCommandService.get_cancel_source(cid)`` so it can stamp
    ``cancelled_by`` on ``agent_run_cancelled``. This pin protects
    that contract: stop-org seeding must surface through the
    accessor.
    """

    rt = MagicMock()
    rt.get_org = MagicMock(return_value=_Org())
    svc = OrgCommandService(rt)
    svc._command_outcomes["cmd-stop"] = {
        "event": "agent_run_cancelled",
        "cancelled_by": "stop_org",
        "ts": time.time(),
    }
    svc._command_outcomes["cmd-watch"] = {
        "event": "agent_run_watchdog_killed",
        "cancelled_by": "watchdog",
        "ts": time.time(),
    }
    svc._command_outcomes["cmd-user"] = {
        "event": "agent_run_cancelled",
        # No cancelled_by: user-initiated path stays None so the
        # executor falls back to the legacy "user_cancel" reason.
        "ts": time.time(),
    }

    assert svc.get_cancel_source("cmd-stop") == "stop_org"
    assert svc.get_cancel_source("cmd-watch") == "watchdog"
    assert svc.get_cancel_source("cmd-user") is None
    assert svc.get_cancel_source("missing") is None


# ---------------------------------------------------------------------------
# P0-2 -- watchdog emit payload carries cancelled_by on disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_kill_emits_cancelled_by_watchdog_to_disk(
    tmp_path: Path,
) -> None:
    """case id: p06.cancelled_by.watchdog_emit_to_disk

    Watchdog parity: pre-Sprint-6 the ``agent_run_watchdog_killed``
    JSONL line carried elapsed_s / threshold_s but no
    ``cancelled_by``. Sprint-6 adds the field so audits can attribute
    the kill to the watchdog source uniformly with stop-org / user
    cancels.
    """

    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    bus = _DiskWiredEventBus(store)

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    rt = MagicMock()
    rt.get_org = MagicMock(return_value=_Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    rt.cancel_user_command = AsyncMock(return_value={"cancelled_roots": ["root1"]})
    rt.send_command = AsyncMock(side_effect=slow_send)
    svc = OrgCommandService(rt, event_bus=bus)
    svc.configure_watchdog(poll_interval_secs=0.1, default_threshold_secs=0.5)
    svc.start_watchdog()
    try:
        res = await svc.submit(OrgCommandRequest(org_id="org-int", content="long"))
        cid = res["command_id"]
        await asyncio.sleep(1.2)
        task = svc._inflight_tasks.get(cid)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
        events = _read_events(jsonl)
        watchdog_events = [
            e for e in events if e.get("type") == "agent_run_watchdog_killed"
        ]
        assert watchdog_events, "watchdog emit must hit events.jsonl"
        assert watchdog_events[0]["cancelled_by"] == "watchdog"
        # The cache side stays consistent.
        outcome = svc._command_outcomes.get(cid)
        assert outcome is not None
        assert outcome["cancelled_by"] == "watchdog"
    finally:
        await svc.stop_watchdog(timeout=0.5)


# ---------------------------------------------------------------------------
# P0-2 -- executor wires cancel_source_provider correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_cancel_consults_cancel_source_provider() -> None:
    """case id: p06.cancelled_by.executor_uses_provider

    The executor accepts a ``cancel_source_provider``; the production
    wiring binds it to ``OrgCommandService.get_cancel_source``. This
    test directly drives the wiring with an in-process bus + a
    provider that returns ``stop_org`` and asserts the emitted
    ``agent_run_cancelled`` payload reflects it.
    """

    from openakita.orgs._runtime_agent_pipeline import (
        AgentCache,
        AgentPipelineExecutor,
        AgentSpec,
        ProfileResolver,
    )

    class _CancellingAgent:
        async def run(self, content: str) -> Any:  # noqa: ARG002
            await asyncio.sleep(60)

    class _Lookup:
        def get_org(self, org_id: str) -> Any:
            return _Org()

        def get_org_dir(self, org_id: str) -> str:  # noqa: ARG002
            return "/tmp"

    class _BypassResolver(ProfileResolver):
        def resolve(self, **kwargs: Any) -> AgentSpec:  # type: ignore[override]
            return AgentSpec(
                org_id=str(kwargs.get("org_id") or ""),
                node_id=str(kwargs.get("node_id") or ""),
                role="worker",
            )

    class _DirectBuilder:
        def build(self, spec: AgentSpec) -> Any:  # noqa: ARG002
            return _CancellingAgent()

        def teardown(self, agent: Any) -> None:  # noqa: ARG002
            return None

    bus = MagicMock()
    bus.emit = AsyncMock()
    cache = AgentCache(builder=_DirectBuilder())
    lookup = _Lookup()
    executor = AgentPipelineExecutor(
        cache=cache,
        resolver=_BypassResolver(lookup=lookup),
        lookup=lookup,
        event_bus=bus,
        cancel_source_provider=lambda cid: "stop_org" if cid == "cid-stop" else None,
    )

    task = asyncio.create_task(
        executor.activate_and_run(
            org_id="org-int",
            node_id="producer",
            content="long task",
            command_id="cid-stop",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Find the agent_run_cancelled emit and check the payload.
    cancel_calls = [
        c for c in bus.emit.await_args_list if c.args and c.args[0] == "agent_run_cancelled"
    ]
    assert cancel_calls, "executor must emit agent_run_cancelled on user cancel"
    payload = cancel_calls[0].args[1]
    assert payload["cancelled_by"] == "stop_org"
    assert payload["reason"] == "stop_org"


@pytest.mark.asyncio
async def test_executor_cancel_defaults_user_cancel_without_provider() -> None:
    """case id: p06.cancelled_by.executor_default_user_cancel

    Provider returns ``None`` -> Sprint-3 default path: payload keeps
    ``reason="user_cancel"`` (back-compat) and sets
    ``cancelled_by="user_cancel"`` so the new schema is always
    well-formed.
    """

    from openakita.orgs._runtime_agent_pipeline import (
        AgentCache,
        AgentPipelineExecutor,
        AgentSpec,
        ProfileResolver,
    )

    class _CancellingAgent:
        async def run(self, content: str) -> Any:  # noqa: ARG002
            await asyncio.sleep(60)

    class _Lookup:
        def get_org(self, org_id: str) -> Any:
            return _Org()

        def get_org_dir(self, org_id: str) -> str:  # noqa: ARG002
            return "/tmp"

    class _BypassResolver(ProfileResolver):
        def resolve(self, **kwargs: Any) -> AgentSpec:  # type: ignore[override]
            return AgentSpec(
                org_id=str(kwargs.get("org_id") or ""),
                node_id=str(kwargs.get("node_id") or ""),
                role="worker",
            )

    class _DirectBuilder:
        def build(self, spec: AgentSpec) -> Any:  # noqa: ARG002
            return _CancellingAgent()

        def teardown(self, agent: Any) -> None:  # noqa: ARG002
            return None

    bus = MagicMock()
    bus.emit = AsyncMock()
    cache = AgentCache(builder=_DirectBuilder())
    lookup = _Lookup()
    executor = AgentPipelineExecutor(
        cache=cache,
        resolver=_BypassResolver(lookup=lookup),
        lookup=lookup,
        event_bus=bus,
        cancel_source_provider=lambda cid: None,
    )
    task = asyncio.create_task(
        executor.activate_and_run(
            org_id="org-int",
            node_id="producer",
            content="long task",
            command_id="cid-user",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cancel_calls = [
        c for c in bus.emit.await_args_list if c.args and c.args[0] == "agent_run_cancelled"
    ]
    assert cancel_calls
    payload = cancel_calls[0].args[1]
    assert payload["reason"] == "user_cancel"
    assert payload["cancelled_by"] == "user_cancel"
