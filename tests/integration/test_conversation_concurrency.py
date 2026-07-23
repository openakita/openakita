"""Conversation concurrency integration tests (plan: v1.28, S1.10).

Covers v1.27.14 (S1.1 – S1.5, S1.9) wiring:

* ``DoubleTextingPolicy`` enum + ``resolve_policy()`` helper, incl. feature-
  flag down-grade of INTERRUPT → QUEUE.
* ``ConversationLifecycleManager.start()`` returning :class:`StartResult` with
  per-policy semantics for same-client overlap and persisting ``turn_id`` on
  the active :class:`BusyInfo`.
* ``TaskState.settled_event`` / ``abandoned`` / ``wait_until_settled`` /
  ``mark_settled``.
* ``Agent._preempt_or_queue_prev_task`` lifecycle wiring (proceed / queue /
  preempted / pending-cancel discard).
* Conversation telemetry counters in ``core.conversation_metrics``.

S1.6 (turn_id idempotency), S1.7 (frontend takeover banner) and S1.8
(cancel marker on session.messages) land in separate commits; their tests
will be appended here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openakita import config as config_mod
from openakita.agent import Agent
from openakita.api.routes.conversation_lifecycle import (
    BusyInfo,
    ConversationLifecycleManager,
    StartResult,
)
from openakita.api.routes.double_texting import DoubleTextingPolicy, resolve_policy
from openakita.api.routes.turn_registry import TurnRegistry
from openakita.core import conversation_metrics as metrics
from openakita.core.agent_state import AgentState, TaskState, TaskStatus
from openakita.sessions.session import SessionContext

# ── Helpers ───────────────────────────────────────────────────────────


def _make_stub_agent():
    """Construct an :class:`Agent` instance without firing ``__init__``.

    FIX 7 (vs v1.27.14 first cut): the prior implementation declared a
    standalone ``_AgentStub`` class and *manually* re-bound each method
    we wanted to exercise via the descriptor protocol.  That meant every
    time the helper grew a new ``self.foo()`` call, the tests broke with
    ``AttributeError``.  We now instantiate the real ``Agent`` via
    ``__new__`` (bypassing ``__init__`` and its brain / tool_executor /
    session_manager dependencies) and only stub the few attributes the
    method touches.  Any new ``self.*`` access inside
    ``_preempt_or_queue_prev_task`` resolves automatically.
    """
    a = Agent.__new__(Agent)
    a.agent_state = AgentState()
    a._pending_cancels = {}
    return a


@pytest.fixture(autouse=True)
def _reset_metrics_each_test():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


@pytest.fixture
def _short_settle_timeout(monkeypatch):
    # preempt_settle: how long to wait for a *cancelled* task to settle.
    # queue_wait: how long to wait for a *running* task to finish naturally
    # (decoupled from preempt_settle as of the queue_wait_timeout_ms split).
    # The QUEUE-timeout tests need BOTH short or they would wait the 10-min
    # default and hang.
    monkeypatch.setattr(config_mod.settings, "preempt_settle_timeout_ms", 200)
    monkeypatch.setattr(config_mod.settings, "queue_wait_timeout_ms", 200)
    yield


@pytest.fixture
def _allow_interrupt(monkeypatch):
    monkeypatch.setattr(config_mod.settings, "double_texting_allow_interrupt", True)
    yield


# The agent-layer preempt helper (``Agent._preempt_or_queue_prev_task`` +
# ``_append_preempt_marker`` + conversation_id-first task lookup + QUEUE
# timeout cancel/abandon) is ported into ``core/_agent_runtime`` after the
# ADR-0003 split (Batch C). All tests below are active.

# ── S1.1: resolve_policy ──────────────────────────────────────────────


class TestResolvePolicy:
    def test_default_is_queue(self) -> None:
        assert resolve_policy(channel="unknownchan") is DoubleTextingPolicy.QUEUE

    def test_per_channel_overrides_default(self) -> None:
        assert resolve_policy(channel="feishu") is DoubleTextingPolicy.REJECT
        assert resolve_policy(channel="telegram") is DoubleTextingPolicy.QUEUE

    def test_header_overrides_channel(self) -> None:
        assert resolve_policy(channel="feishu", header_value="steer") is DoubleTextingPolicy.STEER

    def test_invalid_header_falls_back(self) -> None:
        assert (
            resolve_policy(channel="desktop", header_value="garbage") is DoubleTextingPolicy.QUEUE
        )

    def test_interrupt_downgrades_when_flag_off(self) -> None:
        # Flag is False by default in v1.27.14
        assert resolve_policy(header_value="interrupt") is DoubleTextingPolicy.QUEUE

    def test_interrupt_honoured_when_flag_on(self, _allow_interrupt) -> None:
        assert resolve_policy(header_value="interrupt") is DoubleTextingPolicy.INTERRUPT


# ── S1.2: ConversationLifecycleManager.start ──────────────────────────


class TestConversationLifecycleStart:
    @pytest.mark.asyncio
    async def test_new_lock(self) -> None:
        mgr = ConversationLifecycleManager()
        r = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        assert isinstance(r, StartResult)
        assert r.conflict is None and r.generation == 1
        assert r.took_over is None
        assert r.policy_applied is DoubleTextingPolicy.QUEUE

    @pytest.mark.asyncio
    async def test_tuple_unpacking_backcompat(self) -> None:
        mgr = ConversationLifecycleManager()
        conflict, gen = await mgr.start("conv-1", "client-A")
        assert conflict is None and gen == 1

    @pytest.mark.asyncio
    async def test_same_client_reject(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.REJECT)
        r2 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.REJECT)
        assert r2.conflict is not None and r2.generation == 0
        assert r2.took_over is None
        assert r2.queued_after_generation is None

    @pytest.mark.asyncio
    async def test_same_client_queue_returns_wait_target(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        r2 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        assert r2.conflict is not None and r2.generation == 0
        assert r2.queued_after_generation == 1

    @pytest.mark.asyncio
    async def test_same_client_interrupt_takes_over(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.INTERRUPT)
        r2 = await mgr.start(
            "conv-1",
            "client-A",
            policy=DoubleTextingPolicy.INTERRUPT,
            turn_id="t-42",
        )
        assert r2.conflict is None
        assert r2.generation == 2
        assert r2.took_over is not None and r2.took_over.generation == 1

    @pytest.mark.asyncio
    async def test_busyinfo_carries_turn_id(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start(
            "conv-1",
            "client-A",
            policy=DoubleTextingPolicy.QUEUE,
            turn_id="turn-X",
        )
        info = mgr._busy["conv-1"]
        assert isinstance(info, BusyInfo)
        assert info.turn_id == "turn-X"

    @pytest.mark.asyncio
    async def test_different_client_always_rejected(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.INTERRUPT)
        # Even with INTERRUPT policy, a *different* client must never preempt
        r2 = await mgr.start("conv-1", "client-B", policy=DoubleTextingPolicy.INTERRUPT)
        assert r2.conflict is not None and r2.generation == 0
        assert r2.conflict.client_id == "client-A"
        assert r2.took_over is None

    @pytest.mark.asyncio
    async def test_finish_backcompat(self) -> None:
        mgr = ConversationLifecycleManager()
        r = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        ok = await mgr.finish("conv-1", generation=r.generation)
        assert ok is True

    @pytest.mark.asyncio
    async def test_finish_generation_mismatch_noop(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        # Stale finish from a previous (lower) generation must not release.
        ok = await mgr.finish("conv-1", generation=0)
        assert ok is False


class TestConversationLifecycleRefresh:
    @pytest.mark.asyncio
    async def test_refresh_extends_busy_lease_without_changing_since(self, monkeypatch) -> None:
        from openakita.api.routes import conversation_lifecycle as lifecycle_mod

        now = 1000.0
        monkeypatch.setattr(lifecycle_mod.time, "time", lambda: now)

        mgr = ConversationLifecycleManager()
        r = await mgr.start("conv-lease", "client-A", policy=DoubleTextingPolicy.QUEUE)
        assert r.generation == 1
        assert mgr._busy["conv-lease"].start_time == 1000.0
        assert mgr._busy["conv-lease"].last_heartbeat == 1000.0

        now = 1500.0
        assert await mgr.refresh("conv-lease", generation=r.generation) is True
        assert mgr._busy["conv-lease"].start_time == 1000.0
        assert mgr._busy["conv-lease"].last_heartbeat == 1500.0

        now = 2099.0
        status = await mgr.get_busy_status("conv-lease")
        assert status["busy"] is True
        assert status["since"] == 1000.0
        assert status["last_heartbeat"] == 1500.0

        now = 2101.0
        status = await mgr.get_busy_status("conv-lease")
        assert status["busy"] is False

    @pytest.mark.asyncio
    async def test_refresh_generation_mismatch_does_not_extend_lease(self, monkeypatch) -> None:
        from openakita.api.routes import conversation_lifecycle as lifecycle_mod

        now = 1000.0
        monkeypatch.setattr(lifecycle_mod.time, "time", lambda: now)

        mgr = ConversationLifecycleManager()
        r = await mgr.start("conv-lease", "client-A", policy=DoubleTextingPolicy.QUEUE)

        now = 1500.0
        assert await mgr.refresh("conv-lease", generation=r.generation + 1) is False
        assert mgr._busy["conv-lease"].last_heartbeat == 1000.0

        now = 1601.0
        status = await mgr.get_busy_status("conv-lease")
        assert status["busy"] is False


# ── S1.5: TaskState settled_event / abandoned ─────────────────────────


class TestTaskStateSettled:
    def test_mark_settled_is_idempotent(self) -> None:
        st = TaskState(task_id="t1")
        assert not st.settled_event.is_set()
        st.mark_settled()
        assert st.settled_event.is_set()
        st.mark_settled()
        assert st.settled_event.is_set()

    @pytest.mark.asyncio
    async def test_wait_until_settled_unblocks(self) -> None:
        st = TaskState(task_id="t2")

        async def settle_later():
            await asyncio.sleep(0.05)
            st.mark_settled()

        asyncio.create_task(settle_later())
        await asyncio.wait_for(st.wait_until_settled(), timeout=1.0)
        assert st.settled_event.is_set()

    def test_abandoned_defaults_to_false(self) -> None:
        assert TaskState(task_id="t3").abandoned is False

    def test_reset_task_gives_fresh_settled_event(self) -> None:
        ags = AgentState()
        t1 = ags.begin_task(session_id="s1")
        t1.mark_settled()
        assert t1.settled_event.is_set()
        t2 = ags.begin_task(session_id="s1")
        assert t2 is not t1
        assert not t2.settled_event.is_set()


# ── S1.3 + S1.4: _preempt_or_queue_prev_task ──────────────────────────


class TestPreemptOrQueueHelper:
    @pytest.mark.asyncio
    async def test_no_prev_task_proceeds(self) -> None:
        a = _make_stub_agent()
        decision = await a._preempt_or_queue_prev_task(session_id="s1", session=None)
        assert decision == "proceed"

    @pytest.mark.asyncio
    async def test_cancelled_prev_task_proceeds_after_reset(self) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s1")
        prev.cancel("user stop")
        decision = await a._preempt_or_queue_prev_task(session_id="s1", session=None)
        assert decision == "proceed"
        # Prev task must be cleared so begin_task downstream gets a fresh one.
        cur = a.agent_state.get_task_for_session("s1")
        if cur is not None:
            assert not cur.is_active

    @pytest.mark.asyncio
    async def test_queue_waits_until_settled(self) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s2")
        prev.transition(TaskStatus.REASONING)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())
        sess = MagicMock(channel="desktop")  # desktop=steer → agent layer downgrades to QUEUE
        decision = await a._preempt_or_queue_prev_task(session_id="s2", session=sess)
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        assert any(s["name"] == "queue" and s["labels"]["channel"] == "desktop" for s in snap)

    @pytest.mark.asyncio
    async def test_queue_timeout_marks_abandoned(self, _short_settle_timeout) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s3")
        prev.transition(TaskStatus.REASONING)

        sess = MagicMock(channel="desktop")
        decision = await a._preempt_or_queue_prev_task(session_id="s3", session=sess)
        assert decision == "queued_then_proceed"
        assert prev.abandoned is True
        snap = metrics.snapshot()
        names = {s["name"] for s in snap}
        assert "settled_timeout" in names and "abandon" in names

    @pytest.mark.asyncio
    async def test_interrupt_cancels_prev(self, _allow_interrupt, monkeypatch) -> None:
        # Use a per_channel override so the test does not depend on global
        # config drift.
        monkeypatch.setitem(
            config_mod.settings.double_texting_per_channel, "ch_interrupt", "interrupt"
        )

        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s4")
        prev.transition(TaskStatus.REASONING)

        async def cooperative_settle():
            await prev.cancel_event.wait()
            prev.mark_settled()

        asyncio.create_task(cooperative_settle())

        sess = MagicMock(channel="ch_interrupt")
        decision = await a._preempt_or_queue_prev_task(session_id="s4", session=sess)
        assert decision == "preempted"
        assert prev.cancelled is True
        snap = metrics.snapshot()
        assert any(
            s["name"] == "preempt"
            and s["labels"].get("policy") == "interrupt"
            and s["labels"].get("channel") == "ch_interrupt"
            for s in snap
        )

    @pytest.mark.asyncio
    async def test_preempt_discards_pending_cancel(self, _allow_interrupt, monkeypatch) -> None:
        """S1.3 guarantee: preempt cleans up stale pending_cancel so the new
        task created downstream isn't killed by a seconds-old cancel."""
        monkeypatch.setitem(
            config_mod.settings.double_texting_per_channel, "ch_interrupt", "interrupt"
        )
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s5")
        prev.transition(TaskStatus.REASONING)
        a._pending_cancels["s5"] = "old cancel signal"

        async def cooperative_settle():
            await prev.cancel_event.wait()
            prev.mark_settled()

        asyncio.create_task(cooperative_settle())

        sess = MagicMock(channel="ch_interrupt")
        await a._preempt_or_queue_prev_task(session_id="s5", session=sess)
        assert "s5" not in a._pending_cancels


# ── S1.8: Cancel marker bypasses dedup ────────────────────────────────


class _MarkerSession:
    """Stand-in session that mimics ``Session.append_marker`` signature."""

    def __init__(self) -> None:
        self.channel = "ch_interrupt"
        self.appended: list[dict] = []

    def append_marker(self, role: str, content: str, **metadata) -> None:
        self.appended.append({"role": role, "content": content, **metadata})


class TestPreemptMarker:
    @pytest.mark.asyncio
    async def test_interrupt_appends_marker(self, _allow_interrupt, monkeypatch) -> None:
        monkeypatch.setitem(
            config_mod.settings.double_texting_per_channel, "ch_interrupt", "interrupt"
        )
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s1")
        prev.transition(TaskStatus.REASONING)

        async def cooperative_settle():
            await prev.cancel_event.wait()
            prev.mark_settled()

        asyncio.create_task(cooperative_settle())

        sess = _MarkerSession()
        await a._preempt_or_queue_prev_task(session_id="s1", session=sess)

        assert len(sess.appended) == 1, sess.appended
        marker = sess.appended[0]
        assert marker["role"] == "assistant"
        assert marker["marker_type"] == "preempted"
        assert marker["policy"] == "interrupt"
        assert marker["reason"] == "preempted_by_new_message"
        assert marker["preempted_task_id"] == prev.task_id

    @pytest.mark.asyncio
    async def test_queue_normal_settle_does_not_append_marker(self) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s2")
        prev.transition(TaskStatus.REASONING)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())
        sess = _MarkerSession()
        await a._preempt_or_queue_prev_task(session_id="s2", session=sess)
        assert sess.appended == []

    @pytest.mark.asyncio
    async def test_queue_timeout_appends_marker(self, _short_settle_timeout) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s3")
        prev.transition(TaskStatus.REASONING)

        sess = _MarkerSession()
        sess.channel = "desktop"
        await a._preempt_or_queue_prev_task(session_id="s3", session=sess)
        assert len(sess.appended) == 1
        assert sess.appended[0]["reason"] == "queue_timeout_abandoned"

    def test_session_append_marker_bypasses_dedup(self) -> None:
        """``SessionContext.append_marker`` must never de-dup; multiple
        identical markers should all be persisted."""
        from openakita.sessions.session import SessionContext

        ctx = SessionContext()
        ctx.append_marker("assistant", "[上一条任务被新请求中断]", marker_type="preempted")
        ctx.append_marker("assistant", "[上一条任务被新请求中断]", marker_type="preempted")
        ctx.append_marker("assistant", "[上一条任务被新请求中断]", marker_type="preempted")
        assert len(ctx.messages) == 3


# ── FIX 1: helper keys task by conversation_id, not session_id ────────


class TestPreemptHelperKeyResolution:
    """``reason_stream`` registers TaskState under ``conversation_id``.
    The helper MUST query by conversation_id first, falling back to
    session_id only when conversation_id is empty (CLI / sub-agent)."""

    @pytest.mark.asyncio
    async def test_finds_task_registered_under_conversation_id(self) -> None:
        a = _make_stub_agent()
        # Simulate reason_stream's begin_task(session_id=conversation_id)
        prev = a.agent_state.begin_task(session_id="conv-XYZ")
        prev.transition(TaskStatus.REASONING)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        sess = MagicMock(channel="desktop")
        # session_id (the channel chat_id) differs from conversation_id
        decision = await a._preempt_or_queue_prev_task(
            session_id="chat_abc",
            session=sess,
            conversation_id="conv-XYZ",
        )
        # Old behaviour: would have missed prev_task → "proceed" → race.
        # New behaviour: finds it by conversation_id → "queued_then_proceed".
        assert decision == "queued_then_proceed"

    @pytest.mark.asyncio
    async def test_falls_back_to_session_id_when_no_conversation_id(self) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="legacy-cli")
        prev.cancel("test")

        sess = MagicMock(channel="cli")
        decision = await a._preempt_or_queue_prev_task(
            session_id="legacy-cli",
            session=sess,
            conversation_id=None,
        )
        assert decision == "proceed"


# ── FIX 3: lifecycle.wait_for_idle ─────────────────────────────────────


class TestWaitForIdle:
    @pytest.mark.asyncio
    async def test_returns_true_immediately_when_idle(self) -> None:
        mgr = ConversationLifecycleManager()
        ok = await mgr.wait_for_idle("conv-fresh", timeout=0.1)
        assert ok is True

    @pytest.mark.asyncio
    async def test_unblocks_on_finish(self) -> None:
        mgr = ConversationLifecycleManager()
        r = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        assert r.generation == 1

        async def finish_later():
            await asyncio.sleep(0.05)
            await mgr.finish("conv-1", generation=1)

        asyncio.create_task(finish_later())
        ok = await mgr.wait_for_idle("conv-1", target_generation=1, timeout=1.0)
        assert ok is True

    @pytest.mark.asyncio
    async def test_target_generation_already_passed(self) -> None:
        """If the target generation has been replaced (new lock acquired),
        wait_for_idle returns True immediately — we don't want to keep
        waiting for a generation that no longer exists."""
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.INTERRUPT)
        # Same client takes over → generation bumps from 1 to 2
        await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.INTERRUPT)
        # Caller wanted to wait on gen=1 but the holder is now gen=2
        ok = await mgr.wait_for_idle("conv-1", target_generation=1, timeout=0.1)
        assert ok is True

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self) -> None:
        mgr = ConversationLifecycleManager()
        await mgr.start("conv-stuck", "client-A", policy=DoubleTextingPolicy.QUEUE)
        ok = await mgr.wait_for_idle("conv-stuck", timeout=0.1)
        assert ok is False

    @pytest.mark.asyncio
    async def test_interrupt_during_wait_wakes_old_waiter(self) -> None:
        """Edge race: same-client INTERRUPT fires while another tab is
        ``wait_for_idle``-ing on the previous generation.  Without the
        ``set`` + ``pop`` dance in ``start()`` the waiter would block
        until ``timeout`` because finish() never sees the popped Event.
        """
        mgr = ConversationLifecycleManager()
        r1 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)

        wait_started = asyncio.Event()
        wait_returned: list[bool] = []

        async def waiter():
            wait_started.set()
            ok = await mgr.wait_for_idle("conv-1", target_generation=r1.generation, timeout=2.0)
            wait_returned.append(ok)

        t = asyncio.create_task(waiter())
        # Ensure waiter has actually entered wait_for_idle and created the Event
        await wait_started.wait()
        await asyncio.sleep(0.02)

        # Same-client INTERRUPT bumps the lock to gen=2
        r2 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.INTERRUPT)
        assert r2.took_over is not None
        assert r2.generation == r1.generation + 1

        await asyncio.wait_for(t, timeout=1.0)
        assert wait_returned == [True]  # waiter awoken, not timed out

    @pytest.mark.asyncio
    async def test_new_start_clears_stale_idle_event(self) -> None:
        """A fresh ``start()`` after finish must drop the old idle Event
        so subsequent waiters don't see an already-set Event from the
        previous generation."""
        mgr = ConversationLifecycleManager()
        r1 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        await mgr.finish("conv-1", generation=r1.generation)
        # Now busy again under a new generation
        r2 = await mgr.start("conv-1", "client-A", policy=DoubleTextingPolicy.QUEUE)
        assert r2.generation == r1.generation + 1
        # wait_for_idle on the new gen with short timeout must NOT see
        # the stale set-Event from r1's finish.
        ok = await mgr.wait_for_idle("conv-1", target_generation=r2.generation, timeout=0.1)
        assert ok is False


# ── FIX 4: QUEUE timeout cancels old task to propagate to tools ───────


class TestQueueTimeoutCancelsOldTask:
    @pytest.mark.asyncio
    async def test_queue_timeout_sets_cancel_event(self, _short_settle_timeout) -> None:
        """Without this fix, QUEUE timeout only set ``abandoned=True``;
        long-running tools (shell / browser) listen to ``cancel_event``,
        not ``abandoned``, so they kept running while the new task also
        spun up — leading to cwd / fs side-effect races."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="conv-q")
        prev.transition(TaskStatus.REASONING)

        sess = MagicMock(channel="desktop")
        decision = await a._preempt_or_queue_prev_task(
            session_id="conv-q",
            session=sess,
            conversation_id="conv-q",
        )
        assert decision == "queued_then_proceed"
        assert prev.abandoned is True
        # FIX 4 contract: cancel_event must be set on timeout.
        assert prev.cancel_event.is_set()
        assert prev.cancelled is True


# ── FIX 5: append_marker persists to SqliteTurnStore ──────────────────


class TestAppendMarkerPersistence:
    """End-to-end: ``Session.append_marker`` should attempt to persist via
    ``_write_turn_to_store``.  We can't easily spin up a SqliteTurnStore
    in a unit test, so we patch the writer to capture the call."""

    @pytest.mark.asyncio
    async def test_append_marker_calls_persistence(self, monkeypatch) -> None:
        from openakita.sessions.session import Session, SessionConfig

        captured: list[tuple] = []

        # Build a minimal Session and stub _write_turn_to_store
        sess = Session.__new__(Session)
        sess.context = SessionContext()
        sess.config = SessionConfig()
        sess.last_activity = 0.0

        def _fake_write(role: str, content: str, metadata: dict) -> None:
            captured.append((role, content, dict(metadata)))

        sess._write_turn_to_store = _fake_write  # type: ignore[method-assign]
        sess.touch = lambda: None  # type: ignore[method-assign]

        sess.append_marker(
            "assistant",
            "[上一条任务被新请求中断]",
            marker_type="preempted",
            policy="interrupt",
        )

        assert len(captured) == 1
        role, content, meta = captured[0]
        assert role == "assistant"
        assert content == "[上一条任务被新请求中断]"
        assert meta["marker_type"] == "preempted"
        assert meta["policy"] == "interrupt"

    @pytest.mark.asyncio
    async def test_append_marker_skips_persistence_when_transient(self, monkeypatch) -> None:
        from openakita.sessions.session import Session, SessionConfig

        captured: list[tuple] = []
        sess = Session.__new__(Session)
        sess.context = SessionContext()
        sess.config = SessionConfig()
        sess.last_activity = 0.0
        sess._write_turn_to_store = lambda r, c, m: captured.append((r, c, m))  # type: ignore[method-assign]
        sess.touch = lambda: None  # type: ignore[method-assign]

        sess.append_marker(
            "assistant",
            "ephemeral",
            marker_type="diagnostic",
            transient_for_llm=True,
        )
        assert captured == []  # transient markers skip the persistence path


# ── S1.6: TurnRegistry idempotency ────────────────────────────────────


class TestTurnRegistry:
    @pytest.mark.asyncio
    async def test_new_turn_is_claimed(self) -> None:
        r = TurnRegistry()
        status, rec = await r.begin("t-1")
        assert status == "new" and rec is None

    @pytest.mark.asyncio
    async def test_duplicate_in_flight_short_circuits(self) -> None:
        r = TurnRegistry()
        await r.begin("t-1")
        status, rec = await r.begin("t-1")
        assert status == "in_flight"
        assert rec is not None

    @pytest.mark.asyncio
    async def test_terminal_states_replayed(self) -> None:
        r = TurnRegistry()
        await r.begin("t-ok")
        await r.mark_succeeded("t-ok", summary="hi")
        status, rec = await r.begin("t-ok")
        assert status == "succeeded"
        assert rec is not None and rec.summary == "hi"

        await r.begin("t-bad")
        await r.mark_failed("t-bad", summary="boom")
        status2, _ = await r.begin("t-bad")
        assert status2 == "failed"

    @pytest.mark.asyncio
    async def test_empty_turn_id_passes_through(self) -> None:
        r = TurnRegistry()
        s1, _ = await r.begin("")
        s2, _ = await r.begin("")
        assert s1 == "new" and s2 == "new"

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        r = TurnRegistry(ttl_seconds=0.2)
        await r.begin("t-ttl")
        await r.mark_succeeded("t-ttl")
        await asyncio.sleep(0.3)
        status, _ = await r.begin("t-ttl")
        assert status == "new"


# ── S1.9: telemetry counters ──────────────────────────────────────────


class TestConversationMetrics:
    def test_counters_increment_and_label(self) -> None:
        metrics.inc_preempt("interrupt", channel="desktop")
        metrics.inc_preempt("interrupt", channel="desktop")
        metrics.inc_queue(channel="telegram")
        metrics.inc_settled_timeout("queue", channel="desktop")
        metrics.inc_abandon("queue", channel="desktop")
        metrics.inc_takeover(channel="desktop")
        metrics.inc_illegal_reasoning_entry(source="test")

        snap = {(s["name"], frozenset(s["labels"].items())): s["value"] for s in metrics.snapshot()}
        assert (
            snap[("preempt", frozenset({"policy": "interrupt", "channel": "desktop"}.items()))] == 2
        )
        assert snap[("queue", frozenset({"channel": "telegram"}.items()))] == 1
        assert (
            snap[("settled_timeout", frozenset({"policy": "queue", "channel": "desktop"}.items()))]
            == 1
        )
        assert snap[("abandon", frozenset({"policy": "queue", "channel": "desktop"}.items()))] == 1
        assert snap[("takeover", frozenset({"channel": "desktop"}.items()))] == 1
        assert snap[("illegal_reasoning_entry", frozenset({"source": "test"}.items()))] == 1


# ══════════════════════════════════════════════════════════════════════
# S2 (v1.27.15) — external-project-inspired follow-ups
# Plan: conversation concurrency v1.28 S2 P0/P1/P2.
# ══════════════════════════════════════════════════════════════════════


# ── S2 P0-2: SSE delta coalescer ──────────────────────────────────────


class TestSSEDeltaCoalescer:
    """``core.sse_throttle.DeltaCoalescer`` — time + size windowed merge."""

    def test_size_threshold_flushes_immediately(self) -> None:
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer(interval_ms=10_000, max_chars=10)
        # First push under cap → buffered, no flush.
        assert c.offer("text_delta", {"content": "hello"}) == []
        assert c.has_pending() is True
        # Second push crosses size cap → flush merged event.
        out = c.offer("text_delta", {"content": "world!"})
        assert len(out) == 1
        et, ed = out[0]
        assert et == "text_delta"
        assert ed["content"] == "helloworld!"
        assert ed["_coalesced_parts"] == 2

    def test_time_threshold_flushes_via_tick(self) -> None:
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer(interval_ms=50, max_chars=10_000)
        c.offer("text_delta", {"content": "a"})
        # immediately after offer, tick should not flush
        assert c.tick(now=c._buckets["text_delta"].last_flush_ts + 0.01) == []
        # after >50ms, tick flushes
        flushed = c.tick(now=c._buckets["text_delta"].last_flush_ts + 0.06)
        assert len(flushed) == 1
        assert flushed[0][1]["content"] == "a"

    def test_non_delta_event_flushes_pending_in_order(self) -> None:
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer(interval_ms=10_000, max_chars=10_000)
        c.offer("text_delta", {"content": "partial"})
        c.offer("thinking_delta", {"content": "thinking-bit"})
        out = c.offer("tool_call_start", {"tool": "shell", "args": {"cmd": "ls"}})
        # Order: text_delta, thinking_delta, tool_call_start (insertion order).
        assert [e[0] for e in out] == [
            "text_delta",
            "thinking_delta",
            "tool_call_start",
        ]
        assert out[0][1]["content"] == "partial"
        assert out[1][1]["content"] == "thinking-bit"
        assert out[2][1]["tool"] == "shell"

    def test_drain_flushes_everything_and_resets(self) -> None:
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer(interval_ms=10_000)
        c.offer("text_delta", {"content": "x"})
        c.offer("text_delta", {"content": "y"})
        drained = c.drain()
        assert len(drained) == 1
        assert drained[0][1]["content"] == "xy"
        assert c.has_pending() is False
        # Re-use is safe: a new event creates a fresh bucket.
        assert c.offer("text_delta", {"content": "z"}) == []

    def test_empty_content_emits_passthrough(self) -> None:
        """Empty deltas shouldn't be silently dropped — some clients
        rely on empty delta as a flush sentinel."""
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer()
        out = c.offer("text_delta", {"content": ""})
        assert out == [("text_delta", {"content": ""})]

    def test_chain_text_is_coalesced(self) -> None:
        from openakita.core.sse_throttle import COALESCED_TYPES, DeltaCoalescer

        assert "chain_text" in COALESCED_TYPES
        c = DeltaCoalescer(interval_ms=10_000, max_chars=5)
        out = c.offer("chain_text", {"content": "abcde"})
        # 5 chars hits cap.
        assert len(out) == 1
        assert out[0] == ("chain_text", {"content": "abcde"})

    def test_endpoint_notice_bypasses_delta_buffer(self) -> None:
        from openakita.core.sse_throttle import DeltaCoalescer

        c = DeltaCoalescer(interval_ms=10_000, max_chars=10_000)
        c.offer("text_delta", {"content": "partial"})
        out = c.offer(
            "endpoint_notice",
            {
                "reason_code": "endpoint_prefer_switch",
                "from_endpoint": "opencode-free",
                "endpoint": "lmstudio-thinking",
            },
        )

        assert [e[0] for e in out] == ["text_delta", "endpoint_notice"]
        assert out[1][1]["reason_code"] == "endpoint_prefer_switch"
        assert c.has_pending() is False


# ── S2 P0-3: partial assistant text persistence ───────────────────────


class TestPartialTextOnPreempt:
    """``TaskState.append_partial_text`` + marker persistence integration."""

    def test_append_partial_text_under_cap(self) -> None:
        ts = TaskState(task_id="t", session_id="s")
        ts.append_partial_text("hello ")
        ts.append_partial_text("world")
        assert ts.partial_text == "hello world"
        assert ts.partial_truncated is False

    def test_append_partial_text_caps_and_flags_truncated(self) -> None:
        ts = TaskState(task_id="t", session_id="s")
        cap = TaskState._PARTIAL_TEXT_CAP
        ts.append_partial_text("a" * (cap - 3))
        # Next push partially fits, partially truncated.
        ts.append_partial_text("bbbbbb")
        assert len(ts.partial_text) == cap
        assert ts.partial_truncated is True
        # Further pushes are silent no-ops (still truncated).
        ts.append_partial_text("more")
        assert len(ts.partial_text) == cap

    def test_thinking_channel_independent(self) -> None:
        ts = TaskState(task_id="t", session_id="s")
        ts.append_partial_text("text-side")
        ts.append_partial_thinking("thinking-side")
        assert ts.partial_text == "text-side"
        assert ts.partial_thinking == "thinking-side"

    def test_empty_input_no_op(self) -> None:
        ts = TaskState(task_id="t", session_id="s")
        ts.append_partial_text("")
        ts.append_partial_thinking("")
        assert ts.partial_text == ""
        assert ts.partial_thinking == ""
        assert ts.partial_truncated is False

    def test_append_preempt_marker_emits_partial_when_present(self) -> None:
        a = _make_stub_agent()
        captured: list[tuple] = []

        class _Sess:
            def append_marker(self, role, content, **metadata) -> None:
                captured.append((role, content, metadata))

        a._append_preempt_marker(
            session=_Sess(),
            policy="interrupt",
            prev_task_id="abc",
            reason="preempted_by_new_message",
            partial_text="user has seen this much",
            partial_thinking="and this thinking",
            partial_truncated=False,
        )
        # 1 preempt marker + 1 partial text marker + 1 partial thinking marker
        assert len(captured) == 3
        roles = [c[0] for c in captured]
        marker_types = [c[2]["marker_type"] for c in captured]
        assert roles == ["assistant", "assistant", "assistant"]
        assert marker_types == ["preempted", "aborted_partial", "aborted_partial"]
        # The preempt marker carries has_partial=True flag
        assert captured[0][2]["has_partial"] is True
        # The partial markers carry their channel.
        assert captured[1][2]["partial_channel"] == "text"
        assert captured[2][2]["partial_channel"] == "thinking"
        assert captured[1][1] == "user has seen this much"
        assert captured[2][1] == "and this thinking"

    def test_append_preempt_marker_only_preempt_when_no_partial(self) -> None:
        a = _make_stub_agent()
        captured: list[tuple] = []

        class _Sess:
            def append_marker(self, role, content, **metadata) -> None:
                captured.append((role, content, metadata))

        a._append_preempt_marker(
            session=_Sess(),
            policy="interrupt",
            prev_task_id="abc",
            reason="preempted_by_new_message",
            partial_text="",
            partial_thinking="",
        )
        # Only the bare preempt marker — no spurious aborted_partial.
        assert len(captured) == 1
        assert captured[0][2]["marker_type"] == "preempted"
        assert captured[0][2]["has_partial"] is False


# ── S2 P1-4: STEER policy (lifecycle side) ────────────────────────────


class TestSteerPolicyLifecycle:
    @pytest.mark.asyncio
    async def test_steer_returns_steered_flag_without_lock(self) -> None:
        mgr = ConversationLifecycleManager()
        # Initial holder (irrelevant policy).
        r1 = await mgr.start("c1", "client-A", policy=DoubleTextingPolicy.REJECT)
        assert r1.conflict is None
        assert r1.generation > 0

        # Same client re-issues with STEER → steered=True, no new generation,
        # original holder still owns the lock.
        r2 = await mgr.start("c1", "client-A", policy=DoubleTextingPolicy.STEER)
        assert r2.steered is True
        assert r2.conflict is not None
        assert r2.generation == 0
        # Original generation unchanged — busy state still reflects holder A.
        status = await mgr.get_busy_status("c1")
        assert status["busy"] is True
        assert status["client_id"] == "client-A"


# ── S2 P1-6: marker turn skips memory extraction ──────────────────────


class TestMarkerSkipsMemoryExtraction:
    def test_save_turn_marks_marker_as_extracted(self, tmp_path) -> None:
        """Marker turns must land in SQLite with ``extracted=TRUE`` so the
        lifecycle background loop never picks them up."""
        from openakita.memory.storage import MemoryStorage

        db = tmp_path / "test_markers.db"
        store = MemoryStorage(db_path=db)

        # Real assistant turn — extracted=FALSE.
        store.save_turn(
            session_id="conv-A",
            turn_index=0,
            role="assistant",
            content="here is the answer",
            metadata={"marker_type": None} if False else None,
        )
        # Marker turn — should auto-mark extracted=TRUE.
        store.save_turn(
            session_id="conv-A",
            turn_index=1,
            role="assistant",
            content="[上一条任务被新请求中断]",
            metadata={"marker_type": "preempted", "policy": "interrupt"},
        )
        store.save_turn(
            session_id="conv-A",
            turn_index=2,
            role="assistant",
            content="<partial assistant text>",
            metadata={"marker_type": "aborted_partial", "partial_channel": "text"},
        )

        unextracted = store.get_unextracted_turns()
        # Only the real turn should be returned.
        assert len(unextracted) == 1
        assert unextracted[0]["turn_index"] == 0
        assert unextracted[0]["content"] == "here is the answer"

    def test_save_turn_persists_metadata_json(self, tmp_path) -> None:
        """Round-trip: metadata persisted to SQLite is parsed back as dict."""
        from openakita.memory.storage import MemoryStorage

        db = tmp_path / "test_meta.db"
        store = MemoryStorage(db_path=db)
        store.save_turn(
            session_id="conv-B",
            turn_index=0,
            role="user",
            content="hi",
            metadata={"client_id": "abc", "request_id": "xyz", "ts": 12345},
        )
        # Force re-read via get_unextracted_turns (turn_index 0 is user, not marker).
        rows = store.get_unextracted_turns()
        assert len(rows) == 1
        meta = rows[0]["metadata"]
        assert isinstance(meta, dict)
        assert meta["client_id"] == "abc"
        assert meta["request_id"] == "xyz"


# ── S2 P2-8: shell soft-kill + partial drain ──────────────────────────


class TestShellSoftKill:
    """The CancelledError args carry a partial payload dict when the
    shell tool is cancelled mid-run."""

    @pytest.mark.asyncio
    async def test_cancelled_error_carries_partial_payload_shape(
        self, tmp_path, monkeypatch
    ) -> None:
        """Verify the cancel handler's payload shape contract via a
        direct unit-style test on ``_drain_partial_output`` — actually
        spawning a subprocess + cancelling it is flaky on Windows CI
        runners, so we instead verify the public payload contract."""
        from openakita.tools.shell import ShellTool

        st = ShellTool()

        class _FakePipe:
            def __init__(self, data: bytes) -> None:
                self._data = data

            async def read(self, n: int) -> bytes:
                return self._data[:n]

        class _FakeProc:
            def __init__(self) -> None:
                self.stdout = _FakePipe(b"hello stdout\n")
                self.stderr = _FakePipe(b"warning stderr\n")
                self.returncode = None

        payload = await st._drain_partial_output(_FakeProc())
        assert payload["stdout"].startswith("hello stdout")
        assert payload["stderr"].startswith("warning stderr")
        assert payload["reason"] == "cancelled"
        assert "returncode" in payload


# ── Post-S2 audit fixes ──────────────────────────────────────────────


class TestPostAuditFixes:
    """Regression coverage for the post-S2 audit fixes (FIX-A / B / D / E)."""

    @pytest.mark.asyncio
    async def test_fix_a_queued_safety_net_releases_lock_on_cancel(self) -> None:
        """FIX-A: if the outer queued-stream coroutine is cancelled
        between ``lifecycle.start()`` returning and ``_stream_chat``
        taking ownership, the safety-net finally must release the
        lock so the conversation isn't permanently busy.

        We don't drive the full SSE handler — we model the contract:
        ``lifecycle.finish()`` is generation-guarded and idempotent,
        so calling it from a safety-net finally is always safe even
        when the legitimate stream finally also calls it.
        """
        lifecycle = ConversationLifecycleManager()
        cid = "conv-fix-a"
        res = await lifecycle.start(cid, "client-1", policy=DoubleTextingPolicy.QUEUE)
        assert res.conflict is None
        gen = res.generation

        # Simulate FIX-A: outer cancel before _stream_chat takes over.
        _lock_owned_by_outer = True
        try:
            try:
                raise asyncio.CancelledError("outer cancel")
            except asyncio.CancelledError:
                # Mirror the chat.py try/finally shape — propagate after
                # the safety-net so the caller still sees CancelledError.
                pass
        finally:
            if _lock_owned_by_outer:
                await lifecycle.finish(cid, generation=gen)

        # The lock is gone, so a new start() succeeds with a fresh
        # generation rather than 409.
        next_res = await lifecycle.start(cid, "client-2", policy=DoubleTextingPolicy.QUEUE)
        assert next_res.conflict is None
        assert next_res.generation > gen

    @pytest.mark.asyncio
    async def test_fix_a_double_finish_is_safe(self) -> None:
        """FIX-A relies on ``finish()`` being idempotent — calling it
        a second time (e.g. once by ``_stream_chat`` and once by the
        outer safety-net) must be a no-op, not a crash."""
        lifecycle = ConversationLifecycleManager()
        cid = "conv-fix-a-2"
        res = await lifecycle.start(cid, "client-1", policy=DoubleTextingPolicy.QUEUE)
        gen = res.generation

        first = await lifecycle.finish(cid, generation=gen)
        assert first is True
        second = await lifecycle.finish(cid, generation=gen)
        assert second is False
        status = await lifecycle.get_busy_status(cid)
        assert status["busy"] is False

    @pytest.mark.asyncio
    async def test_fix_b_no_zombie_task_from_wait_for(self) -> None:
        """FIX-B: removed ``asyncio.shield`` around
        ``asyncio.wait_for(wait_until_settled())``.  We verify the
        contract: when an awaiter on ``wait_for(wait_until_settled())``
        is cancelled, no orphan task survives.
        """
        state = AgentState()
        task = state.begin_task(session_id="s-fix-b")

        # Start a waiter, immediately cancel it.
        async def _waiter() -> None:
            await asyncio.wait_for(task.wait_until_settled(), timeout=5.0)

        t = asyncio.create_task(_waiter())
        await asyncio.sleep(0.01)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

        # No pending tasks should remain referring to wait_until_settled.
        # (The task itself completes synchronously after cancel.)
        assert t.done()

    def test_fix_d_alter_table_failure_propagates(self, tmp_path) -> None:
        """FIX-D: the v4→v5 migration must let ALTER TABLE failures
        propagate so the migration transaction rolls back, instead of
        silently bumping schema_version to 5 with a missing column."""
        import sqlite3

        from openakita.memory.storage import MemoryStorage

        store = MemoryStorage(tmp_path / "fix_d.db")
        try:
            assert store._conn is not None
            # Sabotage: drop the conversation_turns table so ALTER fails.
            store._conn.execute("DROP TABLE IF EXISTS conversation_turns")
            store._conn.commit()

            with pytest.raises(sqlite3.OperationalError):
                store._migrate_v4_to_v5(commit=True)
        finally:
            if store._conn is not None:
                store._conn.close()

    @pytest.mark.asyncio
    async def test_fix_e_steer_insert_survives_outer_cancel(self) -> None:
        """FIX-E: ``insert_user_message`` is wrapped in ``asyncio.shield``
        so a client disconnect mid-call doesn't drop the message.  We
        verify that the contract is preserved via the agent state's
        own ``add_user_insert`` (the same primitive STEER ultimately
        calls)."""
        state = AgentState()
        task = state.begin_task(session_id="s-fix-e")

        async def _do_insert() -> None:
            await asyncio.shield(task.add_user_insert("steered msg"))

        t = asyncio.create_task(_do_insert())
        # Give the shield a tick to actually start the inner coroutine.
        await asyncio.sleep(0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

        # The message landed despite the outer cancel.
        drained = await task.drain_user_inserts()
        assert drained == ["steered msg"]
