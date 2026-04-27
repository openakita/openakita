"""Akita Brain adapter for structured ppt-maker generation.

Three-stage LLM pipeline (mirrors presenton):
  1. ``generate_outline``                — title / audience / per-slide titles + bullets + body
  2. ``select_layout_per_slide``         — pick best ``SlideType`` for each outline slide
  3. ``generate_slide_content_per_slide``— fill the layout schema for one slide

Plus ``compose_additional_context`` that consolidates parsed source materials,
table insights, template brand notes, and (optionally) web search results into
a single Markdown-ish blob the prompts can paste directly.

All Brain calls validate against a strict Pydantic model. Failures raise so the
caller can fall back to deterministic builders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, TypeVar

from ppt_layouts import describe_layouts_for_prompt, fill_required, layout_for
from ppt_maker_inline.file_utils import ensure_dir, safe_name
from ppt_maker_inline.llm_json_parser import parse_llm_json_object
from ppt_models import ChartType, DeckMode, SlideType
from pydantic import BaseModel, ConfigDict, Field, ValidationError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


def _strict_model() -> ConfigDict:
    return ConfigDict(extra="forbid", populate_by_name=True)


VERBOSITY_HINT: dict[str, str] = {
    "concise": "20 words per body, 3 bullets max (~10 words each)",
    "balanced": "40 words per body, 4 bullets (~14 words each)",
    "detailed": "60 words per body, 5 bullets (~18 words each)",
}


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
    purpose: str = ""
    slide_type: SlideType = SlideType.CONTENT
    key_points: list[str] = Field(default_factory=list)
    body: str = ""
    speaker_note: str = ""
    image_query: str | None = None
    icon_query: str | None = None


class OutlineDraft(BaseModel):
    model_config = _strict_model()

    title: str
    mode: DeckMode
    audience: str = ""
    storyline: list[str] = Field(default_factory=list)
    slides: list[OutlineSlide]
    confirmation_questions: list[str] = Field(default_factory=list)


class LayoutChoice(BaseModel):
    model_config = _strict_model()

    index: int = Field(ge=1)
    slide_type: SlideType


class LayoutPlan(BaseModel):
    model_config = _strict_model()

    slides: list[LayoutChoice] = Field(default_factory=list)


class SlideContentDraft(BaseModel):
    """Loose container — the validated payload lives in ``content``."""

    model_config = _strict_model()

    slide_type: SlideType
    content: dict[str, Any]


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
        self._activity: Any = None
        self._activity_emit: Any = None

    def bind_activity_logger(self, logger_obj: Any, *, emit: Any = None) -> None:
        """Attach a ``PptActivityLogger`` so each Brain call shows up in UI."""
        self._activity = logger_obj
        self._activity_emit = emit

    async def _record_activity(
        self,
        *,
        project_id: str | None,
        stage: str,
        status: str,
        message: str = "",
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._activity is None or not project_id:
            return
        try:
            event = await self._activity.append(
                project_id=project_id,
                stage=stage,
                status=status,
                level=level,
                message=message,
                details=details or {},
            )
        except Exception:  # noqa: BLE001
            logger.debug("ppt-maker: activity append failed", exc_info=True)
            return
        if self._activity_emit is not None:
            try:
                await self._activity_emit(event)
            except Exception:  # noqa: BLE001
                logger.debug("ppt-maker: activity emit failed", exc_info=True)

    # ── Brain availability ─────────────────────────────────────────────

    def has_brain_access(self) -> bool:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission):
            try:
                return bool(has_permission("brain.access"))
            except Exception:
                return False
        return False

    def get_brain(self) -> Any:
        if not self.has_brain_access():
            raise BrainAccessError("brain.access not granted")
        get_brain = getattr(self._api, "get_brain", None)
        brain = get_brain() if callable(get_brain) else None
        if brain is None:
            raise BrainAccessError("Host Brain is not available")
        return brain

    # ── Requirement / source / table / template helpers (existing) ─────

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

    async def summarize_sources(
        self, *, context_markdown: str, project_id: str | None = None
    ) -> SourceSummary:
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

    # ── Three-stage pipeline ───────────────────────────────────────────

    async def generate_outline(
        self,
        *,
        mode: DeckMode,
        requirements: dict[str, Any],
        context: str = "",
        project_id: str | None = None,
        verbosity: str = "balanced",
        tone: str = "professional",
        language: str = "zh-CN",
    ) -> OutlineDraft:
        verbosity_hint = VERBOSITY_HINT.get(verbosity, VERBOSITY_HINT["balanced"])
        title = (requirements.get("title") or "").strip()
        prompt_text = (requirements.get("prompt") or "").strip()
        audience = (requirements.get("audience") or "").strip()
        style = (requirements.get("style") or "tech_business").strip()
        slide_count = int(requirements.get("slide_count") or 8)
        slide_count = max(3, min(slide_count, 30))

        outline_schema = OutlineDraft.model_json_schema()
        prompt = f"""You are designing a professional presentation outline.

# Goal
- Title hint: {title or "(let model decide)"}
- User prompt: {prompt_text or "(none)"}
- Audience: {audience or "general business audience"}
- Visual style hint: {style}
- Mode: {mode.value}
- Target slide count: {slide_count}
- Tone: {tone}
- Output language: {language}
- Verbosity per slide: {verbosity_hint}

# Required slide structure
1. Slide 1 must be slide_type "cover".
2. Slide 2 should be slide_type "agenda" (skip only if slide_count <= 4).
3. Final slide must be slide_type "closing" or "summary".
4. Use a mix of slide_type values: content, comparison, timeline, metric_cards, chart_bar, chart_line, data_table, insight_summary, summary, section, closing.
5. Each slide must have a meaningful, unique ``title`` (no "Slide 3" placeholders).
6. Each slide must populate ``body`` with a complete sentence (or two) and ``key_points`` with 2-5 short bullets.
7. ``speaker_note`` should be 1-2 sentences a presenter would actually say.
8. ``image_query`` is a short English phrase (3-6 words) suitable for stock-photo search; null when no image needed.
9. ``icon_query`` is a single English keyword (e.g. "growth", "team", "shield") or null.
10. Stay grounded in the provided context — never invent statistics; if uncertain, write qualitative statements.

# Context (sources / table insights / web search snippets)
{context[:18000] if context else "(none)"}

# Output
Return STRICT JSON matching this schema (no Markdown fences, no commentary):
{json.dumps(outline_schema, ensure_ascii=False)}
"""
        return await self._call_json(
            label="outline",
            prompt=prompt,
            model=OutlineDraft,
            project_id=project_id,
        )

    async def select_layout_per_slide(
        self,
        *,
        outline: dict[str, Any],
        project_id: str | None = None,
    ) -> LayoutPlan:
        slide_summaries = []
        for slide in outline.get("slides", [])[:40]:
            slide_summaries.append(
                {
                    "index": slide.get("index"),
                    "title": slide.get("title"),
                    "current_type": slide.get("slide_type"),
                    "key_points": slide.get("key_points", [])[:5],
                    "body": (slide.get("body") or slide.get("purpose") or "")[:240],
                }
            )
        prompt = f"""You are picking the best slide layout per slide for a deck.

# Layouts
{describe_layouts_for_prompt()}

# Rules
- Slide 1 → "cover".
- Last slide → "closing" or "summary".
- "agenda" appears at most once and only near the start.
- "metric_cards", "chart_bar", "chart_line", "chart_pie", "data_table" only when the slide actually needs numbers.
- Variety is good: avoid using "content" more than 60% of slides if alternatives fit.

# Slides
{json.dumps(slide_summaries, ensure_ascii=False)}

# Output
Return STRICT JSON:
{{"slides": [{{"index": 1, "slide_type": "cover"}}, ...]}}
"""
        return await self._call_json(
            label="layout_plan",
            prompt=prompt,
            model=LayoutPlan,
            project_id=project_id,
        )

    async def generate_slide_content_per_slide(
        self,
        *,
        slide_outline: dict[str, Any],
        slide_type: SlideType,
        deck_title: str = "",
        verbosity: str = "balanced",
        tone: str = "professional",
        language: str = "zh-CN",
        context: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        layout_model = layout_for(slide_type)
        schema = layout_model.model_json_schema()
        verbosity_hint = VERBOSITY_HINT.get(verbosity, VERBOSITY_HINT["balanced"])
        prompt = f"""You are filling one slide of a presentation.

# Deck
- Deck title: {deck_title}
- Slide index: {slide_outline.get("index")}
- Slide title: {slide_outline.get("title")}
- Slide type: {slide_type.value}
- Outline body: {slide_outline.get("body") or slide_outline.get("purpose") or ""}
- Outline bullets: {json.dumps(slide_outline.get("key_points", []), ensure_ascii=False)}
- Tone: {tone}
- Language: {language}
- Verbosity: {verbosity_hint}

# Context (use only to enrich; never invent specific numbers)
{context[:8000] if context else "(none)"}

# Schema you must follow
{json.dumps(schema, ensure_ascii=False)}

# Hard rules
- Output STRICT JSON matching the schema above. No Markdown fences.
- Every required field must be populated with meaningful content.
- ``image_query`` (when present) is a 3-6 word English stock-photo query, or null.
- ``icon_query`` (when present) is a single English keyword such as "growth", "shield", "users".
- ``speaker_note`` is 1-2 natural-language sentences.
- For data-heavy layouts (chart_*, data_table, metric_cards), only include numbers that are present in the context; otherwise use qualitative descriptors like "increasing", "majority", "low".
"""
        brain = self.get_brain()
        system = (
            "You are the OpenAkita ppt-maker per-slide content engine. "
            "Return strict JSON only. Do not include Markdown fences."
        )
        log_dir = self._log_dir(project_id)
        started_at = time.time()
        request_path = self._write_log(
            log_dir,
            "slide_content",
            "request",
            {
                "system": system,
                "prompt": prompt,
                "slide_type": slide_type.value,
                "slide_index": slide_outline.get("index"),
            },
        )
        try:
            response = await brain.think(prompt, system=system, max_tokens=2048)
            raw = getattr(response, "content", response)
            text = raw if isinstance(raw, str) else str(raw)
            parsed = parse_llm_json_object(text, fallback=None)
            if parsed is None:
                raise ValueError("Brain response did not contain a JSON object")
            content = fill_required(slide_type, parsed)
        except (ValidationError, ValueError, TypeError) as exc:
            self._write_log(
                log_dir,
                "slide_content",
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
            "slide_content",
            "response",
            {
                "request_path": str(request_path),
                "raw": text,
                "validated": content,
                "elapsed_sec": round(time.time() - started_at, 3),
            },
        )
        return content

    # ── Context composer ───────────────────────────────────────────────

    async def compose_additional_context(
        self,
        *,
        manager: Any,
        project: Any,
        web_search_enabled: bool = False,
        max_web_results: int = 5,
    ) -> str:
        """Gather plugin-side context (sources / table insights / template /
        web search) into a single Markdown blob suitable for prompt injection."""
        chunks: list[str] = []

        # Linked source ids come from project.metadata.source_ids; we also pick
        # up everything else linked to this project as a safety net.
        source_ids: list[str] = []
        meta = getattr(project, "metadata", {}) or {}
        if isinstance(meta, dict):
            ids = meta.get("source_ids")
            if isinstance(ids, list):
                source_ids = [str(x) for x in ids if x]

        try:
            project_sources = await manager.list_sources(project_id=project.id, limit=20)
        except Exception:
            project_sources = []

        seen_ids: set[str] = set()
        ordered = []
        if source_ids:
            id_index = {s.id: s for s in project_sources}
            for sid in source_ids:
                src = id_index.get(sid)
                if src and src.id not in seen_ids:
                    ordered.append(src)
                    seen_ids.add(src.id)
                    continue
                # Fall back to single fetch if not in the listing
                try:
                    src = await manager.get_source(sid)
                except Exception:
                    src = None
                if src and src.id not in seen_ids:
                    ordered.append(src)
                    seen_ids.add(src.id)
        for src in project_sources:
            if src.id not in seen_ids:
                ordered.append(src)
                seen_ids.add(src.id)

        if ordered:
            chunks.append("## Source materials")
            for src in ordered[:8]:
                excerpt = self._source_excerpt(src)
                title = src.filename or src.id
                chunks.append(f"### {title}\n{excerpt}")

        # Linked dataset insights
        dataset_id = getattr(project, "dataset_id", None)
        if dataset_id:
            try:
                dataset = await manager.get_dataset(dataset_id)
            except Exception:
                dataset = None
            if dataset and getattr(dataset, "insights_path", None):
                insights = self._read_json_safe(dataset.insights_path)
                if insights:
                    chunks.append("## Table insights\n" + self._json_to_md(insights))

        # Linked template brand notes
        template_id = getattr(project, "template_id", None)
        if template_id:
            try:
                template = await manager.get_template(template_id)
            except Exception:
                template = None
            if template and getattr(template, "brand_tokens_path", None):
                brand = self._read_json_safe(template.brand_tokens_path)
                if brand:
                    chunks.append("## Template brand tokens\n" + self._json_to_md(brand))

        # Optional web search using OpenAkita's built-in handler
        if web_search_enabled:
            await self._record_activity(
                project_id=getattr(project, "id", None),
                stage="web_search",
                status="start",
                message="启动主程序内置 Web 搜索补充事实",
            )
            web_text = await self._web_search_context(
                title=getattr(project, "title", "") or "",
                prompt=getattr(project, "prompt", "") or "",
                max_results=max_web_results,
            )
            if web_text:
                chunks.append("## Web search context\n" + web_text)
                await self._record_activity(
                    project_id=getattr(project, "id", None),
                    stage="web_search",
                    status="success",
                    message=f"Web 搜索完成（{len(web_text)} 字符）",
                    details={"chars": len(web_text)},
                )
            else:
                await self._record_activity(
                    project_id=getattr(project, "id", None),
                    stage="web_search",
                    status="fallback",
                    level="warn",
                    message="Web 搜索没有返回结果",
                )

        return "\n\n".join(chunks)

    # ── Existing helpers ───────────────────────────────────────────────

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

    # ── Generic Brain JSON call ────────────────────────────────────────

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
        await self._record_activity(
            project_id=project_id,
            stage=f"brain.{label}",
            status="start",
            message=f"调用 Brain：{label}",
            details={
                "schema": model.__name__,
                "prompt_chars": len(prompt),
            },
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
            elapsed = round(time.time() - started_at, 3)
            self._write_log(
                log_dir,
                label,
                "validation_error",
                {
                    "request_path": str(request_path),
                    "error": str(exc),
                    "elapsed_sec": elapsed,
                },
            )
            await self._record_activity(
                project_id=project_id,
                stage=f"brain.{label}",
                status="error",
                level="warn",
                message=f"Brain 返回解析失败：{exc}",
                details={
                    "schema": model.__name__,
                    "elapsed_sec": elapsed,
                    "request_log": str(request_path),
                },
            )
            raise
        elapsed = round(time.time() - started_at, 3)
        self._write_log(
            log_dir,
            label,
            "response",
            {
                "request_path": str(request_path),
                "raw": text,
                "validated": result.model_dump(mode="json"),
                "elapsed_sec": elapsed,
            },
        )
        await self._record_activity(
            project_id=project_id,
            stage=f"brain.{label}",
            status="success",
            message=f"Brain 返回有效 JSON ({elapsed}s)",
            details={
                "schema": model.__name__,
                "elapsed_sec": elapsed,
                "response_chars": len(text),
            },
        )
        return result

    # ── Logging / file helpers ─────────────────────────────────────────

    def _log_dir(self, project_id: str | None) -> Path:
        if project_id:
            return ensure_dir(self._data_root / "projects" / safe_name(project_id) / "logs")
        return ensure_dir(self._data_root / "logs")

    def _write_log(self, log_dir: Path, label: str, kind: str, payload: dict[str, Any]) -> Path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"{timestamp}_{safe_name(label)}_{kind}.json"
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("ppt-maker brain log write failed")
        return path

    # ── Context helpers ────────────────────────────────────────────────

    @staticmethod
    def _read_json_safe(path: str | None) -> Any:
        if not path:
            return None
        file_path = Path(path)
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _json_to_md(payload: Any, *, max_chars: int = 4000) -> str:
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(payload)
        return text[:max_chars]

    @staticmethod
    def _source_excerpt(source: Any, *, max_chars: int = 1800) -> str:
        meta = getattr(source, "metadata", {}) or {}
        if isinstance(meta, dict):
            for key in ("excerpt", "summary", "preview", "text"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:max_chars]
            tables = meta.get("tables") or meta.get("rows")
            if tables:
                return json.dumps(tables, ensure_ascii=False)[:max_chars]
        path = getattr(source, "path", None)
        if path:
            file_path = Path(path)
            if file_path.exists() and file_path.is_file() and file_path.stat().st_size < 200_000:
                try:
                    return file_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
                except OSError:
                    pass
        return f"(no excerpt available; original at {path})"

    @staticmethod
    async def _web_search_context(
        *, title: str, prompt: str, max_results: int
    ) -> str:
        query = (title + " " + prompt).strip()
        if not query:
            return ""
        try:
            from openakita.tools.handlers.web_search import WebSearchHandler  # type: ignore
        except Exception:
            logger.info("ppt-maker: WebSearchHandler unavailable, skipping web context")
            return ""
        handler = WebSearchHandler()
        try:
            text = await asyncio.wait_for(
                handler.handle(
                    "web_search",
                    {"query": query[:200], "max_results": max(1, min(max_results, 8))},
                ),
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("ppt-maker web search failed: %s", exc)
            return ""
        return text or ""
