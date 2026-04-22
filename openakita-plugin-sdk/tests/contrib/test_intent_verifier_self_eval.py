"""Tests for IntentVerifier.self_eval_loop (C0.6).

Modelled on the refs/video-use ``SKILL.md:84-93`` "ship → re-read brief
→ list gaps" protocol.  Pins the fail-safe semantics: any verifier
hiccup must surface as ``passed=False`` with a low-confidence note,
never as a silent green light or an unhandled exception.
"""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import EvalResult, IntentVerifier


# ── no-LLM fallback ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_eval_without_llm_returns_low_confidence_fail() -> None:
    """When no ``llm_call`` is wired, the verifier must skip cleanly —
    NEVER claim ``passed=True`` since no second model actually checked."""
    v = IntentVerifier()  # no llm_call
    res = await v.self_eval_loop(
        original_brief="A 30s corgi reel",
        produced_output="A 30s corgi reel was produced.",
    )
    assert isinstance(res, EvalResult)
    assert res.passed is False
    assert res.confidence == "low"
    assert "skipped" in res.raw.lower()


# ── happy path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_eval_parses_clean_pass_json() -> None:
    """Verifier returns clean JSON saying the output matches the brief —
    we expect ``passed=True`` and no gaps."""
    async def fake_llm(messages, **_):
        return '{"passed": true, "gaps": [], "suggestions": [], "confidence": "high"}'

    v = IntentVerifier(llm_call=fake_llm)
    res = await v.self_eval_loop(
        original_brief="A 30s reel of a corgi running on the beach",
        produced_output="Produced a 30s mp4 of a corgi running on a sandy beach.",
    )
    assert res.passed is True
    assert res.gaps == []
    assert res.confidence == "high"


@pytest.mark.asyncio
async def test_self_eval_passed_is_false_when_gaps_present() -> None:
    """Even if the verifier *says* passed=True, the presence of gaps
    must drag the verdict to fail — same guard as D2.10's badge logic
    (yellow trumps green)."""
    async def fake_llm(messages, **_):
        return (
            '{"passed": true, "gaps": ["缺少结尾镜头"], '
            '"suggestions": ["补一帧海浪退去的画面"], "confidence": "medium"}'
        )

    v = IntentVerifier(llm_call=fake_llm)
    res = await v.self_eval_loop(
        original_brief="brief",
        produced_output="output",
    )
    assert res.passed is False  # gaps overrule the optimistic flag
    assert res.gaps == ["缺少结尾镜头"]
    assert res.suggestions == ["补一帧海浪退去的画面"]


@pytest.mark.asyncio
async def test_self_eval_caps_gaps_at_5() -> None:
    """UI never paginates the gap list — cap at 5 to keep the toast
    readable."""
    import json as _json
    payload = _json.dumps({
        "passed": False,
        "gaps": [f"gap-{i}" for i in range(20)],
        "suggestions": [],
        "confidence": "low",
    })
    async def fake_llm(messages, **_):
        return payload

    v = IntentVerifier(llm_call=fake_llm)
    res = await v.self_eval_loop(
        original_brief="brief",
        produced_output="output",
    )
    assert len(res.gaps) == 5


@pytest.mark.asyncio
async def test_self_eval_handles_code_fenced_json() -> None:
    """Some models love ```json fences — the parser must strip them."""
    async def fake_llm(messages, **_):
        return (
            "Here is my verdict:\n```json\n"
            '{"passed": false, "gaps": ["字幕缺失"], '
            '"suggestions": ["跑一遍字幕生成"], "confidence": "high"}\n'
            "```\n"
        )

    v = IntentVerifier(llm_call=fake_llm)
    res = await v.self_eval_loop(
        original_brief="brief with subtitles",
        produced_output="video.mp4",
    )
    assert res.gaps == ["字幕缺失"]
    assert res.passed is False


# ── failure paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_eval_llm_exception_returns_low_confidence_fail() -> None:
    """The verifier must NEVER let an LLM hiccup propagate — that would
    crash a host pipeline that just wanted a "good enough?" check."""
    async def boom(messages, **_):
        raise RuntimeError("brain disconnected")

    v = IntentVerifier(llm_call=boom)
    res = await v.self_eval_loop(
        original_brief="brief",
        produced_output="output",
    )
    assert res.passed is False
    assert res.confidence == "low"
    assert any("RuntimeError" in g for g in res.gaps)


@pytest.mark.asyncio
async def test_self_eval_non_json_response_returns_low_confidence_fail() -> None:
    """Verifier returned prose — fail-safe."""
    async def chat(messages, **_):
        return "Looks fine to me, ship it!"

    v = IntentVerifier(llm_call=chat)
    res = await v.self_eval_loop(
        original_brief="brief",
        produced_output="output",
    )
    assert res.passed is False
    assert res.confidence == "low"


# ── plumbing ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_eval_passes_brief_and_output_to_llm() -> None:
    """Sanity — both segments must reach the verifier or the check is
    meaningless."""
    captured: list[list[dict]] = []

    async def capture(messages, **_):
        captured.append(messages)
        return '{"passed": true, "gaps": [], "suggestions": [], "confidence": "high"}'

    v = IntentVerifier(llm_call=capture)
    await v.self_eval_loop(
        original_brief="A 30s reel",
        produced_output="reel.mp4 (28.4s)",
    )
    user_msg = captured[0][-1]["content"]
    assert "30s reel" in user_msg
    assert "reel.mp4" in user_msg


def test_eval_result_to_dict_shape() -> None:
    """The wire shape host UI consumes — pin it so future renames
    surface in tests, not in production."""
    r = EvalResult(passed=False, gaps=["g1"], suggestions=["s1"], confidence="medium")
    d = r.to_dict()
    assert d == {
        "passed": False,
        "gaps": ["g1"],
        "suggestions": ["s1"],
        "confidence": "medium",
    }
