"""Brain-assisted planning helpers for word-maker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from word_maker_inline.llm_json_parser import parse_llm_json_object
from word_models import DOC_TYPES


class _BrainLike(Protocol):
    async def think_lightweight(self, prompt: str, **kwargs: Any) -> Any: ...


_BRAIN_SYSTEM_PROMPT = "You are assisting word-maker, an OpenAkita plugin for guided Word document generation."
_SUPPORTED_BRAIN_METHODS = ("think_lightweight", "think", "compiler_think", "access")


def _extract_response_text(response: Any) -> str:
    raw = getattr(response, "content", response)
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else str(raw)


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

    def _brain_state(self) -> tuple[dict[str, Any], Any | None]:
        status: dict[str, Any] = {
            "available": False,
            "permission_granted": True,
            "brain_injected": False,
            "reason": "host_brain_unavailable",
            "message": "OpenAkita Brain is not available",
        }

        has_permission = getattr(self._api, "has_permission", None)
        if callable(has_permission):
            try:
                status["permission_granted"] = bool(has_permission("brain.access"))
            except Exception as exc:  # noqa: BLE001 - host API failures should degrade gracefully.
                status["permission_granted"] = False
                status["reason"] = "permission_check_failed"
                status["message"] = f"brain.access permission check failed: {exc}"
                return status, None
        if not status["permission_granted"]:
            status["reason"] = "permission_denied"
            status["message"] = "brain.access is not granted"
            return status, None

        get_brain = getattr(self._api, "get_brain", None)
        if not callable(get_brain):
            status["reason"] = "host_brain_unavailable"
            status["message"] = "OpenAkita host does not expose get_brain"
            return status, None
        try:
            brain = get_brain()
        except Exception as exc:  # noqa: BLE001 - status should explain host failures.
            status["reason"] = "host_brain_error"
            status["message"] = f"OpenAkita Brain lookup failed: {exc}"
            return status, None
        if brain is None:
            status["reason"] = "host_brain_unavailable"
            status["message"] = "OpenAkita Brain is not available"
            return status, None

        status.update(
            {
                "available": True,
                "brain_injected": True,
                "reason": "available",
                "message": "OpenAkita Brain is available",
            }
        )
        return status, brain

    def brain_status(self) -> dict[str, Any]:
        status, _brain = self._brain_state()
        return dict(status)

    def is_available(self) -> bool:
        return bool(self.brain_status()["available"])

    def _get_brain(self) -> Any | None:
        _status, brain = self._brain_state()
        return brain

    async def _call_brain(self, brain: _BrainLike, prompt: str) -> str:
        last_type_error: TypeError | None = None
        for method_name in _SUPPORTED_BRAIN_METHODS:
            method = getattr(brain, method_name, None)
            if not callable(method):
                continue
            for kwargs in (
                {"prompt": prompt, "system": _BRAIN_SYSTEM_PROMPT, "max_tokens": 1200},
                {"prompt": prompt, "max_tokens": 1200},
            ):
                try:
                    return _extract_response_text(await method(**kwargs))
                except TypeError as exc:
                    last_type_error = exc
            try:
                return _extract_response_text(await method(prompt))
            except TypeError as exc:
                last_type_error = exc
        chat = getattr(brain, "chat", None)
        if callable(chat):
            messages = [
                {"role": "system", "content": _BRAIN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            for kwargs in (
                {"messages": messages, "system": _BRAIN_SYSTEM_PROMPT, "max_tokens": 1200},
                {"messages": messages, "max_tokens": 1200},
                {"messages": messages},
            ):
                try:
                    return _extract_response_text(await chat(**kwargs))
                except TypeError as exc:
                    last_type_error = exc
        if last_type_error is not None:
            raise RuntimeError(f"Brain has no supported think method: {last_type_error}") from last_type_error
        raise RuntimeError("Brain has no supported think method")

    async def _ask_json(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        fallback: dict[str, Any],
        required: set[str],
    ) -> BrainResult:
        status, brain = self._brain_state()
        if brain is None:
            return BrainResult(False, fallback, str(status["message"]), used_brain=False)

        prompt = (
            f"{_BRAIN_SYSTEM_PROMPT}\n"
            "Return ONLY valid JSON. Do not wrap it in Markdown fences.\n\n"
            f"Task: {task}\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        try:
            text = await self._call_brain(brain, prompt)
        except Exception as exc:
            return BrainResult(False, fallback, str(exc), used_brain=True)
        if not text.strip():
            return BrainResult(False, fallback, "Brain returned an empty response", used_brain=True)

        errors: list[str] = []
        parsed = parse_llm_json_object(text, fallback=None, errors=errors)
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
            task=(
                "Generate a business Word document outline from the requirement and source materials. "
                "Extract concrete section titles and bullet points from sources_text; do not invent facts "
                "not supported by the sources. List gaps in missing_inputs."
            ),
            payload={
                "requirement": requirement,
                "doc_type": doc_type,
                "sources_text": sources_text[: self._SOURCES_TEXT_LIMIT],
                "required_schema": {
                    "title": "document title",
                    "sections": [{"id": "string", "title": "string", "goal": "string", "bullets": []}],
                    "missing_inputs": ["information still missing from sources"],
                },
            },
            fallback=fallback,
            required={"title", "sections", "missing_inputs"},
        )

    _SOURCES_TEXT_LIMIT = 24000

    async def analyze_sources_for_doc_type(
        self,
        *,
        doc_type: str,
        requirement: str,
        sources_text: str = "",
        template_vars: list[str] | None = None,
    ) -> BrainResult:
        """Analyze sources into doc-type structured JSON before mapping to template vars."""
        from word_doc_schemas import get_analysis_spec

        vars_list = list(template_vars or [])
        spec = get_analysis_spec(doc_type)
        if spec is not None:
            fallback = dict(spec.fallback)
            task = spec.task
            schema = dict(spec.schema)
            required = set(spec.required)
        else:
            fallback = {"fields": dict.fromkeys(vars_list, "")}
            task = (
                "Analyze source materials and fill template variables with distinct, section-appropriate content."
            )
            schema = {"fields": dict.fromkeys(vars_list, "value")}
            required = {"fields"}

        return await self._ask_json(
            task=task,
            payload={
                "doc_type": doc_type,
                "requirement": requirement,
                "sources_text": sources_text[: self._SOURCES_TEXT_LIMIT],
                "template_vars": vars_list,
                "required_schema": schema,
            },
            fallback=fallback,
            required=required,
        )

    async def extract_fields(
        self,
        *,
        template_vars: list[str],
        requirement: str,
        sources_text: str = "",
        var_contexts: dict[str, str] | None = None,
    ) -> BrainResult:
        fallback = {
            "fields": dict.fromkeys(template_vars, ""),
            "missing": list(template_vars),
            "confidence": "low",
        }
        vars_with_hints: dict[str, str] = {}
        from word_template_convert import VAR_TO_ZH

        for var in template_vars:
            ctx = (var_contexts or {}).get(var, "")
            if ctx:
                vars_with_hints[var] = ctx
            else:
                labels = VAR_TO_ZH.get(var, [])
                vars_with_hints[var] = "（" + "／".join(labels[:3]) + "）" if labels else ""
        task = (
            "Fill DOCX template variables from the requirement and source text. "
            "Each variable has a context hint describing WHERE it appears in the "
            "template document (e.g. which section heading it is under). "
            "Use these hints to understand what DISTINCT content each field expects. "
            "CRITICAL: Each field MUST receive DIFFERENT content appropriate to its "
            "section. Do NOT put the same text into multiple fields. "
            "For example: 'summary' should be a brief overview, 'conclusions' should "
            "be key decisions/findings, 'risks' should be problems/blockers, "
            "'action_items' should be tasks with owners. "
            "Extract bullet lists where the template expects lists. "
            "Do not fabricate data not present in sources."
        )
        return await self._ask_json(
            task=task,
            payload={
                "template_vars": template_vars,
                "var_contexts": vars_with_hints,
                "requirement": requirement,
                "sources_text": sources_text[: self._SOURCES_TEXT_LIMIT],
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

