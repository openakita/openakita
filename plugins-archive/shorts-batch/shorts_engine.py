"""shorts-batch — pure-logic core.

A "short brief" is the user-facing unit: ``{topic, duration_sec,
style?, target_aspect?, language?}``.  The engine turns each brief
into a deterministic pipeline:

1. **expand** — call a user-supplied "scene planner" (LLM or heuristic
   stub) to turn the brief into a list of shot dicts.  Tests inject a
   plain function so we never hit a real LLM.
2. **risk-score** — pass the scene plan through the SDK's
   :func:`evaluate_slideshow_risk` (D2.1).  We surface the verdict
   ("low" / "medium" / "high") **before** any rendering happens; the
   plugin's brain tool ``shorts_batch_preview_risk`` exposes this so
   the agent can ask the user "do you want to spend quota on this
   high-risk plan?".
3. **render** — hand the plan + brief to a user-supplied "renderer"
   (production: seedance-video / ppt-to-video; tests: stub returning a
   zero-byte file).
4. **aggregate** — collect per-brief results, total cost, risk
   distribution, success/failure counts.
5. **verify** — D2.10 envelope.

All side-effecting deps (planner, renderer) are passed in so the
plugin can swap them per request without the engine ever importing
``openai`` or ``ffmpeg``.
"""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable

from _shared import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    SlideshowRisk,
    Verification,
    evaluate_slideshow_risk,
)

__all__ = [
    "ALLOWED_ASPECTS",
    "DEFAULT_MAX_PARALLEL",
    "DEFAULT_MIN_SHOTS",
    "DEFAULT_MAX_SHOTS",
    "ShortBrief",
    "ShortPlan",
    "ShortResult",
    "BatchResult",
    "default_scene_planner",
    "estimate_cost",
    "plan_brief",
    "plan_briefs",
    "run_brief",
    "run_briefs",
    "to_verification",
]


ALLOWED_ASPECTS: tuple[str, ...] = ("9:16", "1:1", "16:9", "4:5")
DEFAULT_MIN_SHOTS = 3
DEFAULT_MAX_SHOTS = 12
DEFAULT_MAX_PARALLEL = 2

# ── Models ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShortBrief:
    """User-facing input unit: one short to generate."""

    topic: str
    duration_sec: float = 15.0
    style: str = "vlog"
    target_aspect: str = "9:16"
    language: str = "zh-CN"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "duration_sec": self.duration_sec,
            "style": self.style,
            "target_aspect": self.target_aspect,
            "language": self.language,
            "extra": dict(self.extra),
        }


@dataclass
class ShortPlan:
    """Frozen plan for one short — brief + scene plan + risk verdict."""

    brief: ShortBrief
    scene_plan: list[dict[str, Any]]
    risk: SlideshowRisk
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief": self.brief.to_dict(),
            "scene_plan": list(self.scene_plan),
            "risk": self.risk.to_dict(),
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
        }


@dataclass
class ShortResult:
    """What rendering one short produced."""

    plan: ShortPlan
    output_path: str | None
    elapsed_sec: float
    bytes_total: int
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.output_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "output_path": self.output_path,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "bytes_total": self.bytes_total,
            "error": self.error,
            "succeeded": self.succeeded,
        }


@dataclass
class BatchResult:
    """Aggregated batch output."""

    results: list[ShortResult]
    risk_distribution: dict[str, int]
    succeeded: int
    failed: int
    elapsed_sec: float
    total_cost_usd: float

    @property
    def total(self) -> int:
        return len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "total_cost_usd": round(self.total_cost_usd, 4),
            "risk_distribution": dict(self.risk_distribution),
            "results": [r.to_dict() for r in self.results],
        }


# ── Cost estimator ────────────────────────────────────────────────────


def estimate_cost(scene_plan: list[dict[str, Any]], duration_sec: float) -> float:
    """Cheap per-brief cost projection.

    Used pre-render so the agent / UI can show "this batch will cost
    ~ $1.23 — proceed?".  Numbers are deliberately conservative; real
    integrators should override via ``cost_estimator=`` on
    :func:`run_briefs`.

    Heuristic: $0.02 per shot + $0.01 per second of target duration.
    """
    n_shots = len(scene_plan or [])
    return max(0.0, n_shots * 0.02 + duration_sec * 0.01)


# ── Default scene planner (deterministic stub) ────────────────────────


def default_scene_planner(brief: ShortBrief) -> list[dict[str, Any]]:
    """Stub planner used when no LLM planner is supplied.

    Produces a deterministic plan that *intentionally* scores
    "medium" on slideshow_risk so default-mode batches surface a
    yellow flag — that's the correct UX prompt: "you didn't supply a
    real planner; the heuristic plan is at risk of feeling like a
    slideshow".
    """
    n_shots = max(DEFAULT_MIN_SHOTS, min(DEFAULT_MAX_SHOTS,
                                          round(brief.duration_sec / 3.0)))
    per_shot = max(1.5, brief.duration_sec / n_shots)
    plan: list[dict[str, Any]] = []
    for i in range(n_shots):
        plan.append({
            "index": i + 1,
            "shot_type": ["wide", "medium", "close-up"][i % 3],
            "duration": per_shot,
            "description": f"{brief.topic} — shot {i + 1}",
            "transition": "cut",
        })
    return plan


# ── plan_brief / plan_briefs ──────────────────────────────────────────


def _validate_brief(b: ShortBrief) -> None:
    if not b.topic or not str(b.topic).strip():
        raise ValueError("brief.topic is required and must not be empty")
    if not (1.0 <= b.duration_sec <= 600.0):
        raise ValueError(
            f"brief.duration_sec must be in [1, 600], got {b.duration_sec!r}",
        )
    if b.target_aspect not in ALLOWED_ASPECTS:
        raise ValueError(
            f"brief.target_aspect must be one of {list(ALLOWED_ASPECTS)}, "
            f"got {b.target_aspect!r}",
        )


def plan_brief(
    brief: ShortBrief,
    *,
    scene_planner: Callable[[ShortBrief], list[dict[str, Any]]] | None = None,
    cost_estimator: Callable[[list[dict[str, Any]], float], float] | None = None,
    min_shots: int = DEFAULT_MIN_SHOTS,
    max_shots: int = DEFAULT_MAX_SHOTS,
) -> ShortPlan:
    """Validate the brief, run the planner, score risk, estimate cost."""
    _validate_brief(brief)
    if min_shots < 1 or max_shots < min_shots:
        raise ValueError(
            f"min_shots ({min_shots}) / max_shots ({max_shots}) invalid",
        )
    planner = scene_planner or default_scene_planner
    estimator = cost_estimator or estimate_cost

    scene_plan = list(planner(brief) or [])
    if len(scene_plan) < min_shots:
        raise ValueError(
            f"scene_plan has {len(scene_plan)} shots, below min_shots={min_shots}",
        )
    if len(scene_plan) > max_shots:
        # Trim instead of erroring — the LLM may overshoot but the
        # first N shots usually carry the gist.
        scene_plan = scene_plan[:max_shots]

    risk = evaluate_slideshow_risk(scene_plan)
    cost = estimator(scene_plan, brief.duration_sec)
    return ShortPlan(
        brief=brief, scene_plan=scene_plan, risk=risk, estimated_cost_usd=cost,
    )


def plan_briefs(
    briefs: Iterable[ShortBrief],
    *,
    scene_planner: Callable[[ShortBrief], list[dict[str, Any]]] | None = None,
    cost_estimator: Callable[[list[dict[str, Any]], float], float] | None = None,
    min_shots: int = DEFAULT_MIN_SHOTS,
    max_shots: int = DEFAULT_MAX_SHOTS,
) -> list[ShortPlan]:
    return [
        plan_brief(b, scene_planner=scene_planner, cost_estimator=cost_estimator,
                   min_shots=min_shots, max_shots=max_shots)
        for b in (briefs or [])
    ]


# ── render dispatch ──────────────────────────────────────────────────


def run_brief(
    plan: ShortPlan,
    *,
    renderer: Callable[[ShortPlan], tuple[str, int]],
    risk_block_threshold: str | None = None,
) -> ShortResult:
    """Render one short via the user-supplied ``renderer``.

    Args:
        renderer: ``(plan) -> (output_path, bytes_total)``.  May raise
            — exceptions are caught and surfaced as ``ShortResult.error``
            so a single bad short never poisons the whole batch.
        risk_block_threshold: If set to ``"high"`` (or ``"medium"``),
            briefs whose ``risk.verdict`` is at or above the threshold
            are *skipped* (returned as a failed result with a clear
            ``error`` message).  Use this to enforce "no high-risk
            renders without an explicit override".
    """
    if risk_block_threshold and _verdict_at_or_above(
        plan.risk.verdict, risk_block_threshold,
    ):
        return ShortResult(
            plan=plan, output_path=None, elapsed_sec=0.0, bytes_total=0,
            error=(
                f"slideshow_risk verdict '{plan.risk.verdict}' "
                f"meets/exceeds block threshold '{risk_block_threshold}' — "
                "render skipped"
            ),
        )

    started = time.monotonic()
    try:
        out_path, n_bytes = renderer(plan)
    except Exception as exc:  # noqa: BLE001
        return ShortResult(
            plan=plan, output_path=None,
            elapsed_sec=time.monotonic() - started,
            bytes_total=0, error=f"renderer raised: {exc!r}",
        )
    elapsed = time.monotonic() - started
    return ShortResult(
        plan=plan, output_path=str(out_path),
        elapsed_sec=elapsed, bytes_total=int(n_bytes),
    )


_VERDICT_RANK = {"low": 0, "medium": 1, "high": 2}


def _verdict_at_or_above(actual: str, threshold: str) -> bool:
    return _VERDICT_RANK.get(actual, -1) >= _VERDICT_RANK.get(threshold, 99)


def run_briefs(
    plans: Iterable[ShortPlan],
    *,
    renderer: Callable[[ShortPlan], tuple[str, int]],
    risk_block_threshold: str | None = None,
    on_progress: Callable[[int, int, ShortResult], None] | None = None,
) -> BatchResult:
    """Render every plan sequentially (one at a time, deterministic).

    Sequential is intentional: most renderers (seedance-video, ComfyUI)
    are GPU-bound and parallelising at this layer would cause queue
    contention.  Use the renderer's own concurrency knob if you need
    parallel work.
    """
    started = time.monotonic()
    plans_list = list(plans or [])
    results: list[ShortResult] = []
    distribution: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    total_cost = 0.0
    for i, plan in enumerate(plans_list):
        distribution[plan.risk.verdict] = distribution.get(plan.risk.verdict, 0) + 1
        total_cost += plan.estimated_cost_usd
        result = run_brief(
            plan, renderer=renderer, risk_block_threshold=risk_block_threshold,
        )
        results.append(result)
        if on_progress is not None:
            on_progress(i + 1, len(plans_list), result)

    succeeded = sum(1 for r in results if r.succeeded)
    failed = sum(1 for r in results if not r.succeeded)
    return BatchResult(
        results=results, risk_distribution=distribution,
        succeeded=succeeded, failed=failed,
        elapsed_sec=time.monotonic() - started,
        total_cost_usd=total_cost,
    )


# ── verification (D2.10) ──────────────────────────────────────────────


def to_verification(batch: BatchResult) -> Verification:
    """D2.10 envelope.

    Yellow flags:
      * any short failed,
      * majority of plans scored "high" risk,
      * total bytes 0 (renderer wrote nothing despite reporting success).
    """
    fields: list[LowConfidenceField] = []

    if batch.failed > 0:
        fields.append(LowConfidenceField(
            path="$.failed",
            value=batch.failed,
            kind=KIND_NUMBER,
            reason=(
                f"{batch.failed}/{batch.total} shorts failed during render — "
                "check per-result error messages"
            ),
        ))

    high_count = batch.risk_distribution.get("high", 0)
    if batch.total > 0 and high_count * 2 > batch.total:
        fields.append(LowConfidenceField(
            path="$.risk_distribution.high",
            value=high_count,
            kind=KIND_NUMBER,
            reason=(
                f"majority of plans ({high_count}/{batch.total}) scored "
                "'high' on slideshow_risk — outputs may feel like static "
                "slideshows; refine the scene planner prompt"
            ),
        ))

    bytes_total = sum(r.bytes_total for r in batch.results)
    if batch.succeeded > 0 and bytes_total == 0:
        fields.append(LowConfidenceField(
            path="$.results[*].bytes_total",
            value=0,
            kind=KIND_OTHER,
            reason=(
                "renderer reported success but every output is 0 bytes — "
                "likely a stub/dry-run renderer is wired up"
            ),
        ))

    return Verification(
        verified=not fields,
        verifier_id="shorts_batch_self_check",
        low_confidence_fields=fields,
    )
