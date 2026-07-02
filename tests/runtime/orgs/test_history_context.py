"""test17 Task2: lightweight, budget-capped org history context injection.

``OrgCommandService._build_history_context`` digests the org's recent
``user_command`` + ``command_done`` events into a short background block, and
``submit`` prepends it to a FRESH command's task (never for continue_previous).
These tests pin the digest shape, the budget caps, and the wiring.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import (
    ORG_HISTORY_INSTRUCTION_CHARS,
    ORG_HISTORY_MAX_COMMANDS,
    ORG_HISTORY_SUMMARY_CHARS,
    OrgCommandService,
)


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, *, roots: tuple[str, ...] = ("root1",)) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _FakeStore:
    """Minimal event store: ``query`` filters an in-memory event list."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.appended: list[dict[str, Any]] = []

    def query(self, *, event_type: str | None = None, limit: int = 100, **_kw: Any):
        items = [
            e
            for e in self._events
            if not event_type or e.get("type") == event_type or e.get("event_type") == event_type
        ]
        return items[-limit:] if limit else []

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        rec = dict(event)
        self._events.append(rec)
        self.appended.append(rec)
        return rec


def _runtime_with_store(store: _FakeStore, org: _Org | None = None) -> MagicMock:
    rt = MagicMock()
    rt.get_org = MagicMock(return_value=org if org is not None else _Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=store)
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    rt.ensure_command_project = MagicMock(return_value=None)
    return rt


def _history_events() -> list[dict[str, Any]]:
    """Two prior commands (each user_command + command_done) + current one."""
    return [
        {"type": "user_command", "command_id": "cmd_a", "content": "策划一份AI沙龙方案", "ts": 100.0},
        {
            "type": "command_done",
            "command_id": "cmd_a",
            "status": "done",
            "result": {"final_message": "沙龙方案V1：主题、议程、预算5500元、风险预案已完成。", "outcome": "done"},
            "ts": 150.0,
        },
        {"type": "user_command", "command_id": "cmd_b", "content": "补一版宣传文案", "ts": 200.0},
        {
            "type": "command_done",
            "command_id": "cmd_b",
            "status": "done",
            "result": {"final_message": "宣传材料包：标题库+多平台文案+SEO关键词已交付。", "outcome": "done"},
            "ts": 250.0,
        },
    ]


def test_history_context_digests_prior_commands() -> None:
    store = _FakeStore(_history_events())
    svc = OrgCommandService(_runtime_with_store(store))
    ctx = svc._build_history_context("o1", "root1", current_command_id="cmd_current")
    assert ctx
    assert "组织历史参考" in ctx
    assert ctx.rstrip().endswith("[本次指令]")
    # both prior instructions + summaries present, chronological order
    assert "策划一份AI沙龙方案" in ctx
    assert "补一版宣传文案" in ctx
    assert "沙龙方案V1" in ctx
    assert "宣传材料包" in ctx
    assert ctx.index("策划一份AI沙龙方案") < ctx.index("补一版宣传文案")


def test_history_context_excludes_current_and_returns_empty_without_history() -> None:
    # Only the current command's own user_command exists -> no prior history.
    store = _FakeStore(
        [{"type": "user_command", "command_id": "cmd_current", "content": "本次指令", "ts": 10.0}]
    )
    svc = OrgCommandService(_runtime_with_store(store))
    assert svc._build_history_context("o1", "root1", current_command_id="cmd_current") == ""


def test_history_context_enforces_budget_caps() -> None:
    long_instr = "指" * 500
    long_summary = "果" * 5000
    events: list[dict[str, Any]] = []
    # More commands than the cap, each oversized.
    for i in range(ORG_HISTORY_MAX_COMMANDS + 3):
        cid = f"cmd_{i}"
        events.append({"type": "user_command", "command_id": cid, "content": long_instr, "ts": float(i)})
        events.append(
            {
                "type": "command_done",
                "command_id": cid,
                "status": "done",
                "result": {"final_message": long_summary},
                "ts": float(i) + 0.5,
            }
        )
    store = _FakeStore(events)
    svc = OrgCommandService(_runtime_with_store(store))
    ctx = svc._build_history_context("o1", "root1", current_command_id="none")
    # At most ORG_HISTORY_MAX_COMMANDS numbered entries.
    assert ctx.count("\n1. 指令：") + ctx.count("1. 指令：") >= 1
    for n in range(ORG_HISTORY_MAX_COMMANDS + 1, ORG_HISTORY_MAX_COMMANDS + 4):
        assert f"{n}. 指令：" not in ctx
    # Field-level truncation applied (no full 500/5000-char field survives).
    assert ("指" * (ORG_HISTORY_INSTRUCTION_CHARS + 1)) not in ctx
    assert ("果" * (ORG_HISTORY_SUMMARY_CHARS + 1)) not in ctx


@pytest.mark.asyncio
async def test_submit_prepends_history_for_fresh_command_only() -> None:
    store = _FakeStore(_history_events())
    captured: dict[str, str] = {}

    def _factory(*, org_id: str, command_id: str, root_node_id: str, task: str, **_kw: Any) -> Any:
        captured["task"] = task
        # a supervisor stub whose run finishes immediately
        sup = MagicMock()

        async def _run() -> Any:
            from openakita.runtime.supervisor import FinalOutcome, SupervisorOutcome

            return SupervisorOutcome(
                outcome=FinalOutcome.DONE,
                final_message="ok",
                final_checkpoint_id="cp1",
                n_turns=1,
                n_replans=0,
                reason="",
            )

        sup.run = _run
        sup.cancel_token = MagicMock(is_cancelled=lambda: False, reason="")
        sup.stall_detector = type("_S", (), {"n_turns": 0, "n_stalls": 0})()
        sup.history = []
        sup.n_replans = 0
        sup.last_checkpoint_id = "cp1"
        return sup

    svc = OrgCommandService(_runtime_with_store(store), supervisor_factory=_factory)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="把上次的方案改成30人精简版"))
    # the factory is invoked inside the spawned background run coroutine
    inflight = svc._inflight_tasks.get(res["command_id"])
    if inflight is not None:
        await asyncio.wait_for(inflight, timeout=5.0)
    task = captured.get("task", "")
    assert "组织历史参考" in task
    assert "把上次的方案改成30人精简版" in task
    # the user's real instruction still appears AFTER the history block
    assert task.index("组织历史参考") < task.index("把上次的方案改成30人精简版")
