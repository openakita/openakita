"""核心1 + 核心2 + 核心3 regression: parent-executed review, rework loop, node timeout.

These pin the orchestration *running logic* fixes (2026-06, test8
``org_1c4a0c81855b`` RCA):

* 核心1 (逐级校验): a child's deliverable is reviewed BY its connected
  upstream node (the parent), not by a central heuristic. The review runs a
  real LLM call through the parent's brain and returns a 通过/退回 verdict.
* 核心2 (重做闭环): on 退回 the child is RE-DISPATCHED with the parent's
  concrete feedback (it genuinely re-enters 进行中 via a fresh
  ``agent_run_started``), bounded by ``OPENAKITA_ORG_REWORK_MAX``; on
  exhaustion the runtime escalates (``node_review_escalated``) instead of
  hanging or looping forever.
* 核心3 (超时隔离): a single node activation that blocks past
  ``OPENAKITA_ORG_NODE_TIMEOUT_S`` is failed-and-reported
  (``agent_run_failed reason=node_timeout``) so one stuck node cannot freeze
  the whole org.

The review is OFF by default in the test-suite (see ``tests/conftest.py``);
these tests opt in explicitly so the deterministic legacy dispatch tests stay
unaffected.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openakita.orgs._default_agent_builder import (
    DefaultAgentBuilder,
    _parse_review_verdict,
)
from openakita.orgs._runtime_agent_pipeline import (
    AgentCache,
    AgentPipelineExecutor,
    ProfileResolver,
    current_command_id_var,
)


class _Node:
    def __init__(self, id_: str, role: str = "worker") -> None:
        self.id = id_
        self.role = role
        self.persona = None


class _Edge:
    def __init__(self, source: str, target: str, edge_type: str = "hierarchy") -> None:
        self.source = source
        self.target = target
        self.edge_type = SimpleNamespace(value=edge_type)


class _Org:
    def __init__(self, node_ids: list[str], *, edges: list[_Edge] | None = None) -> None:
        self.status = SimpleNamespace(value="active")
        self.state = "active"
        self.nodes = [_Node(nid) for nid in node_ids]
        # ``edges`` left as an empty list keeps the topology guard FAIL-OPEN
        # (mirrors a legacy/unwired org); supplying edges opts a test into the
        # hard adjacency enforcement.
        self.edges = list(edges or [])

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes[:1])


class _Lookup:
    def __init__(
        self,
        node_ids: list[str],
        *,
        org_dir: Path | None = None,
        edges: list[_Edge] | None = None,
    ) -> None:
        self._org = _Org(node_ids, edges=edges)
        self._org_dir = org_dir

    def get_org(self, org_id: str) -> _Org | None:  # noqa: ARG002
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


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _is_review_call(kwargs: dict[str, Any]) -> bool:
    """A review call is identified by the reviewer system prompt marker."""
    system = str(kwargs.get("system") or "")
    return "审阅" in system and "裁决" in system


def _make_executor(*, bus: _RecordingBus, lookup: _Lookup, brain: Any) -> AgentPipelineExecutor:
    profile_resolver = ProfileResolver(lookup=lookup)
    holder: dict[str, AgentPipelineExecutor] = {}

    async def _cb(*, org_id: str, parent_node_id: str, child_node_id: str, child_content: str) -> str:
        return await holder["e"].dispatch_subtask(
            org_id=org_id,
            parent_node_id=parent_node_id,
            parent_command_id=current_command_id_var.get("") or None,
            child_node_id=child_node_id,
            child_content=child_content,
        )

    builder = DefaultAgentBuilder(brain_provider=lambda: brain, dispatch_callback=_cb)
    cache = AgentCache(builder=builder)
    executor = AgentPipelineExecutor(
        cache=cache, resolver=profile_resolver, lookup=lookup, event_bus=bus
    )
    holder["e"] = executor
    return executor


def _names(bus: _RecordingBus) -> list[str]:
    return [n for n, _ in bus.events]


# ---------------------------------------------------------------------------
# _parse_review_verdict unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expect_ok"),
    [
        ("裁决: 通过\n理由: 内容完整", True),
        ("裁决: 退回\n理由: 内容太短，缺少预算明细", False),
        ("裁决：未通过\n理由：只是思考过程", False),
        ("通过，质量达标", True),
        ("不达标，需要重做", False),
        ("PASS - looks good", True),
        ("REVISE: missing budget section", False),
        ("", True),  # fail-open on empty
        ("一些无关的话", True),  # fail-open when no verdict token
    ],
)
def test_parse_review_verdict(text: str, expect_ok: bool) -> None:
    ok, reason = _parse_review_verdict(text)
    assert ok is expect_ok
    assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# 核心1: parent reviews and accepts on first pass (no rework)
# ---------------------------------------------------------------------------


def test_parent_review_accepts_first_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])

    def _resolve(**kwargs: Any) -> SimpleNamespace:
        if _is_review_call(kwargs):
            return _resp("裁决: 通过\n理由: 成果完整达标")
        # n0 (root) dispatches to n1; n1 returns a real deliverable.
        user = str(kwargs.get("messages", [{}])[0].get("content", ""))
        if "kickoff" in user:
            return _resp('<dispatch target="n1">写一份完整方案</dispatch>')
        return _resp("这是 n1 的完整方案：第一部分……第二部分……结论。")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="kickoff", command_id="cmd_a"
        )
    )
    assert result["status"] == "ok"
    names = _names(bus)
    assert "node_review_passed" in names
    assert "node_rework_requested" not in names
    assert "node_review_escalated" not in names
    # n1 ran exactly once (no rework): one started/finished pair beyond root.
    n1_started = [p for n, p in bus.events if n == "agent_run_started" and p.get("node_id") == "n1"]
    assert len(n1_started) == 1


# ---------------------------------------------------------------------------
# item 2 (2026-06): hard-signal gate overrides a too-lenient model "通过"
# ---------------------------------------------------------------------------


def test_hard_gate_rejects_thinking_leak_even_if_model_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 'thinking…' leak must be HARD-rejected even when the reviewer model
    says 通过 — the objective gate (same as the central deliverable check)
    overrides a too-lenient model verdict, so a mid-layer parent can't accept
    a half-product."""
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")
    monkeypatch.setenv("OPENAKITA_ORG_REWORK_MAX", "1")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_HARD_GATE", "1")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])

    n1_runs = {"n": 0}

    def _resolve(**kwargs: Any) -> SimpleNamespace:
        if _is_review_call(kwargs):
            # Model is lenient and would always pass — the hard gate must
            # override the first (thinking-leak) attempt.
            return _resp("裁决: 通过\n理由: 看起来还行")
        user = str(kwargs.get("messages", [{}])[0].get("content", ""))
        if "kickoff" in user:
            return _resp('<dispatch target="n1">写一份完整方案</dispatch>')
        n1_runs["n"] += 1
        if n1_runs["n"] == 1:
            # First attempt: a raw thinking leak (objective half-product).
            return _resp("thinking 让我想想这个任务该怎么做，先搜索一下相关资料")
        # Rework attempt: a real, headed deliverable.
        return _resp("# 社区市集方案\n\n## 定位\n完整成文内容……\n\n## 流程\n详尽步骤。")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="kickoff", command_id="cmd_hg"
        )
    )
    assert result["status"] == "ok"
    names = _names(bus)
    # The thinking leak was hard-rejected -> exactly one rework, then the
    # real deliverable passed.
    assert names.count("node_rework_requested") == 1
    assert "node_review_passed" in names
    n1_started = [p for n, p in bus.events if n == "agent_run_started" and p.get("node_id") == "n1"]
    assert len(n1_started) == 2
    assert "社区市集方案" in (result.get("output") or "")


def test_hard_gate_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the hard gate OFF, a lenient model verdict is honoured (no rework)
    — proves the override is gated by OPENAKITA_ORG_REVIEW_HARD_GATE."""
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")
    monkeypatch.setenv("OPENAKITA_ORG_REWORK_MAX", "1")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_HARD_GATE", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])

    def _resolve(**kwargs: Any) -> SimpleNamespace:
        if _is_review_call(kwargs):
            return _resp("裁决: 通过\n理由: 可以")
        user = str(kwargs.get("messages", [{}])[0].get("content", ""))
        if "kickoff" in user:
            return _resp('<dispatch target="n1">写一份完整方案</dispatch>')
        return _resp("thinking 让我想想，先搜索一下")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="kickoff", command_id="cmd_hgoff"
        )
    )
    assert result["status"] == "ok"
    names = _names(bus)
    assert "node_rework_requested" not in names
    assert "node_review_passed" in names


# ---------------------------------------------------------------------------
# 核心2: reject -> rework -> child re-enters 进行中 -> accept
# ---------------------------------------------------------------------------


def test_reject_triggers_rework_then_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")
    monkeypatch.setenv("OPENAKITA_ORG_REWORK_MAX", "2")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])

    review_calls = {"n": 0}
    n1_runs = {"n": 0}

    def _resolve(**kwargs: Any) -> SimpleNamespace:
        if _is_review_call(kwargs):
            i = review_calls["n"]
            review_calls["n"] = i + 1
            # First review rejects, second accepts.
            if i == 0:
                return _resp("裁决: 退回\n理由: 内容太短，请补充活动预算与时间表。")
            return _resp("裁决: 通过\n理由: 已补全，达标。")
        user = str(kwargs.get("messages", [{}])[0].get("content", ""))
        if "kickoff" in user:
            return _resp('<dispatch target="n1">写一份完整方案</dispatch>')
        # n1 child run: detect whether the rework feedback was threaded in.
        n1_runs["n"] += 1
        if "退回意见" in user:
            return _resp("修订版：含活动预算与时间表的完整方案，内容详尽。")
        return _resp("草稿")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="kickoff", command_id="cmd_b"
        )
    )
    assert result["status"] == "ok"
    names = _names(bus)
    # Exactly one rework was requested, then it passed.
    assert names.count("node_rework_requested") == 1
    assert "node_review_passed" in names
    assert "node_review_escalated" not in names
    # The child genuinely re-entered 进行中: TWO agent_run_started for n1.
    n1_started = [p for n, p in bus.events if n == "agent_run_started" and p.get("node_id") == "n1"]
    assert len(n1_started) == 2
    # The rework re-dispatch carried the parent's feedback to the child.
    assert n1_runs["n"] == 2
    # The final accepted output is the revised version.
    assert "修订版" in (result.get("output") or "")


# ---------------------------------------------------------------------------
# 核心2: rework budget exhausted -> escalation (no infinite loop)
# ---------------------------------------------------------------------------


def test_rework_exhaustion_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")
    monkeypatch.setenv("OPENAKITA_ORG_REWORK_MAX", "1")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])

    def _resolve(**kwargs: Any) -> SimpleNamespace:
        if _is_review_call(kwargs):
            return _resp("裁决: 退回\n理由: 始终不达标。")  # never satisfied
        user = str(kwargs.get("messages", [{}])[0].get("content", ""))
        if "kickoff" in user:
            return _resp('<dispatch target="n1">写一份完整方案</dispatch>')
        return _resp("总是不够好的草稿")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="kickoff", command_id="cmd_c"
        )
    )
    assert result["status"] == "ok"  # still converges (returns last output)
    names = _names(bus)
    # rework_max=1 -> exactly one rework, then escalate (bounded, no loop).
    assert names.count("node_rework_requested") == 1
    assert "node_review_escalated" in names
    assert "node_review_passed" not in names
    n1_started = [p for n, p in bus.events if n == "agent_run_started" and p.get("node_id") == "n1"]
    assert len(n1_started) == 2  # original + 1 rework


# ---------------------------------------------------------------------------
# 核心3: a single stuck node times out and reports failure (no org freeze)
# ---------------------------------------------------------------------------


def test_node_timeout_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "0")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "1")
    bus = _RecordingBus()
    lookup = _Lookup(["n0"])

    async def _slow(**_kwargs: Any) -> SimpleNamespace:
        await asyncio.sleep(5)  # exceeds the 1s node timeout
        return _resp("never returned")

    brain = SimpleNamespace(messages_create_async=_slow, set_trace_context=lambda _c: None)
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1", node_id="n0", content="hang please", command_id="cmd_t"
        )
    )
    assert result["status"] == "error"
    assert result["reason"] == "node_timeout"
    failed = [p for n, p in bus.events if n == "agent_run_failed"]
    assert any(p.get("reason") == "node_timeout" for p in failed)


class _AsyncFn:
    """A tiny awaitable wrapper so a sync resolver can back ``messages_create_async``."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    async def __call__(self, **kwargs: Any) -> Any:
        return self._fn(**kwargs)


# ---------------------------------------------------------------------------
# 核心 (audit 2026-06): hard topology guard — dispatch must follow the org chart
# ---------------------------------------------------------------------------


def test_topology_guard_blocks_skip_level_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A coordinator may dispatch ONLY to its DIRECT reports.

    Even if a node EXISTS in the org, a 越级 (skip-level) dispatch to a
    non-child must be hard-refused — the prompt only OFFERS direct reports, but
    this guarantees adjacency structurally regardless of what the LLM emits.
    A valid dispatch to a real direct child still runs.
    """
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "0")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    bus = _RecordingBus()
    # n0 -> n1 -> n2 chain. n0's ONLY direct report is n1; n2 is a grandchild.
    edges = [_Edge("n0", "n1"), _Edge("n1", "n2")]
    lookup = _Lookup(["n0", "n1", "n2"], edges=edges)

    def _resolve(**_kwargs: Any) -> SimpleNamespace:
        return _resp("叶子节点完整成果：第一部分……结论。")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    # Valid: n0 -> n1 (direct child) runs and returns the deliverable.
    out_ok = asyncio.run(
        executor.dispatch_subtask(
            org_id="o1",
            parent_node_id="n0",
            parent_command_id="cmd_topo",
            child_node_id="n1",
            child_content="做这件事",
        )
    )
    assert "成果" in out_ok
    assert any(
        n == "agent_run_started" and p.get("node_id") == "n1" for n, p in bus.events
    )

    # 越级: n0 -> n2 (grandchild, NOT a direct report) is hard-refused before
    # any event is emitted or any agent runs.
    bus.events.clear()
    out_bad = asyncio.run(
        executor.dispatch_subtask(
            org_id="o1",
            parent_node_id="n0",
            parent_command_id="cmd_topo",
            child_node_id="n2",
            child_content="做这件事",
        )
    )
    assert "refused" in out_bad and "n2" in out_bad
    assert "subtask_assigned" not in _names(bus)
    assert not any(p.get("node_id") == "n2" for _, p in bus.events)


def test_topology_guard_fails_open_without_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the org exposes no edge metadata the guard FAILS OPEN.

    Pins the safety valve: a legacy / test stub org (no ``edges``) keeps the
    legacy existence-only check, so the guard never breaks a topology it
    cannot read.
    """
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "0")
    monkeypatch.setenv("OPENAKITA_ORG_NODE_TIMEOUT_S", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["n0", "n1"])  # no edges -> fail-open

    def _resolve(**_kwargs: Any) -> SimpleNamespace:
        return _resp("完整成果内容……")

    brain = SimpleNamespace(
        messages_create_async=_AsyncFn(_resolve), set_trace_context=lambda _c: None
    )
    executor = _make_executor(bus=bus, lookup=lookup, brain=brain)

    out = asyncio.run(
        executor.dispatch_subtask(
            org_id="o1",
            parent_node_id="n0",
            parent_command_id="cmd_open",
            child_node_id="n1",
            child_content="做这件事",
        )
    )
    assert "成果" in out
    assert "refused" not in out
