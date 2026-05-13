"""Design specification and spec_lock generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DesignBuilder:
    """Create human-readable design_spec and machine-readable spec_lock."""

    def build(
        self,
        *,
        outline: dict[str, Any],
        brand_tokens: dict[str, Any] | None = None,
        layout_map: dict[str, Any] | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        brand_tokens = brand_tokens or self._default_brand(style)
        layout_map = layout_map or self._default_layout_map()
        mode = outline.get("mode", "topic_to_deck")
        design_spec = self._markdown(outline, brand_tokens, layout_map)
        spec_lock = {
            "version": 1,
            "mode": mode,
            "theme": {
                "primary_color": brand_tokens.get("primary_color"),
                "secondary_color": brand_tokens.get("secondary_color"),
                "accent_color": brand_tokens.get("accent_color"),
                "background_color": brand_tokens.get("background_color", "#FFFFFF"),
                "font_heading": brand_tokens.get("font_heading"),
                "font_body": brand_tokens.get("font_body"),
            },
            "typography": {
                "hero": 48,
                "title": 32,
                "subtitle": 22,
                "body": 16,
                "caption": 12,
            },
            "spacing": {
                "margin_x": 60,
                "margin_y": 50,
                "gap": 24,
                "card_padding": 24,
                "radius": 12,
            },
            "chart_palette": [
                brand_tokens.get("primary_color"),
                brand_tokens.get("accent_color"),
                "#4A90D9",
                "#10B981",
                "#EF4444",
            ],
            "density_rules": {
                "max_bullets_per_slide": 6,
                "max_table_columns": 8,
                "prefer_split_when_density_above": 0.78,
            },
            "layout_map": layout_map,
            "slide_count": len(outline.get("slides", [])),
            "rules": [
                "Keep all text editable in PPTX.",
                "Use table/chart slide types for table_to_deck data pages.",
                "Use fallback layouts whenever enterprise template layout mapping is incomplete.",
                "Prefer conclusion-first page titles for executive decks.",
                "Keep content inside a 60px safe margin unless a cover/section layout says otherwise.",
            ],
            "confirmed": False,
            "needs_confirmation": True,
        }
        return {
            "design_spec_markdown": design_spec,
            "spec_lock": spec_lock,
            "confirmation_questions": [
                "整体视觉风格是否符合预期？",
                "品牌色和字体是否需要手动调整？",
                "是否允许对超长内容做压缩和分页？",
            ],
        }

    def confirm(self, design: dict[str, Any], updates: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {**design, **(updates or {})}
        spec_lock = dict(result.get("spec_lock", {}))
        spec_lock["confirmed"] = True
        spec_lock["needs_confirmation"] = False
        result["spec_lock"] = spec_lock
        result["confirmed"] = True
        result["needs_confirmation"] = False
        return result

    def save(self, design: dict[str, Any], project_dir: str | Path) -> dict[str, str]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        design_path = root / "design_spec.md"
        spec_path = root / "spec_lock.json"
        design_path.write_text(design["design_spec_markdown"], encoding="utf-8")
        spec_path.write_text(
            json.dumps(design["spec_lock"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"design_spec_path": str(design_path), "spec_lock_path": str(spec_path)}

    def _markdown(
        self,
        outline: dict[str, Any],
        brand_tokens: dict[str, Any],
        layout_map: dict[str, Any],
    ) -> str:
        lines = [
            f"# {outline.get('title', 'PPT Design Spec')}",
            "",
            "## Audience",
            outline.get("audience") or "未指定，默认面向业务决策者。",
            "",
            "## Visual Direction",
            f"- Primary color: {brand_tokens.get('primary_color')}",
            f"- Secondary color: {brand_tokens.get('secondary_color')}",
            f"- Accent color: {brand_tokens.get('accent_color')}",
            f"- Heading font: {brand_tokens.get('font_heading')}",
            f"- Body font: {brand_tokens.get('font_body')}",
            "- Safe margin: 60px left/right, 50px top/bottom",
            "- Preferred card radius: 12px; card padding: 24px",
            "",
            "## Layout Contract",
        ]
        for key, value in layout_map.items():
            if isinstance(value, dict):
                lines.append(f"- {key}: {value.get('pptx_layout') or value.get('fallback')}")
            else:
                lines.append(f"- {key}: {value}")
        lines.extend(["", "## Slide Plan"])
        for slide in outline.get("slides", []):
            lines.append(f"- {slide.get('index')}. {slide.get('title')} ({slide.get('slide_type')})")
        return "\n".join(lines) + "\n"

    STYLE_PRESETS: dict[str, dict[str, str]] = {
        "tech_business": {
            "primary_color": "#3457D5",
            "secondary_color": "#172033",
            "accent_color": "#FFB000",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "consulting": {
            "primary_color": "#143D59",
            "secondary_color": "#1F4E79",
            "accent_color": "#F4B41A",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "data_insight": {
            "primary_color": "#0F766E",
            "secondary_color": "#134E4A",
            "accent_color": "#F97316",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "creative_pitch": {
            "primary_color": "#7C3AED",
            "secondary_color": "#111827",
            "accent_color": "#EC4899",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "education": {
            "primary_color": "#2A9D8F",
            "secondary_color": "#264653",
            "accent_color": "#E9C46A",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "academic": {
            "primary_color": "#34495E",
            "secondary_color": "#2C3E50",
            "accent_color": "#8E44AD",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
        "minimalist": {
            "primary_color": "#1F2933",
            "secondary_color": "#52606D",
            "accent_color": "#F0B429",
            "background_color": "#FFFFFF",
            "font_heading": "Microsoft YaHei",
            "font_body": "Microsoft YaHei",
        },
    }

    def _default_brand(self, style: str | None = None) -> dict[str, Any]:
        key = (style or "tech_business").strip().lower()
        preset = self.STYLE_PRESETS.get(key) or self.STYLE_PRESETS["tech_business"]
        return dict(preset)

    def _default_layout_map(self) -> dict[str, Any]:
        return {
            "cover": {"fallback": "cover", "source": "builtin"},
            "agenda": {"fallback": "agenda", "source": "builtin"},
            "content": {"fallback": "content", "source": "builtin"},
            "chart": {"fallback": "chart_bar", "source": "builtin"},
            "closing": {"fallback": "closing", "source": "builtin"},
        }

