"""Sprint-5 P0-2 + watchdog regression.

Two related cancel-propagation regressions audit v5 flagged:

* **F2 stop-org** -- ``POST /api/v2/orgs/{id}/stop`` returned HTTP 200
  and flipped spec state to STOPPED but per-command ``_inflight_tasks``
  kept burning LLM tokens. 3 sprints of audits flagged this as the
  most user-visible "the cancel button lies" bug. Sprint-5 wires
  ``OrgLifecycleManager.on_stop_org`` -> ``cancel_all_for_org`` so the
  inflight tasks really die.
* **Watchdog (unexpected-finding 2)** -- ``Organization`` exposes
  ``watchdog_stuck_threshold_s`` (default 600 s since Sprint-8 P0-A;
  was 1800 s in Sprint-5 through Sprint-7) but nothing scanned it.
  Sprint-5 ships a background task that cancels stuck commands and
  emits ``agent_run_watchdog_killed`` for the events.jsonl trail.

This file pins both with focused mocks of the runtime / event bus so a
regression in either path fails in under 2 s rather than hanging on a
60 s mock LLM sleep.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    """Minimal org spec carrying the watchdog knobs the service reads."""

    def __init__(
        self,
        *,
        watchdog_enabled: bool = True,
        threshold_s: float | None = None,
        roots: tuple[str, ...] = ("root1",),
    ) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]
        self.watchdog_enabled = watchdog_enabled
        self.watchdog_stuck_threshold_s = threshold_s

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


class _StubEventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Any]] = {}
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, event: str, handler: Any) -> None:
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Any) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.emitted.append((event, dict(payload)))
        for h in list(self._subs.get(event, ())):
            res = h(payload)
            if asyncio.iscoroutine(res):
                await res


def _make_runtime(
    *,
    send_coro: Any = None,
    org: _Org | None = None,
) -> MagicMock:
    rt = MagicMock()
    rt.get_org = MagicMock(return_value=org if org is not None else _Org())
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())
    rt.cancel_user_command = AsyncMock(return_value={"cancelled_roots": ["root1"]})
    if send_coro is None:
        rt.send_command = AsyncMock(return_value={"status": "submitted"})
    else:
        rt.send_command = AsyncMock(side_effect=send_coro)
    return rt


# ---------------------------------------------------------------------------
# F2 stop-org propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_for_org_cancels_every_inflight_task() -> None:
    """case id: p05.stoporg.cancel_all_kills_every_task

    Two commands in flight on the same org. ``cancel_all_for_org``
    must cancel both within ~1 s and clean the secondary index so
    a later request does not see ghosts.
    """

    bus = _StubEventBus()

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    rt = _make_runtime(
        send_coro=slow_send,
        org=_Org(roots=("root1", "root2")),
    )
    svc = OrgCommandService(rt, event_bus=bus)
    res_a = await svc.submit(
        OrgCommandRequest(org_id="o1", target_node_id="root1", content="task A")
    )
    res_b = await svc.submit(
        OrgCommandRequest(org_id="o1", target_node_id="root2", content="task B")
    )
    await asyncio.sleep(0.02)
    cid_a, cid_b = res_a["command_id"], res_b["command_id"]
    assert {cid_a, cid_b} <= set(svc._inflight_by_org.get("o1", set()))

    cancelled = await svc.cancel_all_for_org("o1", reason="stop_org")
    assert set(cancelled) == {cid_a, cid_b}

    # Both tasks must reach a terminal state within ~1 s.
    for cid in (cid_a, cid_b):
        task = svc._inflight_tasks.get(cid)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
    await asyncio.sleep(0.02)

    # Outcome cache carries the explicit source marker so events.jsonl
    # readers can tell stop-org from user cancel.
    for cid in (cid_a, cid_b):
        outcome = svc._command_outcomes.get(cid)
        assert outcome is not None, f"missing outcome for {cid}"
        assert outcome["cancelled_by"] == "stop_org"
        assert outcome["event"] == "agent_run_cancelled"


@pytest.mark.asyncio
async def test_cancel_all_for_org_is_safe_when_no_tasks() -> None:
    """case id: p05.stoporg.no_tasks_is_noop

    Calling stop on an idle org must return ``[]`` quickly and not
    raise. (The lifecycle ``on_stop_org`` callback fires for *every*
    stop, even when no commands are running.)
    """

    svc = OrgCommandService(_make_runtime())
    out = await svc.cancel_all_for_org("idle-org", reason="stop_org")
    assert out == []


@pytest.mark.asyncio
async def test_cancel_all_for_org_swallows_runtime_cancel_failure() -> None:
    """case id: p05.stoporg.runtime_cancel_failure_swallowed

    The runtime's ``cancel_user_command`` may raise (the dispatch
    sibling may be mid-state-flip). The org-wide cancel must still
    finish cancelling the asyncio Task itself so the LLM connection
    closes -- that is the user-visible promise of the stop button.
    """

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    rt = _make_runtime(send_coro=slow_send)
    rt.cancel_user_command = AsyncMock(side_effect=RuntimeError("boom"))
    svc = OrgCommandService(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="t"))
    await asyncio.sleep(0.02)
    cid = res["command_id"]
    cancelled = await svc.cancel_all_for_org("o1", reason="stop_org")
    assert cid in cancelled
    task = svc._inflight_tasks.get(cid)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, TimeoutError):
            pass


@pytest.mark.asyncio
async def test_inflight_by_org_index_clears_on_completion() -> None:
    """case id: p05.stoporg.index_cleans_up

    The secondary ``_inflight_by_org`` index is bookkeeping for
    ``cancel_all_for_org``. Successful runs must clear their slot so a
    later stop-org call does not point at a finished task.
    """

    rt = _make_runtime()
    svc = OrgCommandService(rt)
    res = await svc.submit(OrgCommandRequest(org_id="o1", content="hi"))
    cid = res["command_id"]
    # Let the background task complete.
    await asyncio.sleep(0.05)
    org_set = svc._inflight_by_org.get("o1") or set()
    assert cid not in org_set


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_kills_task_past_threshold() -> None:
    """case id: p05.watchdog.kills_stuck_task

    The v16 audit B5 cases saw LLM runs burn 600 s+ on recursive
    prompts. We configure a tiny threshold + poll, submit a 30 s mock
    send_command, and assert the watchdog cancels it within ~2 s.
    """

    bus = _StubEventBus()

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    rt = _make_runtime(
        send_coro=slow_send, org=_Org(watchdog_enabled=True, threshold_s=0.5)
    )
    svc = OrgCommandService(rt, event_bus=bus)
    svc.configure_watchdog(poll_interval_secs=0.1, default_threshold_secs=0.5)
    started = svc.start_watchdog()
    assert started is True
    try:
        res = await svc.submit(OrgCommandRequest(org_id="o1", content="long"))
        cid = res["command_id"]
        # Wait long enough to exceed the 0.5 s threshold (with a
        # poll-interval buffer).
        await asyncio.sleep(1.2)
        task = svc._inflight_tasks.get(cid)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
        outcome = svc._command_outcomes.get(cid)
        assert outcome is not None
        assert outcome["cancelled_by"] == "watchdog"
        assert outcome["event"] == "agent_run_watchdog_killed"
        # The bus must have received the explicit event so the
        # events.jsonl trail surfaces watchdog kills (v17 audit).
        names = [name for name, _ in bus.emitted]
        assert "agent_run_watchdog_killed" in names
    finally:
        await svc.stop_watchdog(timeout=0.5)


@pytest.mark.asyncio
async def test_watchdog_skips_when_threshold_not_reached() -> None:
    """case id: p05.watchdog.under_threshold_no_kill

    The watchdog must not be trigger-happy. A short task that
    completes under the threshold must NOT be marked as
    watchdog-killed.
    """

    rt = _make_runtime(org=_Org(threshold_s=5.0))
    svc = OrgCommandService(rt)
    svc.configure_watchdog(poll_interval_secs=0.1, default_threshold_secs=5.0)
    svc.start_watchdog()
    try:
        res = await svc.submit(OrgCommandRequest(org_id="o1", content="quick"))
        cid = res["command_id"]
        # Quick task -- under the 5 s budget.
        await asyncio.sleep(0.3)
        outcome = svc._command_outcomes.get(cid) or {}
        assert outcome.get("cancelled_by") != "watchdog"
    finally:
        await svc.stop_watchdog(timeout=0.5)


@pytest.mark.asyncio
async def test_watchdog_disabled_on_spec_means_skip() -> None:
    """case id: p05.watchdog.disabled_on_spec

    Orgs can opt out via ``watchdog_enabled=False``. The loop must
    treat that as "skip cancel, keep running indefinitely" rather
    than falling back to the default threshold.
    """

    rt = _make_runtime(org=_Org(watchdog_enabled=False, threshold_s=0.1))
    svc = OrgCommandService(rt)
    threshold = svc._resolve_watchdog_threshold("o1")
    assert threshold == 0.0


@pytest.mark.asyncio
async def test_watchdog_resolve_threshold_falls_back_on_missing() -> None:
    """case id: p05.watchdog.threshold_fallback

    Spec without ``watchdog_stuck_threshold_s`` -> use the default
    (600 s in production since Sprint-8 P0-A; was 1800 s before;
    always configurable in tests via :meth:`configure_watchdog`).
    """

    rt = _make_runtime(org=_Org(threshold_s=None))
    svc = OrgCommandService(rt)
    svc.configure_watchdog(default_threshold_secs=1234.0)
    assert svc._resolve_watchdog_threshold("o1") == 1234.0


@pytest.mark.asyncio
async def test_watchdog_default_threshold_is_600s_after_sprint8() -> None:
    """case id: p05.watchdog.default_is_600s

    Sprint-8 P0-A (v19 audit ``_orgs_business_capability_audit_v8.md``
    §2 + §8.1): the production default tightens from 1800 s (30 min)
    to 600 s (10 min). The previous Sprint-5 default left genuinely
    stuck commands holding the inflight slot for half an hour; the
    new value still gives a 5x safety margin over the slowest
    legitimate run observed across v13-v19 audits (~3 min).

    The org spec stays the source of truth (``watchdog_stuck_threshold_s``
    on the spec wins over the default), so deployments that explicitly
    want the legacy 1800 s budget set it on the spec.
    """

    svc = OrgCommandService(_make_runtime())
    assert svc._watchdog_default_threshold_secs == 600.0
    # Spec without an explicit threshold falls back to the new default.
    threshold = svc._resolve_watchdog_threshold("o1")
    assert threshold == 600.0


@pytest.mark.asyncio
async def test_watchdog_spec_override_still_wins_over_new_default() -> None:
    """case id: p05.watchdog.spec_override_wins

    Sprint-8 P0-A regression guard: tightening the default must not
    break the spec-side override. Orgs that explicitly set
    ``watchdog_stuck_threshold_s=1800`` (legacy 30 min envelope) or
    ``=120`` (aggressive 2 min envelope) keep their declared value.
    """

    rt_legacy = _make_runtime(org=_Org(threshold_s=1800.0))
    svc_legacy = OrgCommandService(rt_legacy)
    assert svc_legacy._resolve_watchdog_threshold("o1") == 1800.0

    rt_strict = _make_runtime(org=_Org(threshold_s=120.0))
    svc_strict = OrgCommandService(rt_strict)
    assert svc_strict._resolve_watchdog_threshold("o1") == 120.0

    # Edge: zero / negative spec values are treated as "use default" by
    # ``_resolve_watchdog_threshold`` -- the v5 contract -- so they
    # must now resolve to 600 (not 1800). Sprint-5 disabled-via-flag
    # path (watchdog_enabled=False) still resolves to 0 unchanged
    # (covered by ``test_watchdog_disabled_on_spec_means_skip``).
    rt_zero = _make_runtime(org=_Org(threshold_s=0.0))
    svc_zero = OrgCommandService(rt_zero)
    assert svc_zero._resolve_watchdog_threshold("o1") == 600.0


@pytest.mark.asyncio
async def test_watchdog_start_is_idempotent() -> None:
    """case id: p05.watchdog.start_idempotent

    Double-calling ``start_watchdog`` returns ``False`` on the second
    call so the FastAPI lifespan can be defensively re-run without
    spawning twin loops.
    """

    svc = OrgCommandService(_make_runtime())
    svc.configure_watchdog(poll_interval_secs=0.1)
    first = svc.start_watchdog()
    second = svc.start_watchdog()
    try:
        assert first is True
        assert second is False
    finally:
        await svc.stop_watchdog(timeout=0.5)


@pytest.mark.asyncio
async def test_watchdog_stop_is_safe_when_not_started() -> None:
    """case id: p05.watchdog.stop_when_idle_is_noop"""

    svc = OrgCommandService(_make_runtime())
    await svc.stop_watchdog(timeout=0.1)  # must not raise.
    assert svc._watchdog_task is None


# ---------------------------------------------------------------------------
# Event handler co-operation: preserve cancelled_by metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_agent_event_preserves_stop_org_cancelled_by() -> None:
    """case id: p05.stoporg.event_handler_keeps_cancelled_by

    The Sprint-5 outcome cache pre-seeds ``cancelled_by=stop_org``
    when stop-org fires. When the executor's
    ``agent_run_cancelled`` event arrives moments later the handler
    must NOT overwrite the source marker with a generic value -- the
    v17 audit needs to tell apart user cancel / stop-org cancel /
    watchdog kill.
    """

    bus = _StubEventBus()
    svc = OrgCommandService(_make_runtime(), event_bus=bus)
    cid = "cmd_stop"
    # Pre-seed the outcome the way ``cancel_all_for_org`` does.
    svc._command_outcomes[cid] = {
        "event": "agent_run_cancelled",
        "reason": "stop_org",
        "error": None,
        "node_id": None,
        "output_len": None,
        "ts": time.time(),
        "cancelled_by": "stop_org",
    }
    await bus.emit(
        "agent_run_cancelled",
        {
            "org_id": "o1",
            "command_id": cid,
            "reason": "stop_org",
        },
    )
    outcome = svc._command_outcomes.get(cid) or {}
    assert outcome.get("cancelled_by") == "stop_org"
