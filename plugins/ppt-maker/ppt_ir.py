"""Slide intermediate representation for ppt-maker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ppt_models import DeckMode, SlideType

FALLBACK_LAYOUTS: dict[str, str] = {
    SlideType.COVER.value: "cover",
    SlideType.AGENDA.value: "agenda",
    SlideType.SECTION.value: "section",
    SlideType.CONTENT.value: "content",
    SlideType.COMPARISON.value: "comparison",
    SlideType.TIMELINE.value: "timeline",
    SlideType.DATA_OVERVIEW.value: "data_overview",
    SlideType.METRIC_CARDS.value: "metric_cards",
    SlideType.CHART_BAR.value: "chart_bar",
    SlideType.CHART_LINE.value: "chart_line",
    SlideType.CHART_PIE.value: "chart_pie",
    SlideType.DATA_TABLE.value: "data_table",
    SlideType.INSIGHT_SUMMARY.value: "insight_summary",
    SlideType.SUMMARY.value: "summary",
    SlideType.CLOSING.value: "closing",
}


class SlideIrBuilder:
    """Convert outline/design/table/template context into editable slide IR."""

    def build(
        self,
        *,
        outline: dict[str, Any],
        spec_lock: dict[str, Any],
        table_insights: dict[str, Any] | None = None,
        chart_specs: list[dict[str, Any]] | None = None,
        template_id: str | None = None,
        layout_map: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = DeckMode(outline.get("mode", DeckMode.TOPIC_TO_DECK.value))
        slides = [
            self._slide(
                item,
                spec_lock=spec_lock,
                table_insights=table_insights,
                chart_specs=chart_specs or [],
                template_id=template_id,
                layout_map=layout_map or spec_lock.get("layout_map", {}),
            )
            for item in outline.get("slides", [])
        ]
        if mode == DeckMode.TABLE_TO_DECK:
            slides = self._ensure_table_story(slides, spec_lock, table_insights, chart_specs or [])
        return {
            "version": 1,
            "mode": mode.value,
            "title": outline.get("title", ""),
            "template_id": template_id,
            "slides": slides,
            "fallbacks": FALLBACK_LAYOUTS,
        }

    def save(self, ir: dict[str, Any], project_dir: str | Path) -> Path:
        path = Path(project_dir) / "slides_ir.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _slide(
        self,
        outline_slide: dict[str, Any],
        *,
        spec_lock: dict[str, Any],
        table_insights: dict[str, Any] | None,
        chart_specs: list[dict[str, Any]],
        template_id: str | None,
        layout_map: dict[str, Any],
    ) -> dict[str, Any]:
        slide_type = outline_slide.get("slide_type") or SlideType.CONTENT.value
        layout_hint = self._layout_hint(slide_type, layout_map)
        content = self._content_for_type(outline_slide, slide_type, table_insights, chart_specs)
        return {
            "id": outline_slide.get("id") or f"slide_{outline_slide.get('index', 1):02d}",
            "index": outline_slide.get("index", 1),
            "title": outline_slide.get("title", ""),
            "slide_type": slide_type,
            "layout_hint": layout_hint,
            "template_id": template_id,
            "theme": spec_lock.get("theme", {}),
            "content": content,
            "notes": outline_slide.get("purpose", ""),
        }

    def _content_for_type(
        self,
        outline_slide: dict[str, Any],
        slide_type: str,
        table_insights: dict[str, Any] | None,
        chart_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if slide_type == SlideType.METRIC_CARDS.value:
            return {"metrics": self._metric_cards(chart_specs), "bullets": outline_slide.get("key_points", [])}
        if slide_type in {SlideType.CHART_BAR.value, SlideType.CHART_LINE.value, SlideType.CHART_PIE.value}:
            return {"chart_spec": self._first_chart(chart_specs), "bullets": outline_slide.get("key_points", [])}
        if slide_type == SlideType.INSIGHT_SUMMARY.value:
            return {
                "findings": (table_insights or {}).get("key_findings", outline_slide.get("key_points", [])),
                "risks": (table_insights or {}).get("risks_and_caveats", []),
            }
        if slide_type == SlideType.DATA_OVERVIEW.value:
            return {"bullets": (table_insights or {}).get("key_findings", [])[:3]}
        return {"bullets": outline_slide.get("key_points", []), "body": outline_slide.get("purpose", "")}

    def _layout_hint(self, slide_type: str, layout_map: dict[str, Any]) -> dict[str, Any]:
        key = self._layout_key(slide_type)
        mapped = layout_map.get(key, {})
        if isinstance(mapped, dict):
            return {
                "key": key,
                "pptx_layout": mapped.get("pptx_layout"),
                "fallback": mapped.get("fallback") or FALLBACK_LAYOUTS.get(slide_type, "content"),
                "source": mapped.get("source") or "builtin",
            }
        return {"key": key, "pptx_layout": None, "fallback": FALLBACK_LAYOUTS.get(slide_type, "content"), "source": "builtin"}

    def _layout_key(self, slide_type: str) -> str:
        if slide_type == SlideType.COVER.value:
            return "cover"
        if slide_type == SlideType.AGENDA.value:
            return "agenda"
        if slide_type in {SlideType.CHART_BAR.value, SlideType.CHART_LINE.value, SlideType.CHART_PIE.value}:
            return "chart"
        if slide_type == SlideType.CLOSING.value:
            return "closing"
        if slide_type == SlideType.SECTION.value:
            return "section"
        return "content"

    def _ensure_table_story(
        self,
        slides: list[dict[str, Any]],
        spec_lock: dict[str, Any],
        table_insights: dict[str, Any] | None,
        chart_specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        existing = {slide["slide_type"] for slide in slides}
        required = [
            SlideType.DATA_OVERVIEW.value,
            SlideType.METRIC_CARDS.value,
            SlideType.CHART_LINE.value,
            SlideType.INSIGHT_SUMMARY.value,
        ]
        for slide_type in required:
            if slide_type in existing:
                continue
            slides.append(
                self._slide(
                    {
                        "id": f"auto_{slide_type}",
                        "index": len(slides) + 1,
                        "title": FALLBACK_LAYOUTS[slide_type].replace("_", " ").title(),
                        "slide_type": slide_type,
                        "purpose": "Auto-added table_to_deck required page.",
                    },
                    spec_lock=spec_lock,
                    table_insights=table_insights,
                    chart_specs=chart_specs,
                    template_id=None,
                    layout_map=spec_lock.get("layout_map", {}),
                )
            )
        for index, slide in enumerate(slides, start=1):
            slide["index"] = index
        return slides

    def _metric_cards(self, chart_specs: list[dict[str, Any]]) -> list[str]:
        for spec in chart_specs:
            if spec.get("type") == "metric_cards":
                return list(spec.get("metrics", []))
        return []

    def _first_chart(self, chart_specs: list[dict[str, Any]]) -> dict[str, Any] | None:
        for spec in chart_specs:
            if spec.get("type") in {"bar", "horizontal_bar", "line", "pie"}:
                return spec
        return chart_specs[0] if chart_specs else None

