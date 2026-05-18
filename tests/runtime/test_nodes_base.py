"""Tests for runtime.nodes.base — protocol, context, lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.runtime.cancel_token import CancellationToken, CancelledByToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import MessengerNode, NodeAddress, NodeMessage
from openakita.runtime.models import NodeStatus
from openakita.runtime.nodes import (
    BaseNode,
    NodeContext,
    NodeLifecycleEvent,
    NodeProtocol,
    NodeRegistration,
)
from openakita.runtime.stream import StreamBus, StreamEvent
from openakita.runtime.supervisor import DelegationResult

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Echo(BaseNode):
    """Smallest non-trivial subclass: echo the instruction back."""

    node_type = "echo"

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        return DelegationResult(
            success=True,
            speaker=self.node_id,
            message=f"echo: {msg.instruction}",
            metadata={"correlation_id": msg.correlation_id},
        )


class _Crash(BaseNode):
    """Always raises a chosen exception type."""

    node_type = "crash"

    def __init__(self, *, exc: BaseException, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        raise self._exc


class _Slow(BaseNode):
    """Cooperative cancel-aware node that emits progress."""

    node_type = "slow"

    async def handle_message(
        self, ctx: NodeContext, msg: NodeMessage
    ) -> DelegationResult:
        await self.emit_progress({"phase": "started"})
        for _ in range(5):
            ctx.cancel_token.raise_if_cancelled()
            await asyncio.sleep(0)
        return DelegationResult(success=True, speaker=self.node_id, message="done")


def _make_ctx(
    *, cancel: CancellationToken | None = None
) -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    ctx = NodeContext(
        node_id="n-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=cancel or CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )
    return stream, ctx


def _msg(*, instruction: str = "do it") -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("n-1"),
        instruction=instruction,
        correlation_id="corr-1",
    )


class _StreamCollector:
    """Subscribe in the background and collect events."""

    def __init__(self, stream: StreamBus, *channels: str) -> None:
        self._stream = stream
        self._channels = channels
        self._events: list[StreamEvent] = []
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _StreamCollector:
        async def consume() -> None:
            async for ev in self._stream.subscribe(*self._channels):
                self._events.append(ev)

        self._task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._stream.close()
        assert self._task is not None
        try:
            await asyncio.wait_for(self._task, timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()

    async def flush(self, expected: int, *, timeout: float = 1.0) -> list[StreamEvent]:
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self._events) < expected:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.01)
        return list(self._events)


# ---------------------------------------------------------------------------
# protocol surface
# ---------------------------------------------------------------------------


def test_basenode_satisfies_protocol() -> None:
    node = _Echo(node_id="n-1", org_id="org-1", role="echoer")
    assert isinstance(node, NodeProtocol)


def test_basenode_is_drop_in_messenger_node() -> None:
    """Critical: BaseNode must be drop-in for MessengerNode (1-arg signatures)."""
    node = _Echo(node_id="n-1", org_id="org-1", role="echoer")
    assert isinstance(node, MessengerNode)


def test_registration_record_carries_role_and_workbench() -> None:
    plain = _Echo(node_id="n-1", org_id="org-1", role="r")
    assert plain.registration() == NodeRegistration(
        node_id="n-1", role="r", workbench=None
    )
    wb = _Echo(
        node_id="wb-1",
        org_id="org-1",
        role=None,
        workbench=("happyhorse-video", "fast"),
    )
    assert wb.registration().workbench == ("happyhorse-video", "fast")


# ---------------------------------------------------------------------------
# happy path: activation, busy/idle lifecycle, success result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_then_message_emits_full_lifecycle() -> None:
    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = _Echo(node_id="n-1", org_id="org-1", role="echoer")
        await node.on_activate(ctx)
        result = await node.on_message(_msg())
        events = await collector.flush(4)
    assert result.success
    assert result.message == "echo: do it"
    types = [e.type for e in events]
    assert types[:4] == [
        NodeLifecycleEvent.ACTIVATED.value,
        NodeLifecycleEvent.IDLE.value,
        NodeLifecycleEvent.BUSY.value,
        NodeLifecycleEvent.IDLE.value,
    ]
    assert node.status is NodeStatus.IDLE
    assert node.last_progress_at is not None


@pytest.mark.asyncio
async def test_message_before_activation_returns_failure_without_emitting() -> None:
    stream, ctx = _make_ctx()
    node = _Echo(node_id="n-1", org_id="org-1", role="echoer")
    result = await node.on_message(_msg())
    assert not result.success
    assert "before activation" in result.message


# ---------------------------------------------------------------------------
# error path: unexpected exception is converted to ERROR + failed result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_promotes_to_error_status() -> None:
    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = _Crash(
            node_id="n-1",
            org_id="org-1",
            exc=RuntimeError("boom"),
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg())
        events = await collector.flush(4)
    assert not result.success
    assert "RuntimeError" in result.message
    assert "boom" in result.message
    assert node.status is NodeStatus.ERROR
    types = [e.type for e in events]
    assert NodeLifecycleEvent.ERROR.value in types
    busy_idx = types.index(NodeLifecycleEvent.BUSY.value)
    after_busy = types[busy_idx + 1 :]
    assert NodeLifecycleEvent.IDLE.value not in after_busy


# ---------------------------------------------------------------------------
# cooperative cancel: token-aware nodes return failure, never re-raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_token_observed_inside_handle_message() -> None:
    cancel = CancellationToken()
    stream, ctx = _make_ctx(cancel=cancel)
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = _Slow(node_id="n-1", org_id="org-1")
        await node.on_activate(ctx)
        cancel.cancel("user pressed stop")
        result = await node.on_message(_msg())
        events = await collector.flush(5)
    assert not result.success
    assert "user pressed stop" in result.message
    assert node.status is NodeStatus.CANCELLED
    types = [e.type for e in events]
    assert NodeLifecycleEvent.CANCELLED.value in types


@pytest.mark.asyncio
async def test_explicit_cancel_is_idempotent() -> None:
    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = _Echo(node_id="n-1", org_id="org-1")
        await node.on_activate(ctx)
        await node.on_cancel("first")
        await node.on_cancel("second")
        events = await collector.flush(3)
    types = [e.type for e in events]
    assert types.count(NodeLifecycleEvent.CANCELLED.value) == 1


# ---------------------------------------------------------------------------
# terminal state guard: re-delivering after cancel returns failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_after_cancel_returns_failure_without_executing() -> None:
    stream, ctx = _make_ctx()
    node = _Echo(node_id="n-1", org_id="org-1")
    await node.on_activate(ctx)
    await node.on_cancel("stop")
    result = await node.on_message(_msg())
    assert not result.success
    assert "cancelled" in result.message
    assert node.status is NodeStatus.CANCELLED


# ---------------------------------------------------------------------------
# checkpoint round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_load_state_round_trip() -> None:
    stream, ctx = _make_ctx()
    src = _Echo(node_id="n-1", org_id="org-1", role="echoer")
    await src.on_activate(ctx)
    await src.on_message(_msg())
    state = await src.save_state()
    fresh = _Echo(node_id="n-1", org_id="org-1", role="echoer")
    await fresh.load_state(state)
    assert fresh.node_id == src.node_id
    assert fresh.status is src.status
    assert fresh.last_progress_at == src.last_progress_at


# ---------------------------------------------------------------------------
# emit_progress refreshes last_progress_at and fires PROGRESS event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_progress_records_timestamp_and_event() -> None:
    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = _Slow(node_id="n-1", org_id="org-1")
        await node.on_activate(ctx)
        await node.on_message(_msg())
        events = await collector.flush(5)
    types = [e.type for e in events]
    assert NodeLifecycleEvent.PROGRESS.value in types
    assert node.last_progress_at is not None


# ---------------------------------------------------------------------------
# subclass that does NOT override handle_message must raise NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_base_handle_message_is_abstract_in_practice() -> None:
    class _Bare(BaseNode):
        node_type = "bare"

    stream, ctx = _make_ctx()
    node = _Bare(node_id="n-1", org_id="org-1")
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "NotImplementedError" in result.message
    assert node.status is NodeStatus.ERROR


# ---------------------------------------------------------------------------
# cooperative cancel raised cleanly in handle_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_raising_cancelled_by_token_routes_to_on_cancel() -> None:
    cancel = CancellationToken()
    stream, ctx = _make_ctx(cancel=cancel)
    node = _Crash(
        node_id="n-1",
        org_id="org-1",
        exc=CancelledByToken("explicit"),
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "explicit" in result.message
    assert node.status is NodeStatus.CANCELLED
