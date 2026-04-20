"""storyboard — turn a script into a shot list, with 5-level LLM-output fallback parsing.

Inspired by CutClaw's ``GENERATE_STRUCTURE_PROPOSAL_PROMPT`` (三分自检 — the
LLM is told to balance the shot distribution across the timeline).

The 5-level parser is critical: real LLM outputs are messy, so we try, in
order:

1. JSON object directly
2. Fenced ``\u0060\u0060\u0060json ... \u0060\u0060\u0060`` block
3. First balanced ``{...}`` substring
4. Numbered-list fallback (``1. ... \\n 2. ...`` lines)
5. Plain text → single 1-shot stub
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── data shape ─────────────────────────────────────────────────────────


@dataclass
class Shot:
    """One row in the shot list."""

    index: int
    duration_sec: float
    visual: str          # what the camera sees
    camera: str = ""     # camera language: 推 / 拉 / 摇 / 跟 / 固定 / 鸟瞰 ...
    dialogue: str = ""   # voiceover / on-screen speech
    sound: str = ""      # bgm / sfx hint
    notes: str = ""      # director notes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Storyboard:
    """Ordered shot list + meta."""

    title: str
    target_duration_sec: float
    shots: list[Shot] = field(default_factory=list)
    style_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "target_duration_sec": self.target_duration_sec,
            "style_notes": self.style_notes,
            "shots": [s.to_dict() for s in self.shots],
            "actual_duration_sec": sum(s.duration_sec for s in self.shots),
        }


# ── seedance CLI export (issue: storyboard ↔ seedance hand-off) ────────


# Seedance Ark API duration window: 2.0=4-15s, 1.x=2-12s. We clamp to the
# intersection [2, 15] so any model accepts the payload, and we degrade
# gently when the storyboard shot is shorter/longer than the model allows.
_SEEDANCE_MIN_DURATION = 2
_SEEDANCE_MAX_DURATION = 15
_SEEDANCE_DEFAULT_MODEL = "doubao-seedance-2-0-260128"
_SEEDANCE_DEFAULT_RATIO = "16:9"
_SEEDANCE_DEFAULT_RESOLUTION = "720p"


def _shot_to_seedance_prompt(shot: Shot, *, style_notes: str = "") -> str:
    """Combine ``visual + camera + sound`` into a Seedance-friendly prompt.

    The Seedance API takes a single text prompt per task; the storyboard
    however splits the description across several fields.  We concatenate
    them with comma separators so the user can review/edit before invoking
    ``scripts/seedance.py create``.
    """
    parts: list[str] = []
    visual = (shot.visual or "").strip()
    if visual:
        parts.append(visual)
    camera = (shot.camera or "").strip()
    if camera:
        parts.append(f"镜头: {camera}")
    sound = (shot.sound or "").strip()
    if sound:
        parts.append(f"音效: {sound}")
    if style_notes:
        parts.append(f"风格: {style_notes.strip()}")
    return ", ".join(parts) or "一段画面"


def _clamp_seedance_duration(seconds: float) -> int:
    if seconds <= 0:
        return _SEEDANCE_MIN_DURATION
    rounded = int(round(seconds))
    if rounded < _SEEDANCE_MIN_DURATION:
        return _SEEDANCE_MIN_DURATION
    if rounded > _SEEDANCE_MAX_DURATION:
        return _SEEDANCE_MAX_DURATION
    return rounded


def to_seedance_payload(
    sb: Storyboard,
    *,
    model: str = _SEEDANCE_DEFAULT_MODEL,
    ratio: str = _SEEDANCE_DEFAULT_RATIO,
    resolution: str = _SEEDANCE_DEFAULT_RESOLUTION,
) -> dict[str, Any]:
    """Render a storyboard as a JSON payload tailored for ``seedance.py``.

    Output shape::

        {
          "title": "...",
          "model": "doubao-seedance-2-0-260128",
          "ratio": "16:9",
          "resolution": "720p",
          "target_duration_sec": 30,
          "shots": [
            {"index": 1, "prompt": "...", "duration": 5,
             "ratio": "16:9", "resolution": "720p", "model": "..."},
            ...
          ],
          "cli_examples": ["python scripts/seedance.py create --prompt ..."],
        }

    Each shot maps to a single seedance task; the helper does *not* call the
    network — it produces a payload the user (or downstream automation) can
    feed to ``scripts/seedance.py create`` one shot at a time.
    """
    shots_out: list[dict[str, Any]] = []
    cli_examples: list[str] = []
    for s in sb.shots:
        prompt_text = _shot_to_seedance_prompt(s, style_notes=sb.style_notes)
        duration = _clamp_seedance_duration(s.duration_sec)
        shots_out.append({
            "index": s.index,
            "prompt": prompt_text,
            "duration": duration,
            "ratio": ratio,
            "resolution": resolution,
            "model": model,
            "source_shot": s.to_dict(),
        })
        # Build a copy-pasteable CLI line; quote the prompt with double-quotes
        # and escape any embedded quotes so the example survives a shell paste.
        escaped = prompt_text.replace('"', r'\"')
        cli_examples.append(
            f'python scripts/seedance.py create --prompt "{escaped}" '
            f'--model {model} --duration {duration} --ratio {ratio} '
            f'--resolution {resolution} --wait'
        )
    return {
        "title": sb.title,
        "model": model,
        "ratio": ratio,
        "resolution": resolution,
        "target_duration_sec": sb.target_duration_sec,
        "shot_count": len(shots_out),
        "shots": shots_out,
        "cli_examples": cli_examples,
        "notes": (
            "Each shot is one independent seedance task; durations are "
            f"clamped into the [{_SEEDANCE_MIN_DURATION},"
            f"{_SEEDANCE_MAX_DURATION}] second window supported by all "
            "current Seedance models. Run cli_examples one by one, or feed "
            "shots[*] into your own batch script."
        ),
    }


# ── prompt ─────────────────────────────────────────────────────────────


_SYSTEM = """你是 OpenAkita 的「分镜师」。把用户给的文字脚本/想法拆成可以拍/可以生图的分镜表。

输出严格的 JSON：

{
  "title": "短标题",
  "target_duration_sec": 30,
  "style_notes": "整体风格，一句话",
  "shots": [
    {
      "index": 1,
      "duration_sec": 5,
      "visual": "镜头里能看到什么 — 主体 + 环境 + 动作，要具体",
      "camera": "推 | 拉 | 摇 | 跟 | 固定 | 俯拍 | 仰拍 | 鸟瞰 | 特写 | 中景 | 全景",
      "dialogue": "如果有旁白/台词，写在这里；没有就空",
      "sound": "bgm 风格 / 关键音效；可空",
      "notes": "其他给摄影/AI 生图的提示"
    }
  ]
}

三分自检（重要）：
1. 镜头数 = ceil(target_duration_sec / 平均镜头时长)，平均 3~6 秒最合适
2. 时长合计要≈ target_duration_sec（±10% 可接受）
3. 必须把镜头**均匀分布**在整段时长里，不要把所有重点堆在前 1/3

只输出 JSON，不要解释。
"""


# ── 5-level fallback parser ────────────────────────────────────────────


def parse_storyboard_llm_output(text: str, *, fallback_title: str = "未命名分镜",
                                 fallback_duration: float = 30.0) -> Storyboard:
    """Parse a (possibly messy) LLM response into a ``Storyboard``.

    Tries 5 levels in order; never raises.
    """
    if not text or not text.strip():
        return _stub_storyboard(fallback_title, fallback_duration,
                                 reason="LLM returned empty output")

    candidates: list[str] = [text]

    # Level 2: fenced ```json ... ```
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        candidates.append(m.group(1))

    # Level 3: first balanced {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))

    # Levels 1/2/3: try to load each candidate
    for c in candidates:
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        sb = _from_dict(data, fallback_title, fallback_duration)
        if sb.shots:
            return sb

    # Level 4: numbered-list fallback (`1. ... \n 2. ...`)
    sb = _from_numbered_list(text, fallback_title, fallback_duration)
    if sb.shots:
        return sb

    # Level 5: stub fallback
    return _stub_storyboard(fallback_title, fallback_duration,
                            reason="Could not parse LLM output")


def _from_dict(data: dict[str, Any], title: str, duration: float) -> Storyboard:
    raw_shots = data.get("shots") or []
    shots: list[Shot] = []
    for i, item in enumerate(raw_shots):
        if not isinstance(item, dict):
            continue
        try:
            shots.append(Shot(
                index=int(item.get("index", i + 1)),
                duration_sec=float(item.get("duration_sec", 0) or 0),
                visual=str(item.get("visual", "")).strip(),
                camera=str(item.get("camera", "")).strip(),
                dialogue=str(item.get("dialogue", "")).strip(),
                sound=str(item.get("sound", "")).strip(),
                notes=str(item.get("notes", "")).strip(),
            ))
        except (TypeError, ValueError):
            continue
    return Storyboard(
        title=str(data.get("title") or title).strip(),
        target_duration_sec=float(data.get("target_duration_sec") or duration),
        style_notes=str(data.get("style_notes", "")).strip(),
        shots=shots,
    )


_NUMBERED_LINE = re.compile(r"^\s*(\d+)[.、)]\s*(.+)$")


def _from_numbered_list(text: str, title: str, duration: float) -> Storyboard:
    matches = [m for m in (_NUMBERED_LINE.match(line) for line in text.splitlines()) if m]
    shots: list[Shot] = []
    per = duration / max(1, len(matches))
    for m in matches:
        idx = int(m.group(1))
        body = m.group(2).strip()
        shots.append(Shot(index=idx, duration_sec=per, visual=body))
    return Storyboard(title=title, target_duration_sec=duration, shots=shots,
                       style_notes="(parsed from numbered list — partial)")


def _stub_storyboard(title: str, duration: float, *, reason: str) -> Storyboard:
    return Storyboard(
        title=title,
        target_duration_sec=duration,
        style_notes=f"[fallback stub] {reason}",
        shots=[Shot(index=1, duration_sec=duration,
                    visual="(请重写脚本或重试 — LLM 没能给出可用的分镜)",
                    notes=reason)],
    )


# ── three-thirds self-check ────────────────────────────────────────────


@dataclass
class StoryboardSelfCheck:
    """Verdict on a parsed storyboard.  All fields are user-facing strings."""

    ok: bool
    duration_match: str         # ✓ / 警告
    distribution_balance: str   # ✓ / 警告
    minimum_count: str          # ✓ / 警告
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def self_check(sb: Storyboard) -> StoryboardSelfCheck:
    """Apply the three-thirds heuristic + a few sanity checks.

    1. **duration match** — actual sum of shot durations within ±10% of target
    2. **distribution balance** — no single third has > 60% of total time
    3. **minimum count** — at least ``ceil(target / 6)`` shots so it doesn't
       feel like a slideshow
    """
    suggestions: list[str] = []
    actual = sum(s.duration_sec for s in sb.shots)
    target = sb.target_duration_sec
    duration_ok = target == 0 or abs(actual - target) / max(1.0, target) <= 0.10
    duration_match = "✓ 时长匹配" if duration_ok else f"⚠ 实际 {actual:.1f}s / 目标 {target:.1f}s"
    if not duration_ok:
        suggestions.append("调整某些镜头的 duration_sec 让总时长贴近目标")

    # Distribution: split into thirds
    third = max(1.0, target / 3.0) if target else (actual / 3.0 if actual else 1.0)
    buckets = [0.0, 0.0, 0.0]
    cur = 0.0
    for s in sb.shots:
        idx = min(2, int(cur / third))
        buckets[idx] += s.duration_sec
        cur += s.duration_sec
    biggest = max(buckets) / max(1.0, sum(buckets))
    bal_ok = biggest <= 0.60
    distribution_balance = "✓ 分布均匀" if bal_ok else f"⚠ 有一段占了 {biggest*100:.0f}%"
    if not bal_ok:
        suggestions.append("把镜头更均匀地分布在整段时长里")

    import math
    min_needed = math.ceil(max(1.0, target) / 6.0) if target else 1
    count_ok = len(sb.shots) >= min_needed
    minimum_count = "✓ 镜头数充足" if count_ok else f"⚠ 只有 {len(sb.shots)} 个镜头，建议 ≥ {min_needed}"
    if not count_ok:
        suggestions.append("拆得再细一点 — 平均 3~6 秒一个镜头比较有节奏")

    return StoryboardSelfCheck(
        ok=duration_ok and bal_ok and count_ok,
        duration_match=duration_match,
        distribution_balance=distribution_balance,
        minimum_count=minimum_count,
        suggestions=suggestions,
    )


__all__ = [
    "Shot", "Storyboard", "StoryboardSelfCheck",
    "parse_storyboard_llm_output", "self_check",
    "_SYSTEM",
]
