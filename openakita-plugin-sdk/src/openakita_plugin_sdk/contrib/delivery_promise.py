"""DeliveryPromise — validate that the cuts honor the user's expectation.

OpenMontage pattern (corrected per audit3 N1.5: motion_ratio default 0.3,
not 0.5 — beginner videos are typically slower).

Use case: after assembly, compare actual ratio of "moving" shots vs total
duration against the promise made during onboarding.  If the user asked
for "动感剪辑" but only 25% of the cuts have motion, surface a warning so
they don't feel cheated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeliveryPromise:
    """Outcome of one ``validate_cuts()`` call."""

    promised_motion_ratio: float
    actual_motion_ratio: float
    delta: float                          # actual - promised
    verdict: str                          # "honored" | "almost" | "broken"
    motion_seconds: float
    total_seconds: float
    breached_segments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promised_motion_ratio": round(self.promised_motion_ratio, 3),
            "actual_motion_ratio": round(self.actual_motion_ratio, 3),
            "delta": round(self.delta, 3),
            "verdict": self.verdict,
            "motion_seconds": round(self.motion_seconds, 2),
            "total_seconds": round(self.total_seconds, 2),
            "breached_segments": list(self.breached_segments),
        }


def validate_cuts(
    cuts: Iterable[dict[str, Any]],
    *,
    promised_motion_ratio: float = 0.3,
    motion_field: str = "has_motion",
    duration_field: str = "duration",
    label_field: str = "label",
    tolerance: float = 0.05,
) -> DeliveryPromise:
    """Compute actual motion ratio and compare to promise.

    Each cut dict should provide:

    - ``has_motion`` (bool, or callable / object with truthiness)
    - ``duration`` (seconds, float)
    - optional ``label`` for diagnostics

    Verdict rules:

    - ``actual >= promised``               → ``"honored"``
    - ``actual >= promised - tolerance``   → ``"almost"``
    - else                                 → ``"broken"``
    """
    cuts = list(cuts or [])
    total = 0.0
    moving = 0.0
    breached: list[str] = []

    for c in cuts:
        if not isinstance(c, dict):
            continue
        d = _safe_float(c.get(duration_field))
        if d is None or d <= 0:
            continue
        total += d
        if bool(c.get(motion_field)):
            moving += d
        else:
            label = c.get(label_field) or c.get("id") or f"<unnamed:{int(total)}s>"
            breached.append(str(label))

    actual = (moving / total) if total > 0 else 0.0
    delta = actual - promised_motion_ratio

    if actual >= promised_motion_ratio:
        verdict = "honored"
    elif actual >= promised_motion_ratio - tolerance:
        verdict = "almost"
    else:
        verdict = "broken"

    return DeliveryPromise(
        promised_motion_ratio=promised_motion_ratio,
        actual_motion_ratio=actual,
        delta=delta,
        verdict=verdict,
        motion_seconds=moving,
        total_seconds=total,
        breached_segments=breached if verdict == "broken" else [],
    )


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
