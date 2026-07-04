"""Factory for picking the right :class:`ReportRenderer`.

This is the v0.3 Part Biz contract C1 entry point.  Caller passes the report
kind (one of the four YAML-driven kinds for now) and an estimated row count,
and the factory returns a renderer wired to the correct template path.
"""

from __future__ import annotations

from pathlib import Path

from .base import ReportRenderer
from .openpyxl_writer import STATIC_ROW_THRESHOLD, OpenpyxlDirectRenderer
from .xltpl_renderer import XltplRenderer

DYNAMIC_ONLY_KINDS: frozenset[str] = frozenset(
    {
        "ar_aging",
        "ap_aging",
        "inventory_detail",
        "audit_workpaper_detail",
    }
)
"""Report kinds that are always dynamic, regardless of row count."""

STATIC_KINDS: frozenset[str] = frozenset(
    {
        "balance_sheet",
        "income_statement",
        "owners_equity",
        "cash_flow",
    }
)
"""Report kinds that have a known small row count and a hand-laid template."""


def make_renderer(
    report_kind: str,
    rows_estimate: int,
    template_path: Path | str,
) -> ReportRenderer:
    """Return the renderer best suited to ``report_kind`` + ``rows_estimate``.

    Selection rules (v0.3 Part Infra section 1.3):

    1. If ``report_kind`` is on :data:`DYNAMIC_ONLY_KINDS` ->
       :class:`OpenpyxlDirectRenderer`.
    2. If ``report_kind`` is on :data:`STATIC_KINDS` and ``rows_estimate <=
       STATIC_ROW_THRESHOLD`` -> :class:`XltplRenderer`.
    3. Otherwise (large row count, or an unknown kind) ->
       :class:`OpenpyxlDirectRenderer`.

    Parameters
    ----------
    report_kind:
        Logical kind of the report.
    rows_estimate:
        Best-effort estimate of how many *body* rows the report will have.
        Pass ``len(rows)`` of the prepared data; the factory uses it only as
        a routing hint.
    template_path:
        Filesystem path to the .xlsx template.
    """

    template_path = Path(template_path)
    kind = (report_kind or "").strip().lower()
    if rows_estimate < 0:
        raise ValueError(f"rows_estimate must be non-negative, got {rows_estimate}")

    if kind in DYNAMIC_ONLY_KINDS:
        return OpenpyxlDirectRenderer(template_path)

    if kind in STATIC_KINDS and rows_estimate <= STATIC_ROW_THRESHOLD:
        return XltplRenderer(template_path)

    return OpenpyxlDirectRenderer(template_path)
