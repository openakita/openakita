"""Sprint-4 P0-1 regression: explicit XML child dispatch + recursion gate.

The v15 audit (``_orgs_business_capability_audit_v4.md`` §6.2) found
that despite Sprint-3 plumbing the entry-node id correctly, every B
level still showed ``cmds_with_>=2_unique_nodes = 0/35``. The producer
node's LLM was inventing screenwriter / art-director voices inside one
``messages_create_async`` call instead of really handing the work off
to those nodes.

This file pins the new explicit ``<dispatch target="...">...</dispatch>``
parser + ``executor.dispatch_subtask`` recursion path:

* The regex extracts at most :data:`MAX_DISPATCH_BLOCKS` blocks, in
  LLM order, ignoring malformed / empty targets.
* The parent's aggregated reply contains the parent's coordination
  text (with the raw blocks replaced by a ``[dispatched to X]``
  marker) and each child's output fenced by its node id.
* The recursion respects :data:`MAX_DISPATCH_DEPTH`: depth-2 grand-
  children may still produce LLM text but their own ``<dispatch>``
  blocks are silently ignored.
* Unknown / missing child node ids do not raise -- they return a
  short marker string so one bad ``<dispatch>`` cannot poison the
  surviving siblings.
* The executor emits ``subtask_assigned`` + appends a
  ``data/delegation_logs/`` line for every child hop, with the real
  ``parent_node_id`` (entry-dispatch path used ``None``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs import _runtime_dispatch as dispatch_mod
from openakita.orgs._default_agent_builder import (
    DefaultAgentBuilder,
    parse_dispatch_blocks,
)
from openakita.orgs._runtime_agent_pipeline import (
    MAX_DISPATCH_BLOCKS,
    MAX_DISPATCH_DEPTH,
    AgentCache,
    AgentPipelineExecutor,
    AgentSpec,
    ProfileResolver,
    current_command_id_var,
)


# ---------------------------------------------------------------------------
# Fixtures: lookup + bus + brain stubs minimal enough for end-to-end
# ``activate_and_run`` exercises without spinning up the real OrgRuntime.
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str, role: str = "worker") -> None:
        self.id = id_
        self.role = role
        self.persona = None


class _Org:
    def __init__(self, node_ids: list[str]) -> None:
        self.status = SimpleNamespace(value="active")
        self.state = "active"
        self.nodes = [_Node(nid) for nid in node_ids]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes[:1])


class _Lookup:
    def __init__(self, node_ids: list[str], *, org_dir: Path | None = None) -> None:
        self._org = _Org(node_ids)
        self._org_dir = org_dir

    def get_org(self, org_id: str) -> _Org | None:
        return self._org

    def get_org_dir(self, org_id: str) -> Path | None:  # noqa: ARG002
        return self._org_dir


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))

    def add_tap(self, _tap: Any) -> None:
        pass


def _make_brain(reply_factory: Any) -> Any:
    """Build a brain whose ``messages_create_async`` returns different replies
    per call (so the producer can emit dispatch blocks and screenwriter /
    art-director can reply with plain text).

    ``reply_factory`` is either a callable ``(call_index) -> str`` or a list
    of strings. We wrap the result in the Anthropic-shaped
    ``SimpleNamespace(content=[SimpleNamespace(text=...)])`` so
    ``_extract_text_from_response`` finds it.
    """

    call_index = {"n": 0}

    def _resolve(**_kwargs: Any) -> SimpleNamespace:
        idx = call_index["n"]
        call_index["n"] = idx + 1
        if callable(reply_factory):
            text = reply_factory(idx)
        elif isinstance(reply_factory, list):
            text = reply_factory[min(idx, len(reply_factory) - 1)]
        else:
            text = str(reply_factory)
        return SimpleNamespace(content=[SimpleNamespace(text=text)])

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(side_effect=_resolve),
        set_trace_context=lambda _ctx: None,
    )
    return brain


def _make_executor(
    *,
    bus: _RecordingBus,
    lookup: _Lookup,
    brain: Any,
) -> AgentPipelineExecutor:
    profile_resolver = ProfileResolver(lookup=lookup)
    executor_holder: dict[str, AgentPipelineExecutor] = {}

    async def _dispatch_subtask_cb(
        *,
        org_id: str,
        parent_node_id: str,
        child_node_id: str,
        child_content: str,
    ) -> str:
        # Mirror the production wiring in ``api/server.py`` -- the
        # parent's command_id rides the ContextVar so the child gets
        # attributed back to the user-command, not orphaned.
        return await executor_holder["e"].dispatch_subtask(
            org_id=org_id,
            parent_node_id=parent_node_id,
            parent_command_id=current_command_id_var.get("") or None,
            child_node_id=child_node_id,
            child_content=child_content,
        )

    builder = DefaultAgentBuilder(
        brain_provider=lambda: brain,
        dispatch_callback=_dispatch_subtask_cb,
    )
    cache = AgentCache(builder=builder)
    executor = AgentPipelineExecutor(
        cache=cache,
        resolver=profile_resolver,
        lookup=lookup,
        event_bus=bus,
    )
    executor_holder["e"] = executor
    return executor


# ---------------------------------------------------------------------------
# parse_dispatch_blocks unit tests
# ---------------------------------------------------------------------------


def test_parse_dispatch_blocks_extracts_target_and_content() -> None:
    """case id: p0_1.parser.basic_extraction

    The minimum-viable contract: one well-formed ``<dispatch>`` block
    yields one ``(target, content)`` pair with both fields stripped of
    incidental whitespace.
    """

    text = "前置说明\n<dispatch target=\"screenwriter\">写 30 秒短视频脚本</dispatch>\n结束语"
    pairs = parse_dispatch_blocks(text)
    assert pairs == [("screenwriter", "写 30 秒短视频脚本")]


def test_parse_dispatch_blocks_caps_at_max() -> None:
    """case id: p0_1.parser.respects_max_blocks_cap

    A runaway LLM that emits 20 dispatch blocks gets truncated at
    :data:`MAX_DISPATCH_BLOCKS` -- the parser must never let the
    parent fan out more children than the policy allows.
    """

    text = "".join(
        f"<dispatch target=\"n{i}\">do {i}</dispatch>\n" for i in range(20)
    )
    pairs = parse_dispatch_blocks(text)
    assert len(pairs) == MAX_DISPATCH_BLOCKS
    assert pairs[0] == ("n0", "do 0")
    assert pairs[-1] == ("n4", "do 4")


def test_parse_dispatch_blocks_skips_empty_target() -> None:
    """case id: p0_1.parser.skips_blank_target

    ``<dispatch target="">...</dispatch>`` -- the LLM forgot to fill
    the attribute. We skip rather than forwarding to a non-existent
    node (which dispatch_subtask would then warn about anyway).
    """

    text = "<dispatch target=\"\">body</dispatch><dispatch target=\"ok\">x</dispatch>"
    pairs = parse_dispatch_blocks(text)
    assert pairs == [("ok", "x")]


def test_parse_dispatch_blocks_no_blocks_returns_empty() -> None:
    """case id: p0_1.parser.no_blocks_no_pairs"""

    assert parse_dispatch_blocks("just plain text") == []
    assert parse_dispatch_blocks("") == []
    assert parse_dispatch_blocks(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Recursion through activate_and_run -> agent.run -> dispatch_subtask
# ---------------------------------------------------------------------------


def test_dispatch_executes_real_child_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_1.recursion.parent_to_two_children

    The headline pass: producer emits two dispatch blocks
    (screenwriter, art-director). The executor recurses serially into
    both; each one's LLM call is a real ``messages_create_async``
    invocation; the aggregated parent output contains both child
    replies fenced by node id.

    Verifiable signals (the v15 audit's missing pieces):

    * Two ``subtask_assigned`` events fire for the children (one per
      child), each with ``kind=child_dispatch`` and a real
      ``parent_node_id``.
    * Two extra ``agent_run_started`` / ``agent_run_finished`` pairs
      fire (one per child) -- the executor's bookkeeping is per-call,
      not per-command.
    * The aggregated parent text contains both child outputs.
    """

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    bus = _RecordingBus()
    lookup = _Lookup(["producer", "screenwriter", "art-director"])
    brain = _make_brain([
        # producer reply -- emits two dispatch blocks + coordination text
        "我会先让 screenwriter 写脚本, 然后 art-director 出分镜:\n"
        "<dispatch target=\"screenwriter\">写 30 秒短视频脚本</dispatch>\n"
        "<dispatch target=\"art-director\">出 6 帧分镜板</dispatch>",
        # screenwriter reply
        "[剧本]\n场景 1: ...",
        # art-director reply
        "[分镜]\n1. 远景 2. 中景 3. 特写",
    ])
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="策划一个 30 秒短视频",
            command_id="cmd_x",
        )
    )

    assert result["status"] == "ok"
    output = result["output"]
    assert "[from node `screenwriter`]" in output
    assert "[from node `art-director`]" in output
    assert "[剧本]" in output
    assert "[分镜]" in output
    # The raw XML blocks are stripped from the parent text but the
    # surrounding commentary survives so the user can read the parent's
    # plan AND the children's deliverables.
    assert "<dispatch" not in output
    assert "[dispatched to screenwriter]" in output

    event_names = [n for n, _ in bus.events]
    # 1 entry agent_run_started + 1 finished + 2 subtask_assigned
    # + 2 child agent_run_started + 2 child agent_run_finished
    assert event_names.count("agent_run_started") == 3
    assert event_names.count("agent_run_finished") == 3
    assert event_names.count("subtask_assigned") == 2

    subtasks = [p for n, p in bus.events if n == "subtask_assigned"]
    targets = sorted(p["child_node_id"] for p in subtasks)
    assert targets == ["art-director", "screenwriter"]
    for p in subtasks:
        assert p["parent_node_id"] == "producer"
        assert p["depth"] == 1
        assert p["kind"] == "child_dispatch"


def test_dispatch_unknown_child_node_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_1.recursion.unknown_child_skipped

    A typo'd / hallucinated child node id must not crash the parent.
    The executor logs + returns a placeholder marker; the parent's
    aggregated output surfaces the marker so the user sees what went
    wrong. Sibling dispatches in the same parent reply still run.
    """

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    bus = _RecordingBus()
    lookup = _Lookup(["producer", "screenwriter"])  # no art-director
    brain = _make_brain([
        # producer emits two blocks; one points at a missing node
        "<dispatch target=\"screenwriter\">scene 1</dispatch>"
        "<dispatch target=\"art-director\">storyboard</dispatch>",
        "[剧本] ok",
    ])
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="x",
            command_id="cmd_y",
        )
    )

    assert result["status"] == "ok"
    output = result["output"]
    assert "[剧本] ok" in output
    assert "skipped: unknown node" in output
    # Only one subtask_assigned because the unknown node short-circuits
    # before the emit (we don't pretend to dispatch to a node that does
    # not exist).
    subtasks = [p for n, p in bus.events if n == "subtask_assigned"]
    assert len(subtasks) == 1
    assert subtasks[0]["child_node_id"] == "screenwriter"


def test_dispatch_depth_gate_blocks_grandchildren(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_1.recursion.depth_cap_enforced

    Generic over :data:`MAX_DISPATCH_DEPTH` (★ multi-level routing bumped
    it from 3 to 6 so deep org charts can cascade). We build a linear
    chain ``n0 -> n1 -> ... -> n{cap}`` where every node emits a dispatch
    to the next. The in-agent gate (``depth >= MAX_DISPATCH_DEPTH - 1``)
    must let nodes at depths 0..cap-1 run but suppress the dispatch the
    deepest RUNNING node (depth cap-1) emits, so ``n{cap}`` never runs.
    The ``dispatch_subtask`` gate is a defence in depth that would also
    refuse a depth-``cap`` call if the in-agent gate ever failed open.
    """

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    cap = MAX_DISPATCH_DEPTH
    chain = [f"n{i}" for i in range(cap + 1)]  # n0 .. n{cap}
    bus = _RecordingBus()
    lookup = _Lookup(chain)
    # Each node body emits a dispatch to the next node in the chain.
    replies = [
        f"[body{i}]\n<dispatch target=\"{chain[i + 1]}\">go</dispatch>"
        for i in range(len(chain) - 1)
    ]
    replies.append("[bodyN] leaf")  # n{cap} would-be body (never reached)
    brain = _make_brain(replies)
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="n0",
            content="kickoff",
            command_id="cmd_d",
        )
    )

    assert result["status"] == "ok"
    output = result["output"]
    # Bodies of the deepest two RUNNING nodes surface in the tree.
    assert f"[body{cap - 2}]" in output
    assert f"[body{cap - 1}]" in output

    # Exactly ``cap`` LLM calls: nodes at depths 0..cap-1 run; the node at
    # depth cap-1 does NOT dispatch (gate), so n{cap} is never invoked.
    assert brain.messages_create_async.await_count == cap

    # subtask_assigned fired once per successful hop: n0->n1 ...
    # n{cap-2}->n{cap-1} == cap-1 hops. The deepest running node's
    # dispatch to n{cap} is suppressed, so no n{cap} hop exists.
    subtasks = [
        (p["parent_node_id"], p["child_node_id"])
        for n, p in bus.events
        if n == "subtask_assigned"
    ]
    assert subtasks == [(chain[i], chain[i + 1]) for i in range(cap - 1)]

    # The deepest RUNNING node's output still has the raw <dispatch> tag
    # (the gate suppressed parsing rather than executing then hiding).
    assert f'<dispatch target="{chain[cap]}">' in output


def test_dispatch_writes_child_delegation_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_1.recursion.delegation_log_child_line

    Sprint-3 wrote the entry-dispatch line with
    ``parent_node=None, kind=entry_dispatch``. Sprint-4 child
    dispatches must produce a *separate* line with the real parent
    node id and ``kind=child_dispatch`` so analyses that count
    "producer -> screenwriter" hops can filter unambiguously.
    """

    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path
    )

    bus = _RecordingBus()
    lookup = _Lookup(["producer", "screenwriter"])
    brain = _make_brain([
        "<dispatch target=\"screenwriter\">do it</dispatch>",
        "done",
    ])
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="x",
            command_id="cmd_log",
        )
    )

    today = datetime.now().strftime("%Y%m%d")
    log_path = tmp_path / f"{today}.jsonl"
    assert log_path.is_file()
    lines = [
        json.loads(ln)
        for ln in log_path.read_text("utf-8").splitlines()
        if ln.strip()
    ]
    # NB: only the child dispatch is logged from this test path. The
    # entry-dispatch JSONL line is written by ``_runtime_dispatch.
    # send_command`` (not exercised here because we call ``activate_
    # and_run`` directly).
    assert len(lines) == 1
    rec = lines[0]
    assert rec["parent_node"] == "producer"
    assert rec["child_node"] == "screenwriter"
    assert rec["kind"] == "child_dispatch"
    assert rec["depth"] == 1


def test_dispatch_max_depth_constant_matches_recursion_cap() -> None:
    """case id: p0_1.recursion.constant_invariant

    Defensive pin on the recursion ceiling. ★ Multi-level routing raised
    this from 3 to 6 so a deep org chart (主编 → 策划编辑 → 文案写手 → …)
    can cascade level by level; the real terminator is topology
    (``_available_nodes_for`` hands each node only its direct reports, so
    leaves stop the recursion), and this cap is the runaway safety net.
    """

    assert MAX_DISPATCH_DEPTH == 6
    assert MAX_DISPATCH_BLOCKS == 5


def test_dispatch_callback_not_wired_keeps_sprint3_behaviour() -> None:
    """case id: p0_1.builder.no_callback_no_recursion

    When the builder is constructed without a ``dispatch_callback``
    (the Sprint-2 / Sprint-3 default), the agent's ``run`` must NOT
    parse dispatch blocks. This is what keeps the existing
    ``test_default_agent_builder.py`` regression suite green and what
    lets unit tests instantiate the builder standalone.
    """

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            return_value=SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text="<dispatch target=\"x\">ignored</dispatch>plain text"
                    )
                ]
            )
        ),
        set_trace_context=lambda _ctx: None,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    agent = builder.build(
        AgentSpec(org_id="o1", node_id="producer", role="worker")
    )
    out = asyncio.run(agent.run("hi"))
    # Without a dispatch callback the agent returns the raw LLM text
    # verbatim -- no parsing, no recursion. The XML stays in the
    # output because there is nothing to splice it with.
    assert "<dispatch" in out
    assert "plain text" in out


@pytest.mark.asyncio
async def test_dispatch_propagates_cancellation_through_child() -> None:
    """case id: p0_1.recursion.cancel_propagates_to_child

    When the parent task is cancelled while a child dispatch is in
    flight, ``CancelledError`` must propagate up through both
    ``activate_and_run`` calls (child first, then parent) and the
    parent agent's run must not swallow it. The parent's
    ``except Exception`` (which surfaces other child failures as
    text) deliberately does NOT catch ``CancelledError`` because in
    Python 3.11 it is a ``BaseException`` subclass.
    """

    bus = _RecordingBus()
    lookup = _Lookup(["producer", "screenwriter"])

    parent_reply = SimpleNamespace(
        content=[
            SimpleNamespace(
                text="<dispatch target=\"screenwriter\">slow</dispatch>"
            )
        ]
    )
    call_index = {"n": 0}

    async def _resolve(**_kwargs: Any) -> SimpleNamespace:
        idx = call_index["n"]
        call_index["n"] = idx + 1
        if idx == 0:
            return parent_reply
        # Child reply -- sleep so the outer cancel can land while we
        # are parked inside the LLM await (mirrors the production
        # case where ``httpx`` is mid-stream).
        await asyncio.sleep(60)
        return SimpleNamespace(content=[SimpleNamespace(text="never")])

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(side_effect=_resolve),
        set_trace_context=lambda _ctx: None,
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    async def _do_run() -> None:
        await executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="x",
            command_id="cmd_c",
        )

    task = asyncio.create_task(_do_run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The child's executor saw the cancel and emitted ``agent_run_
    # cancelled``; the parent's executor did too (re-raise from child
    # propagates up). Both events should be present.
    cancel_events = [
        p for n, p in bus.events if n == "agent_run_cancelled"
    ]
    assert len(cancel_events) >= 1
    node_ids = {p["node_id"] for p in cancel_events}
    assert "screenwriter" in node_ids or "producer" in node_ids
