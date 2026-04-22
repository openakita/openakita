"""bgm-suggester — turn a scene + mood + duration into a BGM brief.

This is a *recommendation* engine, not a music renderer.  It produces:

* a structured ``BgmBrief`` (style / bpm / mood-arc / keywords / avoid)
* search queries that paste straight into YouTube / Spotify / Epidemic Sound
* a Suno-AI prompt that paste straight into Suno's "Custom" mode

The heavy work is the LLM call (driven by ``plugin.py``) — this module
only owns the prompt template, the 5-level fallback parser, the
``self_check`` quality gate, and the bridge exports.

Why a 5-level fallback (mirrors storyboard / video-translator):
LLM JSON is messy.  We accept, in order:

1. JSON object directly
2. Fenced ```json ... ``` block (delegated to SDK ``parse_llm_json_object``)
3. First balanced ``{...}`` substring (delegated)
4. Numbered-list / "key: value" lines fallback
5. Plain text → single-style stub

The last two are deterministic — they NEVER raise — so the worker thread
always has something to persist.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from openakita_plugin_sdk.contrib import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    Verification,
    parse_llm_json_object,
)

logger = logging.getLogger(__name__)


# ── data shape ─────────────────────────────────────────────────────────


# Shared style / tempo vocab — used by the LLM prompt so the model has
# something to anchor on, and by ``self_check`` to flag low-confidence
# briefs (out-of-vocab style) without blocking the user.
KNOWN_STYLES: tuple[str, ...] = (
    "lofi", "lofi hip-hop", "ambient", "cinematic", "epic", "trailer",
    "edm", "pop", "synthwave", "jazz", "blues", "classical", "rock",
    "indie", "acoustic", "folk", "country", "world", "asian traditional",
    "chinese traditional", "guzheng", "erhu", "j-pop", "k-pop", "anime",
    "8-bit", "chiptune", "corporate", "uplifting", "motivational",
    "sad", "melancholy", "romantic", "tense", "horror", "comedy",
)

KNOWN_TEMPO_LABELS: dict[str, tuple[int, int]] = {
    # label -> inclusive bpm range, used by ``self_check`` to flag
    # bpm/tempo_label mismatches (LLM sometimes says "fast" but emits 70 bpm).
    "very-slow": (40, 60),
    "slow": (60, 80),
    "midtempo": (80, 110),
    "upbeat": (110, 130),
    "fast": (130, 160),
    "very-fast": (160, 200),
}

# Hard clamp — we never persist bpm outside this range even if the LLM
# returns one (200+ is generally unusable as background music; 30- is
# inaudible as a beat).
BPM_HARD_MIN = 30
BPM_HARD_MAX = 220


@dataclass
class BgmBrief:
    """Structured BGM recommendation."""

    title: str
    target_duration_sec: float
    style: str
    tempo_bpm: int
    tempo_label: str
    mood_arc: list[str] = field(default_factory=list)
    energy_curve: list[float] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    instrument_hints: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelfCheck:
    """Quality gate output — ``passed=False`` is informational, not blocking.

    UI surfaces these as yellow warnings so the user can decide whether to
    re-roll or accept.  ``severity`` is "info" / "warning" / "blocker"
    (we never emit blocker today, but the field exists for forward-compat).
    """

    passed: bool
    issues: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "issues": list(self.issues)}


# ── prompts ────────────────────────────────────────────────────────────


_SYSTEM = """你是一位资深短视频/影视配乐导演（BGM director）。
任务：根据用户提供的场景描述、情绪、目标时长，产出一份**结构化的 BGM 简报**。

必须返回**严格 JSON**，键名固定如下（不允许省略；列表为空请用 `[]`）：

{
  "title": "string，简报标题（中文，不超过20字）",
  "style": "string，主风格（建议从 lofi / cinematic / synthwave / epic / ambient ... 等选）",
  "tempo_bpm": 整数 30-220,
  "tempo_label": "very-slow | slow | midtempo | upbeat | fast | very-fast",
  "mood_arc": ["string", ...]   // 情绪曲线，2~5 个阶段，例如 ["calm","build","drop"]
  "energy_curve": [0~1 的数字, ...]  // 与 mood_arc 一一对应
  "keywords": ["string", ...]   // 至少 3 个英文检索关键词，方便去 YouTube/Spotify 搜
  "avoid": ["string", ...]      // 应避免的元素，例如 ["heavy distortion","screaming vocals"]
  "instrument_hints": ["string", ...]   // 推荐乐器，例如 ["soft piano","808 drums"]
  "notes": "string，1-2 句导演备注"
}

只返回 JSON，不要附加任何说明或 markdown 代码栏。
"""


def build_user_prompt(
    *,
    scene: str,
    mood: str,
    duration_sec: float,
    tempo_hint: str = "",
    language: str = "auto",
) -> str:
    """Plain-text user prompt for the LLM call.

    Kept as a free function (not a method) so the same prompt can be
    used in tests without instantiating the plugin.
    """
    parts = [
        f"## 场景\n{scene.strip() or '(未提供)'}",
        f"## 情绪\n{mood.strip() or '(未提供)'}",
        f"## 目标时长\n{duration_sec:.1f} 秒",
    ]
    if tempo_hint.strip():
        parts.append(f"## 节拍偏好\n{tempo_hint.strip()}")
    if language and language != "auto":
        parts.append(f"## 语言偏好\n{language}")
    parts.append(
        "请基于以上信息生成 BGM 简报 JSON。tempo_label 必须与 tempo_bpm "
        "数值范围对应（slow=60-80, midtempo=80-110, upbeat=110-130, fast=130-160）。"
    )
    return "\n\n".join(parts)


# ── parse ──────────────────────────────────────────────────────────────


_NUMBERED_LIST_RE = re.compile(r"^\s*\d+[\.\)、]\s*(.+)$", re.MULTILINE)
_KEY_VALUE_RE = re.compile(
    r"^\s*[-*]?\s*([A-Za-z_\u4e00-\u9fa5]+)\s*[:：]\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_bgm_llm_output(
    text: str,
    *,
    fallback_title: str = "未命名 BGM 简报",
    fallback_duration: float = 30.0,
) -> BgmBrief:
    """Parse LLM output into a ``BgmBrief``.

    Always returns SOMETHING — the worst case is a single-style stub built
    from the raw text (level 5 fallback).  This is the contract the
    plugin worker relies on so a busted LLM response doesn't kill the task.
    """
    if not isinstance(text, str):
        text = str(text or "")

    # Levels 1-3: SDK contrib's robust JSON extraction.  ``errors`` collects
    # diagnostics but we ignore them at this level — the engine just needs
    # the data, the plugin owns logging.
    parsed = parse_llm_json_object(text, fallback={})
    if parsed:
        return _coerce_to_brief(parsed, fallback_title, fallback_duration)

    # Level 4: numbered-list / key-value bullet fallback.
    pairs = _extract_key_value_pairs(text)
    if pairs:
        return _coerce_to_brief(pairs, fallback_title, fallback_duration)

    # Level 5: plain-text stub — guaranteed non-empty so persistence
    # never fails.  We slap the raw text into ``notes`` so the user can
    # see "the LLM did say something, just not in the right shape."
    snippet = (text or "").strip()[:200] or "(LLM 未返回可解析内容)"
    return BgmBrief(
        title=fallback_title,
        target_duration_sec=fallback_duration,
        style="ambient",
        tempo_bpm=80,
        tempo_label="midtempo",
        mood_arc=["neutral"],
        energy_curve=[0.5],
        keywords=["ambient", "background", "calm"],
        avoid=[],
        instrument_hints=[],
        notes=f"stub fallback: {snippet}",
    )


def _extract_key_value_pairs(text: str) -> dict[str, Any]:
    """Best-effort: turn ``key: value`` bullet lines into a dict.

    Used only when JSON parsing fails completely.  Multi-value lines are
    split on commas / Chinese commas to recover keyword / mood lists.
    """
    out: dict[str, Any] = {}
    for m in _KEY_VALUE_RE.finditer(text):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if "," in val or "，" in val or "、" in val:
            out[key] = [v.strip() for v in re.split(r"[,，、]", val) if v.strip()]
        else:
            out[key] = val

    # numbered lists (e.g. "1. lofi" "2. chill") → keywords if no keys yet
    if "keywords" not in out:
        items = [m.group(1).strip() for m in _NUMBERED_LIST_RE.finditer(text)]
        if items:
            out["keywords"] = items
    return out


def _coerce_to_brief(
    raw: dict[str, Any],
    fallback_title: str,
    fallback_duration: float,
) -> BgmBrief:
    """Defensive coercion — every field is type-safe before building the
    dataclass.  Robust to LLMs that sometimes return numbers as strings or
    lists as comma-joined strings."""
    bpm = _coerce_bpm(raw.get("tempo_bpm") or raw.get("bpm"))
    label = _coerce_tempo_label(raw.get("tempo_label") or raw.get("tempo"), bpm)
    duration = _coerce_float(raw.get("target_duration_sec") or raw.get("duration"),
                              fallback_duration)
    return BgmBrief(
        title=str(raw.get("title") or fallback_title).strip()[:80] or fallback_title,
        target_duration_sec=duration,
        style=str(raw.get("style") or "ambient").strip()[:60] or "ambient",
        tempo_bpm=bpm,
        tempo_label=label,
        mood_arc=_coerce_str_list(raw.get("mood_arc") or raw.get("mood")),
        energy_curve=_coerce_float_list(raw.get("energy_curve")),
        keywords=_coerce_str_list(raw.get("keywords")),
        avoid=_coerce_str_list(raw.get("avoid")),
        instrument_hints=_coerce_str_list(
            raw.get("instrument_hints") or raw.get("instruments")
        ),
        notes=str(raw.get("notes") or "").strip()[:300],
    )


def _coerce_bpm(v: Any) -> int:
    try:
        bpm = int(float(v))
    except (TypeError, ValueError):
        return 80
    return max(BPM_HARD_MIN, min(BPM_HARD_MAX, bpm))


def _coerce_tempo_label(v: Any, bpm: int) -> str:
    label = (str(v).strip().lower() if v is not None else "")
    if label in KNOWN_TEMPO_LABELS:
        return label
    # No / unknown label → derive from bpm so the brief is still consistent.
    for name, (lo, hi) in KNOWN_TEMPO_LABELS.items():
        if lo <= bpm <= hi:
            return name
    return "midtempo"


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in re.split(r"[,，、;；\n]", v) if s.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def _coerce_float_list(v: Any) -> list[float]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out: list[float] = []
        for x in v:
            try:
                out.append(max(0.0, min(1.0, float(x))))
            except (TypeError, ValueError):
                continue
        return out
    return []


# ── self check ─────────────────────────────────────────────────────────


def self_check(brief: BgmBrief) -> SelfCheck:
    """Quality gate — all issues are informational, never blocking.

    Surfaced in UI as yellow warnings so the user can decide whether to
    re-roll or accept.  Mirrors storyboard's ``self_check`` shape so
    downstream UI code can reuse the same render logic.
    """
    issues: list[dict[str, str]] = []

    if not brief.style.strip():
        issues.append({"severity": "warning",
                        "code": "missing_style",
                        "message": "style is empty"})

    if len(brief.keywords) < 3:
        issues.append({"severity": "warning",
                        "code": "few_keywords",
                        "message": f"only {len(brief.keywords)} keywords (recommend ≥ 3)"})

    rng = KNOWN_TEMPO_LABELS.get(brief.tempo_label)
    if rng and not (rng[0] <= brief.tempo_bpm <= rng[1]):
        issues.append({"severity": "warning",
                        "code": "bpm_label_mismatch",
                        "message": (f"tempo_label={brief.tempo_label} suggests "
                                    f"{rng[0]}-{rng[1]} bpm but got {brief.tempo_bpm}")})

    if brief.mood_arc and brief.energy_curve and \
            len(brief.mood_arc) != len(brief.energy_curve):
        issues.append({"severity": "info",
                        "code": "arc_curve_length",
                        "message": (f"mood_arc length={len(brief.mood_arc)} != "
                                    f"energy_curve length={len(brief.energy_curve)}")})

    return SelfCheck(passed=not issues, issues=issues)


# ── exports / bridges ─────────────────────────────────────────────────


def to_csv(brief: BgmBrief) -> str:
    """Single-row CSV with stable column order — for spreadsheet handoff."""
    cols = ["title", "duration_sec", "style", "tempo_bpm", "tempo_label",
            "mood_arc", "energy_curve", "keywords", "avoid", "instruments", "notes"]
    row = [
        _csv_safe(brief.title),
        f"{brief.target_duration_sec:.1f}",
        _csv_safe(brief.style),
        str(brief.tempo_bpm),
        _csv_safe(brief.tempo_label),
        _csv_safe(" / ".join(brief.mood_arc)),
        _csv_safe(",".join(f"{x:.2f}" for x in brief.energy_curve)),
        _csv_safe(", ".join(brief.keywords)),
        _csv_safe(", ".join(brief.avoid)),
        _csv_safe(", ".join(brief.instrument_hints)),
        _csv_safe(brief.notes),
    ]
    return ",".join(cols) + "\n" + ",".join(row) + "\n"


def to_search_queries(brief: BgmBrief) -> dict[str, Any]:
    """Build paste-ready search strings for the major BGM platforms.

    Each query is *intentionally* built differently to match each
    platform's discovery patterns:

    * YouTube — natural language + duration hint (helps shorts results)
    * Spotify — terse, comma-tagged, bpm-included (matches their mood
      playlist tags)
    * Epidemic Sound — bpm + style + duration (their search bar is bpm-aware)
    * Artlist — style + mood + length range
    """
    style = brief.style.strip() or "ambient"
    bpm = brief.tempo_bpm
    duration = int(round(brief.target_duration_sec))
    primary_mood = (brief.mood_arc[0] if brief.mood_arc else "").strip()
    keywords_short = ", ".join(brief.keywords[:5])

    return {
        "youtube": (
            f"{style} {primary_mood} instrumental {duration} second"
            if primary_mood else
            f"{style} instrumental {duration} second"
        ).strip(),
        "spotify": (
            f"{style}, {keywords_short}, {bpm} bpm"
            if keywords_short else
            f"{style}, {bpm} bpm"
        ),
        "epidemic_sound": f"{style} {bpm}bpm {duration}s",
        "artlist": (
            f"{style} {primary_mood} {duration}s"
            if primary_mood else
            f"{style} {duration}s"
        ).strip(),
    }


def to_suno_prompt(brief: BgmBrief) -> dict[str, str]:
    """Turn a brief into a Suno AI "Custom" mode prompt + style tags.

    Suno's input has two boxes:
      - ``style``: comma-separated tags (max ~120 chars works best)
      - ``description``: prose describing the song

    The output is a flat dict so the UI can copy-paste each field
    directly.  Empty fields are dropped — Suno rejects empty styles.
    """
    style_tags = [brief.style] + brief.keywords[:5] + brief.instrument_hints[:3]
    seen: set[str] = set()
    deduped: list[str] = []
    for tag in style_tags:
        t = tag.strip().lower()
        if t and t not in seen:
            seen.add(t)
            deduped.append(tag.strip())
    style_str = ", ".join(deduped)[:120]

    arc = " → ".join(brief.mood_arc) if brief.mood_arc else "calm"
    desc_parts = [
        f"A {brief.tempo_bpm} bpm {brief.tempo_label} instrumental",
        f"with {arc} energy",
    ]
    if brief.target_duration_sec:
        desc_parts.append(f"around {int(round(brief.target_duration_sec))} seconds long")
    if brief.avoid:
        desc_parts.append("Avoid: " + ", ".join(brief.avoid[:3]))
    description = ". ".join(desc_parts) + "."

    return {"style": style_str, "description": description}


def to_verification(brief: BgmBrief, check: SelfCheck) -> Verification:
    """Sprint 9 / D2.10 — translate the BGM ``SelfCheck`` issues into the
    SDK's standard :class:`Verification` envelope so the host UI can
    render the same green/yellow/red trust badge it uses for slides,
    storyboards and other structured output.

    Mapping rules (intentionally narrow — only flag what a verifier
    could actually disagree on):

    * ``bpm_label_mismatch``  → flag ``$.brief.tempo_bpm`` with
      :data:`KIND_NUMBER` (the LLM's most common slip-up: "fast" label
      with a 70 bpm number).
    * ``few_keywords``        → flag ``$.brief.keywords`` with
      :data:`KIND_OTHER` (informational; UI shows yellow but no edit
      affordance).
    * ``missing_style``       → flag ``$.brief.style`` (KIND_OTHER).
    * ``arc_curve_length``    → flag ``$.brief.energy_curve``
      (KIND_NUMBER, since the curve is numeric).

    Other issue codes are ignored — they are deliberate "info" notes
    from ``self_check`` and would inflate the badge without giving the
    user a real action to take.

    The ``verifier_id`` is fixed to ``"self_check"`` because this is a
    rule-based checker, not a second model.  Plugins that later add a
    real second-model verifier should compose the result with
    :func:`merge_verifications` so both signals end up on the badge.
    """
    fields: list[LowConfidenceField] = []
    code_map = {
        "bpm_label_mismatch": ("$.brief.tempo_bpm", brief.tempo_bpm, KIND_NUMBER),
        "few_keywords": ("$.brief.keywords", list(brief.keywords), KIND_OTHER),
        "missing_style": ("$.brief.style", brief.style, KIND_OTHER),
        "arc_curve_length": (
            "$.brief.energy_curve", list(brief.energy_curve), KIND_NUMBER,
        ),
    }
    for issue in check.issues:
        code = issue.get("code", "")
        mapped = code_map.get(code)
        if not mapped:
            continue
        path, value, kind = mapped
        fields.append(LowConfidenceField(
            path=path,
            value=value,
            kind=kind,
            reason=issue.get("message", ""),
        ))

    notes = "" if check.passed else f"{len(check.issues)} self-check issue(s)"
    return Verification(
        verified=check.passed and not fields,
        verifier_id="self_check",
        low_confidence_fields=fields,
        notes=notes,
    )


def to_export_payload(brief: BgmBrief, check: SelfCheck) -> dict[str, Any]:
    """One-stop bundle that the ``/tasks/{id}/export-all.json`` route
    returns — folds the brief, self-check, all bridges and the D2.10
    verification badge into a single JSON envelope so consumers don't
    have to glue 5 endpoints together.
    """
    return {
        "brief": brief.to_dict(),
        "self_check": check.to_dict(),
        "verification": to_verification(brief, check).to_dict(),
        "search_queries": to_search_queries(brief),
        "suno": to_suno_prompt(brief),
        "csv": to_csv(brief),
    }


def _csv_safe(s: str) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ")
    if any(c in s for c in (",", '"')):
        s = '"' + s.replace('"', '""') + '"'
    return s


# ── stub LLM output (used when no brain is available) ────────────────


def stub_brief_text(*, scene: str, mood: str, duration_sec: float) -> str:
    """Deterministic stub the parser will accept — used by ``plugin.py``
    when ``brain.think_lightweight`` is not installed.  Lives here (not
    in plugin.py) so tests can exercise it without spinning up the
    full plugin lifecycle."""
    bpm = 80 if "calm" in (mood or "").lower() or "chill" in (mood or "").lower() else 110
    label = "midtempo" if bpm <= 110 else "upbeat"
    return json.dumps({
        "title": f"BGM 简报：{(scene or 'untitled')[:20]}",
        "style": "ambient",
        "tempo_bpm": bpm,
        "tempo_label": label,
        "mood_arc": ["calm", "build"],
        "energy_curve": [0.4, 0.8],
        "keywords": ["ambient", "background", "calm", "soft"],
        "avoid": ["heavy distortion"],
        "instrument_hints": ["soft piano", "synth pad"],
        "notes": f"stub brief (no brain). duration={duration_sec:.1f}s",
    }, ensure_ascii=False)


__all__ = [
    "BPM_HARD_MAX",
    "BPM_HARD_MIN",
    "BgmBrief",
    "KNOWN_STYLES",
    "KNOWN_TEMPO_LABELS",
    "SelfCheck",
    "build_user_prompt",
    "parse_bgm_llm_output",
    "self_check",
    "stub_brief_text",
    "to_csv",
    "to_export_payload",
    "to_search_queries",
    "to_suno_prompt",
    "to_verification",
    "_SYSTEM",
]
