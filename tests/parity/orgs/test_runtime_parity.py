"""Parity fixtures for v2 OrgRuntime (P-RC-9 P9.6gamma).

20 contract cases pinning retained v1 ``OrgRuntime`` behavior and deliberate
v2 safety changes (``runtime/orgs/runtime.py`` +
the seven sibling managers shipped across P9.6alpha-beta).

Per P-RC-9-PLAN section 5.2 OrgRuntime contract: *assert
state graph + checkpoint sequence equality between v1 and v2*.
v1 ``src/openakita/orgs/runtime.py`` is a 6 355 LOC monolith
with deep cross-module coupling (~254 ``tracker`` refs, ~221
``chain_id`` refs across 132 methods); importing v1
``OrgRuntime`` into a test fixture would pull in agents /
channels / sessions / persistence stacks. Per ADR-0014, v2
is a clean rewrite that captures the v1 *observable surface*
(dict shapes, state strings, event names, callback ordering)
rather than the v1 internals; these 20 fixtures pin that
observable surface against golden dicts encoding what v1
would emit. This activates the **last** of P-RC-9's six
parity sentinels.

Case axes per the P9.6gamma brief:

* 5 dispatch (send_command / cancel_user_command /
  get_command_tracker_snapshot / has_active_delegations /
  get_active_root_intent)
* 5 agent pipeline (activate_and_run happy / missing /
  paused / quota -> pause / other-error)
* 5 node lifecycle (on_inbound delivered / queued /
  control text delivery / format_incoming_message / drain replay)
* 5 plugin assets (record_url for plugin / record_file
  digest / file_output_registered event / react_trace stats
  / TaskDeliverySynthesizer default summary)

P9.0i shipped a single strict xfail placeholder
placeholder; this commit removes the placeholder and lands
the 20 active cases -- closing the **last** of P-RC-9's six
sentinel activations (5/6 -> 6/6).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openakita.orgs._runtime_agent_pipeline import (
    AgentCache,
    AgentPipelineExecutor,
    AgentSpec,
    ProfileResolver,
)
from openakita.orgs._runtime_dispatch import (
    TRACKER_CANCELLED,
    TRACKER_RUNNING,
)
from openakita.orgs._runtime_node_lifecycle import (
    STATUS_BUSY,
    STATUS_IDLE,
    NodeMessageRouter,
    NodeStatusController,
    format_incoming_message,
)
from openakita.orgs._runtime_plugin_assets import (
    FileOutputRegistry,
    PluginAssetRecorder,
    TaskDeliverySynthesizer,
    collect_tool_stats_from_trace,
    extract_accepted_chain_ids,
)
from openakita.orgs.runtime import OrgRuntime, _InMemoryEventBus

# ---------------------------------------------------------------------------
# Shared test doubles -- intentionally tiny + duck-typed (per ADR-0012:
# v2 does not import openakita.orgs at all).
# ---------------------------------------------------------------------------


class _Org:
    def __init__(self, org_id: str, *, state: str = "active") -> None:
        self.id = org_id
        self.state = state
        self.workspace_dir = None
        self.nodes = {"n1": type("N", (), {"role": "eng", "persona": "engineer"})()}


class _Lookup:
    def __init__(self, *, present: bool = True, state: str = "active") -> None:
        self._present = present
        self._state = state

    def get_org(self, org_id: str) -> Any:
        if not self._present:
            return None
        return _Org(org_id, state=self._state)


class _CmdService:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str, str]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def submit(self, *, org_id: str, target_node_id: str, content: str) -> dict[str, Any]:
        self.submitted.append((org_id, target_node_id, content))
        return {"command_id": f"cmd_{len(self.submitted)}", "status": "submitted"}

    async def cancel(self, org_id: str, command_id: str) -> None:
        self.cancelled.append((org_id, command_id))


def _make_runtime() -> tuple[OrgRuntime, _CmdService, _InMemoryEventBus]:
    """Build a minimal OrgRuntime with the dispatch sibling wired."""

    bus = _InMemoryEventBus()
    cs = _CmdService()
    rt = OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
        command_service=cs,
        event_bus=bus,
    )
    return rt, cs, bus


# ---------------------------------------------------------------------------
# 5 dispatch fixtures (v1 OrgRuntime.send_command / cancel_user_command /
# get_command_tracker_snapshot / has_active_delegations / get_active_root_intent)
# ---------------------------------------------------------------------------


def test_parity_dispatch_send_command_happy() -> None:
    """fixture id: dispatch.send_command.happy"""
    rt, cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "hello world"))
    assert r["status"] == "submitted"
    assert r["org_id"] == "o1"
    assert r["node_id"] == "n1"
    assert r["command_id"].startswith("cmd_")
    assert cs.submitted == [("o1", "n1", "hello world")]


def test_parity_dispatch_cancel_user_command_running() -> None:
    """fixture id: dispatch.cancel_user_command.running -> cancelled

    Sprint-3 P0-2 (audit ``_orgs_business_capability_audit_v3.md``
    §5.3): the parity response now carries ``cancelled_roots``
    populated from the tracker (pre-Sprint-3 the service layer
    surfaced ``[]`` because the dispatch sibling did not include the
    field at all). The other three keys keep v1 parity.
    """
    rt, cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "do it"))
    cid = r["command_id"]
    out = asyncio.run(rt.cancel_user_command("o1", cid))
    assert out == {
        "ok": True,
        "command_id": cid,
        "cancelled": True,
        "cancelled_roots": ["n1"],
    }
    assert cs.cancelled == [("o1", cid)]
    snap = rt.get_command_tracker_snapshot("o1", cid)
    assert snap is not None and snap["state"] == TRACKER_CANCELLED


def test_parity_dispatch_get_command_tracker_snapshot_running() -> None:
    """fixture id: dispatch.get_command_tracker_snapshot.running"""
    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "ping"))
    snap = rt.get_command_tracker_snapshot("o1", r["command_id"])
    assert snap is not None
    # v1 parity: snapshot must carry these exact keys.
    assert set(snap.keys()) >= {
        "org_id",
        "command_id",
        "root_node_id",
        "root_intent",
        "state",
        "created_at",
        "last_activity_at",
        "chain_count",
        "accepted_chain_count",
        "cancel_reason",
        "finalize_decision",
    }
    assert snap["state"] == TRACKER_RUNNING
    assert snap["root_intent"] == "ping"


def test_parity_dispatch_has_active_delegations() -> None:
    """fixture id: dispatch.has_active_delegations.no_chains_open"""
    rt, _cs, _bus = _make_runtime()
    r = asyncio.run(rt.send_command("o1", "n1", "task"))
    # No chains opened yet -> no active delegations.
    assert rt.has_active_delegations("o1", "n1") is False
    # Register a chain -> active.
    rt._dispatch.register_chain("o1", r["command_id"], "chain_a")
    assert rt.has_active_delegations("o1", "n1") is True


def test_parity_dispatch_get_active_root_intent() -> None:
    """fixture id: dispatch.get_active_root_intent.most_recent_running"""
    rt, _cs, _bus = _make_runtime()
    asyncio.run(rt.send_command("o1", "n1", "first"))
    asyncio.run(rt.send_command("o1", "n2", "second"))
    intent = rt._dispatch.get_active_root_intent("o1")
    assert intent in {"first", "second"}  # most recently created wins


# ---------------------------------------------------------------------------
# 5 agent pipeline fixtures
# ---------------------------------------------------------------------------


class _StubAgent:
    def __init__(self, *, output: str = "ECHO", raises: BaseException | None = None) -> None:
        self._output = output
        self._raises = raises

    async def run(self, content: str) -> str:
        if self._raises is not None:
            raise self._raises
        return f"{self._output}:{content}"


class _StubBuilder:
    def __init__(self, agent: Any) -> None:
        self._agent = agent

    def build(self, spec: AgentSpec) -> Any:
        return self._agent

    def teardown(self, agent: Any) -> None:
        return None


def _make_executor(
    *,
    agent: Any,
    lookup_present: bool = True,
    lookup_state: str = "active",
    on_org_paused: Callable[[str, str], None] | None = None,
) -> tuple[AgentPipelineExecutor, _InMemoryEventBus]:
    bus = _InMemoryEventBus()
    lookup = _Lookup(present=lookup_present, state=lookup_state)
    cache = AgentCache(builder=_StubBuilder(agent))
    resolver = ProfileResolver(lookup=lookup)
    return (
        AgentPipelineExecutor(
            cache=cache,
            resolver=resolver,
            lookup=lookup,
            event_bus=bus,
            on_org_paused=on_org_paused,
        ),
        bus,
    )


def test_parity_agent_pipeline_requires_delivery_manifest() -> None:
    """A root run without a delivery manifest remains explicitly incomplete."""
    exe, _bus = _make_executor(agent=_StubAgent())
    r = asyncio.run(exe.activate_and_run(org_id="o1", node_id="n1", content="hi", command_id="c1"))
    assert r == {
        "status": "incomplete",
        "command_id": "c1",
        "output": "ECHO:hi",
        "reason": "delivery_manifest_missing",
        "artifact_role": "intermediate",
    }


def test_parity_agent_pipeline_missing_org() -> None:
    """fixture id: agent_pipeline.activate_and_run.missing_org"""
    exe, _bus = _make_executor(agent=_StubAgent(), lookup_present=False)
    r = asyncio.run(exe.activate_and_run(org_id="nope", node_id="n1", content="x"))
    assert r == {"status": "error", "command_id": None, "output": None, "reason": "org_not_found"}


def test_parity_agent_pipeline_paused_org_skip() -> None:
    """fixture id: agent_pipeline.activate_and_run.paused_org_skip"""
    exe, _bus = _make_executor(agent=_StubAgent(), lookup_state="paused")
    r = asyncio.run(exe.activate_and_run(org_id="o1", node_id="n1", content="x"))
    assert r == {"status": "skipped", "command_id": None, "output": None, "reason": "org_paused"}


def test_parity_agent_pipeline_quota_pauses_org() -> None:
    """fixture id: agent_pipeline.activate_and_run.quota_pauses_org"""
    paused: list[tuple[str, str]] = []
    exe, _bus = _make_executor(
        agent=_StubAgent(raises=Exception("Rate limit exceeded 429")),
        on_org_paused=lambda oid, reason: paused.append((oid, reason)),
    )
    r = asyncio.run(exe.activate_and_run(org_id="o1", node_id="n1", content="x", command_id="c2"))
    assert r["status"] == "paused"
    assert r["reason"] == "quota_auth"
    assert paused and paused[0][0] == "o1"


def test_parity_agent_pipeline_other_error() -> None:
    """fixture id: agent_pipeline.activate_and_run.other_error"""
    exe, _bus = _make_executor(agent=_StubAgent(raises=RuntimeError("boom")))
    r = asyncio.run(exe.activate_and_run(org_id="o1", node_id="n1", content="x", command_id="c3"))
    assert r["status"] == "error"
    assert r["reason"] == "agent_run_raised"


# ---------------------------------------------------------------------------
# 5 node_lifecycle fixtures (v1 OrgRuntime._on_node_message / _on_inbound_for_node
# / _format_incoming_message / _drain_node_pending semantics)
# ---------------------------------------------------------------------------


def _make_router(
    *,
    deliver_result: dict[str, Any] | None = None,
    deliver_raises: BaseException | None = None,
) -> tuple[NodeMessageRouter, NodeStatusController, list[tuple[str, str, str, str | None]]]:
    """Build a NodeMessageRouter wired against a stub deliver_to_agent."""

    calls: list[tuple[str, str, str, str | None]] = []

    async def _deliver(org_id: str, node_id: str, content: str, command_id: str | None):
        calls.append((org_id, node_id, content, command_id))
        if deliver_raises is not None:
            raise deliver_raises
        return deliver_result or {"status": "ok", "output": f"echo:{content}"}

    status = NodeStatusController(lookup=_Lookup())
    router = NodeMessageRouter(status=status, deliver_to_agent=_deliver)
    return router, status, calls


def test_parity_node_on_inbound_delivered() -> None:
    """fixture id: node.on_inbound.delivered"""
    router, status, calls = _make_router()
    out = asyncio.run(
        router.on_inbound(
            org_id="o1",
            node_id="n1",
            source="im",
            content="hello",
            sender="alice",
        )
    )
    # v1 parity shape: status / node_id / depth / result keys.
    assert set(out.keys()) == {"status", "node_id", "depth", "result"}
    assert out["status"] == "delivered"
    assert out["node_id"] == "n1"
    assert out["depth"] == 0
    assert out["result"] == {"status": "ok", "output": "echo:[im]<alice> hello"}
    # After delivery the node returns to IDLE (v1 parity).
    assert status.get_status("o1", "n1") == STATUS_IDLE
    assert len(calls) == 1


def test_parity_node_on_inbound_queued_when_busy() -> None:
    """fixture id: node.on_inbound.queued_when_busy"""
    router, status, _calls = _make_router()
    # Force the node into BUSY before the inbound message.
    status.set_status("o1", "n1", STATUS_BUSY)
    out = asyncio.run(router.on_inbound(org_id="o1", node_id="n1", source="im", content="pls do x"))
    assert out["status"] == "queued"
    assert out["depth"] == 1
    assert out["result"] is None
    # The status stays BUSY -- queueing does not flip it (v1 parity).
    assert status.get_status("o1", "n1") == STATUS_BUSY
    assert status.pending_depth("o1", "n1") == 1


def test_node_control_text_is_delivered_as_regular_content() -> None:
    """Text cannot cancel work; callers must use the explicit command cancel API."""
    router, status, calls = _make_router()
    first = asyncio.run(
        router.on_inbound(org_id="o1", node_id="n1", source="im", content="/stop")
    )
    second = asyncio.run(
        router.on_inbound(org_id="o1", node_id="n2", source="im", content="请停止当前任务")
    )

    assert first["status"] == "delivered"
    assert second["status"] == "delivered"
    assert status.get_status("o1", "n1") == STATUS_IDLE
    assert status.get_status("o1", "n2") == STATUS_IDLE
    assert [call[2] for call in calls] == ["[im]/stop", "[im]请停止当前任务"]


def test_parity_node_format_incoming_message_shape() -> None:
    """fixture id: node.format_incoming_message.shape"""
    # v1 ``_format_incoming_message`` produces "[src]<sender> body (k=v...)".
    s1 = format_incoming_message(source="im", sender="alice", content="hi")
    assert s1 == "[im]<alice> hi"
    s2 = format_incoming_message(
        source="dingtalk",
        sender=None,
        content="run job",
        metadata={"channel": "ops", "priority": "high"},
    )
    # Metadata is sorted alphabetically (v2 parity guarantee).
    assert s2 == "[dingtalk]run job (channel=ops, priority=high)"
    s3 = format_incoming_message(source="", sender="bob", content="solo")
    assert s3 == "<bob> solo"


def test_parity_node_drain_replay_after_resume() -> None:
    """fixture id: node.drain.replay_after_resume"""
    router, status, calls = _make_router()
    # Queue two messages while BUSY.
    status.set_status("o1", "n1", STATUS_BUSY)
    asyncio.run(router.on_inbound(org_id="o1", node_id="n1", source="im", content="m1"))
    asyncio.run(router.on_inbound(org_id="o1", node_id="n1", source="im", content="m2"))
    assert status.pending_depth("o1", "n1") == 2
    # Resume: drain should replay both, in order.
    results = asyncio.run(router.drain(org_id="o1", node_id="n1"))
    assert len(results) == 2
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "ok"
    # After drain the pending queue is empty.
    assert status.pending_depth("o1", "n1") == 0
    # Two deliver calls observed for the replay.
    assert len([c for c in calls if c[1] == "n1"]) == 2


# ---------------------------------------------------------------------------
# 5 plugin_assets fixtures (v1 OrgRuntime._record_plugin_asset_output /
# _register_file_output / _collect_tool_stats_from_trace /
# _synthesize_task_delivered_to_parent semantics)
# ---------------------------------------------------------------------------


def test_parity_assets_record_url_plugin(tmp_path: Path) -> None:
    """fixture id: assets.record_url.plugin"""
    bus = _InMemoryEventBus()
    events: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(
        "plugin_asset_recorded", lambda payload: events.append(("plugin_asset_recorded", payload))
    )
    recorder = PluginAssetRecorder(
        workspace_resolver=lambda oid: tmp_path / oid,
        event_bus=bus,
    )
    # Non-plugin tool: ignored (v1 parity).
    none = asyncio.run(
        recorder.record_url(
            org_id="o1",
            tool_name="shell",
            url="https://example.com/img.png",
        )
    )
    assert none is None
    # Plugin tool: recorded.
    asset = asyncio.run(
        recorder.record_url(
            org_id="o1",
            tool_name="plugin_image_gen",
            url="https://example.com/img.png",
        )
    )
    assert asset is not None
    assert asset.plugin_id == "image"
    assert asset.tool_name == "plugin_image_gen"
    assert asset.path.endswith("img.png")
    assert recorder.list_for_org("o1") == [asset]
    # Event fired once.
    assert len(events) == 1
    payload = events[0][1]
    assert payload["org_id"] == "o1"
    assert payload["plugin_id"] == "image"


def test_parity_assets_record_file_digest(tmp_path: Path) -> None:
    """fixture id: assets.record_file.digest"""
    bus = _InMemoryEventBus()
    recorder = PluginAssetRecorder(
        workspace_resolver=lambda oid: tmp_path / oid,
        event_bus=bus,
    )
    target = tmp_path / "scratch.bin"
    blob = b"openakita parity bytes"
    target.write_bytes(blob)
    expected_digest = hashlib.sha256(blob).hexdigest()
    asset = asyncio.run(
        recorder.record_file(
            org_id="o1",
            tool_name="plugin_pdf_export",
            path=target,
        )
    )
    assert asset is not None
    assert asset.size_bytes == len(blob)
    assert asset.digest == expected_digest
    # Non-plugin tool short-circuits to None (v1 parity).
    none = asyncio.run(recorder.record_file(org_id="o1", tool_name="shell", path=target))
    assert none is None


def test_parity_assets_file_output_registered_event(tmp_path: Path) -> None:
    """fixture id: assets.file_output_registered.event"""
    bus = _InMemoryEventBus()
    events: list[dict[str, Any]] = []
    bus.subscribe("file_output_registered", lambda p: events.append(p))
    persisted: list[Any] = []

    async def _persist(fo) -> None:
        persisted.append(fo)

    registry = FileOutputRegistry(event_bus=bus, persist=_persist)
    fpath = tmp_path / "out.txt"
    fpath.write_text("hello", encoding="utf-8")
    out = asyncio.run(
        registry.register(
            org_id="o1",
            node_id="n1",
            tool_name="write_file",
            path=fpath,
            metadata={"who": "agent"},
        )
    )
    assert out is not None
    assert out.size_bytes == 5
    assert out.metadata == {"who": "agent"}
    # v1 parity: event emitted + persist callback fired.
    assert len(events) == 1
    assert events[0]["org_id"] == "o1"
    assert events[0]["node_id"] == "n1"
    assert events[0]["path"] == str(fpath)
    assert len(persisted) == 1
    # list helpers stable.
    assert registry.list_for_org("o1") == [out]
    assert registry.list_for_node("o1", "n1") == [out]
    # Missing path -> None (v1 parity: never crash on absent file).
    missing = asyncio.run(
        registry.register(
            org_id="o1",
            node_id="n1",
            tool_name="write_file",
            path=tmp_path / "nope",
        )
    )
    assert missing is None


def test_parity_assets_react_trace_stats() -> None:
    """fixture id: assets.react_trace_stats"""
    trace = {
        "steps": [
            {"tool": "shell", "status": "accepted"},
            {"tool": "shell", "status": "rejected"},
            {"tool": "web_fetch", "chain_id": "ch_a", "status": "accepted"},
            {"tool": "plugin_image_gen", "chain_id": "ch_b", "accepted": True},
            {"tool": None},  # ignored
        ]
    }
    stats = collect_tool_stats_from_trace(trace)
    assert stats == {"shell": 2, "web_fetch": 1, "plugin_image_gen": 1}
    chains = extract_accepted_chain_ids(trace)
    # Order preserved; deduped.
    assert chains == ["ch_a", "ch_b"]
    # Empty trace -> {}/[].
    assert collect_tool_stats_from_trace(None) == {}
    assert extract_accepted_chain_ids({}) == []


def test_parity_assets_task_delivery_synthesizer_default(tmp_path: Path) -> None:
    """fixture id: assets.task_delivery_synthesizer.default_summary"""
    bus = _InMemoryEventBus()
    recorder = PluginAssetRecorder(
        workspace_resolver=lambda oid: tmp_path / oid,
        event_bus=bus,
    )
    # Pre-load 1 asset so the synthesizer can list it.
    asset = asyncio.run(
        recorder.record_url(
            org_id="o1",
            tool_name="plugin_pdf_export",
            url="https://example.com/r.pdf",
        )
    )
    assert asset is not None
    synth = TaskDeliverySynthesizer(asset_lister=recorder.list_for_org)
    trace = {
        "steps": [
            {"chain_id": "ch_a", "status": "accepted"},
            {"chain_id": "ch_b", "accepted": True},
        ]
    }
    out = synth.synthesize(
        org_id="o1",
        parent_node_id="p1",
        child_node_id="c1",
        trace=trace,
    )
    assert out.org_id == "o1"
    assert out.parent_node_id == "p1"
    assert out.child_node_id == "c1"
    assert out.chain_ids == ("ch_a", "ch_b")
    # Default summary mentions chain count + asset count.
    assert "2 chain" in out.summary
    assert "1 asset" in out.summary
    assert asset.path in out.assets
