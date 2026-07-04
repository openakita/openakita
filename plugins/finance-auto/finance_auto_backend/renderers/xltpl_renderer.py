"""xltpl-backed renderer for static accounting reports.

Used for fixed-shape reports where the row count is bounded and known at
template-design time (BS / PL / OE / cash-flow).  See v0.3 Part Infra section
1.1: xltpl 0.21 provides Jinja-style cell-level substitution while preserving
the workbook's style runs, merges, named ranges, and formula cells, but it
does *not* support cross-cell ``{% for %}`` loops -- so for dynamic tables
:class:`OpenpyxlDirectRenderer` is selected by the factory instead.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .base import RenderResult, ReportRenderer


class XltplRenderer(ReportRenderer):
    """Render a static report by feeding ``cells`` into named slots.

    The template is expected to use Jinja-style placeholders such as
    ``{{ org.name }}`` for headers and indexed slots ``{{ cells['1001'].value
    }}`` for each line item.  Slot keying by accounting code (``code``)
    rather than positional index keeps the YAML config decoupled from the
    spreadsheet layout: the YAML's ``code`` is the single source of truth.
    """

    BACKEND = "xltpl"

    def _render(self, context: dict[str, Any], output_path: Path) -> RenderResult:
        from xltpl.writerx import BookWriter

        cells_by_code: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        for cell in context.get("cells", []) or []:
            code = str(cell.get("code") or "").strip()
            if not code:
                continue
            if code == "TBD":
                warnings.append(
                    f"line '{cell.get('label')}' has code=TBD; rendered as 0"
                )
            cells_by_code[code] = cell

        payload: dict[str, Any] = {
            "report_kind": context.get("report_kind", ""),
            "standard": context.get("standard", ""),
            "year": context.get("year", 0),
            "period": context.get("period", ""),
            "org": context.get("org", {}),
            "cells": cells_by_code,
        }

        started = time.perf_counter()
        writer = BookWriter(str(self._template_path))
        writer.render_book(payloads=[payload])
        writer.save(str(output_path))
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        return RenderResult(
            output_path=output_path,
            rows_written=len(cells_by_code),
            elapsed_ms=elapsed_ms,
            backend=self.BACKEND,
            warnings=warnings,
        )
