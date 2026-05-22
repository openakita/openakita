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
    monkeypatch.setattr(config_mod.settings, "preempt_settle_timeout_ms", 200)
    yield


@pytest.fixture
def _allow_interrupt(monkeypatch):
    """Enable INTERRUPT semantic; channel default is still QUEUE so we
    set per_channel override per-test."""
    monkeypatch.setattr(config_mod.settings, "double_texting_allow_interrupt", True)
    yield


@pytest.fixture
def _interrupt_channel(monkeypatch, _allow_interrupt):
    monkeypatch.setitem(
        config_mod.settings.double_texting_per_channel, "ch_intr", "interrupt"
    )
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
        assert sorted(t.get_in_flight_tools()) == sorted(
            ["read_file", "write_file", "grep"]
        )
        t.end_tool("write_file")
        assert sorted(t.get_in_flight_tools()) == sorted(["read_file", "grep"])


# ── tool_executor wiring ────────────────────────────────────────────


class TestToolExecutorBeginEndWiring:
    """Verify the source of ``ToolExecutor.execute_tool`` actually wires
    in_flight tracking — we can't easily run a real executor in unit
    tests, so we inspect the synthesized control-flow to lock in the
    invariant that begin and end both fire and pair up via ``finally``."""

    def test_execute_tool_source_contains_begin_and_end(self) -> None:
        import inspect

        from openakita.core.tool_executor import ToolExecutor

        src = inspect.getsource(ToolExecutor.execute_tool)
        # Begin in try, end in finally — both must be present.
        assert "task.begin_tool(tool_name)" in src
        assert "task.end_tool(tool_name)" in src
        # The end_tool MUST be reached on every code path → live in finally.
        # We don't dissect AST here but the presence + S4 comment is enough
        # of a tripwire for future refactors.
        assert "finally" in src
        # Same task lookup serves S3 AbortScope + S4 in_flight tracking.
        assert "_resolve_task" in src or "task = self._resolve_task" in src

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

        decision = await a._preempt_or_queue_prev_task(
            session_id="s1", session=sess
        )

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

        decision = await a._preempt_or_queue_prev_task(
            session_id="s2", session=sess
        )
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        assert any(
            s["name"] == "interrupt_downgrade"
            and s["labels"]["reason"] == "block_in_flight"
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
        decision = await a._preempt_or_queue_prev_task(
            session_id="s3", session=sess
        )
        assert decision == "queued_then_proceed"


class TestNoDowngradeWhenAllCancelSafe:
    """INTERRUPT must NOT downgrade when every in-flight tool is cancel-safe."""

    @pytest.mark.asyncio
    async def test_only_cancel_tools_real_preempt(
        self, _interrupt_channel
    ) -> None:
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
        decision = await a._preempt_or_queue_prev_task(
            session_id="s4", session=sess
        )
        assert decision == "preempted"
        assert prev.cancelled is True
        snap = metrics.snapshot()
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)
        assert any(
            s["name"] == "preempt" and s["labels"]["policy"] == "interrupt"
            for s in snap
        )

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
        decision = await a._preempt_or_queue_prev_task(
            session_id="s5", session=sess
        )
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
        decision = await a._preempt_or_queue_prev_task(
            session_id="s6", session=sess
        )
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
    async def test_queue_policy_does_not_check_in_flight(
        self, _short_settle_timeout
    ) -> None:
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

        # Default channel resolves to QUEUE.
        sess = MagicMock(channel="desktop")
        decision = await a._preempt_or_queue_prev_task(
            session_id="s8", session=sess
        )
        assert decision == "queued_then_proceed"
        snap = metrics.snapshot()
        # No interrupt_downgrade because policy was already QUEUE.
        assert not any(s["name"] == "interrupt_downgrade" for s in snap)

    @pytest.mark.asyncio
    async def test_reject_policy_does_not_consult_in_flight(
        self, monkeypatch
    ) -> None:
        monkeypatch.setitem(
            config_mod.settings.double_texting_per_channel, "ch_reject", "reject"
        )
        a = _make_stub_agent()
        prev = a.agent_state.begin_task(session_id="s9")
        prev.transition(TaskStatus.REASONING)
        prev.begin_tool("write_file")
        prev.begin_tool("read_file")

        sess = MagicMock(channel="ch_reject")
        decision = await a._preempt_or_queue_prev_task(
            session_id="s9", session=sess
        )
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
