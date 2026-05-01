"""Quality checks for ppt-maker slide IR and exported files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PptAudit:
    """Small deterministic audit before and after export."""

    def run(self, slides_ir: dict[str, Any], export_path: str | Path | None = None) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        slides = slides_ir.get("slides", [])
        if not slides:
            issues.append(self._issue("error", "empty_deck", "Deck has no slides."))
        titles = [str(slide.get("title", "")).strip() for slide in slides]
        duplicates = {title for title in titles if title and titles.count(title) > 1}
        for title in sorted(duplicates):
            issues.append(self._issue("warning", "duplicate_title", f"Duplicate slide title: {title}"))
        for slide in slides:
            self._audit_slide(slide, issues)
        if export_path:
            path = Path(export_path)
            if not path.exists() or path.stat().st_size == 0:
                issues.append(self._issue("error", "missing_export", "Export file was not created."))
        ok = not any(issue["severity"] == "error" for issue in issues)
        return {"ok": ok, "issue_count": len(issues), "issues": issues}

    def save(self, report: dict[str, Any], project_dir: str | Path) -> Path:
        path = Path(project_dir) / "audit_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _audit_slide(self, slide: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        slide_id = slide.get("id", "unknown")
        title = str(slide.get("title", "")).strip()
        content = slide.get("content", {})
        if not title:
            issues.append(self._issue("warning", "missing_title", f"{slide_id} has no title."))
        text_len = len(json.dumps(content, ensure_ascii=False))
        if text_len > 1800:
            issues.append(self._issue("warning", "text_too_dense", f"{slide_id} content is dense."))
        if slide.get("slide_type") == "data_table":
            columns = content.get("columns", [])
            if len(columns) > 8:
                issues.append(self._issue("warning", "table_too_wide", f"{slide_id} has more than 8 columns."))
        layout_hint = slide.get("layout_hint", {})
        if isinstance(layout_hint, dict) and layout_hint.get("source") == "builtin":
            issues.append(self._issue("info", "template_fallback", f"{slide_id} uses fallback layout."))
        if slide.get("slide_type", "").startswith("chart_") and not content.get("chart_spec"):
            issues.append(self._issue("warning", "missing_chart_spec", f"{slide_id} has no chart spec."))

    def _issue(self, severity: str, code: str, message: str) -> dict[str, str]:
        return {"severity": severity, "code": code, "message": message}

