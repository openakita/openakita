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


def build_catalog() -> dict[str, Any]:
    return {
        "doc_types": DOC_TYPES,
        "project_statuses": sorted(PROJECT_STATUSES),
        "output_formats": sorted(OUTPUT_FORMATS),
        "experimental_formats": sorted(EXPERIMENTAL_FORMATS),
    }

