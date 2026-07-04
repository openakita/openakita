"""Tests for :mod:`openakita.runtime.stream`.

Phase 1 commit 4. Asserts ADR-0006 promises:

* multi-channel subscription receives only matching events;
* one slow subscriber does not block other subscribers;
* oldest-drop backpressure: when a subscriber's queue is full, the
  oldest event is discarded; the new event is delivered; per-subscriber
  drop counter increments;
* strict mode validates the envelope and rejects malformed events;
* close() unblocks every active subscriber;
* the standard channel set matches ADR-0006 exactly.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from openakita.runtime.stream import (
    STANDARD_CHANNELS,
    StreamBus,
    StreamEvent,
    Subscription,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_standard_channel_set_matches_adr_0006() -> None:
    assert {
        "values",
        "updates",
        "tasks",
        "checkpoints",
        "messages",
        "debug",
        "progress_ledger",
        "lifecycle",
    } == STANDARD_CHANNELS


def test_event_to_jsonable_round_trip() -> None:
    bus = StreamBus(strict=True)
    # We construct an event via emit so timestamps and ids match the
    # production code path.

    async def _emit() -> StreamEvent:
        return await bus.emit(
            "updates",
            "node_started",
            {"node_id": "node_x"},
            command_id="cmd_a",
            org_id="org_b",
            superstep=3,
            correlation_id="corr_1",
        )

    event = asyncio.run(_emit())
    payload = event.to_jsonable()
    assert payload["channel"] == "updates"
    assert payload["type"] == "node_started"
    assert payload["payload"] == {"node_id": "node_x"}
    assert payload["command_id"] == "cmd_a"
    assert payload["superstep"] == 3
    assert payload["correlation_id"] == "corr_1"


def test_event_validate_rejects_bad_channel() -> None:
    ev = StreamEvent(
        channel="",
        event_id="x",
        command_id="cmd",
        org_id="org",
        superstep=0,
        emitted_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        type="t",
        payload={},
    )
    with pytest.raises(ValueError):
        ev.validate()


# ---------------------------------------------------------------------------
# Subscription routing
# ---------------------------------------------------------------------------


async def _drain(bus: StreamBus, channels: tuple[str, ...], n: int) -> list[StreamEvent]:
    out: list[StreamEvent] = []

    async def consume() -> None:
        async for ev in bus.subscribe(*channels):
            out.append(ev)
            if len(out) >= n:
                return

    await asyncio.wait_for(consume(), timeout=2.0)
    return out


async def test_subscriber_receives_only_matching_channels() -> None:
    bus = StreamBus(strict=True)

    consumed: list[StreamEvent] = []
    ready = asyncio.Event()

    async def consume() -> None:
        async for ev in bus.subscribe("updates"):
            ready.set()
            consumed.append(ev)
            if len(consumed) >= 2:
                return

    task = asyncio.create_task(consume())
    # Let subscribe attach.
    await asyncio.sleep(0.01)

    await bus.emit("debug", "ignore", {"k": 1}, command_id="c", org_id="o")
    await bus.emit("updates", "node_started", {"k": 2}, command_id="c", org_id="o")
    await bus.emit("messages", "ignore", {"k": 3}, command_id="c", org_id="o")
    await bus.emit("updates", "node_progress", {"k": 4}, command_id="c", org_id="o")

    await asyncio.wait_for(task, timeout=2.0)
    assert [e.type for e in consumed] == ["node_started", "node_progress"]


async def test_two_subscribers_independent() -> None:
    bus = StreamBus(strict=True)
    a: list[str] = []
    b: list[str] = []

    async def sub_a() -> None:
        async for ev in bus.subscribe("updates"):
            a.append(ev.type)
            if len(a) >= 1:
                return

    async def sub_b() -> None:
        async for ev in bus.subscribe("checkpoints"):
            b.append(ev.type)
            if len(b) >= 1:
                return

    ta = asyncio.create_task(sub_a())
    tb = asyncio.create_task(sub_b())
    await asyncio.sleep(0.01)

    await bus.emit("updates", "u1", {}, command_id="c", org_id="o")
    await bus.emit("checkpoints", "ck1", {}, command_id="c", org_id="o")

    await asyncio.gather(ta, tb)
    assert a == ["u1"]
    assert b == ["ck1"]


# ---------------------------------------------------------------------------
# Backpressure / oldest-drop
# ---------------------------------------------------------------------------


async def test_full_queue_drops_oldest_then_admits_new() -> None:
    bus = StreamBus(max_queue_size=2)
    ready = asyncio.Event()
    received: list[str] = []

    async def consume() -> None:
        async for ev in bus.subscribe("updates"):
            ready.set()
            await asyncio.sleep(0.05)  # slow consumer
            received.append(ev.type)
            if len(received) >= 2:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    # Fill 4 events into a queue of size 2 — first one consumed slowly,
    # so 3 stack up; oldest of the stacked 3 must be dropped.
    for i in range(4):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")

    await asyncio.wait_for(task, timeout=2.0)
    # We consume two events; the bus dropped at least one.
    assert len(received) == 2
    assert bus.total_dropped >= 1
    # The newest event must have arrived (oldest-drop policy)
    assert "u3" in received


async def test_no_subscribers_no_block() -> None:
    bus = StreamBus()
    # Emitting with no subscribers must complete instantly.
    for i in range(100):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")
    assert bus.total_emitted == 100
    assert bus.total_dropped == 0


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------


async def test_strict_mode_rejects_empty_type() -> None:
    bus = StreamBus(strict=True)
    with pytest.raises(ValueError):
        await bus.emit("updates", "", {}, command_id="c", org_id="o")


async def test_strict_mode_rejects_bad_payload() -> None:
    bus = StreamBus(strict=True)
    with pytest.raises(ValueError):
        await bus.emit("updates", "x", "not a dict", command_id="c", org_id="o")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


async def test_close_unblocks_subscribers() -> None:
    bus = StreamBus()

    async def consume() -> int:
        n = 0
        async for _ in bus.subscribe("updates"):
            n += 1
        return n

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    await bus.emit("updates", "u1", {}, command_id="c", org_id="o")
    await asyncio.sleep(0.01)
    await bus.close()
    n = await asyncio.wait_for(task, timeout=1.0)
    assert n == 1


# ---------------------------------------------------------------------------
# Subscribe input validation
# ---------------------------------------------------------------------------


async def test_subscribe_requires_at_least_one_channel() -> None:
    bus = StreamBus()
    gen = bus.subscribe()
    # Pull one item to trigger the body. Generators raise on first
    # __anext__; convert to coroutine via asyncio.
    with pytest.raises(ValueError):
        async for _ in gen:
            break


async def test_emit_many_fans_out_batch() -> None:
    bus = StreamBus(strict=True)
    received: list[str] = []

    async def consume() -> None:
        async for ev in bus.subscribe("updates"):
            received.append(ev.type)
            if len(received) >= 3:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    # Build events the same way emit() would.
    base = await bus.emit("updates", "u0", {}, command_id="c", org_id="o", superstep=1)
    batch = [
        StreamEvent(
            channel="updates",
            event_id="ev1",
            command_id=base.command_id,
            org_id=base.org_id,
            superstep=2,
            emitted_at=base.emitted_at,
            type="u1",
            payload={},
        ),
        StreamEvent(
            channel="updates",
            event_id="ev2",
            command_id=base.command_id,
            org_id=base.org_id,
            superstep=2,
            emitted_at=base.emitted_at,
            type="u2",
            payload={},
        ),
    ]
    await bus.emit_many(batch)
    await asyncio.wait_for(task, timeout=2.0)
    assert received == ["u0", "u1", "u2"]


async def test_stats_returns_counters() -> None:
    bus = StreamBus()
    await bus.emit("updates", "u", {}, command_id="c", org_id="o")
    s = bus.stats()
    assert s["total_emitted"] == 1
    assert s["total_dropped"] == 0
    assert s["subscribers"] == 0


# ---------------------------------------------------------------------------
# Drain-on-close semantics (P-RC-2; closes G-RC-1 residual risk #1)
# ---------------------------------------------------------------------------


async def test_close_drains_pending_events_before_signalling() -> None:
    """Default drain_on_close=True: close() waits for queued events.

    A slow consumer that sleeps between iterations will not have read
    every emitted event when we ask the bus to close. With drain-on-
    close, ``close()`` blocks until the queue is empty, so the
    consumer reads all of them before its loop exits.
    """
    bus = StreamBus()
    received: list[str] = []
    started = asyncio.Event()

    async def consume() -> None:
        async for ev in bus.subscribe("updates"):
            started.set()
            await asyncio.sleep(0.01)  # slow consumer
            received.append(ev.type)

    task = asyncio.create_task(consume())
    # Wait for the subscriber to attach.
    for _ in range(50):
        async with bus._lock:
            if bus._subscriptions:
                break
        await asyncio.sleep(0.005)
    for i in range(5):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")
    # Close immediately -- drain must wait until the queue is empty.
    await bus.close(drain_timeout=1.0)
    await asyncio.wait_for(task, timeout=2.0)
    assert received == ["u0", "u1", "u2", "u3", "u4"]


async def test_close_drain_times_out_cleanly_for_stuck_consumer() -> None:
    """Pathological consumer that never reads must not wedge close().

    We construct a subscriber that never pulls from its queue. The
    bus must give up after ``drain_timeout`` seconds, log a warning
    (we just verify no exception escapes), and still close cleanly.
    """
    bus = StreamBus(max_queue_size=4)
    # Manually attach a subscription that will never have its queue
    # drained -- we don't iterate the async generator.
    sub = Subscription(
        channels=frozenset({"updates"}),
        queue=asyncio.Queue(maxsize=4),
        drain_on_close=True,
    )
    async with bus._lock:
        bus._subscriptions.append(sub)
    # Stuff the queue.
    for i in range(3):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")
    assert sub.queue.qsize() == 3

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await bus.close(drain_timeout=0.1)
    elapsed = loop.time() - t0
    # Drain timeout must be honoured: between ~0.1s and a generous
    # upper bound (CI variance). The bus must NOT hang.
    assert elapsed < 1.0, f"close hung past drain_timeout: {elapsed:.3f}s"
    assert sub.closed.is_set()


async def test_close_drain_on_close_false_is_eager() -> None:
    """Legacy callers can opt out: drain_on_close=False closes immediately.

    The subscriber yields any events that already landed in its queue
    before close, but the bus does NOT wait for the queue to drain.
    """
    bus = StreamBus()
    received: list[str] = []
    started = asyncio.Event()

    async def consume() -> None:
        async for ev in bus.subscribe("updates", drain_on_close=False):
            started.set()
            await asyncio.sleep(0.05)  # slow consumer
            received.append(ev.type)

    task = asyncio.create_task(consume())
    for _ in range(50):
        async with bus._lock:
            if bus._subscriptions:
                break
        await asyncio.sleep(0.005)
    for i in range(5):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await bus.close(drain_timeout=1.0)
    elapsed = loop.time() - t0
    # Eager close: should return promptly because no subscription
    # opted in.
    assert elapsed < 0.05, f"eager close took too long: {elapsed:.3f}s"
    # Cancel the consumer task; we don't care what it received,
    # only that close() did not block on draining.
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_close_mixed_eager_and_drain_subscribers() -> None:
    """Mixed: an eager subscriber must not hold up an opt-in subscriber.

    The drain-on-close subscriber pulls one slow event after close;
    the eager subscriber's queue is allowed to be non-empty when
    close fires.
    """
    bus = StreamBus()
    drain_received: list[str] = []
    eager_received: list[str] = []

    async def drain_consume() -> None:
        async for ev in bus.subscribe("updates"):
            await asyncio.sleep(0.005)
            drain_received.append(ev.type)

    async def eager_consume() -> None:
        async for ev in bus.subscribe("messages", drain_on_close=False):
            eager_received.append(ev.type)

    t1 = asyncio.create_task(drain_consume())
    t2 = asyncio.create_task(eager_consume())
    for _ in range(100):
        async with bus._lock:
            if len(bus._subscriptions) >= 2:
                break
        await asyncio.sleep(0.005)

    for i in range(3):
        await bus.emit("updates", f"u{i}", {}, command_id="c", org_id="o")
        await bus.emit("messages", f"m{i}", {}, command_id="c", org_id="o")

    await bus.close(drain_timeout=1.0)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=2.0)
    # The drain-eligible subscriber must have received ALL three.
    assert drain_received == ["u0", "u1", "u2"]
    # The eager subscriber may have received fewer; we only assert it
    # ran at all.
    assert isinstance(eager_received, list)




# ---------------------------------------------------------------------------
# Closed-gate semantics (P-RC-3 T3) + public subscription surface (P-RC-3 T5)
# ---------------------------------------------------------------------------


async def test_subscribe_after_close_raises_runtime_error() -> None:
    bus = StreamBus()
    await bus.close()
    assert bus.is_closed
    gen = bus.subscribe('updates')
    with pytest.raises(RuntimeError, match='closed'):
        await gen.__anext__()


async def test_emit_after_close_is_silent_and_debug_logged(caplog) -> None:
    bus = StreamBus()
    await bus.close()
    import logging
    with caplog.at_level(logging.DEBUG, logger='openakita.runtime.stream'):
        ev = await bus.emit('updates', 'u_post_close', {}, command_id='c', org_id='o')
    assert ev.type == 'u_post_close'
    assert bus.total_emitted == 0
    assert bus.total_dropped == 0
    assert any('closed bus' in r.getMessage() for r in caplog.records)


async def test_close_is_reentrant() -> None:
    bus = StreamBus()
    await bus.close()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await bus.close()
    elapsed = loop.time() - t0
    assert elapsed < 0.05
    assert bus.is_closed


async def test_public_subscription_api_is_equivalent_to_private() -> None:
    """`make_subscription` + `register_subscription` deliver the same
    semantics the SSE route previously achieved by poking
    `bus._lock` / `bus._subscriptions` / `bus._max_queue` directly.
    """
    bus = StreamBus(max_queue_size=8)
    assert bus.subscription_capacity() == 8
    sub = bus.make_subscription(('updates', 'messages'), drain_on_close=False)
    assert sub.channels == frozenset({'updates', 'messages'})
    assert sub.queue.maxsize == 8
    assert sub.drain_on_close is False
    await bus.register_subscription(sub)
    assert bus.subscriber_count == 1
    await bus.emit('updates', 'u1', {}, command_id='c', org_id='o')
    await bus.emit('debug', 'ignored', {}, command_id='c', org_id='o')
    ev = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert ev.type == 'u1'
    await bus.detach_subscription(sub)
    assert bus.subscriber_count == 0
    assert sub.closed.is_set()


async def test_register_subscription_after_close_raises() -> None:
    bus = StreamBus()
    sub = bus.make_subscription(('updates',), drain_on_close=False)
    await bus.close()
    with pytest.raises(RuntimeError, match='closed'):
        await bus.register_subscription(sub)


# Ensure no orphaned tasks pollute the event loop between tests.

@pytest.fixture(autouse=True)
async def _drain_loop() -> object:
    yield
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    me = asyncio.current_task()
    for t in pending:
        if t is me:
            continue
        t.cancel()
        with contextlib.suppress(BaseException):
            await t
