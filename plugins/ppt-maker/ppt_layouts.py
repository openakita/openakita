"""Slide layout registry & per-type content schemas.

This module defines the structured content shape that the LLM must produce for
each `SlideType`. Each schema corresponds (loosely) to a "layout" in the
presenton sense: a Pydantic model that the LLM fills, then the exporter knows
how to render. It is the contract shared by:
  - `PptBrainAdapter.generate_slide_content_per_slide` (uses `model_json_schema`)
  - `SlideIrBuilder._content_for_type` (validates AI output / picks fallback)
  - `PptxExporter` renderers (consume validated dicts)

All models forbid extra fields so the LLM cannot smuggle in unexpected keys.
Optional fields default to safe values so partial responses still validate.
"""

from __future__ import annotations

from typing import Any

from ppt_models import SlideType
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LAYOUT_REGISTRY",
    "LayoutAgenda",
    "LayoutChart",
    "LayoutClosing",
    "LayoutComparison",
    "LayoutContentBullets",
    "LayoutCover",
    "LayoutDataOverview",
    "LayoutDataTable",
    "LayoutInsightSummary",
    "LayoutMetricCards",
    "LayoutSection",
    "LayoutSummary",
    "LayoutTimeline",
    "TimelineMilestone",
    "MetricCard",
    "ComparisonColumn",
    "ChartSeries",
    "layout_for",
    "fill_required",
    "describe_layouts_for_prompt",
]


def _strict() -> ConfigDict:
    return ConfigDict(extra="forbid", populate_by_name=True)


class _BaseLayout(BaseModel):
    """Common knobs every slide can express."""

    model_config = _strict()

    speaker_note: str = Field(default="", max_length=600)


# ── Cover & closing ────────────────────────────────────────────────────────


class LayoutCover(_BaseLayout):
    title: str
    subtitle: str = ""
    presenter: str = ""
    date: str = ""
    image_query: str | None = None


class LayoutSection(_BaseLayout):
    """A divider slide announcing the next chapter."""

    section_title: str
    section_subtitle: str = ""
    icon_query: str | None = None


class LayoutClosing(_BaseLayout):
    headline: str = "Thank You"
    body: str = ""
    contact: str = ""


# ── Generic content ────────────────────────────────────────────────────────


class LayoutAgenda(_BaseLayout):
    items: list[str] = Field(default_factory=list, min_length=1, max_length=12)
    icon_query: str | None = None


class LayoutContentBullets(_BaseLayout):
    body: str = ""
    bullets: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    image_query: str | None = None
    icon_query: str | None = None


class ComparisonColumn(BaseModel):
    model_config = _strict()

    title: str
    bullets: list[str] = Field(default_factory=list, max_length=6)


class LayoutComparison(_BaseLayout):
    left: ComparisonColumn
    right: ComparisonColumn
    summary: str = ""


class TimelineMilestone(BaseModel):
    model_config = _strict()

    label: str
    title: str
    description: str = ""


class LayoutTimeline(_BaseLayout):
    milestones: list[TimelineMilestone] = Field(default_factory=list, min_length=2, max_length=8)
    body: str = ""


# ── Data-oriented layouts ──────────────────────────────────────────────────


class MetricCard(BaseModel):
    model_config = _strict()

    label: str
    value: str
    delta: str = ""


class LayoutMetricCards(_BaseLayout):
    metrics: list[MetricCard] = Field(default_factory=list, min_length=1, max_length=6)
    bullets: list[str] = Field(default_factory=list, max_length=4)


class ChartSeries(BaseModel):
    model_config = _strict()

    name: str
    values: list[float]


class LayoutChart(_BaseLayout):
    chart_title: str = ""
    chart_type: str = "bar"
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list, max_length=4)


class LayoutDataTable(_BaseLayout):
    headers: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    rows: list[list[str]] = Field(default_factory=list, min_length=0, max_length=12)
    bullets: list[str] = Field(default_factory=list, max_length=4)


class LayoutDataOverview(_BaseLayout):
    body: str = ""
    bullets: list[str] = Field(default_factory=list, min_length=1, max_length=6)
    metrics: list[MetricCard] = Field(default_factory=list, max_length=4)


class LayoutInsightSummary(_BaseLayout):
    findings: list[str] = Field(default_factory=list, min_length=1, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=5)


class LayoutSummary(_BaseLayout):
    body: str = ""
    bullets: list[str] = Field(default_factory=list, min_length=1, max_length=6)


# ── Registry ───────────────────────────────────────────────────────────────


LAYOUT_REGISTRY: dict[SlideType, type[_BaseLayout]] = {
    SlideType.COVER: LayoutCover,
    SlideType.SECTION: LayoutSection,
    SlideType.CLOSING: LayoutClosing,
    SlideType.AGENDA: LayoutAgenda,
    SlideType.CONTENT: LayoutContentBullets,
    SlideType.COMPARISON: LayoutComparison,
    SlideType.TIMELINE: LayoutTimeline,
    SlideType.METRIC_CARDS: LayoutMetricCards,
    SlideType.CHART_BAR: LayoutChart,
    SlideType.CHART_LINE: LayoutChart,
    SlideType.CHART_PIE: LayoutChart,
    SlideType.DATA_TABLE: LayoutDataTable,
    SlideType.DATA_OVERVIEW: LayoutDataOverview,
    SlideType.INSIGHT_SUMMARY: LayoutInsightSummary,
    SlideType.SUMMARY: LayoutSummary,
}


def layout_for(slide_type: SlideType | str) -> type[_BaseLayout]:
    """Return the Pydantic model that backs the given slide type."""
    if isinstance(slide_type, str):
        try:
            slide_type = SlideType(slide_type)
        except ValueError:
            return LayoutContentBullets
    return LAYOUT_REGISTRY.get(slide_type, LayoutContentBullets)


def fill_required(slide_type: SlideType | str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Validate-or-coerce raw dict into the layout schema, falling back to safe
    defaults when the LLM returned partial data. Always returns a dict that is
    safe to render."""
    model = layout_for(slide_type)
    payload = dict(raw or {})
    try:
        return model.model_validate(payload).model_dump(mode="json")
    except Exception:
        # Last-resort: keep as much as we can and fill required slots with empty placeholders.
        cleaned = {key: payload[key] for key in payload if key in model.model_fields}
        for name, field in model.model_fields.items():
            if name in cleaned:
                continue
            if field.is_required():
                annotation = field.annotation
                if annotation is str:
                    cleaned[name] = ""
                elif annotation is list[str] or repr(annotation).endswith("list[str]"):
                    cleaned[name] = []
                else:
                    cleaned[name] = []
        try:
            return model.model_validate(cleaned).model_dump(mode="json")
        except Exception:
            return cleaned


def describe_layouts_for_prompt() -> str:
    """Return a compact human-readable description of every layout, used as
    additional context for the structure-selection LLM call."""
    lines = ["# Available slide layouts (choose the best fit per slide):"]
    description = {
        SlideType.COVER: "Title slide. Use for slide 1 only.",
        SlideType.SECTION: "Section divider between major chapters.",
        SlideType.AGENDA: "Numbered list of upcoming sections. Use early in the deck.",
        SlideType.CONTENT: "Body paragraph + 3-5 bullets. The default for narrative slides.",
        SlideType.COMPARISON: "Two-column comparison. Use when explaining A vs B / before vs after.",
        SlideType.TIMELINE: "2-8 milestones with labels. Use for roadmaps / phased plans.",
        SlideType.METRIC_CARDS: "Highlighted numeric KPIs. Use only when content has metrics.",
        SlideType.CHART_BAR: "Bar chart with categories+series. Use for comparisons of quantities.",
        SlideType.CHART_LINE: "Line chart with categories+series. Use for trends over time.",
        SlideType.CHART_PIE: "Pie chart. Use only for share/composition (single series, <=8 slices).",
        SlideType.DATA_TABLE: "Headered table. Use for textual or non-graphable data.",
        SlideType.DATA_OVERVIEW: "Overview of a dataset (scope, freshness, caveats).",
        SlideType.INSIGHT_SUMMARY: "Key findings + risks. Use after a chart / data block.",
        SlideType.SUMMARY: "Wrap-up slide near the end (conclusions + next steps).",
        SlideType.CLOSING: "Final thank-you / Q&A. Last slide only.",
    }
    for st in SlideType:
        if st in LAYOUT_REGISTRY:
            lines.append(f"- {st.value}: {description.get(st, '')}")
    return "\n".join(lines)
