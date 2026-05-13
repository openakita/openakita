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
        self._audit_deck_variety(slides, issues)
        if export_path:
            path = Path(export_path)
            if not path.exists() or path.stat().st_size == 0:
                issues.append(self._issue("error", "missing_export", "Export file was not created."))
        ok = not any(issue["severity"] == "error" for issue in issues)
        score = self._score(issues)
        return {"ok": ok, "score": score, "issue_count": len(issues), "issues": issues}

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
        if len(title) > 48:
            issues.append(self._issue("warning", "title_too_long", f"{slide_id} title is long."))
        text_len = len(json.dumps(content, ensure_ascii=False))
        if text_len > 1800:
            issues.append(self._issue("warning", "text_too_dense", f"{slide_id} content is dense."))
        quality = slide.get("quality") or {}
        if isinstance(quality, dict):
            density_score = float(quality.get("density_score") or 0)
            if density_score > 0.78:
                issues.append(self._issue("warning", "density_high", f"{slide_id} density score is high."))
            if quality.get("needs_split"):
                issues.append(self._issue("warning", "needs_split", f"{slide_id} likely needs splitting."))
        if slide.get("slide_type") == "data_table":
            columns = content.get("headers") or content.get("columns") or []
            rows = content.get("rows") or []
            if len(columns) > 8:
                issues.append(self._issue("warning", "table_too_wide", f"{slide_id} has more than 8 columns."))
            if len(rows) > 12:
                issues.append(self._issue("warning", "table_too_tall", f"{slide_id} has more than 12 rows."))
        layout_hint = slide.get("layout_hint", {})
        if isinstance(layout_hint, dict) and layout_hint.get("source") == "builtin":
            issues.append(self._issue("info", "template_fallback", f"{slide_id} uses fallback layout."))
        if slide.get("slide_type", "").startswith("chart_"):
            categories = content.get("categories") or []
            series = content.get("series") or []
            if not categories or not series:
                issues.append(self._issue("warning", "missing_chart_data", f"{slide_id} has no chart data."))
                # Backward-compatible code expected by older callers/tests.
                issues.append(self._issue("warning", "missing_chart_spec", f"{slide_id} has no chart spec."))
        if (content.get("image_query") or content.get("icon_query")) and not slide.get("assets"):
            issues.append(self._issue("info", "asset_unresolved", f"{slide_id} has unresolved asset hints."))

    def _issue(self, severity: str, code: str, message: str) -> dict[str, str]:
        return {"severity": severity, "code": code, "message": message}

    def _audit_deck_variety(self, slides: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
        if len(slides) < 4:
            return
        slide_types = [str(slide.get("slide_type") or "content") for slide in slides]
        unique_ratio = len(set(slide_types)) / max(1, len(slide_types))
        if unique_ratio < 0.35:
            issues.append(self._issue("warning", "layout_variety_low", "Deck uses too few layout types."))
        repeated = 1
        previous = None
        for current in slide_types:
            repeated = repeated + 1 if current == previous else 1
            previous = current
            if repeated >= 4:
                issues.append(self._issue("warning", "layout_repetition", f"{current} repeats too often."))
                break

    @staticmethod
    def _score(issues: list[dict[str, Any]]) -> dict[str, Any]:
        base = 100
        weights = {"error": 30, "warning": 8, "info": 1}
        for issue in issues:
            base -= weights.get(issue.get("severity"), 0)
        value = max(0, min(100, base))
        return {
            "overall": value,
            "readability": value,
            "visual_consistency": max(0, value - sum(1 for issue in issues if issue.get("code") == "layout_variety_low") * 10),
            "data_trust": max(0, value - sum(1 for issue in issues if issue.get("code") == "missing_chart_data") * 10),
        }

