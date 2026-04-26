"""Akita Brain adapter for structured ppt-maker generation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypeVar

from ppt_maker_inline.file_utils import ensure_dir, safe_name
from ppt_maker_inline.llm_json_parser import parse_llm_json_object
from ppt_models import ChartType, DeckMode, SlideType
from pydantic import BaseModel, ConfigDict, Field, ValidationError

T = TypeVar("T", bound=BaseModel)


def _strict_model() -> ConfigDict:
    return ConfigDict(extra="forbid", populate_by_name=True)


class BrainAccessError(RuntimeError):
    """Raised when host Brain cannot be used by this plugin."""


class RequirementQuestion(BaseModel):
    model_config = _strict_model()

    id: str
    question: str
    reason: str = ""
    options: list[str] = Field(default_factory=list)
    required: bool = True


class RequirementQuestions(BaseModel):
    model_config = _strict_model()

    mode: DeckMode
    questions: list[RequirementQuestion]
    recommended_slide_count: int = Field(default=8, ge=1, le=80)
    recommended_style: str = "tech_business"


class SourceSummary(BaseModel):
    model_config = _strict_model()

    title: str
    executive_summary: str
    key_points: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class TableInsightDraft(BaseModel):
    model_config = _strict_model()

    key_findings: list[str]
    chart_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    recommended_storyline: list[str] = Field(default_factory=list)
    risks_and_caveats: list[str] = Field(default_factory=list)


class TemplateBrandDraft(BaseModel):
    model_config = _strict_model()

    primary_color: str = "#3457D5"
    secondary_color: str = "#172033"
    accent_color: str = "#FFB000"
    font_heading: str = "Microsoft YaHei"
    font_body: str = "Microsoft YaHei"
    layout_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OutlineSlide(BaseModel):
    model_config = _strict_model()

    index: int = Field(ge=1)
    title: str
    purpose: str
    slide_type: SlideType = SlideType.CONTENT
    key_points: list[str] = Field(default_factory=list)


class OutlineDraft(BaseModel):
    model_config = _strict_model()

    title: str
    mode: DeckMode
    audience: str = ""
    storyline: list[str] = Field(default_factory=list)
    slides: list[OutlineSlide]
    confirmation_questions: list[str] = Field(default_factory=list)


class DesignSpecDraft(BaseModel):
    model_config = _strict_model()

    design_spec_markdown: str
    spec_lock: dict[str, Any]
    confirmation_questions: list[str] = Field(default_factory=list)


class SlideIrDraft(BaseModel):
    model_config = _strict_model()

    slides: list[dict[str, Any]]
    audit_notes: list[str] = Field(default_factory=list)


class RewriteSlideDraft(BaseModel):
    model_config = _strict_model()

    slide_id: str
    title: str
    slide_type: SlideType
    content: dict[str, Any]
    change_summary: str


class PptBrainAdapter:
    """Thin wrapper around Akita Brain with strict JSON/Pydantic validation."""

    def __init__(self, api: Any, *, data_root: str | Path) -> None:
        self._api = api
        self._data_root = Path(data_root)

    def has_brain_access(self) -> bool:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission):
            return bool(has_permission("brain.access"))
        return False

    def get_brain(self) -> Any:
        if not self.has_brain_access():
            raise BrainAccessError("brain.access not granted")
        get_brain = getattr(self._api, "get_brain", None)
        brain = get_brain() if callable(get_brain) else None
        if brain is None:
            raise BrainAccessError("Host Brain is not available")
        return brain

    async def build_requirement_questions(
        self,
        *,
        mode: DeckMode,
        user_prompt: str,
        project_id: str | None = None,
    ) -> RequirementQuestions:
        prompt = f"""
Return JSON for requirement questions for a PowerPoint project.
Mode: {mode.value}
User prompt: {user_prompt}
Schema: {{
  "mode": "{mode.value}",
  "questions": [{{"id": "...", "question": "...", "reason": "...", "options": [], "required": true}}],
  "recommended_slide_count": 8,
  "recommended_style": "tech_business"
}}
"""
        return await self._call_json(
            label="requirement_questions",
            prompt=prompt,
            model=RequirementQuestions,
            project_id=project_id,
        )

    async def summarize_sources(self, *, context_markdown: str, project_id: str | None = None) -> SourceSummary:
        prompt = f"""
Summarize the source material for a presentation. Return strict JSON.
Source material:
{context_markdown[:20000]}
"""
        return await self._call_json(
            label="source_summary",
            prompt=prompt,
            model=SourceSummary,
            project_id=project_id,
        )

    async def profile_table(
        self,
        *,
        dataset_profile: dict[str, Any],
        project_id: str | None = None,
    ) -> TableInsightDraft:
        return await self.generate_table_insights(
            dataset_profile=dataset_profile,
            project_id=project_id,
        )

    async def generate_table_insights(
        self,
        *,
        dataset_profile: dict[str, Any],
        project_id: str | None = None,
    ) -> TableInsightDraft:
        prompt = f"""
Turn this deterministic dataset profile into executive presentation insights.
Return JSON with key_findings, chart_suggestions, recommended_storyline, risks_and_caveats.
Allowed chart types: {[item.value for item in ChartType]}
Dataset profile:
{json.dumps(dataset_profile, ensure_ascii=False)}
"""
        return await self._call_json(
            label="table_insights",
            prompt=prompt,
            model=TableInsightDraft,
            project_id=project_id,
        )

    async def diagnose_template_brand(
        self,
        *,
        template_profile: dict[str, Any],
        project_id: str | None = None,
    ) -> TemplateBrandDraft:
        prompt = f"""
Infer brand tokens from this PPTX template profile. Return strict JSON.
Template profile:
{json.dumps(template_profile, ensure_ascii=False)}
"""
        return await self._call_json(
            label="template_brand",
            prompt=prompt,
            model=TemplateBrandDraft,
            project_id=project_id,
        )

    async def generate_outline(
        self,
        *,
        mode: DeckMode,
        requirements: dict[str, Any],
        context: str = "",
        project_id: str | None = None,
    ) -> OutlineDraft:
        prompt = f"""
Generate a confirmed-ready presentation outline. Return JSON only.
Mode: {mode.value}
Requirements: {json.dumps(requirements, ensure_ascii=False)}
Context: {context[:16000]}
"""
        return await self._call_json(
            label="outline",
            prompt=prompt,
            model=OutlineDraft,
            project_id=project_id,
        )

    async def revise_outline(
        self,
        *,
        outline: dict[str, Any],
        instruction: str,
        project_id: str | None = None,
    ) -> OutlineDraft:
        prompt = f"""
Revise this presentation outline according to the instruction. Return JSON only.
Instruction: {instruction}
Outline: {json.dumps(outline, ensure_ascii=False)}
"""
        return await self._call_json(
            label="outline_revision",
            prompt=prompt,
            model=OutlineDraft,
            project_id=project_id,
        )

    async def generate_design_spec(
        self,
        *,
        outline: dict[str, Any],
        brand_tokens: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> DesignSpecDraft:
        prompt = f"""
Create design_spec_markdown and machine-readable spec_lock JSON for this deck.
Outline: {json.dumps(outline, ensure_ascii=False)}
Brand tokens: {json.dumps(brand_tokens or {}, ensure_ascii=False)}
"""
        return await self._call_json(
            label="design_spec",
            prompt=prompt,
            model=DesignSpecDraft,
            project_id=project_id,
        )

    async def generate_slide_ir(
        self,
        *,
        outline: dict[str, Any],
        design_spec: dict[str, Any],
        project_id: str | None = None,
    ) -> SlideIrDraft:
        prompt = f"""
Generate editable slide IR JSON for ppt-maker.
Outline: {json.dumps(outline, ensure_ascii=False)}
Design spec: {json.dumps(design_spec, ensure_ascii=False)}
"""
        return await self._call_json(
            label="slide_ir",
            prompt=prompt,
            model=SlideIrDraft,
            project_id=project_id,
        )

    async def rewrite_slide(
        self,
        *,
        slide: dict[str, Any],
        instruction: str,
        project_id: str | None = None,
    ) -> RewriteSlideDraft:
        prompt = f"""
Rewrite one slide IR item according to the instruction. Return JSON only.
Instruction: {instruction}
Slide: {json.dumps(slide, ensure_ascii=False)}
"""
        return await self._call_json(
            label="rewrite_slide",
            prompt=prompt,
            model=RewriteSlideDraft,
            project_id=project_id,
        )

    async def _call_json(
        self,
        *,
        label: str,
        prompt: str,
        model: type[T],
        project_id: str | None,
    ) -> T:
        brain = self.get_brain()
        system = (
            "You are the OpenAkita ppt-maker planning engine. "
            "Return strict JSON only. Do not include Markdown fences unless unavoidable."
        )
        log_dir = self._log_dir(project_id)
        started_at = time.time()
        request_path = self._write_log(
            log_dir,
            label,
            "request",
            {"label": label, "system": system, "prompt": prompt, "model": model.__name__},
        )
        try:
            response = await brain.think(prompt, system=system, max_tokens=4096)
            raw = getattr(response, "content", response)
            text = raw if isinstance(raw, str) else str(raw)
            parsed = parse_llm_json_object(text, fallback=None)
            if parsed is None:
                raise ValueError("Brain response did not contain a JSON object")
            result = model.model_validate(parsed)
        except (ValidationError, ValueError, TypeError) as exc:
            self._write_log(
                log_dir,
                label,
                "validation_error",
                {
                    "request_path": str(request_path),
                    "error": str(exc),
                    "elapsed_sec": round(time.time() - started_at, 3),
                },
            )
            raise
        self._write_log(
            log_dir,
            label,
            "response",
            {
                "request_path": str(request_path),
                "raw": text,
                "validated": result.model_dump(mode="json"),
                "elapsed_sec": round(time.time() - started_at, 3),
            },
        )
        return result

    def _log_dir(self, project_id: str | None) -> Path:
        if project_id:
            return ensure_dir(self._data_root / "projects" / safe_name(project_id) / "logs")
        return ensure_dir(self._data_root / "logs")

    def _write_log(self, log_dir: Path, label: str, kind: str, payload: dict[str, Any]) -> Path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"{timestamp}_{safe_name(label)}_{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

