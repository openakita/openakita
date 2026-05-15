"""Build generation-v2 artifacts from the existing ppt-maker pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ppt_context_distiller import ContextDistiller
from ppt_generation_models import (
    DesignSystem,
    GenerationBrief,
    RenderComponent,
    RenderModel,
    RenderSlide,
    SlideSpec,
    StoryPlan,
    StorySlide,
)
from ppt_layout_catalog import LayoutCatalog
from ppt_scenario_skills import select_scenario_skill


def build_generation_brief(project: Any, settings: dict[str, str] | None = None) -> GenerationBrief:
    settings = settings or {}
    quality_mode = settings.get("quality_mode") or "standard"
    if quality_mode not in {"draft", "standard", "deep_design"}:
        quality_mode = "standard"
    output_mode = settings.get("output_mode") or "editable"
    if output_mode not in {"editable", "creative_image"}:
        output_mode = "editable"
    return GenerationBrief(
        title=getattr(project, "title", "") or "Untitled",
        mode=getattr(project, "mode"),
        prompt=getattr(project, "prompt", "") or "",
        audience=getattr(project, "audience", "") or "",
        style=getattr(project, "style", "") or "tech_business",
        slide_count=getattr(project, "slide_count", None) or 8,
        language=settings.get("language") or "zh-CN",
        tone=settings.get("tone") or "professional",
        verbosity=settings.get("verbosity") or "balanced",
        quality_mode=quality_mode,  # type: ignore[arg-type]
        output_mode=output_mode,  # type: ignore[arg-type]
    )


def build_story_plan(outline: dict[str, Any], *, style: str = "", prompt: str = "") -> StoryPlan:
    skill = select_scenario_skill(
        mode=str(outline.get("mode") or ""),
        style=style,
        prompt=prompt,
        has_table=bool(outline.get("table_insights_summary")),
    )
    slides = []
    for slide in outline.get("slides", []):
        slides.append(
            StorySlide(
                index=int(slide.get("index") or len(slides) + 1),
                title=str(slide.get("title") or ""),
                purpose=str(slide.get("purpose") or ""),
                slide_type=str(slide.get("slide_type") or "content"),
                narrative_role=_narrative_role(slide, len(outline.get("slides", []))),
                key_message=str(slide.get("body") or slide.get("purpose") or ""),
            )
        )
    return StoryPlan(
        title=str(outline.get("title") or ""),
        storyline=list(outline.get("storyline") or skill.storyline),
        slides=slides,
        rhythm=[_rhythm_for_slide(item.index, len(slides), item.slide_type) for item in slides],
    )


def build_design_system(
    spec_lock: dict[str, Any],
    *,
    style: str = "tech_business",
    quality_mode: str = "standard",
) -> DesignSystem:
    theme = dict(spec_lock.get("theme") or {})
    density = "airy" if quality_mode == "deep_design" else "balanced"
    if quality_mode == "draft":
        density = "dense"
    primary = theme.get("primary_color") or "#3457D5"
    secondary = theme.get("secondary_color") or "#172033"
    accent = theme.get("accent_color") or "#FFB000"
    return DesignSystem(
        theme_id=style or "tech_business",
        primary_color=primary,
        secondary_color=secondary,
        accent_color=accent,
        font_heading=theme.get("font_heading") or "Microsoft YaHei",
        font_body=theme.get("font_body") or "Microsoft YaHei",
        chart_palette=[primary, accent, "#4A90D9", "#10B981", "#EF4444"],
        image_style=_image_style(style),
        icon_style=_icon_style(style),
        density=density,  # type: ignore[arg-type]
        rules=list(spec_lock.get("rules") or []),
    )


def build_slide_specs(slides_ir: dict[str, Any]) -> list[SlideSpec]:
    specs: list[SlideSpec] = []
    catalog = LayoutCatalog()
    for slide in slides_ir.get("slides", []):
        metadata = slide.get("quality") or {}
        content = dict(slide.get("content") or {})
        slide_type = str(slide.get("slide_type") or "content")
        layout_id = catalog.pick_layout(
            slide_type=slide_type,
            has_image=bool(content.get("image_query")),
            has_data=bool(content.get("categories") or content.get("series") or content.get("headers")),
        )
        specs.append(
            SlideSpec(
                id=str(slide.get("id") or f"slide_{len(specs) + 1:02d}"),
                index=int(slide.get("index") or len(specs) + 1),
                title=str(slide.get("title") or ""),
                slide_type=slide_type,
                layout_id=layout_id,
                narrative_role=str(metadata.get("narrative_role") or "supporting"),
                content=content,
                notes=str(slide.get("notes") or content.get("speaker_note") or ""),
                density_score=float(metadata.get("density_score") or 0),
                visual_role=str(metadata.get("visual_role") or _visual_role(slide)),
                needs_split=bool(metadata.get("needs_split")),
                asset_slots=_asset_slots(content),
                repair_hints=list(metadata.get("repair_hints") or []),
            )
        )
    return specs


def build_render_model(
    *,
    title: str,
    design_system: DesignSystem,
    slide_specs: list[SlideSpec],
    output_mode: str = "editable",
    exporter: str = "python-pptx",
) -> RenderModel:
    slides = []
    for spec in slide_specs:
        components = [
            RenderComponent(
                id=f"{spec.id}_title",
                type="textbox",
                role="title",
                content={"text": spec.title},
                constraints={"priority": 1, "max_lines": 2},
            ),
            RenderComponent(
                id=f"{spec.id}_body",
                type="group",
                role=spec.visual_role,
                content=spec.content,
                constraints={"density_score": spec.density_score, "needs_split": spec.needs_split},
            ),
        ]
        slides.append(
            RenderSlide(
                id=spec.id,
                index=spec.index,
                title=spec.title,
                layout_id=spec.layout_id,
                background={"color": design_system.background_color},
                components=components,
                notes=spec.notes,
            )
        )
    safe_output_mode = output_mode if output_mode in {"editable", "creative_image"} else "editable"
    safe_exporter = exporter if exporter in {"pptxgenjs", "python-pptx", "creative-image"} else "python-pptx"
    return RenderModel(
        title=title,
        output_mode=safe_output_mode,  # type: ignore[arg-type]
        design_system=design_system,
        slides=slides,
        exporter=safe_exporter,  # type: ignore[arg-type]
    )


def save_generation_artifacts(
    *,
    project: Any,
    settings: dict[str, str],
    outline: dict[str, Any],
    spec_lock: dict[str, Any],
    slides_ir: dict[str, Any],
    output_dir: str | Path,
    table_insights: dict[str, Any] | None = None,
    chart_specs: list[dict[str, Any]] | None = None,
    brand_tokens: dict[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    brief = build_generation_brief(project, settings)
    context_pack = ContextDistiller().build(
        project_id=getattr(project, "id", ""),
        outline=outline,
        table_insights=table_insights,
        chart_specs=chart_specs,
        brand_tokens=brand_tokens,
        source_context=str(outline.get("__brain_context__") or ""),
    )
    story_plan = build_story_plan(outline, style=brief.style, prompt=brief.prompt)
    design_system = build_design_system(spec_lock, style=brief.style, quality_mode=brief.quality_mode)
    slide_specs = build_slide_specs(slides_ir)
    render_model = build_render_model(
        title=brief.title,
        design_system=design_system,
        slide_specs=slide_specs,
        output_mode=brief.output_mode,
        exporter="creative-image" if brief.output_mode == "creative_image" else settings.get("exporter", "python-pptx"),
    )

    payloads = {
        "brief.json": brief.model_dump(mode="json"),
        "context_pack.json": context_pack.model_dump(mode="json"),
        "story_plan.json": story_plan.model_dump(mode="json"),
        "design_system.json": design_system.model_dump(mode="json"),
        "slide_specs.json": [item.model_dump(mode="json") for item in slide_specs],
        "render_model.json": render_model.model_dump(mode="json"),
    }
    paths: dict[str, str] = {}
    for filename, payload in payloads.items():
        path = root / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[filename] = str(path)
    return paths


def _narrative_role(slide: dict[str, Any], total: int) -> str:
    index = int(slide.get("index") or 1)
    slide_type = str(slide.get("slide_type") or "")
    if index == 1 or slide_type == "cover":
        return "anchor"
    if index == total or slide_type in {"summary", "closing"}:
        return "closing"
    if slide_type in {"section", "agenda"}:
        return "navigation"
    if slide_type.startswith("chart") or slide_type in {"metric_cards", "data_table"}:
        return "evidence"
    return "supporting"


def _rhythm_for_slide(index: int, total: int, slide_type: str) -> str:
    if index == 1 or slide_type == "cover":
        return "anchor"
    if index == total or slide_type == "closing":
        return "closing"
    if slide_type in {"section", "summary"}:
        return "breathing"
    if slide_type.startswith("chart") or slide_type in {"data_table", "metric_cards"}:
        return "evidence"
    return "content"


def _visual_role(slide: dict[str, Any]) -> str:
    slide_type = str(slide.get("slide_type") or "")
    if slide_type.startswith("chart"):
        return "chart"
    if slide_type in {"data_table"}:
        return "table"
    if slide_type in {"timeline", "comparison", "metric_cards"}:
        return "diagram"
    if (slide.get("assets") or {}).get("image_path"):
        return "image"
    return "text"


def _image_style(style: str) -> str:
    style = (style or "").lower()
    if style.startswith("swiss_"):
        return (
            "Swiss International Style evidence image: straight edges, single accent color, "
            "no title, no footer, no page chrome, no logo, no border"
        )
    if style == "editorial_ink":
        return "editorial magazine image, warm paper tone, documentary or crafted infographic feel"
    return "clean editorial stock-photo style"


def _icon_style(style: str) -> str:
    style = (style or "").lower()
    if style.startswith("swiss_"):
        return "minimal single-color line or geometric mark, no emoji, no shadow"
    if style == "editorial_ink":
        return "restrained line icon, ink-like, no emoji"
    return "single-color outline icons"


def _asset_slots(content: dict[str, Any]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    image_query = content.get("image_query")
    if image_query:
        slots.append({"kind": "image", "query": str(image_query), "required": False})
    icon_query = content.get("icon_query")
    if icon_query:
        slots.append({"kind": "icon", "query": str(icon_query), "required": False})
    if content.get("categories") or content.get("series"):
        slots.append({"kind": "chart", "query": str(content.get("chart_title") or ""), "required": True})
    if content.get("headers") or content.get("rows"):
        slots.append({"kind": "table", "query": str(content.get("title") or ""), "required": True})
    return slots

