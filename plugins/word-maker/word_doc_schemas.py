"""Doc-type structured analysis schemas for Brain and field preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from word_models import (
    ACCEPTANCE_REPORT_VARIABLES,
    MEETING_MINUTES_VARIABLES,
    PROPOSAL_VARIABLES,
    RESEARCH_REPORT_VARIABLES,
    SOP_VARIABLES,
    WEEKLY_REPORT_VARIABLES,
)


@dataclass(frozen=True, slots=True)
class DocTypeAnalysisSpec:
    fallback: dict[str, Any]
    task: str
    schema: dict[str, Any]
    required: frozenset[str]
    list_fields: frozenset[str] = field(default_factory=frozenset)
    string_fields: frozenset[str] = field(default_factory=frozenset)


def _empty_fallback(keys: list[str], *, lists: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {key: [] if key in lists else "" for key in keys}


DOC_TYPE_ANALYSIS: dict[str, DocTypeAnalysisSpec] = {
    "meeting_minutes": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            MEETING_MINUTES_VARIABLES,
            lists=frozenset({"conclusions", "action_items", "next_meeting_focus"}),
        ),
        task=(
            "Analyze meeting source materials and return structured meeting minutes data. "
            "Extract ONLY facts supported by sources_text. "
            "title: meeting subject. meeting_info: time/place/attendees/background if present, else empty string. "
            "summary: 100-200 Chinese characters, brief overview (NOT full topic dump). "
            "conclusions: 3-8 distinct decision bullets from 会议结论 or equivalent. "
            "topic_sections: per-topic narrative with 【议题名】 subsections where appropriate. "
            "risks: risks/blockers from sources; empty string if none stated. "
            "action_items: list of objects {task, owner, status, due_date} from 会后待办 table or bullets; "
            "prefer tabular tasks over narrative. "
            "next_meeting_focus: 1-3 follow-up focus bullets. "
            "CRITICAL: Do NOT duplicate topic_sections text into summary, conclusions, or action_items."
        ),
        schema={
            "title": "string",
            "meeting_info": "string",
            "summary": "string max 200 chars",
            "conclusions": ["string bullets"],
            "topic_sections": "string",
            "risks": "string",
            "action_items": [{"task": "", "owner": "", "status": "", "due_date": ""}],
            "next_meeting_focus": ["string"],
        },
        required=frozenset(MEETING_MINUTES_VARIABLES),
        list_fields=frozenset({"conclusions", "action_items", "next_meeting_focus"}),
        string_fields=frozenset(
            {"title", "meeting_info", "summary", "topic_sections", "risks"},
        ),
    ),
    "weekly_report": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            WEEKLY_REPORT_VARIABLES,
            lists=frozenset({"highlights", "metrics", "risks", "next_week_plan"}),
        ),
        task=(
            "Analyze weekly work source materials and return structured weekly report data. "
            "Extract ONLY facts supported by sources_text. "
            "title: report title. report_period: week range if stated. "
            "summary: 100-200 Chinese characters overview of the week. "
            "highlights: 3-8 key progress bullets. metrics: KPI or numeric achievements as bullets. "
            "risks: blockers or risks; empty list if none. "
            "next_week_plan: 3-6 planned items for next week. "
            "CRITICAL: Do NOT duplicate highlights into summary or next_week_plan."
        ),
        schema={
            "title": "string",
            "report_period": "string",
            "summary": "string",
            "highlights": ["string"],
            "metrics": ["string"],
            "risks": ["string"],
            "next_week_plan": ["string"],
        },
        required=frozenset(WEEKLY_REPORT_VARIABLES),
        list_fields=frozenset({"highlights", "metrics", "risks", "next_week_plan"}),
        string_fields=frozenset({"title", "report_period", "summary"}),
    ),
    "proposal": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            PROPOSAL_VARIABLES,
            lists=frozenset({"implementation_plan", "benefits"}),
        ),
        task=(
            "Analyze proposal source materials and return structured project proposal data. "
            "Extract ONLY facts supported by sources_text. "
            "title: proposal title. background: project/business context. "
            "objective: goals to achieve. solution: core solution design bullets or narrative. "
            "implementation_plan: phased milestones or steps as list. "
            "benefits: expected business value bullets. "
            "summary: 100-200 char executive summary. "
            "CRITICAL: Each section must have distinct content."
        ),
        schema={
            "title": "string",
            "background": "string",
            "objective": "string",
            "solution": "string",
            "implementation_plan": ["string"],
            "benefits": ["string"],
            "summary": "string",
        },
        required=frozenset(PROPOSAL_VARIABLES),
        list_fields=frozenset({"implementation_plan", "benefits"}),
        string_fields=frozenset({"title", "background", "objective", "solution", "summary"}),
    ),
    "acceptance_report": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            ACCEPTANCE_REPORT_VARIABLES,
            lists=frozenset({"deliverables", "metrics", "open_issues"}),
        ),
        task=(
            "Analyze acceptance/ delivery source materials and return structured acceptance report data. "
            "Extract ONLY facts supported by sources_text. "
            "title: report title. company_name: client org. project_name: project name. "
            "summary: 100-200 char acceptance overview. "
            "deliverables: delivered features/items as bullets. "
            "metrics: KPI or acceptance criteria results. "
            "open_issues: remaining issues or risks. "
            "conclusion: acceptance recommendation. "
            "CRITICAL: metrics and deliverables must not duplicate summary."
        ),
        schema={
            "title": "string",
            "company_name": "string",
            "project_name": "string",
            "summary": "string",
            "deliverables": ["string"],
            "metrics": ["string"],
            "open_issues": ["string"],
            "conclusion": "string",
        },
        required=frozenset(ACCEPTANCE_REPORT_VARIABLES),
        list_fields=frozenset({"deliverables", "metrics", "open_issues"}),
        string_fields=frozenset({"title", "company_name", "project_name", "summary", "conclusion"}),
    ),
    "research_report": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            RESEARCH_REPORT_VARIABLES,
            lists=frozenset({"findings", "conclusions", "recommendations"}),
        ),
        task=(
            "Analyze research source materials and return structured research report data. "
            "Extract ONLY facts supported by sources_text. "
            "title: report title. background: research context. methodology: methods and data sources. "
            "summary: 100-200 char overview. findings: key findings as bullets. "
            "conclusions: 3-8 conclusion bullets. recommendations: actionable recommendations. "
            "CRITICAL: findings, conclusions, and recommendations must be distinct."
        ),
        schema={
            "title": "string",
            "background": "string",
            "methodology": "string",
            "summary": "string",
            "findings": ["string"],
            "conclusions": ["string"],
            "recommendations": ["string"],
        },
        required=frozenset(RESEARCH_REPORT_VARIABLES),
        list_fields=frozenset({"findings", "conclusions", "recommendations"}),
        string_fields=frozenset({"title", "background", "methodology", "summary"}),
    ),
    "sop": DocTypeAnalysisSpec(
        fallback=_empty_fallback(
            SOP_VARIABLES,
            lists=frozenset({"roles", "prerequisites", "procedure_steps", "exceptions"}),
        ),
        task=(
            "Analyze SOP source materials and return structured standard operating procedure data. "
            "Extract ONLY facts supported by sources_text. "
            "title: SOP name. purpose: why this SOP exists. scope: applicable scenarios. "
            "roles: role/responsibility bullets. prerequisites: required conditions or tools. "
            "procedure_steps: ordered operational steps. exceptions: escalation or exception handling. "
            "revision_info: version/date/author if present. "
            "CRITICAL: procedure_steps must be ordered and actionable."
        ),
        schema={
            "title": "string",
            "purpose": "string",
            "scope": "string",
            "roles": ["string"],
            "prerequisites": ["string"],
            "procedure_steps": ["string"],
            "exceptions": ["string"],
            "revision_info": "string",
        },
        required=frozenset(SOP_VARIABLES),
        list_fields=frozenset({"roles", "prerequisites", "procedure_steps", "exceptions"}),
        string_fields=frozenset({"title", "purpose", "scope", "revision_info"}),
    ),
}


def has_structured_schema(doc_type: str) -> bool:
    return doc_type in DOC_TYPE_ANALYSIS


def get_analysis_spec(doc_type: str) -> DocTypeAnalysisSpec | None:
    return DOC_TYPE_ANALYSIS.get(doc_type)
