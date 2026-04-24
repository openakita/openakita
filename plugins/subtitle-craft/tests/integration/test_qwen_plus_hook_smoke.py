"""Phase 5 integration smoke for Qwen-Plus hook_picker (mode v1.1).

Marked ``integration``; **skipped by default** when ``DASHSCOPE_API_KEY``
is not present (CI-friendly per ``docs/subtitle-craft-plan.md §8.4``
runtime contract; mirrors :mod:`test_paraformer_smoke`).

Run with::

    pytest plugins/subtitle-craft/tests/integration/ -m integration -v -k hook

Optional env:

- ``DASHSCOPE_API_KEY`` — required, real DashScope key.
- ``SUBTITLE_CRAFT_HOOK_FIXTURE`` — override fixture path
  (defaults to ``tests/fixtures/sample_short.srt``).

Asserts on contract only — no content match:

- ``select_hook_dialogue`` returns a ``hook`` dict with ``lines``,
  ``timed_lines``, ``source_start/_end`` and ``duration_seconds``.
- All hook dialogue lines are matchable back to the input SRT
  (``selection_method == "llm_srt_matched"``).
- Reported duration falls inside the requested ±5 s window.
- Telemetry ``rejected_attempts`` is a list (may be empty on first hit).

Cost guard: the call uses ≤2 LLM round-trips (one window, max 2 retries),
so the bill is < ¥0.01 per the Qwen-Plus rate (¥0.005/round, see
:data:`subtitle_models.HOOK_PICKER_MODELS`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_FIXTURE_DEFAULT = Path(__file__).resolve().parent.parent / "fixtures" / "sample_short.srt"


def _api_key() -> str | None:
    return os.environ.get("DASHSCOPE_API_KEY") or None


def _fixture_path() -> Path:
    override = os.environ.get("SUBTITLE_CRAFT_HOOK_FIXTURE")
    return Path(override) if override else _FIXTURE_DEFAULT


@pytest.fixture(autouse=True)
def _skip_without_key() -> None:
    if not _api_key():
        pytest.skip("DASHSCOPE_API_KEY not set; skipping live Qwen-Plus hook smoke")
    if not _fixture_path().exists():
        pytest.skip(f"hook smoke fixture missing: {_fixture_path()}")


def _parse_srt(text: str) -> list[dict[str, object]]:
    """Tiny SRT parser — same shape as ``subtitle_pipeline._load_srt_input``
    produces for ``ctx.cues`` (index/start/end/text).  Kept inline so the
    integration test does not import private pipeline helpers."""

    def _ts(s: str) -> float:
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0

    cues: list[dict[str, object]] = []
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    for blk in blocks:
        lines = blk.split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        a, b = [s.strip() for s in lines[1].split("-->", 1)]
        cues.append(
            {
                "index": idx,
                "start": _ts(a),
                "end": _ts(b),
                "text": "\n".join(lines[2:]).strip(),
            }
        )
    return cues


async def test_qwen_plus_hook_pick_smoke() -> None:
    """End-to-end ``select_hook_dialogue`` against a real Qwen-Plus key."""

    from subtitle_asr_client import SubtitleAsrClient
    from subtitle_hook_picker import select_hook_dialogue

    fixture = _fixture_path()
    cues = _parse_srt(fixture.read_text(encoding="utf-8"))
    assert len(cues) >= 5, f"fixture has only {len(cues)} cues — need ≥5"

    client = SubtitleAsrClient(api_key=_api_key() or "", poll_interval=3.0, poll_max_seconds=180.0)

    target_dur = 12.0
    result = await asyncio.wait_for(
        select_hook_dialogue(
            subtitles=cues,
            instruction="pick the most dramatic opening line",
            main_character=None,
            target_duration_sec=target_dur,
            prompt_window_mode="tail_then_head",
            random_window_attempts=2,
            model="qwen-plus",
            llm_caller=client.call_qwen_plus,
        ),
        timeout=180.0,
    )

    assert isinstance(result, dict)
    hook = result["hook"]
    telemetry = result.get("telemetry", {})

    assert isinstance(hook, dict)
    assert hook.get("selection_method") == "llm_srt_matched", (
        f"unexpected selection_method={hook.get('selection_method')!r}"
    )
    lines = hook.get("lines") or []
    assert lines, "hook.lines must be non-empty"
    assert hook.get("source_start"), "hook.source_start missing"
    assert hook.get("source_end"), "hook.source_end missing"

    duration = float(hook.get("duration_seconds") or 0.0)
    lo, hi = max(6.0, target_dur - 5.0), target_dur + 5.0
    assert lo <= duration <= hi, (
        f"hook duration {duration:.2f}s outside target window [{lo:.1f}, {hi:.1f}]s"
    )

    timed = hook.get("timed_lines") or []
    assert timed, "hook.timed_lines must be non-empty"

    src_lines = {c["text"].strip() for c in cues if isinstance(c.get("text"), str)}
    for tl in timed:
        text = (tl.get("text") or "").strip()
        assert text, f"empty timed line: {tl!r}"
        assert text in src_lines, f"timed line not present in source SRT: {text!r}"

    assert isinstance(telemetry.get("rejected_attempts", []), list)

