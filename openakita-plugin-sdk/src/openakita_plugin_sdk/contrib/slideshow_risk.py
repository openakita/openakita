"""SlideshowRisk — heuristic detector for "static slideshow disguised as video".

Replicates OpenMontage's ``slideshow_risk.py`` (corrected per audit3 C0.3:
this is **structural heuristic** based on the scene-plan dict, NOT video
signal analysis).  No ffprobe / opencv dependency.

Use case: after the LLM produces a scene plan / shot list / cut list,
score it on 6 dimensions; if too few dimensions show motion-like signal,
warn the user that the output may feel like a slideshow.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlideshowRisk:
    """Verdict for one scene plan.

    Attributes:
        score: Dimensions-with-motion (0..6).
        verdict: ``"low" | "medium" | "high"`` risk of feeling like a slideshow.
            Thresholds (audit3 default): ``score < 2 → high``, ``< 3 →
            medium``, ``< 4 → still medium``, ``>= 4 → low``.
        signals: Per-dimension boolean (True = has motion).
        reasons: Per-dimension human-readable evidence (matched or missing).
    """

    score: int
    verdict: str
    signals: dict[str, bool] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "signals": dict(self.signals),
            "reasons": dict(self.reasons),
        }


_MOTION_KEYWORDS = (
    "推", "拉", "摇", "移", "跟", "升降", "环绕",
    "pan", "tilt", "zoom", "dolly", "track", "crane", "orbit", "handheld", "shake",
)
_SUBJECT_ACTION_KEYWORDS = (
    "走", "跑", "跳", "转身", "挥", "举", "推开", "抓",
    "walk", "run", "jump", "turn", "wave", "lift", "push", "grab",
)
_ENVIRONMENT_KEYWORDS = (
    "风", "雨", "雪", "落叶", "光斑", "尘埃", "雾", "粒子", "火苗",
    "wind", "rain", "snow", "leaves", "particles", "dust", "fog", "ember",
)


def evaluate_slideshow_risk(
    scene_plan: Iterable[dict[str, Any]],
    *,
    motion_keywords: Iterable[str] = _MOTION_KEYWORDS,
    subject_action_keywords: Iterable[str] = _SUBJECT_ACTION_KEYWORDS,
    environment_keywords: Iterable[str] = _ENVIRONMENT_KEYWORDS,
) -> SlideshowRisk:
    """Score a scene plan on 6 motion dimensions.

    The 6 dimensions are:

    1. **camera_motion** — any scene mentions camera movement
    2. **subject_action** — any scene has the subject doing an active verb
    3. **environment_motion** — wind / rain / particles / etc.
    4. **shot_variety** — at least 3 distinct shot types in the plan
    5. **transition_variety** — at least 2 transition styles (cut, dissolve, …)
    6. **avg_duration_short_enough** — average shot duration ≤ 6 seconds
       (longer shots feel more like static slides)

    Each dimension contributes 1 point if satisfied.  Verdict thresholds
    (audit3 C0.3): ``<2 → high``, ``<4 → medium``, ``>=4 → low``.
    """
    plan = list(scene_plan or [])
    if not plan:
        return SlideshowRisk(
            score=0, verdict="high",
            signals=dict.fromkeys(_DIMENSIONS, False),
            reasons=dict.fromkeys(_DIMENSIONS, "scene plan is empty"),
        )

    signals: dict[str, bool] = dict.fromkeys(_DIMENSIONS, False)
    reasons: dict[str, str] = {}

    # 1. camera_motion
    found = _first_match(plan, ("camera", "camera_motion", "lens", "shot"), motion_keywords) \
        or _first_match(plan, ("description", "desc", "prompt"), motion_keywords)
    signals["camera_motion"] = bool(found)
    reasons["camera_motion"] = found or "no camera-movement verbs detected"

    # 2. subject_action
    found = _first_match(plan, ("subject", "action", "description", "desc", "prompt"), subject_action_keywords)
    signals["subject_action"] = bool(found)
    reasons["subject_action"] = found or "no active subject verbs detected"

    # 3. environment_motion
    found = _first_match(plan, ("environment", "background", "description", "desc", "prompt"), environment_keywords)
    signals["environment_motion"] = bool(found)
    reasons["environment_motion"] = found or "no environment motion cues detected"

    # 4. shot_variety
    shot_types = {str(s.get("shot_type") or s.get("shot") or "").lower() for s in plan if s}
    shot_types.discard("")
    signals["shot_variety"] = len(shot_types) >= 3
    reasons["shot_variety"] = f"distinct shot_type count = {len(shot_types)}"

    # 5. transition_variety
    transitions = {str(s.get("transition") or "").lower() for s in plan if s}
    transitions.discard("")
    signals["transition_variety"] = len(transitions) >= 2
    reasons["transition_variety"] = f"distinct transition count = {len(transitions)}"

    # 6. avg_duration_short_enough
    durations = [_safe_float(s.get("duration")) for s in plan if s]
    durations = [d for d in durations if d is not None and d > 0]
    avg = sum(durations) / len(durations) if durations else 0.0
    signals["avg_duration_short_enough"] = bool(durations) and avg <= 6.0
    reasons["avg_duration_short_enough"] = f"avg duration = {avg:.2f}s (threshold 6.0s)"

    score = sum(1 for v in signals.values() if v)
    if score < 2:
        verdict = "high"
    elif score < 4:
        verdict = "medium"
    else:
        verdict = "low"

    return SlideshowRisk(score=score, verdict=verdict, signals=signals, reasons=reasons)


_DIMENSIONS = (
    "camera_motion",
    "subject_action",
    "environment_motion",
    "shot_variety",
    "transition_variety",
    "avg_duration_short_enough",
)


def _first_match(
    plan: list[dict[str, Any]],
    fields: Iterable[str],
    keywords: Iterable[str],
) -> str:
    """Return the first matched ``"field=keyword"`` evidence string."""
    fields = tuple(fields)
    keywords = tuple(k.lower() for k in keywords)
    for s in plan:
        if not isinstance(s, dict):
            continue
        for f in fields:
            v = s.get(f)
            if not isinstance(v, str):
                continue
            lower = v.lower()
            for k in keywords:
                if k in lower:
                    return f"{f} mentions '{k}'"
    return ""


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
