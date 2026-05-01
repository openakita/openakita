"""Brain-assisted planning helpers for word-maker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from word_maker_inline.llm_json_parser import parse_llm_json_object
from word_models import DOC_TYPES


class _BrainLike(Protocol):
    async def compiler_think(self, prompt: str, **kwargs: Any) -> str: ...


@dataclass(slots=True)
class BrainResult:
    ok: bool
    data: dict[str, Any]
    error: str = ""
    used_brain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "used_brain": self.used_brain,
        }


class WordBrainHelper:
    """Small wrapper around host Brain for structured document sub-tasks."""

    def __init__(self, api: Any) -> None:
        self._api = api

    def is_available(self) -> bool:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission) and not has_permission("brain.access"):
            return False
        get_brain = getattr(self._api, "get_brain", None)
        return callable(get_brain) and get_brain() is not None

    def _get_brain(self) -> Any | None:
        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission) and not has_permission("brain.access"):
            return None
        get_brain = getattr(self._api, "get_brain", None)
        return get_brain() if callable(get_brain) else None

    async def _ask_json(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        fallback: dict[str, Any],
        required: set[str],
    ) -> BrainResult:
        brain = self._get_brain()
        if brain is None:
            return BrainResult(False, fallback, "brain.access is not granted", used_brain=False)

        prompt = (
            "You are assisting word-maker, an OpenAkita plugin for guided Word document generation.\n"
            "Return ONLY valid JSON. Do not wrap it in Markdown fences.\n\n"
            f"Task: {task}\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        try:
            if hasattr(brain, "compiler_think"):
                raw = await brain.compiler_think(prompt, max_tokens=1200)
            elif hasattr(brain, "think_lightweight"):
                raw = await brain.think_lightweight(prompt, max_tokens=1200)
            else:
                raw = await brain.think(prompt, max_tokens=1200)
        except Exception as exc:
            return BrainResult(False, fallback, str(exc), used_brain=True)

        errors: list[str] = []
        parsed = parse_llm_json_object(str(raw), fallback=None, errors=errors)
        if not isinstance(parsed, dict):
            return BrainResult(False, fallback, "; ".join(errors) or "Brain did not return JSON", True)
        missing = sorted(key for key in required if key not in parsed)
        if missing:
            return BrainResult(False, fallback, f"Brain JSON missing keys: {', '.join(missing)}", True)
        return BrainResult(True, parsed, used_brain=True)

    async def clarify_requirements(
        self,
        *,
        requirement: str,
        doc_type: str | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> BrainResult:
        fallback = {
            "doc_type": doc_type or "research_report",
            "questions": ["请补充目标受众、交付用途、期望篇幅和是否有公司模板。"],
            "assumptions": [],
            "next_action": "collect_requirements",
        }
        return await self._ask_json(
            task="Clarify the user's Word document requirements.",
            payload={
                "requirement": requirement,
                "doc_type": doc_type,
                "available_doc_types": list(DOC_TYPES),
                "sources": sources or [],
                "required_schema": {
                    "doc_type": "one available doc type",
                    "questions": ["clarifying questions"],
                    "assumptions": ["safe assumptions"],
                    "next_action": "collect_requirements | generate_outline",
                },
            },
            fallback=fallback,
            required={"doc_type", "questions", "assumptions", "next_action"},
        )

    async def generate_outline(
        self,
        *,
        requirement: str,
        doc_type: str,
        sources_text: str = "",
    ) -> BrainResult:
        fallback = {
            "title": "文档初稿",
            "sections": [
                {"id": "background", "title": "背景", "goal": "说明任务背景", "bullets": []},
                {"id": "content", "title": "正文", "goal": "展开核心内容", "bullets": []},
                {"id": "next_steps", "title": "下一步", "goal": "给出后续行动", "bullets": []},
            ],
            "missing_inputs": [],
        }
        return await self._ask_json(
            task="Generate a business Word document outline.",
            payload={
                "requirement": requirement,
                "doc_type": doc_type,
                "sources_text": sources_text[:12000],
                "required_schema": {
                    "title": "document title",
                    "sections": [{"id": "string", "title": "string", "goal": "string", "bullets": []}],
                    "missing_inputs": [],
                },
            },
            fallback=fallback,
            required={"title", "sections", "missing_inputs"},
        )

    async def extract_fields(
        self,
        *,
        template_vars: list[str],
        requirement: str,
        sources_text: str = "",
    ) -> BrainResult:
        fallback = {
            "fields": dict.fromkeys(template_vars, ""),
            "missing": list(template_vars),
            "confidence": "low",
        }
        return await self._ask_json(
            task="Fill DOCX template variables from the requirement and source text.",
            payload={
                "template_vars": template_vars,
                "requirement": requirement,
                "sources_text": sources_text[:12000],
                "required_schema": {
                    "fields": dict.fromkeys(template_vars, "value"),
                    "missing": ["vars that need user input"],
                    "confidence": "low | medium | high",
                },
            },
            fallback=fallback,
            required={"fields", "missing", "confidence"},
        )

    async def rewrite_section(
        self,
        *,
        section_markdown: str,
        instruction: str,
        tone: str = "professional",
    ) -> BrainResult:
        fallback = {"markdown": section_markdown, "change_summary": "AI rewrite unavailable."}
        return await self._ask_json(
            task="Rewrite one section of a Word document.",
            payload={
                "section_markdown": section_markdown,
                "instruction": instruction,
                "tone": tone,
                "required_schema": {"markdown": "rewritten markdown", "change_summary": "short summary"},
            },
            fallback=fallback,
            required={"markdown", "change_summary"},
        )

    async def summarize_for_ppt(
        self,
        *,
        outline: dict[str, Any],
        doc_markdown: str,
    ) -> BrainResult:
        fallback = {
            "summary_md": doc_markdown[:2000],
            "slide_outline": [],
            "key_messages": [],
        }
        return await self._ask_json(
            task="Summarize this Word project for a future PPT deck.",
            payload={
                "outline": outline,
                "doc_markdown": doc_markdown[:12000],
                "required_schema": {
                    "summary_md": "markdown summary",
                    "slide_outline": [{"title": "slide title", "message": "core message"}],
                    "key_messages": [],
                },
            },
            fallback=fallback,
            required={"summary_md", "slide_outline", "key_messages"},
        )

