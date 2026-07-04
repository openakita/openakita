"""Tests for :mod:`openakita.runtime.cancel_token`.

Phase 1 commit 2. Asserts the cooperative semantics promised by ADR-0004
and ADR-0007:

* a cancel never raises by surprise — only cooperative checks raise;
* callbacks fire on first transition only, in registration order;
* late callbacks (added after cancel) still fire;
* buggy callbacks do not block other callbacks;
* asyncio Future and Task linkage cancels the underlying object;
* ``CancellationToken.race`` distinguishes cooperative cancel from
  spurious :class:`asyncio.CancelledError`.
"""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.cancel_token import CancellationToken, CancelledByToken

# ---------------------------------------------------------------------------
# Basic state machine
# ---------------------------------------------------------------------------


def test_token_starts_uncancelled() -> None:
    t = CancellationToken()
    assert t.is_cancelled() is False
    assert t.reason == ""
    t.raise_if_cancelled()  # must not raise


def test_token_cancel_is_idempotent() -> None:
    t = CancellationToken()
    assert t.cancel("first") is True
    assert t.cancel("second") is False
    assert t.is_cancelled() is True
    # Reason from first call wins; second call does not overwrite.
    assert t.reason == "first"


def test_raise_if_cancelled_reports_reason() -> None:
    t = CancellationToken()
    t.cancel("user pressed stop")
    with pytest.raises(CancelledByToken) as info:
        t.raise_if_cancelled()
    assert info.value.reason == "user pressed stop"
    assert "user pressed stop" in str(info.value)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def test_callbacks_fire_in_registration_order_on_cancel() -> None:
    t = CancellationToken()
    log: list[int] = []
    for i in range(3):
        t.add_callback(lambda i=i: log.append(i))
    t.cancel("done")
    assert log == [0, 1, 2]


def test_callbacks_fire_only_once() -> None:
    t = CancellationToken()
    counter = {"n": 0}
    t.add_callback(lambda: counter.update(n=counter["n"] + 1))
    t.cancel()
    t.cancel()  # idempotent
    assert counter["n"] == 1


def test_late_callbacks_fire_eagerly_when_token_already_cancelled() -> None:
    t = CancellationToken()
    t.cancel()
    fired = {"ok": False}
    t.add_callback(lambda: fired.update(ok=True))
    assert fired["ok"] is True


def test_buggy_callback_does_not_block_others() -> None:
    t = CancellationToken()
    log: list[int] = []

    def bad() -> None:
        raise RuntimeError("boom")

    t.add_callback(lambda: log.append(1))
    t.add_callback(bad)
    t.add_callback(lambda: log.append(2))
    t.cancel()
    assert log == [1, 2]
    assert any(isinstance(e, RuntimeError) for e in t.callback_errors)


# ---------------------------------------------------------------------------
# asyncio integration
# ---------------------------------------------------------------------------


async def test_link_future_cancels_future() -> None:
    t = CancellationToken()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[int] = loop.create_future()
    t.link_future(fut)
    assert fut.done() is False
    t.cancel()
    # Cancellation is immediate via the callback.
    assert fut.cancelled() is True


async def test_link_task_cancels_task() -> None:
    async def long_op() -> None:
        await asyncio.sleep(10)

    t = CancellationToken()
    task = asyncio.create_task(long_op())
    t.link_task(task)
    await asyncio.sleep(0.01)
    assert task.done() is False
    t.cancel("stop")
    # Yield once so the task observes its cancellation.
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_wait_cancelled_returns_promptly_after_cancel() -> None:
    t = CancellationToken()

    async def cancel_after_delay() -> None:
        await asyncio.sleep(0.05)
        t.cancel("late")

    asyncio.create_task(cancel_after_delay())
    await asyncio.wait_for(t.wait_cancelled(poll_interval=0.01), timeout=1.0)
    assert t.reason == "late"


# ---------------------------------------------------------------------------
# race() helper
# ---------------------------------------------------------------------------


async def test_race_returns_operation_result_when_no_cancel() -> None:
    async def op() -> str:
        await asyncio.sleep(0.01)
        return "ok"

    t = CancellationToken()
    out = await CancellationToken.race(op(), t)
    assert out == "ok"


async def test_race_raises_cancelled_by_token_with_on_cancel_hook() -> None:
    fired = {"saved": False}

    async def op() -> None:
        await asyncio.sleep(10)

    t = CancellationToken()

    async def cancel_after() -> None:
        await asyncio.sleep(0.02)
        t.cancel("test")

    asyncio.create_task(cancel_after())

    def save_state() -> None:
        fired["saved"] = True

    with pytest.raises(CancelledByToken) as info:
        await CancellationToken.race(op(), t, on_cancel=save_state)
    assert info.value.reason == "test"
    assert fired["saved"] is True


async def test_race_propagates_unrelated_cancelled_error() -> None:
    """When asyncio.CancelledError is raised but the token is *not*
    cancelled, ``race`` must let the CancelledError propagate. This
    keeps test runners and external task cancellations working as
    expected, distinct from cooperative cancel.
    """

    async def op() -> None:
        # The body raises CancelledError directly (e.g. parent cancelled
        # the runner); the token stays clean.
        raise asyncio.CancelledError()

    t = CancellationToken()
    with pytest.raises(asyncio.CancelledError):
        await CancellationToken.race(op(), t)
    assert t.is_cancelled() is False
