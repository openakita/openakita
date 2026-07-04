"""Tests for :mod:openakita.runtime.stream_registry cleanup (P-RC-3 T4)."""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.stream_registry import (
    cleanup_idle,
    cleanup_idle_buses_periodically,
    get_or_create_org_stream_bus,
    list_org_stream_buses,
    mark_subscriber_attached,
    mark_subscriber_lost,
    reset_org_stream_buses,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_org_stream_buses()
    yield
    reset_org_stream_buses()


# ----------------------------------------------------------------- cleanup


async def test_idle_bus_is_recycled_after_idle_seconds() -> None:
    bus = get_or_create_org_stream_bus('org_a')
    # Subscriber count is zero straight after creation; the registry
    # stamps the loss timestamp inside get_or_create. Force a known
    # `now` past the deadline.
    mark_subscriber_lost('org_a')
    n = cleanup_idle(now=1_000_000.0, idle_seconds=60.0)
    assert n == 1
    assert 'org_a' not in list_org_stream_buses()
    # The bus.close() coroutine was scheduled; await a tick so it
    # actually completes before the test exits.
    await asyncio.sleep(0)
    assert bus.is_closed


async def test_active_bus_is_retained() -> None:
    bus = get_or_create_org_stream_bus('org_b')
    sub = bus.make_subscription(('updates',), drain_on_close=False)
    await bus.register_subscription(sub)
    mark_subscriber_attached('org_b')
    n = cleanup_idle(now=1_000_000.0, idle_seconds=60.0)
    assert n == 0
    assert 'org_b' in list_org_stream_buses()
    await bus.detach_subscription(sub)


async def test_reattach_before_timeout_keeps_bus() -> None:
    bus = get_or_create_org_stream_bus('org_c')
    mark_subscriber_lost('org_c')
    # 30 s elapsed, idle threshold is 60 s -- bus stays.
    n = cleanup_idle(now=30.0, idle_seconds=60.0)
    assert n == 0
    # New subscriber attaches; idle stamp is cleared.
    sub = bus.make_subscription(('updates',), drain_on_close=False)
    await bus.register_subscription(sub)
    mark_subscriber_attached('org_c')
    # Even far past the original deadline, the bus survives.
    n = cleanup_idle(now=10_000.0, idle_seconds=60.0)
    assert n == 0
    assert 'org_c' in list_org_stream_buses()
    await bus.detach_subscription(sub)


async def test_cleanup_idle_returns_count_across_multiple_orgs() -> None:
    for oid in ('o1', 'o2', 'o3'):
        get_or_create_org_stream_bus(oid)
        mark_subscriber_lost(oid)
    bus_keep = get_or_create_org_stream_bus('o_keep')
    sub = bus_keep.make_subscription(('updates',), drain_on_close=False)
    await bus_keep.register_subscription(sub)
    mark_subscriber_attached('o_keep')
    n = cleanup_idle(now=1_000_000.0, idle_seconds=60.0)
    assert n == 3
    remaining = list_org_stream_buses()
    assert set(remaining.keys()) == {'o_keep'}
    await bus_keep.detach_subscription(sub)


async def test_recycled_bus_reattach_gets_fresh_instance() -> None:
    first = get_or_create_org_stream_bus('org_d')
    mark_subscriber_lost('org_d')
    cleanup_idle(now=1_000_000.0, idle_seconds=60.0)
    await asyncio.sleep(0)
    assert first.is_closed
    # Next access mints a brand new bus.
    second = get_or_create_org_stream_bus('org_d')
    assert second is not first
    assert not second.is_closed


async def test_periodic_task_is_cooperatively_cancelled() -> None:
    """`cleanup_idle_buses_periodically` must exit promptly on cancel."""
    task = asyncio.create_task(
        cleanup_idle_buses_periodically(interval=0.05, idle_seconds=0.01)
    )
    await asyncio.sleep(0.15)  # let it run a few iterations
    task.cancel()
    # The coroutine swallows CancelledError and returns; awaiting must not raise.
    await task
    assert task.done()
