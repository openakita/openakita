"""Unified template field preparation: inspect → AI/rule structure → map → dedupe → placeholders."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from word_brain_helper import WordBrainHelper
from word_doc_schemas import get_analysis_spec, has_structured_schema
from word_models import DOC_TYPE_TEMPLATE_VARIABLES
from word_outline_sync import (
    dedupe_template_fields,
    extract_fields_from_sources,
    fill_missing_template_fields,
    merge_outline_into_fields,
    outline_brief_summary,
)
from word_template_engine import extract_template_vars

_MISSING_FIELD_PLACEHOLDER = "（待补充）"


@dataclass(slots=True)
class PrepareResult:
    fields: dict[str, Any]
    field_sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    used_brain: bool = False
    error: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    template_variables: list[str] = field(default_factory=list)
    var_contexts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": self.fields,
            "field_sources": self.field_sources,
            "missing": self.missing,
            "used_brain": self.used_brain,
            "error": self.error,
            "structured": self.structured,
            "template_variables": self.template_variables,
        }


def _non_empty(value: Any) -> bool:
    return bool(str(value or "").strip())


def _format_list_items(items: Any) -> str:
    if isinstance(items, str):
        return items.strip()
    if not isinstance(items, list):
        return str(items or "").strip()
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, dict):
            task = str(item.get("task") or item.get("description") or "").strip()
            owner = str(item.get("owner") or item.get("assignee") or "").strip()
            status = str(item.get("status") or "").strip()
            due = str(item.get("due_date") or item.get("due") or "").strip()
            if not task:
                continue
            parts = [p for p in [owner, status, due] if p]
            suffix = f"（{'，'.join(parts)}）" if parts else ""
            lines.append(f"{idx}. {task}{suffix}")
        else:
            line = str(item or "").strip()
            if line:
                lines.append(f"{idx}. {line}" if not line.startswith(("-", "•", "*")) else line)
    return "\n".join(lines).strip()


def map_structured_to_template_vars(
    structured: dict[str, Any],
    template_vars: list[str],
    *,
    doc_type: str = "",
) -> dict[str, Any]:
    """Map doc-type structured JSON onto template variable names."""
    if "fields" in structured and isinstance(structured["fields"], dict) and not has_structured_schema(doc_type):
        mapped = dict(structured["fields"])
        result: dict[str, Any] = {}
        for name in template_vars:
            if name in mapped and _non_empty(mapped.get(name)):
                result[name] = mapped[name]
        return result

    spec = get_analysis_spec(doc_type)
    list_fields = spec.list_fields if spec else frozenset()
    mapped: dict[str, Any] = {}

    if doc_type == "meeting_minutes" or (
        not doc_type and "conclusions" in structured and "topic_sections" in structured
    ):
        if _non_empty(structured.get("title")):
            mapped["title"] = structured["title"]
        for key in (
            "meeting_info",
            "summary",
            "topic_sections",
            "risks",
        ):
            if _non_empty(structured.get(key)):
                mapped[key] = structured[key]
        if structured.get("conclusions"):
            mapped["conclusions"] = _format_list_items(structured["conclusions"])
        if structured.get("action_items"):
            mapped["action_items"] = _format_list_items(structured["action_items"])
        if structured.get("next_meeting_focus"):
            mapped["next_meeting_focus"] = _format_list_items(structured["next_meeting_focus"])
    else:
        keys = list(DOC_TYPE_TEMPLATE_VARIABLES.get(doc_type, template_vars))
        for key in keys:
            value = structured.get(key)
            if not _non_empty(value) and value not in ([], {}):
                continue
            if key in list_fields or isinstance(value, list):
                mapped[key] = _format_list_items(value)
            else:
                mapped[key] = value

    result = {}
    for name in template_vars:
        if name in mapped and _non_empty(mapped.get(name)):
            result[name] = mapped[name]
    return result


def build_meeting_structured_from_rules(
    *,
    template_vars: list[str],
    fields: dict[str, Any] | None,
    requirement: str,
    sources_text: str,
    outline: dict[str, Any] | None,
    var_contexts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rule-based meeting minutes structure for Brain fallback."""
    extracted, _sources = extract_fields_from_sources(
        template_vars,
        fields=fields,
        requirement=requirement,
        sources_text=sources_text,
        outline=outline,
        var_contexts=var_contexts,
    )
    structured: dict[str, Any] = {
        "title": extracted.get("title") or (outline or {}).get("title") or "",
        "meeting_info": extracted.get("meeting_info") or "",
        "summary": extracted.get("summary") or "",
        "conclusions": extracted.get("conclusions") or "",
        "topic_sections": extracted.get("topic_sections") or "",
        "risks": extracted.get("risks") or "",
        "action_items": extracted.get("action_items") or "",
        "next_meeting_focus": extracted.get("next_meeting_focus") or "",
    }
    if not _non_empty(structured["summary"]) and outline:
        structured["summary"] = outline_brief_summary(outline)
    return structured


def build_structured_from_rules(
    *,
    doc_type: str,
    template_vars: list[str],
    fields: dict[str, Any] | None,
    requirement: str,
    sources_text: str,
    outline: dict[str, Any] | None,
    var_contexts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rule-based structured data for Brain fallback."""
    if doc_type == "meeting_minutes":
        return build_meeting_structured_from_rules(
            template_vars=template_vars,
            fields=fields,
            requirement=requirement,
            sources_text=sources_text,
            outline=outline,
            var_contexts=var_contexts,
        )

    keys = list(DOC_TYPE_TEMPLATE_VARIABLES.get(doc_type, template_vars))
    extracted, _sources = extract_fields_from_sources(
        template_vars,
        fields=fields,
        requirement=requirement,
        sources_text=sources_text,
        outline=outline,
        var_contexts=var_contexts,
    )
    structured: dict[str, Any] = {key: extracted.get(key) or "" for key in keys}
    if "title" in keys:
        structured["title"] = extracted.get("title") or (outline or {}).get("title") or ""
    if "summary" in keys and not _non_empty(structured.get("summary")) and outline:
        structured["summary"] = outline_brief_summary(outline)
    return structured


async def prepare_template_fields(
    *,
    doc_type: str,
    template_path: Path,
    sources_text: str,
    requirement: str = "",
    outline: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
    brain_helper: WordBrainHelper | None = None,
    placeholder: str = _MISSING_FIELD_PLACEHOLDER,
    preserve_user_fields: bool = True,
) -> PrepareResult:
    """Inspect template, extract structured data from sources, map and fill placeholders."""
    base_fields = dict(fields or {})
    inspection = extract_template_vars(template_path, context=base_fields)
    template_vars = list(inspection.variables)
    var_contexts = dict(inspection.var_contexts or {})

    if not template_vars:
        return PrepareResult(
            fields=base_fields,
            template_variables=[],
            var_contexts=var_contexts,
        )

    merged = dict(base_fields)
    field_sources: dict[str, str] = {}
    structured: dict[str, Any] = {}
    used_brain = False
    error = ""

    if brain_helper is not None and brain_helper.is_available():
        result = await brain_helper.analyze_sources_for_doc_type(
            doc_type=doc_type,
            requirement=requirement,
            sources_text=sources_text,
            template_vars=template_vars,
        )
        used_brain = result.used_brain
        error = result.error
        if result.ok:
            structured = dict(result.data)
            if has_structured_schema(doc_type):
                mapped = map_structured_to_template_vars(structured, template_vars, doc_type=doc_type)
            else:
                mapped = dict(structured.get("fields") or {})
            for key, value in mapped.items():
                if not _non_empty(value):
                    continue
                if preserve_user_fields and _non_empty(merged.get(key)):
                    continue
                merged[key] = value
                field_sources[key] = "ai"

    # Always supplement unfilled fields from rules/sources (even when AI partially succeeded).
    structured = build_structured_from_rules(
        doc_type=doc_type,
        template_vars=template_vars,
        fields=merged,
        requirement=requirement,
        sources_text=sources_text,
        outline=outline,
        var_contexts=var_contexts,
    )
    if has_structured_schema(doc_type):
        mapped = map_structured_to_template_vars(structured, template_vars, doc_type=doc_type)
        for key, value in mapped.items():
            if not _non_empty(value):
                continue
            if preserve_user_fields and _non_empty(merged.get(key)):
                continue
            merged[key] = value
            if key not in field_sources:
                field_sources[key] = "rule"
    else:
        rule_fields, rule_sources = extract_fields_from_sources(
            template_vars,
            fields=merged,
            requirement=requirement,
            sources_text=sources_text,
            outline=outline,
            var_contexts=var_contexts,
        )
        for key, value in rule_fields.items():
            if not _non_empty(value):
                continue
            if preserve_user_fields and _non_empty(merged.get(key)):
                continue
            merged[key] = value
            if key not in field_sources:
                field_sources[key] = rule_sources.get(key, "rule")

    if outline and (outline.get("sections") or outline.get("title")):
        merged = merge_outline_into_fields(
            outline,
            merged,
            template_variables=template_vars,
            var_contexts=var_contexts,
            sources_text=sources_text,
            requirement=requirement,
        )
        for name in template_vars:
            if _non_empty(merged.get(name)) and name not in field_sources:
                field_sources[name] = "outline"

    dedupe_template_fields(merged, template_vars)

    merged = fill_missing_template_fields(
        template_vars,
        merged,
        outline=outline,
        sources_text=sources_text,
        requirement=requirement,
        var_contexts=var_contexts,
        placeholder=placeholder,
    )
    for name in template_vars:
        if _non_empty(merged.get(name)) and name not in field_sources:
            if str(merged.get(name)) == placeholder:
                field_sources[name] = "fallback"
            else:
                field_sources[name] = field_sources.get(name, "rule")

    missing = [name for name in template_vars if not _non_empty(merged.get(name))]

    return PrepareResult(
        fields=merged,
        field_sources=field_sources,
        missing=missing,
        used_brain=used_brain,
        error=error,
        structured=structured,
        template_variables=template_vars,
        var_contexts=var_contexts,
    )
