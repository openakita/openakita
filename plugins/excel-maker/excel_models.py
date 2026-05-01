"""Data models for the excel-maker report workbook plugin.

The plugin uses Pydantic models as the API contract between the UI, Agent
tools, and deterministic workbook builders. LLM output is always normalized
into these models before any file operation is executed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    IMPORTED = "imported"
    PROFILED = "profiled"
    PLANNED = "planned"
    BUILDING = "building"
    GENERATED = "generated"
    AUDITED = "audited"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkbookStatus(str, Enum):
    UPLOADED = "uploaded"
    IMPORTED = "imported"
    PROFILED = "profiled"
    FAILED = "failed"


class ArtifactKind(str, Enum):
    WORKBOOK = "workbook"
    AUDIT = "audit"
    PLAN = "plan"
    PROFILE = "profile"


class TemplateStatus(str, Enum):
    CREATED = "created"
    DIAGNOSED = "diagnosed"
    FAILED = "failed"


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    goal: str = ""
    audience: str = ""
    period: str = ""
    style: str = "business"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRecord(ProjectCreate):
    id: str
    status: ProjectStatus = ProjectStatus.DRAFT
    report_brief: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class WorkbookRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: Optional[str] = None
    filename: str
    original_path: str
    imported_path: Optional[str] = None
    profile_path: Optional[str] = None
    status: WorkbookStatus = WorkbookStatus.UPLOADED
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class SheetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workbook_id: str
    name: str
    index: int = 0
    row_count: int = 0
    column_count: int = 0
    header_row: Optional[int] = None
    data_range: str = ""
    formula_count: int = 0
    merged_range_count: int = 0
    hidden_row_count: int = 0
    hidden_column_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    kind: ArtifactKind
    path: str
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class AuditItemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    artifact_id: Optional[str] = None
    severity: Literal["info", "warning", "error"] = "info"
    category: str = "general"
    message: str
    location: str = ""
    suggestion: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class TemplateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    original_path: str
    diagnostic_path: Optional[str] = None
    status: TemplateStatus = TemplateStatus.CREATED
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class WorkbookPlanSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: Literal[
        "readme",
        "raw_data",
        "clean_data",
        "summary",
        "charts",
        "formula_check",
        "audit_log",
        "pivot",
        "mapping",
        "exceptions",
        "appendix",
    ]
    source_sheet: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    description: str = ""


class WorkbookPlanFormula(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet: str
    cell: str
    formula: str
    explanation: str = ""

    @field_validator("formula")
    @classmethod
    def formula_must_start_with_equals(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("="):
            raise ValueError("Excel formulas must start with '='")
        return value


class WorkbookPlanOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "rename_column",
        "cast_type",
        "fill_missing",
        "drop_duplicates",
        "derive_column",
        "groupby",
        "pivot",
        "sort",
        "filter",
        "write_formula",
    ]
    source_sheet: Optional[str] = None
    target_sheet: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)


class WorkbookPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "Excel Report"
    locale: str = "zh-CN"
    purpose: str = ""
    source_workbook_id: Optional[str] = None
    sheets: list[WorkbookPlanSheet] = Field(default_factory=list)
    operations: list[WorkbookPlanOperation] = Field(default_factory=list)
    formulas: list[WorkbookPlanFormula] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    audit_expectations: list[str] = Field(default_factory=list)


class FormulaSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: str
    explanation: str
    applies_to: str = ""
    test_example: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: str = ""
    uploads_dir: str = ""
    workbooks_dir: str = ""
    projects_dir: str = ""
    export_dir: str = ""
    templates_dir: str = ""
    cache_dir: str = ""
    default_style: str = "business"
    brand_color: str = "#2563eb"
    font_family: str = "Microsoft YaHei"
    number_format: str = "#,##0.00"
    updated_at: Optional[float] = None

