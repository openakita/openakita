"""Tests for openakita_plugin_sdk.contrib.parallel_executor."""

from __future__ import annotations

import asyncio

import pytest
from openakita_plugin_sdk.contrib import (
    ParallelResult,
    ParallelSummary,
    run_parallel,
    summarize_parallel,
)

# ── happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_input_returns_empty_list() -> None:
    async def runner(x):
        return x
    out = await run_parallel([], runner)
    assert out == []


@pytest.mark.asyncio
async def test_basic_success_preserves_order() -> None:
    async def runner(x):
        await asyncio.sleep(0)
        return x * 2
    out = await run_parallel([1, 2, 3, 4], runner, max_concurrency=2)
    assert [r.index for r in out] == [0, 1, 2, 3]
    assert [r.value for r in out] == [2, 4, 6, 8]
    assert all(r.ok for r in out)
    assert all(r.error is None for r in out)


@pytest.mark.asyncio
async def test_result_records_elapsed_time() -> None:
    async def runner(x):
        await asyncio.sleep(0.01)
        return x
    out = await run_parallel([1], runner)
    assert out[0].elapsed_sec >= 0.005


# ── failures don't kill siblings ───────────────────────────────────


@pytest.mark.asyncio
async def test_failures_do_not_skip_silently() -> None:
    async def runner(x):
        if x == 2:
            raise ValueError("boom")
        return x
    out = await run_parallel([1, 2, 3], runner)
    assert len(out) == 3
    assert out[0].ok and out[0].value == 1
    assert out[1].failed
    assert isinstance(out[1].error, ValueError)
    assert out[2].ok and out[2].value == 3


@pytest.mark.asyncio
async def test_all_failures_still_one_result_per_input() -> None:
    async def runner(_x):
        raise RuntimeError("nope")
    out = await run_parallel([0, 1, 2, 3, 4], runner)
    assert len(out) == 5
    assert all(r.failed for r in out)


# ── max_concurrency enforcement ────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_is_bounded() -> None:
    in_flight = {"n": 0, "peak": 0}

    async def runner(_x):
        in_flight["n"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["n"])
        await asyncio.sleep(0.01)
        in_flight["n"] -= 1

    await run_parallel(range(20), runner, max_concurrency=3)
    assert in_flight["peak"] <= 3


@pytest.mark.asyncio
async def test_concurrency_one_serialises() -> None:
    order: list[int] = []

    async def runner(x):
        order.append(("start", x))
        await asyncio.sleep(0.005)
        order.append(("end", x))
        return x

    await run_parallel([1, 2, 3], runner, max_concurrency=1)
    # Strict serialisation: every "start" must be followed by its "end"
    # before the next "start" appears.
    for i in range(0, len(order), 2):
        assert order[i][0] == "start"
        assert order[i + 1][0] == "end"
        assert order[i][1] == order[i + 1][1]


@pytest.mark.asyncio
async def test_invalid_concurrency_rejected() -> None:
    async def runner(x):
        return x
    with pytest.raises(ValueError, match="max_concurrency"):
        await run_parallel([1], runner, max_concurrency=0)


# ── progress callback ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_called_per_item() -> None:
    seen: list[tuple[int, int]] = []

    def on_progress(done, total):
        seen.append((done, total))

    async def runner(x):
        return x

    await run_parallel([1, 2, 3], runner, on_progress=on_progress)
    assert len(seen) == 3
    assert seen[-1] == (3, 3)
    assert all(t == 3 for _d, t in seen)
    assert sorted(d for d, _t in seen) == [1, 2, 3]


@pytest.mark.asyncio
async def test_broken_progress_callback_does_not_break_worker() -> None:
    def boom(_done, _total):
        raise RuntimeError("UI exploded")

    async def runner(x):
        return x

    out = await run_parallel([1, 2, 3], runner, on_progress=boom)
    assert all(r.ok for r in out)


# ── fail_fast ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_fast_raises_first_error() -> None:
    async def runner(x):
        if x == 2:
            raise KeyError("oops")
        await asyncio.sleep(0.01)
        return x

    with pytest.raises(KeyError):
        await run_parallel(
            [1, 2, 3, 4, 5], runner, max_concurrency=1, fail_fast=True,
        )


@pytest.mark.asyncio
async def test_fail_fast_attaches_partial_results() -> None:
    async def runner(x):
        if x == 1:
            raise ValueError("bad")
        await asyncio.sleep(0.01)
        return x

    try:
        await run_parallel(
            [0, 1, 2, 3], runner, max_concurrency=1, fail_fast=True,
        )
    except ValueError as e:
        partials = getattr(e, "partial_results", None)
        assert partials is not None
        assert len(partials) == 4
        # At least the first item ran to success before the failure.
        assert partials[0].ok
        # The failed item is reported.
        assert partials[1].failed


@pytest.mark.asyncio
async def test_fail_fast_off_collects_all() -> None:
    async def runner(x):
        if x % 2 == 0:
            raise RuntimeError("even nope")
        return x

    out = await run_parallel([0, 1, 2, 3], runner, max_concurrency=2)
    assert len(out) == 4
    assert sum(1 for r in out if r.failed) == 2
    assert sum(1 for r in out if r.ok) == 2


# ── outer cancellation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outer_cancel_propagates_and_fills_slots() -> None:
    async def runner(_x):
        await asyncio.sleep(1.0)
        return 42

    task = asyncio.create_task(
        run_parallel(range(10), runner, max_concurrency=2),
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── summarize ─────────────────────────────────────────────────────


def test_summarize_empty() -> None:
    s = summarize_parallel([])
    assert s.total == 0
    assert s.all_ok is True
    assert s.error_messages == []


def test_summarize_mixed() -> None:
    rows: list[ParallelResult] = [
        ParallelResult(index=0, item="a", status="ok", value=1, elapsed_sec=0.1),
        ParallelResult(
            index=1, item="b", status="failed",
            error=ValueError("x"), elapsed_sec=0.2,
        ),
        ParallelResult(
            index=2, item="c", status="failed",
            error=ValueError("y"), elapsed_sec=0.3,
        ),
        ParallelResult(index=3, item="d", status="cancelled", elapsed_sec=0.0),
    ]
    s = summarize_parallel(rows)
    assert s.total == 4
    assert s.ok == 1
    assert s.failed == 2
    assert s.cancelled == 1
    assert not s.all_ok
    assert s.total_elapsed_sec == pytest.approx(0.6)
    assert len(s.error_messages) == 2  # distinct only


def test_summarize_caps_error_messages_at_5() -> None:
    rows = [
        ParallelResult(
            index=i, item=i, status="failed",
            error=ValueError(f"err-{i}"),
        )
        for i in range(20)
    ]
    s = summarize_parallel(rows)
    assert len(s.error_messages) == 5


def test_summarize_dedupes_repeated_errors() -> None:
    rows = [
        ParallelResult(
            index=i, item=i, status="failed",
            error=ValueError("identical"),
        )
        for i in range(10)
    ]
    s = summarize_parallel(rows)
    assert len(s.error_messages) == 1


def test_parallel_summary_to_dict() -> None:
    s = ParallelSummary(
        total=2, ok=1, failed=1, cancelled=0,
        total_elapsed_sec=0.1234,
        error_messages=["ValueError('x')"],
    )
    raw = s.to_dict()
    assert raw["total"] == 2
    assert raw["all_ok"] is False
    assert raw["error_messages"] == ["ValueError('x')"]


def test_parallel_result_to_dict() -> None:
    r = ParallelResult(
        index=0, item="x", status="failed",
        error=ValueError("boom"), elapsed_sec=0.1,
    )
    d = r.to_dict()
    assert d["status"] == "failed"
    assert "ValueError" in d["error"]


# ── result helper properties ─────────────────────────────────────


def test_result_status_helpers() -> None:
    ok = ParallelResult(index=0, item="x", status="ok", value=1)
    fail = ParallelResult(
        index=1, item="x", status="failed", error=ValueError(),
    )
    cnc = ParallelResult(index=2, item="x", status="cancelled")
    assert ok.ok and not ok.failed and not ok.cancelled
    assert fail.failed and not fail.ok and not fail.cancelled
    assert cnc.cancelled and not cnc.ok and not cnc.failed
