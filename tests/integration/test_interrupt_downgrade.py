"""Stage 4: INTERRUPT-policy downgrade tests (plan: v1.28 S4).

Covers v1.28.2 wiring:

* ``TaskState.begin_tool`` / ``end_tool`` / ``get_in_flight_tools`` —
  the in-flight tool registry that ``_preempt_or_queue_prev_task``
  inspects before honouring an INTERRUPT request.
* ``tool_executor.execute_tool`` ↔ ``TaskState`` integration — every
  tool dispatch increments the in-flight list, every finally decrements
  it, parallel/nested dispatch handled correctly.
* ``Agent._preempt_or_queue_prev_task`` INTERRUPT path:
    * Empty in-flight list → real cancel + ``preempted`` decision.
    * Only ``cancel``-class tools in flight → real cancel + ``preempted``.
    * Any ``block``-class tool in flight → downgrade to QUEUE +
      ``inc_interrupt_downgrade(reason="block_in_flight")`` + the QUEUE
      branch is actually executed (task settled/abandoned semantics).
    * Unknown tool in flight → downgrade with ``reason="unknown_tool"``.
* ``inc_interrupt_downgrade`` counter labels (channel + reason) wired
  through ``conversation_metrics.snapshot()``.

Mirrors ``test_conversation_concurrency.TestPreemptOrQueueHelper`` style:
construct an ``Agent`` via ``__new__`` to bypass the heavyweight
``__init__``, then exercise the helper directly with a stub TaskState.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openakita import config as config_mod
from openakita.core import conversation_metrics as metrics
from openakita.core.agent import Agent
from openakita.core.agent_state import AgentState, TaskState, TaskStatus
from openakita.core.tool_interrupt_behavior import (
    DEFAULT_BEHAVIOR,
    get_tool_interrupt_behavior,
    has_any_block_tool,
    is_unknown_tool,
    known_tools,
    partition_by_behavior,
    warn_unclassified_tools,
)

# The agent-layer preempt orchestration (``Agent._preempt_or_queue_prev_task``
# + INTERRUPT→QUEUE downgrade + QUEUE block-tool timeout extension + the
# tool-executor begin/end source wiring) is ported into ``core/_agent_legacy``
# after the ADR-0003 split (Batch C). All tests below are active.

# ── Fixtures ─────────────────────────────────────────────────────────


def _make_stub_agent() -> Agent:
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
    # The QUEUE wait now reads queue_wait_timeout_ms (decoupled from
    # preempt_settle_timeout_ms). Both share the same max(0.5, ms/1000)
    # floor, so pinning both to 200ms keeps the historical 500ms first-wait
    # floor these tests are built around, while still exercising the
    # cancel/abandon-after-timeout paths instead of waiting the 10-min default.
    monkeypatch.setattr(config_mod.settings, "preempt_settle_timeout_ms", 200)
    monkeypatch.setattr(config_mod.settings, "queue_wait_timeout_ms", 200)
    yield


@pytest.fixture
def _allow_interrupt(monkeypatch):
    """Enable INTERRUPT semantic; channel default is still QUEUE so we
    set per_channel override per-test."""
    monkeypatch.setattr(config_mod.settings, "double_texting_allow_interrupt", True)
    yield


@pytest.fixture
def _interrupt_channel(monkeypatch, _allow_interrupt):
    monkeypatch.setitem(config_mod.settings.double_texting_per_channel, "ch_intr", "interrupt")
    yield "ch_intr"


# ── In-flight tracking primitives ────────────────────────────────────


class TestInFlightTrackingPrimitives:
    """``TaskState.begin_tool`` / ``end_tool`` / ``get_in_flight_tools``."""

    def test_begin_appends(self) -> None:
        t = TaskState(task_id="t1")
        assert t.get_in_flight_tools() == []
        t.begin_tool("read_file")
        assert t.get_in_flight_tools() == ["read_file"]

    def test_end_removes_one_instance(self) -> None:
        t = TaskState(task_id="t1")
        t.begin_tool("read_file")
        t.begin_tool("read_file")  # parallel: same tool twice
        t.end_tool("read_file")
        assert t.get_in_flight_tools() == ["read_file"]
        t.end_tool("read_file")
        assert t.get_in_flight_tools() == []

    def test_end_without_begin_is_noop(self, caplog) -> None:
        import logging

        caplog.set_level(logging.DEBUG)
        t = TaskState(task_id="t1")
        t.end_tool("read_file")  # never began
        assert t.get_in_flight_tools() == []

    def test_get_returns_snapshot_not_alias(self) -> None:
        t = TaskState(task_id="t1")
        t.begin_tool("read_file")
        snap = t.get_in_flight_tools()
        snap.append("ghost")
        assert t.get_in_flight_tools() == ["read_file"]

    def test_empty_or_falsy_names_skipped(self) -> None:
        t = TaskState(task_id="t1")
        t.begin_tool("")
        t.begin_tool(None)  # type: ignore[arg-type]
        t.end_tool("")
        t.end_tool(None)  # type: ignore[arg-type]
        assert t.get_in_flight_tools() == []

    def test_multiple_distinct_tools(self) -> None:
        t = TaskState(task_id="t1")
        t.begin_tool("read_file")
        t.begin_tool("write_file")
        t.begin_tool("grep")
        assert sorted(t.get_in_flight_tools()) == sorted(["read_file", "write_file", "grep"])
        t.end_tool("write_file")
        assert sorted(t.get_in_flight_tools()) == sorted(["read_file", "grep"])


# ── tool_executor wiring ────────────────────────────────────────────


class TestToolExecutorBeginEndWiring:
    """Verify the source of ``ToolExecutor.execute_tool`` AND
    ``execute_tool_with_policy`` wire in_flight tracking.

    Critical: the original v1.28.2 ship had `begin_tool`/`end_tool` only
    in `execute_tool`, but `execute_batch` (reasoning_engine's primary
    dispatch path) calls `execute_tool_with_policy` directly — bypassing
    the wrapper.  The v1.28.2 hotfix (FIX-S4-1) added tracking to BOTH
    entry points; these tests lock that in.
    """

    def test_execute_tool_source_contains_begin_and_end(self) -> None:
        import inspect

        from openakita.core.tool_executor import ToolExecutor

        src = inspect.getsource(ToolExecutor.execute_tool)
        # Begin in try, end in finally — both must be present.  The
        # parameter is ``in_flight_name`` (FOLLOW-UP-S4-B) — a regular
        # tool name passes through unchanged, ``call_mcp_tool`` is
        # rewritten to ``mcp:server:sub`` so the preempt resolver can
        # consult MCP annotations per sub-tool.
        assert "task.begin_tool(in_flight_name)" in src
        assert "task.end_tool(in_flight_name)" in src
        # The end_tool MUST be reached on every code path → live in finally.
        assert "finally" in src
        # Same task lookup serves S3 AbortScope + S4 in_flight tracking.
        assert "_resolve_task" in src or "task = self._resolve_task" in src

    def test_execute_tool_with_policy_source_contains_begin_and_end(self) -> None:
        """Regression guard for FIX-S4-1: execute_tool_with_policy is the
        production-path entry from execute_batch.  Wiring begin/end here is
        what actually makes in_flight_tools non-empty during a real LLM
        turn."""
        import inspect

        from openakita.core.tool_executor import ToolExecutor

        src = inspect.getsource(ToolExecutor.execute_tool_with_policy)
        assert "task.begin_tool(in_flight_name)" in src, (
            "FIX-S4-1 regression: execute_tool_with_policy must call "
            "task.begin_tool — it is the primary dispatch path; wiring "
            "only execute_tool leaves in_flight_tools empty in production."
        )
        assert "task.end_tool(in_flight_name)" in src
        assert "finally" in src
        assert "_resolve_task" in src

    def test_resolve_task_helper_returns_session_task(self) -> None:
        from openakita.core.tool_executor import ToolExecutor

        executor = ToolExecutor.__new__(ToolExecutor)
        agent_stub = MagicMock()
        agent_stub.agent_state = AgentState()
        task = agent_stub.agent_state.begin_task(session_id="s1")
        executor._agent_ref = agent_stub

        resolved = executor._resolve_task("s1")
        assert resolved is task

    def test_resolve_task_falls_back_to_current_when_no_session(self) -> None:
        from openakita.core.tool_executor import ToolExecutor

        executor = ToolExecutor.__new__(ToolExecutor)
        agent_stub = MagicMock()
        agent_stub.agent_state = AgentState()
        task = agent_stub.agent_state.begin_task(session_id="any")
        executor._agent_ref = agent_stub

        resolved = executor._resolve_task(None)
        assert resolved is task

    def test_resolve_task_returns_none_when_no_agent_state(self) -> None:
        from openakita.core.tool_executor import ToolExecutor

        executor = ToolExecutor.__new__(ToolExecutor)
        agent_stub = MagicMock()
        agent_stub.agent_state = None
        executor._agent_ref = agent_stub
        assert executor._resolve_task("s1") is None

    def test_resolve_task_handles_missing_agent_ref(self) -> None:
        from openakita.core.tool_executor import ToolExecutor

        executor = ToolExecutor.__new__(ToolExecutor)
        executor._agent_ref = None
        assert executor._resolve_task("s1") is None

    @pytest.mark.asyncio
    async def test_execute_tool_with_policy_registers_in_flight(self, monkeypatch) -> None:
        """End-to-end smoke for FIX-S4-1: calling execute_tool_with_policy
        with a real (stubbed) handler dispatch must observe the tool in
        the task's in_flight list WHILE the handler is running.

        Before the fix, this test would catch in_flight == [] at the
        observe point — proving the original v1.28.2 ship was a no-op in
        the execute_batch path."""
        from openakita.core.tool_executor import ToolExecutor

        executor = ToolExecutor.__new__(ToolExecutor)
        agent_stub = MagicMock()
        agent_stub.agent_state = AgentState()
        task = agent_stub.agent_state.begin_task(session_id="e2e")
        task.transition(TaskStatus.REASONING)
        executor._agent_ref = agent_stub

        # Capture in_flight state from inside the handler dispatch.
        observed_during_exec: list[str] = []

        async def fake_dispatch(tool_name, params):
            observed_during_exec.extend(task.get_in_flight_tools())
            return "ok"

        # Wire the handler_registry stub
        executor._handler_registry = MagicMock()
        executor._handler_registry.has_tool = MagicMock(return_value=True)
        executor._handler_registry.execute_by_tool = fake_dispatch

        # Bypass policy / hook / experience / canonicalize side effects
        executor._canonicalize_tool_name = lambda n: n
        executor._check_todo_required = lambda *a, **kw: None
        executor._check_current_turn_grounding = lambda *a, **kw: None
        executor._dispatch_hook = MagicMock(side_effect=lambda *a, **kw: asyncio.sleep(0))
        executor._record_experience = MagicMock()
        executor._observe_current_turn_tool_result = MagicMock()
        executor._guard_truncate = lambda _n, r: r
        executor._suggest_similar_tool = MagicMock(return_value="?")

        # Build a fake policy_result that lets the call through.
        policy_result = MagicMock()
        policy_result.action = "allow"
        policy_result.metadata = {}

        result, _hint = await executor.execute_tool_with_policy(
            "read_file",
            {"path": "/x"},
            policy_result,
            session_id="e2e",
        )

        # FIX-S4-1 invariant: the tool was registered DURING execution.
        assert observed_during_exec == ["read_file"], (
            f"in_flight observed during execution: {observed_during_exec!r} — "
            "expected ['read_file']; this is the test that would have caught "
            "the original v1.28.2 bug (execute_tool wired but bypassed)"
        )
        # And cleared after.
        assert task.get_in_flight_tools() == []


# ── Preempt downgrade decisions ─────────────────────────────────────


class TestPreemptDowngradeWhenBlockToolInFlight:
    """The core S4 invariant: INTERRUPT must downgrade to QUEUE whenever
    any in-flight tool is classified ``block``."""

    @pytest.mark.asyncio
    async def test_block_tool_in_flight_downgrades_to_queue(
        self, _interrupt_channel, _short_settle_timeout
    ) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s1")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")  # block-class

        sess = MagicMock(channel=_interrupt_channel)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        decision = await a._preempt_or_queue_prev_task(session_id="s1", session=sess)

        # Downgraded → QUEUE branch taken → queued_then_proceed.
        assert decision == "queued_then_proceed"
        # The old task should NOT have been hard-cancelled (cooperative settle).
        assert not prev.cancelled
        # Downgrade counter fired with channel + reason=block_in_flight.
        snap = metrics.snapshot()
        downgrade = [s for s in snap if s["name"] == "interrupt_downgrade"]
        assert len(downgrade) == 1
        assert downgrade[0]["labels"]["channel"] == _interrupt_channel
        assert downgrade[0]["labels"]["reason"] == "block_in_flight"
        # QUEUE counter also incremented (we took the QUEUE branch).
        assert any(s["name"] == "queue" for s in snap)
        # No INTERRUPT preempt counter — downgrade short-circuited it.
        assert not any(s["name"] == "preempt" for s in snap)

    @pytest.mark.asyncio
    async def test_run_shell_in_flight_also_downgrades(
        self, _interrupt_channel, _short_settle_timeout
    ) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s2")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("run_shell")

        sess = MagicMock(channel=_interrupt_channel)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        decision = await a._preempt_or_queue_prev_task(session_id="s2", session=sess)
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        assert any(
            s["name"] == "interrupt_downgrade" and s["labels"]["reason"] == "block_in_flight"
            for s in snap
        )

    @pytest.mark.asyncio
    async def test_mixed_in_flight_still_downgrades(
        self, _interrupt_channel, _short_settle_timeout
    ) -> None:
        """Even one block tool in a mixed batch forces downgrade."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s3")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("read_file")  # cancel
        prev.begin_tool("write_file")  # block — this one wins
        prev.begin_tool("grep")  # cancel

        sess = MagicMock(channel=_interrupt_channel)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())
        decision = await a._preempt_or_queue_prev_task(session_id="s3", session=sess)
        assert decision == "queued_then_proceed"


class TestNoDowngradeWhenAllCancelSafe:
    """INTERRUPT must NOT downgrade when every in-flight tool is cancel-safe."""

    @pytest.mark.asyncio
    async def test_only_cancel_tools_real_preempt(self, _interrupt_channel) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s4")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("read_file")  # cancel
        prev.begin_tool("grep")  # cancel

        async def cooperative_settle():
            await prev.cancel_event.wait()
            prev.mark_settled()

        asyncio.create_task(cooperative_settle())
        sess = MagicMock(channel=_interrupt_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s4", session=sess)
        assert decision == "preempted"
        assert prev.cancelled is True
        snap = metrics.snapshot()
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)
        assert any(s["name"] == "preempt" and s["labels"]["policy"] == "interrupt" for s in snap)

    @pytest.mark.asyncio
    async def test_empty_in_flight_real_preempt(self, _interrupt_channel) -> None:
        """No tools in flight at all → INTERRUPT is unambiguously safe."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s5")
        prev.transition(TaskStatus.REASONING)
        # No begin_tool calls

        async def cooperative_settle():
            await prev.cancel_event.wait()
            prev.mark_settled()

        asyncio.create_task(cooperative_settle())
        sess = MagicMock(channel=_interrupt_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s5", session=sess)
        assert decision == "preempted"
        assert prev.cancelled is True
        snap = metrics.snapshot()
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)


class TestUnknownToolDowngrade:
    """Unknown tools default to block — downgrade should happen but the
    counter should record ``reason='unknown_tool'`` so ops can spot
    missing registry entries vs legitimate writes."""

    @pytest.mark.asyncio
    async def test_unknown_tool_downgrades_with_distinct_reason(
        self, _interrupt_channel, _short_settle_timeout
    ) -> None:
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s6")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("third_party_mcp_unknown_xxx")

        sess = MagicMock(channel=_interrupt_channel)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())
        decision = await a._preempt_or_queue_prev_task(session_id="s6", session=sess)
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        downgrade = [s for s in snap if s["name"] == "interrupt_downgrade"]
        assert len(downgrade) == 1
        # Distinct reason — lets ops alert on "we keep losing INTERRUPT
        # for tools that should be classified explicitly".
        assert downgrade[0]["labels"]["reason"] == "unknown_tool"

    @pytest.mark.asyncio
    async def test_mixed_known_block_and_unknown_uses_block_reason(
        self, _interrupt_channel, _short_settle_timeout
    ) -> None:
        """When at least one known block tool is in flight, the reason is
        ``block_in_flight`` even if an unknown is also present — the
        downgrade is legitimate, not driven by missing classification."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s7")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")  # known block
        prev.begin_tool("unknown_xxx")

        sess = MagicMock(channel=_interrupt_channel)

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())
        await a._preempt_or_queue_prev_task(session_id="s7", session=sess)
        snap = metrics.snapshot()
        downgrade = [s for s in snap if s["name"] == "interrupt_downgrade"]
        assert downgrade[0]["labels"]["reason"] == "block_in_flight"


# ── REJECT / STEER / QUEUE policies are NOT affected ────────────────


class TestOtherPoliciesUnaffected:
    @pytest.mark.asyncio
    async def test_queue_policy_does_not_check_in_flight(self, _short_settle_timeout) -> None:
        """QUEUE is supposed to wait regardless of in-flight state.  The
        S4 downgrade logic must not divert it elsewhere."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s8")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        # ``cli`` channel resolves to QUEUE.
        sess = MagicMock(channel="cli")
        decision = await a._preempt_or_queue_prev_task(session_id="s8", session=sess)
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        # No interrupt_downgrade because policy was already QUEUE.
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)

    @pytest.mark.asyncio
    async def test_steer_policy_downgrades_to_queue_in_agent_layer(
        self, _short_settle_timeout
    ) -> None:
        """STEER is HTTP-short-circuited; if it ever reaches the agent layer
        (e.g. a channel configured ``steer`` without an HTTP entry point) it
        must NOT preempt/cancel the running task — it falls back to QUEUE so
        the old task's in-flight work is preserved."""
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s_steer")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        # ``desktop`` channel now resolves to STEER.
        sess = MagicMock(channel="desktop")
        decision = await a._preempt_or_queue_prev_task(session_id="s_steer", session=sess)
        # QUEUE behaviour, NOT preempt: old task settled naturally.
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        # Recorded as a QUEUE wait, never a preempt.
        assert any(s["name"] == "queue" for s in snap)
        assert not any(s["name"] == "preempt" for s in snap)

    @pytest.mark.asyncio
    async def test_reject_policy_does_not_consult_in_flight(self, monkeypatch) -> None:
        monkeypatch.setitem(config_mod.settings.double_texting_per_channel, "ch_reject", "reject")
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s9")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")
        prev.begin_tool("read_file")

        sess = MagicMock(channel="ch_reject")
        decision = await a._preempt_or_queue_prev_task(session_id="s9", session=sess)
        # REJECT in agent layer is treated as recoverable proceed
        # (HTTP layer should have blocked); no downgrade telemetry.
        assert decision == "proceed"
        snap = metrics.snapshot()
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)


# ── Registry-level functional checks (parity with completeness tests) ──


class TestRegistryRuntime:
    def test_partition_by_behavior(self) -> None:
        b, c = partition_by_behavior(["write_file", "read_file", "run_shell"])
        assert b == ["write_file", "run_shell"]
        assert c == ["read_file"]

    def test_has_any_block_tool(self) -> None:
        assert not has_any_block_tool([])
        assert not has_any_block_tool(["read_file", "grep"])
        assert has_any_block_tool(["read_file", "write_file"])
        assert has_any_block_tool(["unknown_tool_zzz"])  # unknown → block

    def test_get_with_mcp_annotations(self) -> None:
        assert (
            get_tool_interrupt_behavior(
                "unknown_z", mcp_annotations={"interruptBehavior": "cancel"}
            )
            == "cancel"
        )
        # Built-in classification wins over MCP annotation
        assert (
            get_tool_interrupt_behavior(
                "write_file", mcp_annotations={"interruptBehavior": "cancel"}
            )
            == "block"
        )

    def test_default_behavior_is_block(self) -> None:
        assert DEFAULT_BEHAVIOR == "block"

    def test_known_tools_nonempty(self) -> None:
        assert len(known_tools()) >= 100

    def test_warn_unclassified_counts_correctly(self, caplog) -> None:
        import logging

        caplog.set_level(logging.WARNING)
        n = warn_unclassified_tools(["read_file", "unknown_a", "unknown_b"])
        assert n == 2

    def test_is_unknown_tool(self) -> None:
        assert not is_unknown_tool("read_file")
        assert is_unknown_tool("never_registered_xyz")


# ── FOLLOW-UP-S4-A: QUEUE timeout extension when block tool still in flight ─


class TestQueueTimeoutBlockExtension:
    """v1.28.2 FOLLOW-UP-S4-A: when the QUEUE wait times out but the
    old task is still running a block-class tool, extend the wait
    once before cancelling.  Prevents long-write tools (write_file
    spanning > preempt_settle_timeout_ms) from being cancelled mid
    flight purely because the operator picked a tight timeout."""

    @pytest.fixture
    def _queue_channel(self, monkeypatch):
        """Channel resolving to QUEUE policy (no allow_interrupt)."""
        monkeypatch.setitem(config_mod.settings.double_texting_per_channel, "ch_q", "queue")
        return "ch_q"

    @pytest.mark.asyncio
    async def test_extension_kicks_in_when_block_tool_in_flight(
        self, monkeypatch, _queue_channel, _short_settle_timeout
    ) -> None:
        """First wait times out with write_file in_flight, extension
        window settles the task, decision is 'queued_then_proceed'
        WITHOUT firing the cancel marker."""
        # ``timeout_s = max(0.5, preempt_settle_timeout_ms/1000)`` so the
        # first wait floor is 500ms regardless of fixture-set 200ms.  We
        # need the settle to happen AFTER first timeout (>500ms) but
        # WITHIN the extension window (extension_ms=500 -> total <1000ms).
        monkeypatch.setattr(config_mod.settings, "preempt_block_tool_extension_ms", 500)

        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s_ext1")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")

        a._append_preempt_marker = MagicMock()

        async def settle_during_extension():
            # Settle at 700ms: past 500ms first-timeout, before 1000ms
            # second-timeout (500 + 500 extension).
            await asyncio.sleep(0.7)
            prev.mark_settled()

        asyncio.create_task(settle_during_extension())

        sess = MagicMock(channel=_queue_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s_ext1", session=sess)

        assert decision == "queued_then_proceed"
        labels = [r["labels"] for r in metrics.snapshot() if r["name"] == "queue_extended"]
        assert any(label.get("reason") == "block_in_flight" for label in labels)
        # Settled within extension window -> no cancel marker.
        a._append_preempt_marker.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_extension_when_in_flight_is_cancel_only(
        self, monkeypatch, _queue_channel, _short_settle_timeout
    ) -> None:
        """Old task has only cancel-class tools in flight -> no extension,
        normal timeout -> cancel marker written."""
        monkeypatch.setattr(config_mod.settings, "preempt_block_tool_extension_ms", 5000)

        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s_ext2")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("read_file")

        a._append_preempt_marker = MagicMock()

        sess = MagicMock(channel=_queue_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s_ext2", session=sess)
        assert decision == "queued_then_proceed"
        labels = [r["labels"] for r in metrics.snapshot() if r["name"] == "queue_extended"]
        assert labels == []
        a._append_preempt_marker.assert_called_once()

    @pytest.mark.asyncio
    async def test_extension_disabled_when_ms_zero(
        self, monkeypatch, _queue_channel, _short_settle_timeout
    ) -> None:
        """preempt_block_tool_extension_ms=0 disables the mechanism even
        with block tools in flight (back-compat with v1.28.2 pre-fix)."""
        monkeypatch.setattr(config_mod.settings, "preempt_block_tool_extension_ms", 0)

        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s_ext3")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")

        a._append_preempt_marker = MagicMock()

        sess = MagicMock(channel=_queue_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s_ext3", session=sess)
        assert decision == "queued_then_proceed"
        labels = [r["labels"] for r in metrics.snapshot() if r["name"] == "queue_extended"]
        assert labels == []
        a._append_preempt_marker.assert_called_once()


# ── FOLLOW-UP-S4-B: MCP sub-tool name encoding ─────────────────────────


class TestMcpSubToolEncoding:
    """v1.28.2 FOLLOW-UP-S4-B: when ``call_mcp_tool`` is dispatched,
    in_flight_tools registers the encoded sub-tool reference
    (``mcp:server:tool_name``) and the preempt resolver consults MCP
    annotations to make a per-sub-tool decision."""

    def test_encode_round_trip(self) -> None:
        from openakita.core.tool_interrupt_behavior import (
            encode_mcp_sub_tool,
            parse_mcp_sub_tool,
        )

        enc = encode_mcp_sub_tool("notion", "create_page")
        assert enc == "mcp:notion:create_page"
        assert parse_mcp_sub_tool(enc) == ("notion", "create_page")

    def test_encode_missing_pieces_falls_back(self) -> None:
        from openakita.core.tool_interrupt_behavior import encode_mcp_sub_tool

        assert encode_mcp_sub_tool("", "x") == "call_mcp_tool"
        assert encode_mcp_sub_tool("srv", "") == "call_mcp_tool"
        assert encode_mcp_sub_tool("", "") == "call_mcp_tool"

    def test_parse_rejects_non_mcp_names(self) -> None:
        from openakita.core.tool_interrupt_behavior import parse_mcp_sub_tool

        assert parse_mcp_sub_tool("read_file") is None
        assert parse_mcp_sub_tool("mcp:malformed") is None
        assert parse_mcp_sub_tool("") is None

    def test_resolve_mcp_tool_behavior_uses_annotations(self) -> None:
        from openakita.core.tool_interrupt_behavior import (
            resolve_mcp_tool_behavior,
        )

        fake_tool = MagicMock()
        fake_tool.annotations = {"interruptBehavior": "cancel"}
        client = MagicMock()
        client._tools = {"notion:search_pages": fake_tool}

        assert resolve_mcp_tool_behavior("notion", "search_pages", mcp_client=client) == "cancel"

    def test_resolve_mcp_falls_back_to_block_when_no_annotation(self) -> None:
        from openakita.core.tool_interrupt_behavior import (
            resolve_mcp_tool_behavior,
        )

        fake_tool = MagicMock()
        fake_tool.annotations = {}
        client = MagicMock()
        client._tools = {"github:create_issue": fake_tool}

        assert resolve_mcp_tool_behavior("github", "create_issue", mcp_client=client) == "block"

    def test_resolve_mcp_no_client(self) -> None:
        from openakita.core.tool_interrupt_behavior import (
            resolve_mcp_tool_behavior,
        )

        assert resolve_mcp_tool_behavior("any", "any", mcp_client=None) == "block"

    def test_has_any_block_in_flight_with_readonly_mcp(self) -> None:
        """Mixed in_flight: pure-cancel built-in + read-only MCP sub-tool
        -> no block tool actually in flight -> resolver returns False
        even though a generic 'call_mcp_tool' would have been block."""
        from openakita.core.tool_interrupt_behavior import (
            has_any_block_in_flight,
        )

        fake_tool = MagicMock()
        fake_tool.annotations = {"interruptBehavior": "cancel"}
        client = MagicMock()
        client._tools = {"notion:search_pages": fake_tool}

        result = has_any_block_in_flight(
            ["read_file", "mcp:notion:search_pages"],
            mcp_client=client,
        )
        assert result is False

    def test_has_any_block_in_flight_with_writing_mcp(self) -> None:
        """MCP sub-tool with no annotation -> defaults to block."""
        from openakita.core.tool_interrupt_behavior import (
            has_any_block_in_flight,
        )

        fake_tool = MagicMock()
        fake_tool.annotations = {}
        client = MagicMock()
        client._tools = {"notion:create_page": fake_tool}

        result = has_any_block_in_flight(
            ["read_file", "mcp:notion:create_page"],
            mcp_client=client,
        )
        assert result is True

    def test_in_flight_name_helper(self) -> None:
        """ToolExecutor._in_flight_name encodes call_mcp_tool inputs."""
        from openakita.core.tool_executor import ToolExecutor

        assert (
            ToolExecutor._in_flight_name(
                "call_mcp_tool",
                {"server": "notion", "tool_name": "search_pages"},
            )
            == "mcp:notion:search_pages"
        )
        assert ToolExecutor._in_flight_name("call_mcp_tool", {}) == "call_mcp_tool"
        assert ToolExecutor._in_flight_name("read_file", {"path": "/x"}) == "read_file"

    @pytest.mark.asyncio
    async def test_readonly_mcp_does_not_downgrade_interrupt(
        self,
        monkeypatch,
        _short_settle_timeout,
        _interrupt_channel,
    ) -> None:
        """End-to-end: only an annotated-as-cancel MCP sub-tool is in
        flight -> INTERRUPT proceeds with a real preempt, not a downgrade."""
        a = _make_stub_agent()
        a._append_preempt_marker = MagicMock()

        fake_tool = MagicMock()
        fake_tool.annotations = {"interruptBehavior": "cancel"}
        client = MagicMock()
        client._tools = {"notion:search_pages": fake_tool}
        a.mcp_client = client

        prev = a.agent_state.begin_task(session_id="s_mcp_ro")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("mcp:notion:search_pages")

        async def settle_later():
            await asyncio.sleep(0.05)
            prev.mark_settled()

        asyncio.create_task(settle_later())

        sess = MagicMock(channel=_interrupt_channel)
        decision = await a._preempt_or_queue_prev_task(session_id="s_mcp_ro", session=sess)
        assert decision == "preempted"
        # No downgrade counter — INTERRUPT proceeded as real preempt.
        labels = [r["labels"] for r in metrics.snapshot() if r["name"] == "interrupt_downgrade"]
        assert labels == []
