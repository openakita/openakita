"""Sprint 7 smoke tests for ``long_video.py`` — proves the SDK
``contrib`` integrations (``parse_llm_json_object``, ``CostTracker``,
``run_parallel``) are wired up correctly without hitting the network or
ffmpeg.

We mock the ``brain`` (LLM), ``ark_client`` and ``task_manager`` so the
test suite stays hermetic and fast.  The goal is *behaviour*, not
coverage of every ffmpeg branch — those live behind real binaries.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita_plugin_sdk.contrib import CostTracker

from long_video import (
    DEFAULT_SEGMENT_COST_USD,
    ChainGenerator,
    decompose_storyboard,
    ffmpeg_available,
)


# ── decompose_storyboard / parse_llm_json_object (C5) ─────────────────


class _FakeBrainThink:
    """LLM stub exposing the ``think`` interface (preferred path)."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def think(self, *, prompt: str, system: str) -> dict:
        self.calls.append({"prompt": prompt, "system": system})
        return {"content": self._content}


class _FakeBrainChat:
    """LLM stub exposing the ``chat`` interface (fallback path)."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, *, messages: list[dict]) -> dict:
        return {"content": self._content}


@pytest.mark.asyncio
async def test_decompose_storyboard_parses_clean_json() -> None:
    payload = (
        '{"segments":[{"index":1,"duration":5,"prompt":"a"}],'
        '"style_prefix":"cinematic"}'
    )
    brain = _FakeBrainThink(payload)
    out = await decompose_storyboard(brain, story="story", total_duration=5)
    assert out["segments"][0]["index"] == 1
    assert out["style_prefix"] == "cinematic"
    assert "error" not in out


@pytest.mark.asyncio
async def test_decompose_storyboard_handles_fenced_json_block() -> None:
    """C5 — the brittle ``find('{')`` heuristic used to fail on this.
    SDK's ``parse_llm_json_object`` strips ``\u0060\u0060\u0060json`` fences."""
    payload = (
        "好的，下面是分镜：\n"
        "```json\n"
        '{"segments":[{"index":1,"duration":4,"prompt":"sunset"}]}\n'
        "```\n"
        "希望对你有帮助。"
    )
    brain = _FakeBrainThink(payload)
    out = await decompose_storyboard(brain, story="x", total_duration=4)
    assert "error" not in out
    assert out["segments"][0]["prompt"] == "sunset"


@pytest.mark.asyncio
async def test_decompose_storyboard_returns_error_on_garbage() -> None:
    """When the LLM returns non-JSON we return an ``error`` envelope and
    surface the raw text — never raise."""
    brain = _FakeBrainThink("just some prose, no json here")
    out = await decompose_storyboard(brain, story="x", total_duration=4)
    assert "error" in out
    assert "raw" in out
    assert out["raw"] == "just some prose, no json here"


@pytest.mark.asyncio
async def test_decompose_storyboard_uses_chat_fallback() -> None:
    payload = '{"segments":[{"index":1,"duration":3,"prompt":"b"}]}'
    brain = _FakeBrainChat(payload)
    out = await decompose_storyboard(brain, story="x", total_duration=3)
    assert "error" not in out
    assert out["segments"][0]["prompt"] == "b"


@pytest.mark.asyncio
async def test_decompose_storyboard_no_llm_returns_error() -> None:
    out = await decompose_storyboard(brain=object(), story="x")
    assert out == {"error": "No LLM available"}


# ── ChainGenerator + CostTracker (B5) + run_parallel (B4/N1.1) ────────


def _seg(idx: int, prompt: str = "p") -> dict:
    return {"index": idx, "duration": 5, "prompt": prompt}


def _make_chain_gen(
    *,
    create_task_returns: Any = None,
    create_task_raises: Exception | None = None,
) -> tuple[ChainGenerator, AsyncMock, AsyncMock]:
    """Build a ChainGenerator wired to AsyncMock ark_client and task_manager.

    The task_manager.create_task auto-assigns deterministic ids so the
    chain logic can index results.  ``_wait_for_task`` is short-circuited
    to return the same dict (skip polling).
    """
    ark = AsyncMock()
    if create_task_raises is not None:
        ark.create_task.side_effect = create_task_raises
    else:
        ark.create_task.return_value = create_task_returns or {
            "id": "ark-x",
            "last_frame_url": "https://frame/y.png",
        }

    tm = AsyncMock()
    counter = {"n": 0}

    async def _create(**kwargs: Any) -> dict:
        counter["n"] += 1
        return {"id": f"task-{counter['n']}", **kwargs}

    tm.create_task.side_effect = _create

    cg = ChainGenerator(ark, tm)
    cg._wait_for_task = AsyncMock(side_effect=lambda tid: {"id": tid, "status": "done"})
    return cg, ark, tm


@pytest.mark.asyncio
async def test_chain_serial_reserves_and_reconciles_per_segment() -> None:
    """B5 activation: each segment must reserve up front and reconcile on
    success.  After three segments the ledger should report 3 committed
    entries (reserved → committed) and zero outstanding reservations.
    """
    cg, ark, tm = _make_chain_gen()
    ct = CostTracker()
    out = await cg.generate_chain(
        segments=[_seg(1), _seg(2), _seg(3)],
        model_id="doubao-seedance-2-0-260128",
        mode="serial",
        cost_tracker=ct,
    )
    assert len(out) == 3
    assert ark.create_task.await_count == 3
    summary = ct.summary()
    expected_committed = 3 * DEFAULT_SEGMENT_COST_USD
    assert summary.committed == pytest.approx(expected_committed)
    assert summary.reserved == pytest.approx(0.0)
    assert summary.refunded == pytest.approx(0.0)
    assert summary.entry_count == 3


@pytest.mark.asyncio
async def test_chain_serial_refunds_on_failure() -> None:
    """B5 invariant: a non-image-rejection failure must refund the
    reservation (not double-charge or leak budget)."""
    cg, ark, tm = _make_chain_gen(
        create_task_raises=RuntimeError("network boom"),
    )
    ct = CostTracker()
    out = await cg.generate_chain(
        segments=[_seg(1)],
        model_id="m",
        mode="serial",
        cost_tracker=ct,
    )
    assert out[0]["error"]
    summary = ct.summary()
    assert summary.refunded == pytest.approx(DEFAULT_SEGMENT_COST_USD)
    assert summary.reserved == pytest.approx(0.0)
    assert summary.committed == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_chain_parallel_no_silent_skip_on_failure() -> None:
    """N1.1 invariant: ``run_parallel`` must surface every input — even
    when the per-segment submit raises — so we never silently drop a
    shot.  Here ark.create_task always raises, so we expect 2 error
    entries (one per segment), not 0.

    Also validates the ``ParallelResult.ok`` API path (the wrong attribute
    name ``.success`` would have raised ``AttributeError`` and crashed
    the whole chain — Sprint 7 caught this regression here)."""
    cg, ark, tm = _make_chain_gen(
        create_task_raises=RuntimeError("ark down"),
    )
    ct = CostTracker()
    out = await cg.generate_chain(
        segments=[_seg(1), _seg(2)],
        model_id="m",
        mode="parallel",
        cost_tracker=ct,
        max_parallel=2,
    )
    assert len(out) == 2
    assert all("error" in r for r in out)
    summary = ct.summary()
    assert summary.refunded == pytest.approx(2 * DEFAULT_SEGMENT_COST_USD)


@pytest.mark.asyncio
async def test_chain_parallel_reserves_default_cost_per_segment() -> None:
    """The reservation amount comes from ``DEFAULT_SEGMENT_COST_USD``;
    a 4-segment batch must therefore touch the ledger 4× and end up
    with 4× DEFAULT_SEGMENT_COST_USD committed (no leftover reservation,
    no refund)."""
    cg, ark, tm = _make_chain_gen()
    ct = CostTracker()
    await cg.generate_chain(
        segments=[_seg(i) for i in range(1, 5)],
        model_id="m",
        mode="parallel",
        cost_tracker=ct,
        max_parallel=4,
    )
    summary = ct.summary()
    assert summary.entry_count == 4
    assert summary.committed == pytest.approx(4 * DEFAULT_SEGMENT_COST_USD)
    assert summary.reserved == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_chain_parallel_max_parallel_respected_lower_bound() -> None:
    """``max(1, max_parallel)`` clamps non-positive values so a caller
    passing 0 still makes progress instead of deadlocking on a
    zero-permit semaphore."""
    cg, ark, tm = _make_chain_gen()
    ct = CostTracker()
    out = await cg.generate_chain(
        segments=[_seg(1)],
        model_id="m",
        mode="parallel",
        cost_tracker=ct,
        max_parallel=0,  # would deadlock without the max(1, ...) guard
    )
    assert len(out) == 1
    assert "error" not in out[0]


@pytest.mark.asyncio
async def test_chain_serial_chains_last_frame_into_next_first_frame() -> None:
    """Validates the storyboard "chain" contract: segment N+1's content
    must include an ``image_url`` carrying segment N's ``last_frame_url``."""
    cg, ark, tm = _make_chain_gen(create_task_returns={
        "id": "ark", "last_frame_url": "https://cdn/last.png",
    })
    cg._wait_for_task = AsyncMock(side_effect=lambda tid: {
        "id": tid, "status": "done", "last_frame_url": "https://cdn/last.png",
    })
    ct = CostTracker()
    await cg.generate_chain(
        segments=[_seg(1), _seg(2)],
        model_id="m",
        mode="serial",
        cost_tracker=ct,
    )
    second_call = ark.create_task.await_args_list[1]
    content = second_call.kwargs["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "https://cdn/last.png"


# ── ffmpeg availability probe (no binary required) ────────────────────


def test_ffmpeg_available_returns_bool() -> None:
    """Smoke: never raises, regardless of whether ffmpeg is installed."""
    result = ffmpeg_available()
    assert isinstance(result, bool)
