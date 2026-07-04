"""ReportRenderer abstraction (v0.3 Part Biz contract C1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RenderResult:
    """The result of a single render() call.

    Attributes
    ----------
    output_path:
        Absolute path to the generated workbook on disk.
    rows_written:
        Total number of body rows the renderer placed into the workbook
        (excludes header / total rows).  Used by the report-generation
        pipeline for telemetry and for the cell-level traceability layer.
    elapsed_ms:
        Wall-clock render time in milliseconds.  Populated by the renderer
        itself so the caller does not have to wrap each call in ``time``.
    backend:
        The renderer family that produced the file.  One of ``"xltpl"``,
        ``"openpyxl"``, or ``"win32com"``.  Used by /reports endpoints to
        record provenance.
    warnings:
        Free-form non-fatal messages (missing formula, TBD account code,
        empty section, ...).  The pipeline forwards these to the API
        response under ``report.warnings`` so the desktop client can show a
        toast.
    """

    output_path: Path
    rows_written: int
    elapsed_ms: float
    backend: str
    warnings: list[str] = field(default_factory=list)


class ReportRenderer(ABC):
    """Abstract base class for all renderer backends.

    A renderer is created via :func:`make_renderer` and is single-use: each
    instance owns a template path and a backend identifier.  ``render()`` may
    only be called once; subsequent calls raise :class:`RuntimeError`.

    Subclasses implement :meth:`_render` which receives a normalised
    ``context`` dict with at least the following shape (Stage 4 will extend
    it once :class:`ReportInstance` exists)::

        {
          "report_kind": "balance_sheet" | "income_statement" | ...,
          "standard": "small_enterprise" | "general_enterprise",
          "year": 2025,
          "period": "2025-12",
          "org": {"id": "...", "name": "...", "industry": "..."},
          "cells": [
              {"code": "1001", "label": "货币资金",
               "row": 5, "column": "C", "value": Decimal("12345.67"),
               "formula": "1001 - 1601", "source_rows": [...]},
              ...
          ],
          # Only populated for dynamic detail tables (renderer family
          # OpenpyxlDirectRenderer):
          "rows": [
              {"account_code": "1122.01", "name": "...", "balance": ...},
              ...
          ],
        }
    """

    BACKEND: str = "abstract"

    def __init__(self, template_path: Path) -> None:
        self._template_path = Path(template_path)
        if not self._template_path.exists():
            raise FileNotFoundError(
                f"renderer template not found: {self._template_path}"
            )
        self._consumed = False

    @property
    def template_path(self) -> Path:
        return self._template_path

    @property
    def backend(self) -> str:
        return self.BACKEND

    def render(self, context: dict[str, Any], output_path: Path) -> RenderResult:
        if self._consumed:
            raise RuntimeError(
                f"{type(self).__name__} is single-use; create a new renderer "
                "per generation."
            )
        self._consumed = True
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return self._render(context, output_path)

    @abstractmethod
    def _render(self, context: dict[str, Any], output_path: Path) -> RenderResult:
        """Subclasses implement the actual rendering work."""
        raise NotImplementedError
