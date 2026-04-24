"""hook_picker mode core algorithm — ported 1:1 from CutClaw
``Screenwriter_scene_short.py`` (lines 576-723 + 800-960).

This module is **decoupled from the LLM transport**: callers must inject an
``llm_caller`` callable so that the algorithm can be unit-tested against
deterministic mocks.  In production ``subtitle_pipeline._do_hook_pick``
wires it to ``SubtitleAsrClient.call_qwen_plus``.

Red lines (do NOT change without re-validating against CutClaw):

1. ``SELECT_HOOK_DIALOGUE_PROMPT`` is copied **word-for-word** from the
   battle-tested CutClaw prompt — adjusting wording will measurably
   degrade Qwen-Plus selection quality.
2. The ``min_score=0.55`` fuzzy-match threshold and the per-window
   2-attempt LLM retry loop are CutClaw defaults.  Lowering them to
   "make tests pass" is a red-line violation.
3. Window strategy is fixed to ``tail → head → N × random_window``.
4. This module **must not import** ``subtitle_asr_client`` (decoupling
   contract — see plan §2.1).
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any

from subtitle_craft_inline.llm_json_parser import parse_llm_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types & constants
# ---------------------------------------------------------------------------

LLMCaller = Callable[
    [list[dict[str, str]], str, dict[str, Any]],
    Awaitable[str | None],
]
"""Async LLM callable signature: ``(messages, model, kwargs) -> raw_content``.

``kwargs`` carries provider-agnostic hints (``temperature``, ``max_tokens``,
``response_format_json``).  Returning ``None`` signals a transport-level
failure that the picker treats as a window rejection (and retries).
"""

HOOK_DIALOGUE_MAX_SUBTITLE_CHARS: int = 24000
"""Per-prompt subtitle window cap.

CutClaw's original 20K is bumped to 24K to leave headroom for typical
8-15s hooks selected from 60-90 minute episodes — Qwen-Plus 32K input
window comfortably absorbs the difference.
"""


class HookSelectionError(Exception):
    """Raised when ALL three windows × 2 attempts fail.

    The pipeline catches this and surfaces ``error_kind='unknown'`` to the
    UI ErrorPanel.  The full ``telemetry`` payload (rejected_attempts,
    window history, llm_calls counter) is preserved on the exception for
    metadata.json so users can debug / share.
    """

    def __init__(self, message: str, *, telemetry: dict[str, Any] | None = None):
        super().__init__(message)
        self.telemetry: dict[str, Any] = telemetry or {}


# ---------------------------------------------------------------------------
# 5 helper functions — copied verbatim from CutClaw (no behavioral changes)
# ---------------------------------------------------------------------------


def _seconds_to_srt_time(seconds: float) -> str:
    """``HH:MM:SS,mmm`` SRT timestamp formatter (CutClaw line 576-582)."""
    total_ms = max(0, int(round(seconds * 1000)))
    hh = total_ms // 3600000
    mm = (total_ms % 3600000) // 60000
    ss = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _subtitle_line_text(sub: dict[str, Any]) -> str:
    """Format a subtitle dict for the prompt (``[Speaker] text`` if labelled)."""
    text = (sub.get("text") or "").strip()
    speaker = (sub.get("speaker") or "").strip()
    if speaker:
        return f"[{speaker}] {text}"
    return text


def _normalize_dialogue_text(text: str) -> str:
    """Normalize dialogue text for robust subtitle matching.

    Lowercase + strip square-bracket speaker tags + strip HTML/SRT tags +
    collapse non-word chars to spaces.
    """
    if not text:
        return ""
    clean = str(text).lower().strip()
    clean = re.sub(r"\[[^\]]+\]", " ", clean)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"[^\w]+", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _dialogue_similarity(a: str, b: str) -> float:
    """Hybrid similarity: ``SequenceMatcher`` ratio + Jaccard token blend.

    Returns ``1.0`` for exact match, ``≥0.9`` when one is a substring of
    the other, otherwise ``max(seq, 0.65*seq + 0.35*jaccard)``.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq_score = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        seq_score = max(seq_score, 0.9)

    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return seq_score
    jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    return max(seq_score, 0.65 * seq_score + 0.35 * jaccard)


def _match_dialogue_lines_to_subtitles(
    lines: list[str],
    subtitles: list[dict[str, Any]],
    *,
    min_score: float = 0.55,
) -> list[dict[str, Any]]:
    """Match LLM-selected lines back to original SRT entries (forward-only).

    Greedy left-to-right scan: each subsequent line must match an entry
    later than the previous one (so we always recover a contiguous-ish
    monotonic source range).  Returns ``[]`` when zero lines reach the
    fuzzy threshold — caller treats that as ``unknown``-class failure.
    """
    if not lines or not subtitles:
        return []

    subtitle_norm = [_normalize_dialogue_text(_subtitle_line_text(s)) for s in subtitles]
    matched_indices: list[int] = []
    last_idx = -1

    for raw_line in lines:
        norm_line = _normalize_dialogue_text(str(raw_line))
        if not norm_line:
            continue

        best_idx: int | None = None
        best_score = 0.0
        for idx in range(last_idx + 1, len(subtitles)):
            score = _dialogue_similarity(norm_line, subtitle_norm[idx])
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= min_score:
            matched_indices.append(best_idx)
            last_idx = best_idx

    if not matched_indices:
        return []

    unique_sorted = sorted(set(matched_indices))
    return [subtitles[i] for i in unique_sorted]


def _build_timed_lines(
    matched: list[dict[str, Any]],
    *,
    clip_start_sec: float,
) -> list[dict[str, Any]]:
    """Build per-line clip-relative timing records (start from 0).

    Both absolute (``source_start``/``source_end``) and clip-relative
    (``start``/``end``) timestamps are emitted so downstream consumers
    (NLE / render pipeline) can pick whichever they need.
    """
    timed: list[dict[str, Any]] = []
    for sub in matched:
        abs_start = float(sub.get("start_sec", 0.0))
        abs_end = float(sub.get("end_sec", 0.0))
        rel_start = max(0.0, abs_start - clip_start_sec)
        rel_end = max(rel_start, abs_end - clip_start_sec)
        timed.append(
            {
                "text": _subtitle_line_text(sub),
                "start": _seconds_to_srt_time(rel_start),
                "end": _seconds_to_srt_time(rel_end),
                "source_start": _seconds_to_srt_time(abs_start),
                "source_end": _seconds_to_srt_time(abs_end),
            }
        )
    return timed


def _format_subtitles_for_prompt(
    subtitles: list[dict[str, Any]],
    *,
    max_chars: int = HOOK_DIALOGUE_MAX_SUBTITLE_CHARS,
    window_mode: str = "tail",
    start_index: int | None = None,
) -> tuple[str, int]:
    """Pack subtitles into a ≤``max_chars`` prompt window.

    - ``tail``  : reverse-iterate, then re-reverse so output is chronological
    - ``head``  : forward-iterate from the very first cue
    - ``random_window``: forward-iterate from ``start_index`` (or random)

    Returns ``(joined_text, n_blocks)``.  Empty input yields ``("", 0)``.
    """
    all_blocks: list[str] = []
    for idx, sub in enumerate(subtitles, start=1):
        text = _subtitle_line_text(sub).strip()
        if not text:
            continue
        dur = max(0.0, float(sub.get("end_sec", 0.0)) - float(sub.get("start_sec", 0.0)))
        block = (
            f"{idx}\n"
            f"{_seconds_to_srt_time(float(sub.get('start_sec', 0.0)))} --> "
            f"{_seconds_to_srt_time(float(sub.get('end_sec', 0.0)))} [{dur:.1f}s]\n"
            f"{text}"
        )
        all_blocks.append(block)

    if not all_blocks:
        return "", 0

    used = 0
    selected: list[str] = []

    if window_mode == "random_window":
        if start_index is None:
            start_index = random.randrange(len(all_blocks))
        start_index = max(0, min(start_index, len(all_blocks) - 1))
        iterable: list[str] = all_blocks[start_index:]
    elif window_mode == "head":
        iterable = all_blocks
    else:
        iterable = list(reversed(all_blocks))

    for block in iterable:
        if selected and used + len(block) + 2 > max_chars:
            break
        selected.append(block)
        used += len(block) + 2

    if window_mode == "tail":
        selected.reverse()

    return "\n\n".join(selected), len(selected)


# ---------------------------------------------------------------------------
# Prompt — 1:1 copied from CutClaw, do NOT "improve" wording.
# ---------------------------------------------------------------------------

SELECT_HOOK_DIALOGUE_PROMPT = """\
You are a film editor selecting ONE hook dialogue for a short-form video.

CRITICAL RULES:
- Strongly prefer dialogue spoken BY {main_character}.
- The hook MUST be {min_duration:.1f}-{max_duration:.1f} seconds long.
- Choose dialogue that is dramatic, conflict-driven, philosophical, or stands strongly on its own.
- Quote the exact subtitle lines verbatim — do not paraphrase.

Project intent:
{instruction}

Reference plan (or repeat of intent if no plan):
{shot_plan_summary}

Subtitles ({window_mode} window, format: [duration_sec] text):
{subtitles_block}

Respond ONLY with valid JSON:
{{
  "lines": ["exact line 1", "exact line 2"],
  "start": "HH:MM:SS,mmm",
  "end": "HH:MM:SS,mmm",
  "reason": "one-sentence editorial rationale"
}}"""


# ---------------------------------------------------------------------------
# Main entry — three-window strategy with retry
# ---------------------------------------------------------------------------


async def select_hook_dialogue(
    *,
    subtitles: list[dict[str, Any]],
    instruction: str,
    main_character: str | None,
    target_duration_sec: float,
    prompt_window_mode: str,
    random_window_attempts: int,
    model: str,
    llm_caller: LLMCaller,
) -> dict[str, Any]:
    """Pick ONE opening hook from ``subtitles`` using ``llm_caller``.

    Args:
        subtitles: List of cue dicts with ``text``/``start_sec``/``end_sec``
            (and optional ``speaker``).  Caller is responsible for the
            seconds-vs-string conversion (pipeline does this in
            ``_do_hook_pick``).
        instruction: Free-form project intent (≤200 chars typical).
        main_character: Preferred speaker name; falls back to a generic
            placeholder so the prompt template never has empty slots.
        target_duration_sec: Hook duration target in seconds (UI slider
            6-30s).  Acceptance band is ``[max(6, t-5), t+5]``.
        prompt_window_mode: ``"tail_then_head"`` (default) or
            ``"random_window"`` (skip tail/head, randoms only).
        random_window_attempts: Number of additional random-window
            fallbacks if tail+head both fail (UI slider 1-5).
        model: Qwen model id passed through to ``llm_caller``.
        llm_caller: Async transport callable.

    Returns:
        Hook dict with keys: ``lines``, ``timed_lines``, ``duration_seconds``,
        ``selected_window``, ``selected_attempt``, ``selection_method``,
        ``reason``, ``source_start``, ``source_end``, ``_telemetry``.

    Raises:
        HookSelectionError: All windows × 2 attempts exhausted.
    """
    min_dur = max(6.0, target_duration_sec - 5.0)
    max_dur = target_duration_sec + 5.0
    main_char = (main_character or "").strip() or "the main character"
    shot_plan_summary = (instruction or "").strip() or "(no extra plan; use intent above)"

    windows: list[tuple[str, int]] = []
    if prompt_window_mode == "random_window":
        # randoms-only mode (rare; advanced UI option)
        pass
    else:
        windows = [("tail", 0), ("head", 0)]
    n_subs = len(subtitles)
    for _ in range(max(0, int(random_window_attempts))):
        rand_start = random.randint(0, max(0, n_subs - 1)) if n_subs else 0
        windows.append(("random_window", rand_start))

    if not windows:
        raise HookSelectionError(
            "No windows configured (set random_window_attempts ≥ 1 for random_window prompt mode)",
            telemetry={"llm_calls": 0, "windows_tried": [], "rejected_attempts": []},
        )

    telemetry: dict[str, Any] = {
        "llm_calls": 0,
        "windows_tried": [],
        "rejected_attempts": [],
        "model": model,
        "target_duration_sec": target_duration_sec,
    }

    for win_name, start_idx in windows:
        telemetry["windows_tried"].append(win_name)
        for attempt in range(1, 3):
            subs_block, n_blocks = _format_subtitles_for_prompt(
                subtitles,
                window_mode=win_name,
                start_index=start_idx if win_name == "random_window" else None,
            )
            if not subs_block:
                telemetry["rejected_attempts"].append(
                    {"window": win_name, "attempt": attempt, "reason": "empty_window"}
                )
                continue
            prompt = SELECT_HOOK_DIALOGUE_PROMPT.format(
                main_character=main_char,
                min_duration=min_dur,
                max_duration=max_dur,
                instruction=(instruction or "").strip() or "(no specific intent)",
                shot_plan_summary=shot_plan_summary,
                window_mode=win_name,
                subtitles_block=subs_block,
            )
            telemetry["llm_calls"] += 1
            try:
                raw = await llm_caller(
                    [{"role": "user", "content": prompt}],
                    model,
                    {
                        "temperature": 0.3,
                        "max_tokens": 2000,
                        "response_format_json": True,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — surface as window-level reject
                logger.warning(
                    "hook_picker LLM call failed (window=%s attempt=%d): %s",
                    win_name,
                    attempt,
                    exc,
                )
                telemetry["rejected_attempts"].append(
                    {
                        "window": win_name,
                        "attempt": attempt,
                        "reason": f"llm_exception:{type(exc).__name__}",
                        "detail": str(exc)[:200],
                    }
                )
                continue

            if raw is None:
                telemetry["rejected_attempts"].append(
                    {"window": win_name, "attempt": attempt, "reason": "llm_returned_none"}
                )
                continue
            parsed = parse_llm_json(raw, expect=dict)
            if not isinstance(parsed, dict) or "lines" not in parsed:
                telemetry["rejected_attempts"].append(
                    {
                        "window": win_name,
                        "attempt": attempt,
                        "reason": "non_json_or_missing_lines",
                        "raw": raw[:200],
                        "n_blocks": n_blocks,
                    }
                )
                continue
            lines_raw = parsed.get("lines") or []
            if not isinstance(lines_raw, list) or not lines_raw:
                telemetry["rejected_attempts"].append(
                    {"window": win_name, "attempt": attempt, "reason": "lines_not_list"}
                )
                continue
            matched = _match_dialogue_lines_to_subtitles(
                [str(line) for line in lines_raw],
                subtitles,
                min_score=0.55,
            )
            if not matched:
                telemetry["rejected_attempts"].append(
                    {
                        "window": win_name,
                        "attempt": attempt,
                        "reason": "fuzzy_match_below_0.55",
                    }
                )
                continue
            clip_start = float(matched[0].get("start_sec", 0.0))
            clip_end = float(matched[-1].get("end_sec", 0.0))
            duration = clip_end - clip_start
            if duration < min_dur or duration > max_dur:
                telemetry["rejected_attempts"].append(
                    {
                        "window": win_name,
                        "attempt": attempt,
                        "reason": (
                            f"duration_out_of_range:{duration:.1f}s!in[{min_dur:.1f},{max_dur:.1f}]"
                        ),
                    }
                )
                continue
            timed = _build_timed_lines(matched, clip_start_sec=clip_start)
            return {
                "lines": [_subtitle_line_text(m) for m in matched],
                "timed_lines": timed,
                "duration_seconds": round(duration, 2),
                "selected_window": win_name,
                "selected_attempt": attempt,
                "selection_method": "llm_srt_matched",
                "reason": str(parsed.get("reason", "")).strip(),
                "source_start": _seconds_to_srt_time(clip_start),
                "source_end": _seconds_to_srt_time(clip_end),
                "_telemetry": telemetry,
            }

    raise HookSelectionError(
        f"All {len(windows)} windows × 2 attempts failed "
        f"(see telemetry.rejected_attempts; total llm_calls={telemetry['llm_calls']})",
        telemetry=telemetry,
    )


__all__ = [
    "HOOK_DIALOGUE_MAX_SUBTITLE_CHARS",
    "HookSelectionError",
    "LLMCaller",
    "SELECT_HOOK_DIALOGUE_PROMPT",
    "select_hook_dialogue",
]


# Internal helpers exported under leading underscore for the test module.
_normalize_dialogue_text  # noqa: B018 — silence unused-name lint in __all__
_dialogue_similarity  # noqa: B018
_match_dialogue_lines_to_subtitles  # noqa: B018
_build_timed_lines  # noqa: B018
_format_subtitles_for_prompt  # noqa: B018


# Round-tripping json import so static checkers see it; used implicitly by
# the parser when test fixtures stress unicode escapes.
_ = json

