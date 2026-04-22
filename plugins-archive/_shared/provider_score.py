"""ProviderScore — 7-dimensional weighted scoring for picking a vendor.

Verbatim from OpenMontage (audit3 C0.5 corrected weights):

| dimension       | weight |
|-----------------|--------|
| quality         | 0.30   |
| speed           | 0.20   |
| cost            | 0.15   |
| reliability     | 0.15   |
| control         | 0.10   |
| latency         | 0.05   |
| compatibility   | 0.05   |

Each dimension is normalized to [0, 1] before weighting.  Use
:func:`score_providers` to rank a list of provider candidates.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_WEIGHTS: dict[str, float] = {
    "quality": 0.30,
    "speed": 0.20,
    "cost": 0.15,
    "reliability": 0.15,
    "control": 0.10,
    "latency": 0.05,
    "compatibility": 0.05,
}

_DIMENSIONS = tuple(_WEIGHTS.keys())


@dataclass(frozen=True)
class ProviderScore:
    """Computed score for one provider."""

    provider_id: str
    total: float                             # weighted sum, [0, 1]
    dimensions: dict[str, float] = field(default_factory=dict)  # raw [0,1] per dim
    weights: dict[str, float] = field(default_factory=lambda: dict(_WEIGHTS))
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "total": round(self.total, 4),
            "dimensions": {k: round(v, 4) for k, v in self.dimensions.items()},
            "weights": dict(self.weights),
            "notes": list(self.notes),
        }


def score_providers(
    candidates: Iterable[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    must_have: tuple[str, ...] = (),
) -> list[ProviderScore]:
    """Score and rank providers (descending).

    Args:
        candidates: Iterable of dicts with at least ``id`` and the 7 dimension
            keys (any missing dim is treated as 0.0; values clamped to [0,1]).
        weights: Override the default weights (must include all 7 dims; the
            sum is renormalized to 1.0 for safety).
        must_have: Dimension keys that must be > 0.  Candidates failing this
            are scored 0.0 with a note.

    Returns:
        List of :class:`ProviderScore` sorted by ``total`` descending.
    """
    w = dict(_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in _WEIGHTS})
        s = sum(w.values()) or 1.0
        w = {k: v / s for k, v in w.items()}

    out: list[ProviderScore] = []
    for c in candidates or []:
        cid = str(c.get("id") or c.get("provider_id") or "?")
        dims: dict[str, float] = {}
        for k in _DIMENSIONS:
            v = c.get(k, 0.0)
            try:
                fv = float(v)
            except (TypeError, ValueError):
                fv = 0.0
            dims[k] = max(0.0, min(1.0, fv))

        notes: list[str] = []
        zeroed = False
        for k in must_have:
            if dims.get(k, 0.0) <= 0.0:
                notes.append(f"must_have '{k}' missing — disqualified")
                zeroed = True

        total = 0.0 if zeroed else sum(dims[k] * w[k] for k in _DIMENSIONS)
        out.append(ProviderScore(provider_id=cid, total=total, dimensions=dims, weights=w, notes=notes))

    out.sort(key=lambda p: p.total, reverse=True)
    return out
