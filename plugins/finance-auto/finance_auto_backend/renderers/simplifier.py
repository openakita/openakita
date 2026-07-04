"""Report simplifier (W3 Stage 2, v0.2 Part 1 §3).

A single report cell can aggregate hundreds of sub-account / auxiliary rows
(e.g. ``BS_2202`` = 应付账款 over 1 500 suppliers).  When the YAML config
enables ``simplify``, this module returns a small set of "kept" rows + one
synthetic "其他" merged row that preserves the total but compresses noise.

The original detail is NEVER lost: the simplifier returns a
:class:`SimplifyResult` whose ``merged_row_ids`` field records the IDs of all
rows that were folded into the "其他" line, so the audit trail can drill back
down to the raw data even after simplification.

Used by:

* :mod:`report_generator` — only when the YAML rule has a ``simplify`` block.
* :mod:`report_routes`     — for the ``PATCH /reports/.../cells/.../simplify``
  per-cell toggle that lets the UI flip simplification on/off interactively.
* :mod:`renderers.openpyxl_writer` — to apply the grey-italic style to the
  "其他" row.

The algorithm follows v0.2 Part 1 §3.3.1 directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


Strategy = Literal["top_n", "threshold", "both"]
SortBy = Literal["amount_desc", "amount_abs_desc"]


@dataclass(frozen=True)
class DetailRow:
    """One aux-detail line participating in simplification.

    ``row_id`` is whatever the caller wants to stash (a
    ``trial_balance_rows.id`` for now); the simplifier treats it as opaque.
    """

    row_id: str
    name: str
    amount: float
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SimplifyConfig:
    enabled: bool = False
    strategy: Strategy = "top_n"
    top_n: int = 10
    sort_by: SortBy = "amount_desc"
    merge_label: str = "其他"
    min_threshold: float | None = None
    keep_negative_separate: bool = True
    footnote_template: str = "其他 {count} 项合计 {amount}"

    @classmethod
    def from_yaml(cls, raw: dict | None) -> SimplifyConfig:
        if not raw:
            return cls(enabled=False)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            strategy=str(raw.get("strategy", "top_n")),  # type: ignore[arg-type]
            top_n=int(raw.get("top_n", 10)),
            sort_by=str(raw.get("sort_by", "amount_desc")),  # type: ignore[arg-type]
            merge_label=str(raw.get("merge_label", "其他")),
            min_threshold=(
                None if raw.get("min_threshold") in (None, "") else float(raw["min_threshold"])
            ),
            keep_negative_separate=bool(raw.get("keep_negative_separate", True)),
            footnote_template=str(
                raw.get("footnote_template", "其他 {count} 项合计 {amount}")
            ),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy,
            "top_n": self.top_n,
            "sort_by": self.sort_by,
            "merge_label": self.merge_label,
            "min_threshold": self.min_threshold,
            "keep_negative_separate": self.keep_negative_separate,
            "footnote_template": self.footnote_template,
        }


@dataclass
class SimplifyResult:
    """What the simplifier returns.

    ``kept_rows`` contains the visible detail (Top N + any kept negative
    rows).  ``merged_row`` is the synthetic "其他" line (``None`` if no
    merging happened — i.e. ``len(rows) <= top_n`` and no threshold cuts
    fired).  ``all_source_ids`` is the union of every row id that fed the
    cell (kept + merged); the caller persists this into
    ``ReportCell.source_rows`` so the audit trail is complete.
    """

    kept_rows: list[DetailRow] = field(default_factory=list)
    merged_row: DetailRow | None = None
    merged_count: int = 0
    merged_row_ids: list[str] = field(default_factory=list)
    all_source_ids: list[str] = field(default_factory=list)
    footnote: str = ""
    config_used: SimplifyConfig | None = None

    @property
    def total(self) -> float:
        return sum(r.amount for r in self.kept_rows)

    @property
    def visible_count(self) -> int:
        return len(self.kept_rows)


# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------


def _sort_key(sort_by: SortBy):
    if sort_by == "amount_abs_desc":
        return lambda r: abs(r.amount)
    return lambda r: r.amount  # amount_desc — also used as default


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def simplify_aux_details(
    rows: Iterable[DetailRow], cfg: SimplifyConfig
) -> SimplifyResult:
    """Apply the simplification strategy.

    When ``cfg.enabled == False`` the input is returned unchanged.  The
    return type is always a :class:`SimplifyResult` so the caller can rely
    on ``result.all_source_ids`` regardless.
    """
    rows = list(rows)
    all_ids = [r.row_id for r in rows]

    if not cfg.enabled or not rows:
        return SimplifyResult(
            kept_rows=rows, merged_row=None, merged_count=0,
            merged_row_ids=[], all_source_ids=all_ids,
            footnote="", config_used=cfg,
        )

    if cfg.keep_negative_separate:
        positive = [r for r in rows if r.amount >= 0]
        negative = [r for r in rows if r.amount < 0]
    else:
        positive, negative = list(rows), []

    positive_sorted = sorted(positive, key=_sort_key(cfg.sort_by), reverse=True)

    kept: list[DetailRow] = []
    merged: list[DetailRow] = []

    if cfg.strategy == "top_n":
        kept = positive_sorted[: cfg.top_n]
        merged = positive_sorted[cfg.top_n :]
    elif cfg.strategy == "threshold":
        thr = cfg.min_threshold if cfg.min_threshold is not None else 0.0
        kept = [r for r in positive_sorted if abs(r.amount) >= thr]
        merged = [r for r in positive_sorted if abs(r.amount) < thr]
    elif cfg.strategy == "both":
        thr = cfg.min_threshold if cfg.min_threshold is not None else 0.0
        passing = [r for r in positive_sorted if abs(r.amount) >= thr]
        kept = passing[: cfg.top_n]
        # rows that passed threshold but missed top_n + rows that failed
        # threshold all merge into "其他".
        merged = passing[cfg.top_n :] + [
            r for r in positive_sorted if abs(r.amount) < thr
        ]
    else:
        raise ValueError(f"unsupported simplify strategy: {cfg.strategy!r}")

    merged_row: DetailRow | None = None
    footnote = ""
    if merged:
        total_merged = sum(r.amount for r in merged)
        merged_row = DetailRow(
            row_id=f"__merged__:{cfg.merge_label}",
            name=cfg.merge_label,
            amount=round(total_merged, 2),
            extra={
                "is_merged": True,
                "merged_count": len(merged),
                "merged_row_ids": [r.row_id for r in merged],
            },
        )
        footnote = cfg.footnote_template.format(
            count=len(merged), amount=f"{total_merged:,.2f}",
        )

    final_kept = list(kept)
    if merged_row is not None:
        final_kept.append(merged_row)
    final_kept.extend(negative)

    return SimplifyResult(
        kept_rows=final_kept,
        merged_row=merged_row,
        merged_count=len(merged),
        merged_row_ids=[r.row_id for r in merged],
        all_source_ids=all_ids,
        footnote=footnote,
        config_used=cfg,
    )


__all__ = [
    "DetailRow",
    "SimplifyConfig",
    "SimplifyResult",
    "simplify_aux_details",
]
