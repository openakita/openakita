"""Unit tests for ``subtitle_hook_picker`` (mode v1.1).

Covers each of the 5 helper functions ported from CutClaw plus the main
``select_hook_dialogue`` orchestrator across 5 mock LLM scenarios.

Red-line guarantees verified:

- ``min_score=0.55`` fuzzy threshold respected (no fallback below that)
- 3 windows × 2 attempts = 6 max LLM calls without random fallbacks
- ``HookSelectionError`` carries telemetry that lists every rejection
- The picker never raises a transport exception — LLM failures are
  caught and recorded as ``llm_exception`` rejections, then retried.
"""

from __future__ import annotations

import json

import pytest
from subtitle_hook_picker import (
    HOOK_DIALOGUE_MAX_SUBTITLE_CHARS,
    SELECT_HOOK_DIALOGUE_PROMPT,
    HookSelectionError,
    _build_timed_lines,
    _dialogue_similarity,
    _format_subtitles_for_prompt,
    _match_dialogue_lines_to_subtitles,
    _normalize_dialogue_text,
    select_hook_dialogue,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _mk_subs(*lines: tuple[float, float, str, str | None]) -> list[dict]:
    """Build subtitle dicts: ``(start_sec, end_sec, text, speaker_or_None)``."""
    out = []
    for start, end, text, speaker in lines:
        sub = {"start_sec": start, "end_sec": end, "text": text}
        if speaker:
            sub["speaker"] = speaker
        out.append(sub)
    return out


@pytest.fixture
def short_subs() -> list[dict]:
    """A 30-cue × 2-min synthetic dialogue for the orchestrator tests."""
    out = []
    for i in range(30):
        # Every cue is ~4s long, so a 3-cue hook is ~12s (within default band)
        out.append(
            {
                "start_sec": i * 4.0,
                "end_sec": i * 4.0 + 3.5,
                "text": f"Line number {i} speaks important things to remember.",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Helper 1 · _normalize_dialogue_text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("Hello, World!", "hello world"),
        ("[Speaker] hi there", "hi there"),
        ("<i>italic</i> text", "italic text"),
        ("multiple   spaces\t\nhere", "multiple spaces here"),
        ("MIXED Case-PUNCT.123!", "mixed case punct 123"),
    ],
)
def test_normalize_dialogue_text(raw: str, expected: str) -> None:
    assert _normalize_dialogue_text(raw) == expected


# ---------------------------------------------------------------------------
# Helper 2 · _dialogue_similarity
# ---------------------------------------------------------------------------


def test_similarity_exact_match() -> None:
    assert _dialogue_similarity("hello world", "hello world") == 1.0


def test_similarity_substring_pegs_to_0_9() -> None:
    score = _dialogue_similarity("hello", "hello world how are you today")
    assert score >= 0.9


def test_similarity_completely_different() -> None:
    # Totally different strings (no shared tokens, no shared char runs).
    assert _dialogue_similarity("apples bananas mango", "xyzzy quark plover") < 0.5


def test_similarity_token_overlap_blend() -> None:
    # Same tokens but reordered: jaccard is high, seq_score modest.
    score = _dialogue_similarity("alpha beta gamma", "gamma alpha beta")
    assert score >= 0.55


def test_similarity_empty_returns_zero() -> None:
    assert _dialogue_similarity("", "anything") == 0.0
    assert _dialogue_similarity("anything", "") == 0.0


# ---------------------------------------------------------------------------
# Helper 3 · _match_dialogue_lines_to_subtitles
# ---------------------------------------------------------------------------


def test_match_all_lines_hit() -> None:
    subs = _mk_subs(
        (0.0, 2.0, "I trusted you with everything.", None),
        (2.0, 4.0, "And you betrayed me.", None),
        (4.0, 6.0, "There is no coming back.", None),
    )
    matched = _match_dialogue_lines_to_subtitles(
        ["I trusted you with everything", "And you betrayed me"],
        subs,
    )
    assert len(matched) == 2
    assert matched[0]["text"].startswith("I trusted")


def test_match_below_threshold_returns_empty() -> None:
    subs = _mk_subs((0.0, 2.0, "Hello there", None))
    matched = _match_dialogue_lines_to_subtitles(
        ["Lorem ipsum dolor sit amet consectetur"],
        subs,
        min_score=0.55,
    )
    assert matched == []


def test_match_empty_inputs() -> None:
    assert _match_dialogue_lines_to_subtitles([], _mk_subs((0, 1, "x", None))) == []
    assert _match_dialogue_lines_to_subtitles(["anything"], []) == []


def test_match_monotonic_forward_only() -> None:
    """Each subsequent line must match a *later* cue (no backtrack)."""
    subs = _mk_subs(
        (0.0, 1.0, "alpha beta", None),
        (1.0, 2.0, "gamma delta", None),
        (2.0, 3.0, "alpha beta", None),
    )
    matched = _match_dialogue_lines_to_subtitles(
        ["alpha beta", "alpha beta"],
        subs,
    )
    # The 2nd "alpha beta" must come from cue idx 2, never idx 0 again.
    assert len(matched) == 2
    assert matched[1]["start_sec"] == 2.0


# ---------------------------------------------------------------------------
# Helper 4 · _build_timed_lines
# ---------------------------------------------------------------------------


def test_build_timed_lines_relative_zero_start() -> None:
    matched = _mk_subs(
        (10.0, 12.5, "first", None),
        (12.5, 15.0, "second", None),
    )
    timed = _build_timed_lines(matched, clip_start_sec=10.0)
    assert timed[0]["start"] == "00:00:00,000"
    assert timed[0]["end"] == "00:00:02,500"
    assert timed[1]["start"] == "00:00:02,500"
    assert timed[1]["end"] == "00:00:05,000"
    assert timed[0]["source_start"] == "00:00:10,000"


def test_build_timed_lines_with_speaker_label() -> None:
    matched = _mk_subs((5.0, 7.0, "hello", "Alice"))
    timed = _build_timed_lines(matched, clip_start_sec=5.0)
    assert timed[0]["text"] == "[Alice] hello"


# ---------------------------------------------------------------------------
# Helper 5 · _format_subtitles_for_prompt
# ---------------------------------------------------------------------------


def test_format_tail_window_chronological_output() -> None:
    subs = _mk_subs(
        (0.0, 1.0, "first", None),
        (1.0, 2.0, "middle", None),
        (2.0, 3.0, "last", None),
    )
    block, n = _format_subtitles_for_prompt(subs, window_mode="tail")
    assert n == 3
    assert block.index("first") < block.index("middle") < block.index("last")


def test_format_head_window_starts_from_first() -> None:
    subs = _mk_subs(
        (0.0, 1.0, "first", None),
        (1.0, 2.0, "second", None),
    )
    block, _ = _format_subtitles_for_prompt(subs, window_mode="head")
    assert "first" in block and "second" in block


def test_format_random_window_starts_from_index() -> None:
    subs = _mk_subs(
        (0.0, 1.0, "alpha", None),
        (1.0, 2.0, "beta", None),
        (2.0, 3.0, "gamma", None),
    )
    block, _ = _format_subtitles_for_prompt(subs, window_mode="random_window", start_index=1)
    assert "alpha" not in block
    assert "beta" in block and "gamma" in block


def test_format_respects_max_chars_cap() -> None:
    # Many tiny cues → cap should kick in well before all are emitted.
    big_subs = _mk_subs(*[(i * 1.0, i * 1.0 + 0.5, "x" * 200, None) for i in range(2000)])
    block, n = _format_subtitles_for_prompt(big_subs, max_chars=5000, window_mode="head")
    assert len(block) <= 5000 + 250  # +1 block worth of slack
    assert n < len(big_subs)


def test_format_empty_input() -> None:
    block, n = _format_subtitles_for_prompt([])
    assert block == ""
    assert n == 0


def test_max_chars_constant_value() -> None:
    """Plan §2.1 fixed window: 24K (CutClaw 20K + headroom)."""
    assert HOOK_DIALOGUE_MAX_SUBTITLE_CHARS == 24000


# ---------------------------------------------------------------------------
# select_hook_dialogue · 5 mock-LLM scenarios
# ---------------------------------------------------------------------------


def _success_payload(lines: list[str], reason: str = "Strong dramatic line.") -> str:
    return json.dumps(
        {
            "lines": lines,
            "start": "00:00:00,000",
            "end": "00:00:12,000",
            "reason": reason,
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_tail_succeeds_first_attempt(short_subs: list[dict]) -> None:
    expected_lines = [s["text"] for s in short_subs[27:30]]
    call_log: list[str] = []

    async def llm(messages, model, kwargs):
        call_log.append(model)
        return _success_payload(expected_lines)

    hook = await select_hook_dialogue(
        subtitles=short_subs,
        instruction="dramatic ending",
        main_character="Alice",
        target_duration_sec=12.0,
        prompt_window_mode="tail_then_head",
        random_window_attempts=3,
        model="qwen-plus",
        llm_caller=llm,
    )
    assert hook["selected_window"] == "tail"
    assert hook["selected_attempt"] == 1
    assert len(call_log) == 1
    assert hook["selection_method"] == "llm_srt_matched"


@pytest.mark.asyncio
async def test_tail_fails_head_succeeds(short_subs: list[dict]) -> None:
    expected_lines = [s["text"] for s in short_subs[0:3]]
    state = {"calls": 0}

    async def llm(messages, model, kwargs):
        state["calls"] += 1
        # Tail attempts 1 + 2 return junk; head attempt 1 succeeds.
        if state["calls"] <= 2:
            return "I am unable to produce JSON :("
        return _success_payload(expected_lines)

    hook = await select_hook_dialogue(
        subtitles=short_subs,
        instruction="strong opening",
        main_character=None,
        target_duration_sec=12.0,
        prompt_window_mode="tail_then_head",
        random_window_attempts=2,
        model="qwen-plus",
        llm_caller=llm,
    )
    assert hook["selected_window"] == "head"
    # Two tail rejections recorded
    rejected_windows = [r["window"] for r in hook["_telemetry"]["rejected_attempts"]]
    assert rejected_windows.count("tail") == 2


@pytest.mark.asyncio
async def test_random_window_fallback(short_subs: list[dict]) -> None:
    expected = [s["text"] for s in short_subs[10:13]]
    state = {"calls": 0}

    async def llm(messages, model, kwargs):
        state["calls"] += 1
        # First 4 calls (tail × 2 + head × 2) all fail.
        if state["calls"] <= 4:
            return None
        return _success_payload(expected)

    hook = await select_hook_dialogue(
        subtitles=short_subs,
        instruction="middle hook",
        main_character=None,
        target_duration_sec=12.0,
        prompt_window_mode="tail_then_head",
        random_window_attempts=3,
        model="qwen-plus",
        llm_caller=llm,
    )
    assert hook["selected_window"] == "random_window"


@pytest.mark.asyncio
async def test_all_windows_fail_raises(short_subs: list[dict]) -> None:
    state = {"calls": 0}

    async def llm(messages, model, kwargs):
        state["calls"] += 1
        return "definitely not json"

    with pytest.raises(HookSelectionError) as excinfo:
        await select_hook_dialogue(
            subtitles=short_subs,
            instruction="x",
            main_character=None,
            target_duration_sec=12.0,
            prompt_window_mode="tail_then_head",
            random_window_attempts=2,
            model="qwen-plus",
            llm_caller=llm,
        )
    # tail(2) + head(2) + 2×random(2) = 8 LLM calls
    assert state["calls"] == 8
    assert excinfo.value.telemetry["llm_calls"] == 8
    assert len(excinfo.value.telemetry["rejected_attempts"]) == 8


@pytest.mark.asyncio
async def test_duration_out_of_range_rejected(short_subs: list[dict]) -> None:
    """Hook spans only 1 cue (~3.5s) — must fail the [7, 17]s band."""
    too_short_lines = [short_subs[0]["text"]]
    expected = [s["text"] for s in short_subs[5:8]]  # ~12s, in-range
    state = {"calls": 0}

    async def llm(messages, model, kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return _success_payload(too_short_lines)  # rejected: too short
        return _success_payload(expected)  # accepted

    hook = await select_hook_dialogue(
        subtitles=short_subs,
        instruction="x",
        main_character=None,
        target_duration_sec=12.0,
        prompt_window_mode="tail_then_head",
        random_window_attempts=2,
        model="qwen-plus",
        llm_caller=llm,
    )
    assert "duration_out_of_range" in "".join(
        str(r) for r in hook["_telemetry"]["rejected_attempts"]
    )
    assert hook["selected_attempt"] == 2
    assert 7.0 <= hook["duration_seconds"] <= 17.0


@pytest.mark.asyncio
async def test_llm_exception_is_caught_not_propagated(short_subs: list[dict]) -> None:
    """Transport-level exceptions should be recorded as window rejection."""
    state = {"calls": 0}
    expected = [s["text"] for s in short_subs[0:3]]

    async def llm(messages, model, kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated network blip")
        return _success_payload(expected)

    hook = await select_hook_dialogue(
        subtitles=short_subs,
        instruction="x",
        main_character=None,
        target_duration_sec=12.0,
        prompt_window_mode="tail_then_head",
        random_window_attempts=1,
        model="qwen-plus",
        llm_caller=llm,
    )
    rejected_reasons = [r.get("reason", "") for r in hook["_telemetry"]["rejected_attempts"]]
    assert any("llm_exception" in r for r in rejected_reasons)


def test_prompt_template_has_required_placeholders() -> None:
    """Red line: don't drift from the CutClaw battle-tested wording."""
    for placeholder in (
        "{main_character}",
        "{min_duration:.1f}",
        "{max_duration:.1f}",
        "{instruction}",
        "{shot_plan_summary}",
        "{window_mode}",
        "{subtitles_block}",
    ):
        assert placeholder in SELECT_HOOK_DIALOGUE_PROMPT
