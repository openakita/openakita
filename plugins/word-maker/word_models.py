"""Shared models and constants for word-maker."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DOC_TYPES: dict[str, dict[str, str]] = {
    "weekly_report": {"zh": "周报", "en": "Weekly Report"},
    "monthly_report": {"zh": "月报", "en": "Monthly Report"},
    "meeting_minutes": {"zh": "会议纪要", "en": "Meeting Minutes"},
    "proposal": {"zh": "项目建议书", "en": "Proposal"},
    "requirements_doc": {"zh": "需求文档", "en": "Requirements Document"},
    "acceptance_report": {"zh": "验收报告", "en": "Acceptance Report"},
    "contract_draft": {"zh": "合同初稿", "en": "Contract Draft"},
    "sop": {"zh": "SOP", "en": "SOP"},
    "research_report": {"zh": "调研报告", "en": "Research Report"},
}

PROJECT_STATUSES = frozenset(
    {
        "draft",
        "clarifying",
        "outline_ready",
        "template_ready",
        "rendering",
        "succeeded",
        "failed",
        "cancelled",
    }
)

OUTPUT_FORMATS = frozenset({"docx", "md", "pdf"})
EXPERIMENTAL_FORMATS = frozenset({"pdf"})

ProjectStatus = Literal[
    "draft",
    "clarifying",
    "outline_ready",
    "template_ready",
    "rendering",
    "succeeded",
    "failed",
    "cancelled",
]


@dataclass(slots=True)
class ProjectSpec:
    """User-facing project metadata collected before generation."""

    title: str
    doc_type: str = "research_report"
    audience: str = ""
    tone: str = "professional"
    language: str = "zh-CN"
    requirements: str = ""

    def validate(self) -> None:
        if self.doc_type not in DOC_TYPES:
            raise ValueError(f"Unsupported doc_type: {self.doc_type}")
        if not self.title.strip():
            raise ValueError("title is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(slots=True)
class AuditResult:
    """Minimal document audit summary."""

    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MEETING_MINUTES_VARIABLES = [
    "title",
    "meeting_info",
    "summary",
    "conclusions",
    "topic_sections",
    "risks",
    "action_items",
    "next_meeting_focus",
]

WEEKLY_REPORT_VARIABLES = [
    "title",
    "report_period",
    "summary",
    "highlights",
    "metrics",
    "risks",
    "next_week_plan",
]

PROPOSAL_VARIABLES = [
    "title",
    "background",
    "objective",
    "solution",
    "implementation_plan",
    "benefits",
    "summary",
]

ACCEPTANCE_REPORT_VARIABLES = [
    "title",
    "company_name",
    "project_name",
    "summary",
    "deliverables",
    "metrics",
    "open_issues",
    "conclusion",
]

RESEARCH_REPORT_VARIABLES = [
    "title",
    "background",
    "methodology",
    "summary",
    "findings",
    "conclusions",
    "recommendations",
]

SOP_VARIABLES = [
    "title",
    "purpose",
    "scope",
    "roles",
    "prerequisites",
    "procedure_steps",
    "exceptions",
    "revision_info",
]

DOC_TYPE_TEMPLATE_VARIABLES: dict[str, list[str]] = {
    "meeting_minutes": MEETING_MINUTES_VARIABLES,
    "weekly_report": WEEKLY_REPORT_VARIABLES,
    "proposal": PROPOSAL_VARIABLES,
    "acceptance_report": ACCEPTANCE_REPORT_VARIABLES,
    "research_report": RESEARCH_REPORT_VARIABLES,
    "sop": SOP_VARIABLES,
}

# Human-readable hints for common template variables (shown in wizard UI).
TEMPLATE_VAR_HINTS: dict[str, str] = {
    "title": "文档或会议标题",
    "meeting_info": "会议时间、地点、参会人员及背景等基本信息",
    "meeting_date": "会议日期",
    "meeting_time": "会议时间",
    "participants": "参会人员名单",
    "attendees": "参会人员名单",
    "summary": "100–200 字简要概述，概括主要内容",
    "conclusions": "3–8 条核心结论或决议",
    "topic_sections": "按议题分段整理的讨论要点",
    "discussion": "分主题讨论内容",
    "risks": "关键问题、风险与卡点",
    "risk": "风险与问题描述",
    "action_items": "会后待办清单（含负责人与截止时间）",
    "next_meeting_focus": "下次会议需跟进或准备的事项",
    "report_period": "报告周期（如 2026/01/06–01/10）",
    "highlights": "本期亮点与关键进展",
    "metrics": "关键指标或量化成果",
    "next_week_plan": "下周计划与待办",
    "background": "项目或业务背景",
    "objective": "建设目标或希望确定的关键事项",
    "solution": "方案要点或核心设计",
    "implementation_plan": "实施计划与里程碑",
    "benefits": "预期收益与业务价值",
    "company_name": "公司或客户名称",
    "project_name": "项目名称",
    "deliverables": "已交付功能或成果清单",
    "open_issues": "遗留问题与未关闭风险",
    "conclusion": "验收结论与建议",
    "methodology": "调研方法与数据来源",
    "findings": "主要发现与关键信息",
    "recommendations": "可执行的建议与下一步",
    "purpose": "SOP 目的与适用场景说明",
    "scope": "适用范围与边界",
    "roles": "角色与职责分工",
    "prerequisites": "前置条件、工具或权限要求",
    "procedure_steps": "操作步骤（按顺序列出）",
    "exceptions": "异常处理与升级路径",
    "revision_info": "版本、修订日期与作者",
    "client": "客户名称",
    "next_steps": "下一步计划",
    "owner": "负责人",
    "due_date": "截止日期",
    "location": "会议地点或形式",
    "host": "主持人",
    "recorder": "记录人",
}

DOC_TYPE_STARTERS: dict[str, dict[str, Any]] = {
    "meeting_minutes": {
        "file": "meeting-minutes.docx",
        "zh_label": "会议纪要",
        "variables": MEETING_MINUTES_VARIABLES,
        "default_for_doc_type": True,
    },
    "meeting_minutes_simple": {
        "file": "meeting-minutes-simple.docx",
        "zh_label": "会议纪要（精简）",
        "variables": ["title", "meeting_date", "attendees", "summary", "action_items"],
        "default_for_doc_type": False,
    },
    "weekly_report": {
        "file": "weekly-report.docx",
        "zh_label": "周报",
        "variables": WEEKLY_REPORT_VARIABLES,
        "default_for_doc_type": True,
    },
    "proposal": {
        "file": "proposal.docx",
        "zh_label": "项目建议书",
        "variables": PROPOSAL_VARIABLES,
        "default_for_doc_type": True,
    },
    "acceptance_report": {
        "file": "acceptance-report.docx",
        "zh_label": "验收报告",
        "variables": ACCEPTANCE_REPORT_VARIABLES,
        "default_for_doc_type": True,
    },
    "research_report": {
        "file": "research-report.docx",
        "zh_label": "调研报告",
        "variables": RESEARCH_REPORT_VARIABLES,
        "default_for_doc_type": True,
    },
    "sop": {
        "file": "sop.docx",
        "zh_label": "SOP",
        "variables": SOP_VARIABLES,
        "default_for_doc_type": True,
    },
    "monthly_report": {
        "file": "monthly-report.docx",
        "zh_label": "月报",
        "variables": ["title", "summary", "next_steps"],
        "default_for_doc_type": False,
    },
}


def default_starter_doc_type(doc_type: str) -> str | None:
    """Return doc_type key to use for auto-default template, if any."""
    spec = DOC_TYPE_STARTERS.get(doc_type)
    if spec and spec.get("default_for_doc_type"):
        return doc_type
    return None


def build_catalog() -> dict[str, Any]:
    return {
        "doc_types": DOC_TYPES,
        "project_statuses": sorted(PROJECT_STATUSES),
        "output_formats": sorted(OUTPUT_FORMATS),
        "experimental_formats": sorted(EXPERIMENTAL_FORMATS),
        "starter_templates": [
            {
                "doc_type": key,
                "label": spec.get("zh_label") or DOC_TYPES.get(key, {}).get("zh", key),
                "variables": list(spec.get("variables") or []),
                "default_for_doc_type": bool(spec.get("default_for_doc_type")),
            }
            for key, spec in DOC_TYPE_STARTERS.items()
        ],
    }

