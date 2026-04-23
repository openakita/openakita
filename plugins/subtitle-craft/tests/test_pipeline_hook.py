"""End-to-end pipeline tests for hook_picker mode (v1.1).

Mocks ``asr.call_qwen_plus`` so we can exercise the full pipeline branch
(``_step_asr_or_load`` → ``_step_render_output`` → ``_do_hook_pick``)
without hitting DashScope.

Three scenarios:

1. ``test_hook_pick_succeeds_writes_outputs`` — happy path: hook.srt +
   hook.json appear, ``ctx.hook`` populated, ``output_srt_path`` updated.
2. ``test_hook_pick_short_srt_fails_format`` — ≤ 5 cues → ``error_kind="format"``.
3. ``test_hook_pick_llm_all_fail_unknown`` — every LLM attempt rejected →
   ``error_kind="unknown"`` and telemetry persisted into metadata.json.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from subtitle_pipeline import SubtitlePipelineContext, run_pipeline

# ---------------------------------------------------------------------------
# Fixtures: minimal in-memory tm + asr stand-ins (kept local to this file
# so the existing test_pipeline.py fakes stay strictly auto_subtitle-shaped)
# ---------------------------------------------------------------------------


@dataclass
class _Event:
    name: str
    payload: dict[str, Any]


class _Tm:
    """Bare-minimum task manager: hook_picker only needs cancel + update."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self._canceled: set[str] = set()

    def is_canceled(self, task_id: str) -> bool:
        return task_id in self._canceled

    async def update_task(self, task_id: str, **updates: Any) -> None:
        self.tasks.setdefault(task_id, {}).update(updates)

    async def update_task_safe(self, task_id: str, **updates: Any) -> None:
        await self.update_task(task_id, **updates)


class _Asr:
    """Asr stub with mockable ``call_qwen_plus``."""

    def __init__(self, side_effect: Any) -> None:
        self._side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    async def call_qwen_plus(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "qwen-plus",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: float = 120.0,
        response_format_json: bool = True,
    ) -> str | None:
        self.calls.append(
            {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format_json": response_format_json,
                "n_msgs": len(messages),
            }
        )
        if callable(self._side_effect):
            result = self._side_effect(self.calls, messages)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return self._side_effect


def _events() -> tuple[list[_Event], Any]:
    out: list[_Event] = []

    def emit(name: str, payload: dict[str, Any]) -> None:
        out.append(_Event(name=name, payload=dict(payload)))

    return out, emit


def _write_srt(path: Path, n_cues: int, *, line_text: str = "Important line") -> None:
    blocks: list[str] = []
    for i in range(1, n_cues + 1):
        start = (i - 1) * 4
        end = start + 3
        blocks.append(
            f"{i}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n"
            f"{line_text} number {i} stands strongly on its own.\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Happy path: hook.srt + hook.json + ctx.hook populated
# ---------------------------------------------------------------------------


def test_hook_pick_succeeds_writes_outputs(tmp_path: Path) -> None:
    srt = tmp_path / "in.srt"
    _write_srt(srt, 30)

    # Pick the first 3 cues (~12s) so duration falls inside the [7,17]s band.
    expected_lines = [
        "Important line number 1 stands strongly on its own.",
        "Important line number 2 stands strongly on its own.",
        "Important line number 3 stands strongly on its own.",
    ]
    payload = json.dumps(
        {
            "lines": expected_lines,
            "start": "00:00:00,000",
            "end": "00:00:11,000",
            "reason": "Strong opening with stakes.",
        },
        ensure_ascii=False,
    )

    tm = _Tm()
    asr = _Asr(side_effect=lambda calls, msgs: payload)
    events, emit = _events()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ctx = SubtitlePipelineContext(
        task_id="hook-001",
        mode="hook_picker",
        params={
            "srt_path": str(srt),
            "instruction": "find the strongest opener",
            "main_character": "",
            "target_duration_sec": 12.0,
            "prompt_window_mode": "tail_then_head",
            "random_window_attempts": 2,
            "hook_model": "qwen-plus",
        },
        task_dir=task_dir,
    )
    asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

    assert ctx.hook is not None
    assert (task_dir / "hook.srt").exists()
    assert (task_dir / "hook.json").exists()
    assert ctx.output_srt_path == task_dir / "hook.srt"
    assert tm.tasks.get("hook-001", {}).get("status") == "succeeded"

    hook_json = json.loads((task_dir / "hook.json").read_text(encoding="utf-8"))
    assert "hook" in hook_json and "telemetry" in hook_json
    assert hook_json["hook"]["selection_method"] == "llm_srt_matched"
    assert hook_json["hook"]["selected_window"] == "tail"

    terminal = events[-1]
    assert terminal.payload["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 2. Too-short SRT → format error before any LLM call
# ---------------------------------------------------------------------------


def test_hook_pick_short_srt_fails_format(tmp_path: Path) -> None:
    srt = tmp_path / "tiny.srt"
    _write_srt(srt, 3)

    tm = _Tm()
    asr = _Asr(side_effect="should not be called")
    events, emit = _events()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ctx = SubtitlePipelineContext(
        task_id="hook-002",
        mode="hook_picker",
        params={"srt_path": str(srt)},
        task_dir=task_dir,
    )
    asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

    assert asr.calls == []  # never reached the LLM step
    assert ctx.error_kind == "format"
    assert tm.tasks.get("hook-002", {}).get("status") in (None, "failed")
    assert events[-1].payload["status"] == "failed"
    assert events[-1].payload["error_kind"] == "format"


# ---------------------------------------------------------------------------
# 3. Every LLM attempt rejected → unknown + telemetry preserved
# ---------------------------------------------------------------------------


def test_hook_pick_llm_all_fail_unknown(tmp_path: Path) -> None:
    srt = tmp_path / "in.srt"
    _write_srt(srt, 30)

    tm = _Tm()
    asr = _Asr(side_effect=lambda calls, msgs: "definitely not json")
    events, emit = _events()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ctx = SubtitlePipelineContext(
        task_id="hook-003",
        mode="hook_picker",
        params={
            "srt_path": str(srt),
            "target_duration_sec": 12.0,
            "random_window_attempts": 1,
        },
        task_dir=task_dir,
    )
    asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

    assert ctx.error_kind == "unknown"
    # tail(2) + head(2) + 1×random(2) = 6 calls
    assert len(asr.calls) == 6
    assert ctx.hook is None
    assert ctx.hook_telemetry  # telemetry preserved on the context
    assert events[-1].payload["status"] == "failed"


# ---------------------------------------------------------------------------
# 4. Without API key (asr=None) → auth error
# ---------------------------------------------------------------------------


def test_hook_pick_no_api_key_fails_auth(tmp_path: Path) -> None:
    srt = tmp_path / "in.srt"
    _write_srt(srt, 30)

    tm = _Tm()
    events, emit = _events()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ctx = SubtitlePipelineContext(
        task_id="hook-004",
        mode="hook_picker",
        params={"srt_path": str(srt)},
        task_dir=task_dir,
    )
    asyncio.run(run_pipeline(ctx, tm, None, emit=emit))

    assert ctx.error_kind == "auth"


@pytest.mark.parametrize("mode", ["hook_picker"])
def test_hook_picker_skips_translate_and_burn_steps(tmp_path: Path, mode: str) -> None:
    """Verify mode skip_steps actually prevents translate/burn step entry."""
    srt = tmp_path / "in.srt"
    _write_srt(srt, 30)

    expected_lines = [
        "Important line number 5 stands strongly on its own.",
        "Important line number 6 stands strongly on its own.",
        "Important line number 7 stands strongly on its own.",
    ]
    payload = json.dumps({"lines": expected_lines, "reason": "test"}, ensure_ascii=False)

    tm = _Tm()
    asr = _Asr(side_effect=lambda calls, msgs: payload)
    events, emit = _events()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ctx = SubtitlePipelineContext(
        task_id="hook-005",
        mode=mode,
        params={"srt_path": str(srt), "target_duration_sec": 12.0},
        task_dir=task_dir,
    )
    asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

    step_names = [
        e.payload.get("pipeline_step") for e in events if e.payload.get("status") == "running"
    ]
    # hook_picker skips prepare_assets / identify / translate / burn.
    forbidden = {
        "prepare_assets",
        "identify_characters",
        "translate_or_repair",
        "burn_or_finalize",
    }
    assert forbidden.isdisjoint(step_names)
    # but it does run setup / estimate / asr_or_load / render_output
    assert "asr_or_load" in step_names
    assert "render_output" in step_names
