"""CostEstimator — produce a {low, high, sample_cost, confidence} preview.

Inspired by OpenMontage ``cost_tracker.estimate_from_reference()``.  The
preview is intentionally vendor-agnostic — plugins register pricing tables
and the estimator does the math + retry/safety buffer.

Design choices:

- Numbers are returned in **vendor-native units** (e.g. CNY / USD); the
  caller (plugin) is responsible for currency formatting.
- A separate ``to_human_units(cost, locale)`` helper translates a raw cost
  into AnyGen-style readable text (e.g.
  "≈ 10 张图片 / 200 credits ≈ 一次短文档生成").
- ``retry_multiplier`` defaults to 1.10 — meaning we assume up to ~10%
  retried calls; the **upper bound** then adds another 15% buffer (= 1.265
  effective multiplier) so users are not surprised.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CostBreakdown:
    """Per-item cost breakdown row."""

    label: str           # e.g. "Seedance 2.0  720p  5s"
    units: float         # e.g. 5  (seconds, images, requests, ...)
    unit_label: str      # e.g. "s"
    unit_price: float    # vendor unit price
    subtotal: float      # units * unit_price


@dataclass(frozen=True)
class CostPreview:
    """User-facing preview block.

    Attributes:
        low: Best-case total (no retries, no buffer).
        high: Worst-case total (with retries + 15% safety margin).
        sample_cost: A representative single-shot cost (e.g. one image).
        confidence: ``"high" | "medium" | "low"`` — quantitative reliability.
        currency: 3-letter code (``CNY``/``USD``) or ``"credit"``.
        breakdown: Per-item details (caller can render as table).
        notes: Free-form notes (e.g. "本次预览不含通义万相高清后处理").
        human_label: Optional AnyGen-style readable translation.
    """

    low: float
    high: float
    sample_cost: float
    confidence: str
    currency: str
    breakdown: list[CostBreakdown] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    human_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "sample_cost": round(self.sample_cost, 4),
            "confidence": self.confidence,
            "currency": self.currency,
            "breakdown": [
                {
                    "label": b.label,
                    "units": b.units,
                    "unit_label": b.unit_label,
                    "unit_price": b.unit_price,
                    "subtotal": round(b.subtotal, 4),
                }
                for b in self.breakdown
            ],
            "notes": list(self.notes),
            "human_label": self.human_label,
        }


_DEFAULT_HUMAN_TRANSLATOR: dict[str, Callable[[float], str]] = {
    "CNY": lambda c: f"≈ ¥{c:.2f}" if c < 50 else f"≈ ¥{c:.0f}",
    "USD": lambda c: f"≈ ${c:.2f}" if c < 10 else f"≈ ${c:.0f}",
    "credit": lambda c: f"≈ {int(c)} credits",
}


class CostEstimator:
    """Aggregate per-item costs into a preview block.

    Usage::

        est = CostEstimator(currency="CNY", retry_multiplier=1.10, safety_margin=0.15)
        est.add("Seedance 2.0 720p 5s", units=5, unit_label="s", unit_price=0.20)
        preview = est.build(confidence="high", sample_label="单条 5 秒视频")
    """

    def __init__(
        self,
        *,
        currency: str = "CNY",
        retry_multiplier: float = 1.10,
        safety_margin: float = 0.15,
    ) -> None:
        if retry_multiplier < 1.0:
            raise ValueError("retry_multiplier must be >= 1.0")
        if safety_margin < 0.0:
            raise ValueError("safety_margin must be >= 0.0")
        self.currency = currency
        self.retry_multiplier = retry_multiplier
        self.safety_margin = safety_margin
        self._items: list[CostBreakdown] = []
        self._notes: list[str] = []

    def add(
        self,
        label: str,
        *,
        units: float,
        unit_label: str,
        unit_price: float,
    ) -> CostEstimator:
        """Add one cost line.  Returns self for chaining."""
        if units < 0 or unit_price < 0:
            raise ValueError("units and unit_price must be >= 0")
        self._items.append(
            CostBreakdown(
                label=label,
                units=float(units),
                unit_label=unit_label,
                unit_price=float(unit_price),
                subtotal=float(units) * float(unit_price),
            )
        )
        return self

    def note(self, text: str) -> CostEstimator:
        """Attach a free-form note (e.g. excluded fees).  Returns self."""
        if text:
            self._notes.append(text)
        return self

    def build(
        self,
        *,
        confidence: str = "medium",
        sample_label: str | None = None,
        translator: Callable[[float], str] | None = None,
    ) -> CostPreview:
        """Compute final preview.

        Args:
            confidence: ``"high" | "medium" | "low"``.  ``high`` is fixed-price
                vendors; ``medium`` is variable but well-bounded; ``low`` is
                ranges that depend on user prompt complexity.
            sample_label: If given, picks the matching breakdown row's
                ``subtotal`` as ``sample_cost``; otherwise uses the smallest
                non-zero subtotal.
            translator: Override the default human translator.
        """
        if not self._items:
            return CostPreview(
                low=0.0, high=0.0, sample_cost=0.0,
                confidence="low", currency=self.currency,
                notes=list(self._notes),
            )

        low = sum(b.subtotal for b in self._items)
        high = low * self.retry_multiplier * (1.0 + self.safety_margin)

        sample_cost = 0.0
        if sample_label:
            for b in self._items:
                if b.label == sample_label:
                    sample_cost = b.subtotal
                    break
        if sample_cost == 0.0:
            non_zero = [b.subtotal for b in self._items if b.subtotal > 0]
            sample_cost = min(non_zero) if non_zero else 0.0

        trans = translator or _DEFAULT_HUMAN_TRANSLATOR.get(self.currency)
        human_label = trans(high) if trans else ""

        return CostPreview(
            low=low,
            high=high,
            sample_cost=sample_cost,
            confidence=confidence,
            currency=self.currency,
            breakdown=list(self._items),
            notes=list(self._notes),
            human_label=human_label,
        )

    def reset(self) -> None:
        """Clear items and notes — useful when re-using the same estimator."""
        self._items.clear()
        self._notes.clear()


def to_human_units(cost: float, currency: str = "CNY") -> str:
    """Stand-alone human translator (no estimator instance needed)."""
    trans = _DEFAULT_HUMAN_TRANSLATOR.get(currency)
    return trans(cost) if trans else f"{cost:.2f} {currency}"
