"""Generation v2 models for ppt-maker.

These models describe the higher-level planning artifacts that sit above the
legacy ``slides_ir.json`` file. They let the pipeline persist each decision
stage without forcing the exporter rewrite to happen in the same change.
"""

from __future__ import annotations

from typing import Any, Literal

from ppt_models import DeckMode
from pydantic import BaseModel, ConfigDict, Field


def _strict() -> ConfigDict:
    return ConfigDict(extra="forbid", populate_by_name=True)


OutputMode = Literal["editable", "creative_image"]
QualityMode = Literal["draft", "standard", "deep_design"]


class GenerationBrief(BaseModel):
    model_config = _strict()

    title: str
    mode: DeckMode
    prompt: str = ""
    audience: str = ""
    style: str = "tech_business"
    slide_count: int = Field(default=8, ge=1, le=80)
    language: str = "zh-CN"
    tone: str = "professional"
    verbosity: str = "balanced"
    quality_mode: QualityMode = "standard"
    output_mode: OutputMode = "editable"


class SourceDigest(BaseModel):
    model_config = _strict()

    title: str
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    source_id: str | None = None


class ContextPack(BaseModel):
    model_config = _strict()

    project_id: str
    source_digests: list[SourceDigest] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    table_insights: dict[str, Any] | None = None
    chart_specs: list[dict[str, Any]] = Field(default_factory=list)
    brand_tokens: dict[str, Any] | None = None
    citations: list[dict[str, str]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class StorySlide(BaseModel):
    model_config = _strict()

    index: int = Field(ge=1)
    title: str
    purpose: str = ""
    slide_type: str = "content"
    narrative_role: str = "supporting"
    key_message: str = ""


class StoryPlan(BaseModel):
    model_config = _strict()

    title: str
    storyline: list[str] = Field(default_factory=list)
    slides: list[StorySlide] = Field(default_factory=list)
    rhythm: list[str] = Field(default_factory=list)


class TypographyScale(BaseModel):
    model_config = _strict()

    title: int = 32
    subtitle: int = 22
    body: int = 16
    caption: int = 12
    hero: int = 48


class SpacingScale(BaseModel):
    model_config = _strict()

    margin_x: int = 60
    margin_y: int = 50
    gap: int = 24
    card_padding: int = 24
    radius: int = 12


class DesignSystem(BaseModel):
    model_config = _strict()

    theme_id: str = "tech_business"
    primary_color: str = "#3457D5"
    secondary_color: str = "#172033"
    accent_color: str = "#FFB000"
    background_color: str = "#FFFFFF"
    font_heading: str = "Microsoft YaHei"
    font_body: str = "Microsoft YaHei"
    typography: TypographyScale = Field(default_factory=TypographyScale)
    spacing: SpacingScale = Field(default_factory=SpacingScale)
    chart_palette: list[str] = Field(default_factory=list)
    image_style: str = "clean editorial stock-photo style"
    icon_style: str = "single-color outline icons"
    density: Literal["airy", "balanced", "dense"] = "balanced"
    rules: list[str] = Field(default_factory=list)


class AssetSlot(BaseModel):
    model_config = _strict()

    kind: Literal["image", "icon", "chart", "table"]
    query: str = ""
    required: bool = False


class SlideSpec(BaseModel):
    model_config = _strict()

    id: str
    index: int = Field(ge=1)
    title: str
    slide_type: str = "content"
    layout_id: str = "content"
    narrative_role: str = "supporting"
    content: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    density_score: float = Field(default=0, ge=0, le=1)
    visual_role: str = "text"
    needs_split: bool = False
    asset_slots: list[AssetSlot] = Field(default_factory=list)
    repair_hints: list[str] = Field(default_factory=list)


class RenderComponent(BaseModel):
    model_config = _strict()

    id: str
    type: Literal["textbox", "shape", "image", "icon", "chart", "table", "group"]
    role: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class RenderSlide(BaseModel):
    model_config = _strict()

    id: str
    index: int = Field(ge=1)
    title: str
    layout_id: str
    background: dict[str, Any] = Field(default_factory=dict)
    components: list[RenderComponent] = Field(default_factory=list)
    notes: str = ""


class RenderModel(BaseModel):
    model_config = _strict()

    version: int = 1
    title: str
    output_mode: OutputMode = "editable"
    design_system: DesignSystem
    slides: list[RenderSlide] = Field(default_factory=list)
    exporter: Literal["pptxgenjs", "python-pptx", "creative-image"] = "python-pptx"

