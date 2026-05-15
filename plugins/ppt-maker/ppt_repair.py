"""Lightweight audit repair pass for ppt-maker."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class PptRepair:
    """Apply deterministic low-risk repairs and record remaining suggestions."""

    def repair(self, slides_ir: dict[str, Any], audit_report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        repaired = copy.deepcopy(slides_ir)
        issues = audit_report.get("issues") or []
        actions: list[dict[str, Any]] = []
        issue_codes_by_slide = self._issue_codes_by_slide(issues)
        for slide in repaired.get("slides", []):
            slide_id = str(slide.get("id") or "")
            codes = issue_codes_by_slide.get(slide_id, set())
            if "title_too_long" in codes:
                title = str(slide.get("title") or "")
                slide["title"] = title[:45].rstrip() + "..."
                actions.append({"slide_id": slide_id, "action": "truncate_title"})
            if {"density_high", "needs_split", "text_too_dense"} & codes:
                self._compress_slide(slide)
                actions.append({"slide_id": slide_id, "action": "compress_text"})
            if "table_too_wide" in codes:
                content = slide.get("content") or {}
                headers = content.get("headers") or content.get("columns") or []
                rows = content.get("rows") or []
                content["headers"] = list(headers)[:8]
                content["rows"] = [list(row)[:8] for row in rows]
                content.setdefault("bullets", []).append("表格已自动保留前 8 列，完整数据请查看源文件。")
                slide["content"] = content
                actions.append({"slide_id": slide_id, "action": "trim_table_columns"})

        repair_plan = {
            "changed": bool(actions),
            "actions": actions,
            "remaining_hints": self._remaining_hints(issues),
        }
        return repaired, repair_plan

    def save(self, repair_plan: dict[str, Any], project_dir: str | Path) -> Path:
        path = Path(project_dir) / "repair_plan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(repair_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _issue_codes_by_slide(issues: list[dict[str, Any]]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for issue in issues:
            message = str(issue.get("message") or "")
            slide_id = message.split(" ", 1)[0]
            if not slide_id:
                continue
            out.setdefault(slide_id, set()).add(str(issue.get("code") or ""))
        return out

    @staticmethod
    def _compress_slide(slide: dict[str, Any]) -> None:
        content = slide.get("content") or {}
        for key in ("bullets", "items", "findings", "risks"):
            if isinstance(content.get(key), list):
                content[key] = [str(item)[:110] for item in content[key][:5]]
        if isinstance(content.get("body"), str):
            content["body"] = content["body"][:320]
        slide["content"] = content
        quality = dict(slide.get("quality") or {})
        quality["repair_hints"] = list(quality.get("repair_hints") or []) + ["已执行轻量文字压缩。"]
        quality["needs_split"] = False
        slide["quality"] = quality

    @staticmethod
    def _remaining_hints(issues: list[dict[str, Any]]) -> list[str]:
        hints = []
        for issue in issues:
            code = issue.get("code")
            if code in {"missing_chart_data", "asset_unresolved", "template_fallback", "layout_variety_low"}:
                hints.append(str(issue.get("message") or code))
        return hints[:20]

