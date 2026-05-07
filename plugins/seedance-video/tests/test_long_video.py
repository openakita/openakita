"""Smoke tests for ``long_video.py`` — proves the inlined helpers
(``parse_llm_json_object``, ``run_parallel``, vendored under
``seedance_inline``) are wired up correctly without hitting the network or
ffmpeg.

The cost-tracking demo bookkeeping (``CostTracker.reserve / reconcile /
refund``) was removed in 0.7.0 along with the rest of the SDK ``contrib``
retraction; this file keeps the parse-correctness, chain-shape, and
"never silently drop a shot" (N1.1) invariants but no longer asserts on
budget ledgers.

We mock the ``brain`` (LLM), ``ark_client`` and ``task_manager`` so the
test suite stays hermetic and fast.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from _plugin_loader import load_seedance_plugin
from long_video import (
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
    The vendored ``parse_llm_json_object`` strips ``\u0060\u0060\u0060json`` fences."""
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


# ── ChainGenerator + run_parallel (B4/N1.1) ───────────────────────────


def _seg(idx: int, prompt: str = "p") -> dict:
    return {"index": idx, "duration": 5, "prompt": prompt}


def _make_chain_gen(
    *,
    create_task_returns: Any = None,
    create_task_raises: Exception | None = None,
) -> tuple[ChainGenerator, AsyncMock, AsyncMock]:
    """Build a ChainGenerator wired to AsyncMock ark_client and task_manager."""
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
async def test_chain_serial_emits_one_task_per_segment() -> None:
    """Three segments → three Ark calls → three task rows; no segment is
    silently dropped on the happy path."""
    cg, ark, tm = _make_chain_gen()
    out = await cg.generate_chain(
        segments=[_seg(1), _seg(2), _seg(3)],
        model_id="doubao-seedance-2-0-260128",
        mode="serial",
    )
    assert len(out) == 3
    assert ark.create_task.await_count == 3


@pytest.mark.asyncio
async def test_chain_serial_records_error_and_stops_on_failure() -> None:
    """A non-image-rejection failure must surface as an ``error`` row and
    halt the chain (no half-built downstream)."""
    cg, ark, tm = _make_chain_gen(
        create_task_raises=RuntimeError("network boom"),
    )
    out = await cg.generate_chain(
        segments=[_seg(1)],
        model_id="m",
        mode="serial",
    )
    assert len(out) == 1
    assert out[0]["error"]


@pytest.mark.asyncio
async def test_chain_parallel_no_silent_skip_on_failure() -> None:
    """N1.1 invariant: ``run_parallel`` must surface every input — even
    when the per-segment submit raises — so we never silently drop a
    shot.  Here ark.create_task always raises, so we expect 2 error
    entries (one per segment), not 0.

    Also validates the ``ParallelResult.ok`` API path (the wrong attribute
    name ``.success`` would have raised ``AttributeError`` and crashed
    the whole chain)."""
    cg, ark, tm = _make_chain_gen(
        create_task_raises=RuntimeError("ark down"),
    )
    out = await cg.generate_chain(
        segments=[_seg(1), _seg(2)],
        model_id="m",
        mode="parallel",
        max_parallel=2,
    )
    assert len(out) == 2
    assert all("error" in r for r in out)


@pytest.mark.asyncio
async def test_chain_parallel_returns_one_row_per_segment() -> None:
    """Happy path parity for the parallel branch: 4 segments → 4 task rows."""
    cg, ark, tm = _make_chain_gen()
    out = await cg.generate_chain(
        segments=[_seg(i) for i in range(1, 5)],
        model_id="m",
        mode="parallel",
        max_parallel=4,
    )
    assert len(out) == 4
    assert all("error" not in r for r in out)


@pytest.mark.asyncio
async def test_chain_parallel_max_parallel_respected_lower_bound() -> None:
    """``max(1, max_parallel)`` clamps non-positive values so a caller
    passing 0 still makes progress instead of deadlocking on a
    zero-permit semaphore."""
    cg, ark, tm = _make_chain_gen()
    out = await cg.generate_chain(
        segments=[_seg(1)],
        model_id="m",
        mode="parallel",
        max_parallel=0,
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
    await cg.generate_chain(
        segments=[_seg(1), _seg(2)],
        model_id="m",
        mode="serial",
    )
    second_call = ark.create_task.await_args_list[1]
    content = second_call.kwargs["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "https://cdn/last.png"
    assert image_parts[0]["role"] == "first_frame"


# ── chain_group plumbing (Sprint 8 / V2) ──────────────────────────────


@pytest.mark.asyncio
async def test_chain_serial_writes_chain_group_into_params() -> None:
    """Every per-segment task row must carry params.chain_group so
    ``GET /long-video/tasks/<group_id>`` can recover the chain after a
    page refresh — this is the core fix for "分镜出现两次/生成两次"."""
    cg, ark, tm = _make_chain_gen()
    out = await cg.generate_chain(
        segments=[_seg(1), _seg(2)],
        model_id="m",
        mode="serial",
        chain_group="abcd1234",
    )
    assert len(out) == 2
    # Inspect the kwargs every create_task call received: params should
    # always include chain_group + segment_index.
    calls = tm.create_task.await_args_list
    assert len(calls) == 2
    for call in calls:
        params = call.kwargs.get("params") or {}
        assert params.get("chain_group") == "abcd1234"
        assert "segment_index" in params
        assert params.get("chain_mode") == "serial"


@pytest.mark.asyncio
async def test_chain_parallel_writes_chain_group_into_params() -> None:
    """Same invariant for the parallel branch."""
    cg, ark, tm = _make_chain_gen()
    await cg.generate_chain(
        segments=[_seg(1), _seg(2), _seg(3)],
        model_id="m",
        mode="parallel",
        max_parallel=2,
        chain_group="grp-xyz",
    )
    calls = tm.create_task.await_args_list
    assert len(calls) == 3
    for call in calls:
        params = call.kwargs.get("params") or {}
        assert params.get("chain_group") == "grp-xyz"
        assert params.get("chain_mode") == "parallel"


@pytest.mark.asyncio
async def test_chain_without_group_keeps_params_empty() -> None:
    """When the caller omits chain_group (e.g. legacy callers), we
    must NOT pollute params with stray keys."""
    cg, ark, tm = _make_chain_gen()
    await cg.generate_chain(
        segments=[_seg(1)],
        model_id="m",
        mode="serial",
    )
    params = tm.create_task.await_args_list[0].kwargs.get("params") or {}
    assert params == {}


# ── chain_signature dedupe (Sprint 8 / V2) ────────────────────────────


def test_chain_signature_is_stable_for_identical_storyboards() -> None:
    """Same prompts in the same order should hash the same — that's
    what makes the "duplicate submission" guard in /long-video/generate
    refuse a second click."""
    Plugin = load_seedance_plugin().Plugin
    s = [
        {"index": 1, "duration": 5, "prompt": "深夜，年轻人躺在床上"},
        {"index": 2, "duration": 5, "prompt": "傍晚，年轻人坐在书桌前"},
    ]
    a = Plugin._chain_signature(s)
    b = Plugin._chain_signature(s)
    assert a == b
    assert isinstance(a, str) and len(a) == 40  # sha1 hex


def test_chain_signature_differs_when_prompts_change() -> None:
    """Reordering or rewording must change the signature so the
    dedupe guard does not block legitimate re-submissions of an
    EDITED storyboard."""
    Plugin = load_seedance_plugin().Plugin
    s1 = [{"index": 1, "duration": 5, "prompt": "A"}]
    s2 = [{"index": 1, "duration": 5, "prompt": "B"}]
    s3 = [{"index": 1, "duration": 6, "prompt": "A"}]  # duration change
    s4 = [
        {"index": 1, "duration": 5, "prompt": "A"},
        {"index": 2, "duration": 5, "prompt": "C"},
    ]
    sigs = {
        Plugin._chain_signature(s1),
        Plugin._chain_signature(s2),
        Plugin._chain_signature(s3),
        Plugin._chain_signature(s4),
    }
    # Four different storyboards must yield four different signatures.
    assert len(sigs) == 4


# ── ffmpeg availability probe (no binary required) ────────────────────


def test_ffmpeg_available_returns_bool() -> None:
    """Smoke: never raises, regardless of whether ffmpeg is installed."""
    result = ffmpeg_available()
    assert isinstance(result, bool)
