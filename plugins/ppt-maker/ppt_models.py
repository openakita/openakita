"""Shared data models for ppt-maker."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strict_model() -> ConfigDict:
    return ConfigDict(extra="forbid", populate_by_name=True)


class DeckMode(StrEnum):
    TOPIC_TO_DECK = "topic_to_deck"
    FILES_TO_DECK = "files_to_deck"
    OUTLINE_TO_DECK = "outline_to_deck"
    TABLE_TO_DECK = "table_to_deck"
    TEMPLATE_DECK = "template_deck"
    REVISE_DECK = "revise_deck"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    REQUIREMENTS = "requirements"
    OUTLINE_READY = "outline_ready"
    OUTLINE_CONFIRMED = "outline_confirmed"
    DESIGN_READY = "design_ready"
    DESIGN_CONFIRMED = "design_confirmed"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class ErrorKind(StrEnum):
    VALIDATION = "validation"
    DEPENDENCY = "dependency"
    BRAIN = "brain"
    SOURCE_PARSE = "source_parse"
    TABLE_PARSE = "table_parse"
    TEMPLATE = "template"
    EXPORT = "export"
    AUDIT = "audit"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class SlideType(StrEnum):
    COVER = "cover"
    AGENDA = "agenda"
    SECTION = "section"
    CONTENT = "content"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    DATA_OVERVIEW = "data_overview"
    METRIC_CARDS = "metric_cards"
    CHART_BAR = "chart_bar"
    CHART_LINE = "chart_line"
    CHART_PIE = "chart_pie"
    DATA_TABLE = "data_table"
    INSIGHT_SUMMARY = "insight_summary"
    SUMMARY = "summary"
    CLOSING = "closing"


class ColumnType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    EMPTY = "empty"
    MIXED = "mixed"


class ChartType(StrEnum):
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    LINE = "line"
    PIE = "pie"
    TABLE = "table"
    METRIC_CARDS = "metric_cards"


class TemplateCategory(StrEnum):
    BUSINESS = "business"
    TECH = "tech"
    CONSULTING = "consulting"
    EDUCATION = "education"
    ACADEMIC = "academic"


class BrandTokens(BaseModel):
    model_config = _strict_model()

    primary_color: str = "#3457D5"
    secondary_color: str = "#172033"
    accent_color: str = "#FFB000"
    font_heading: str = "Microsoft YaHei"
    font_body: str = "Microsoft YaHei"
    logo_path: str | None = None
    footer_text: str = ""


class TemplateRegistryItem(BaseModel):
    model_config = _strict_model()

    id: str
    name: str
    category: TemplateCategory
    description: str
    brand_tokens: BrandTokens = Field(default_factory=BrandTokens)


BUILTIN_TEMPLATES: tuple[TemplateRegistryItem, ...] = (
    TemplateRegistryItem(
        id="business-default",
        name="Business",
        category=TemplateCategory.BUSINESS,
        description="Clean executive deck with blue accents.",
    ),
    TemplateRegistryItem(
        id="tech-default",
        name="Tech",
        category=TemplateCategory.TECH,
        description="Dark technology deck with high-contrast sections.",
        brand_tokens=BrandTokens(primary_color="#5468FF", secondary_color="#10182B"),
    ),
    TemplateRegistryItem(
        id="consulting-default",
        name="Consulting",
        category=TemplateCategory.CONSULTING,
        description="Structured consulting report with strong section breaks.",
    ),
    TemplateRegistryItem(
        id="education-default",
        name="Education",
        category=TemplateCategory.EDUCATION,
        description="Readable lesson-style deck with calm colors.",
        brand_tokens=BrandTokens(primary_color="#2A9D8F", accent_color="#E9C46A"),
    ),
    TemplateRegistryItem(
        id="academic-default",
        name="Academic",
        category=TemplateCategory.ACADEMIC,
        description="Research presentation with conservative typography.",
        brand_tokens=BrandTokens(primary_color="#34495E", accent_color="#8E44AD"),
    ),
)


ERROR_HINTS: dict[ErrorKind, list[str]] = {
    ErrorKind.VALIDATION: ["检查输入字段是否完整。", "重新确认大纲或设计规范。"],
    ErrorKind.DEPENDENCY: ["到 Settings 安装对应依赖组。", "安装后重试当前任务。"],
    ErrorKind.BRAIN: ["检查 Akita 模型配置。", "减少资料量后重试。"],
    ErrorKind.SOURCE_PARSE: ["确认文件格式可读。", "尝试上传 Markdown 或文本版本。"],
    ErrorKind.TABLE_PARSE: ["确认 CSV/XLSX 表头清晰。", "减少超宽列后重试。"],
    ErrorKind.TEMPLATE: ["补充品牌 tokens。", "退回内置模板后重试。"],
    ErrorKind.EXPORT: ["检查导出目录权限。", "减少单页文本或表格宽度。"],
    ErrorKind.AUDIT: ["查看 audit_report.json。", "根据问题列表修正后重试。"],
    ErrorKind.CANCELLED: ["任务已取消，可从项目页重试。"],
    ErrorKind.UNKNOWN: ["查看项目 logs。", "保留输入并重试。"],
}


class ProjectCreate(BaseModel):
    model_config = _strict_model()

    mode: DeckMode
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(default="", max_length=10000)
    audience: str = Field(default="", max_length=240)
    style: str = Field(default="tech_business", max_length=80)
    slide_count: int = Field(default=8, ge=1, le=80)
    template_id: str | None = None
    dataset_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return value.strip()


class ProjectRecord(ProjectCreate):
    id: str
    status: ProjectStatus = ProjectStatus.DRAFT
    created_at: float
    updated_at: float


class TaskCreate(BaseModel):
    model_config = _strict_model()

    project_id: str | None = None
    task_type: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class TaskRecord(TaskCreate):
    id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = Field(default=0, ge=0, le=1)
    result: dict[str, Any] = Field(default_factory=dict)
    error_kind: ErrorKind | None = None
    error_message: str | None = None
    error_hints: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float
    completed_at: float | None = None


class SourceRecord(BaseModel):
    model_config = _strict_model()

    id: str
    project_id: str | None = None
    kind: str
    filename: str
    path: str
    status: SourceStatus = SourceStatus.UPLOADED
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class DatasetRecord(BaseModel):
    model_config = _strict_model()

    id: str
    project_id: str | None = None
    name: str
    original_path: str
    profile_path: str | None = None
    insights_path: str | None = None
    chart_specs_path: str | None = None
    status: Literal["created", "profiled", "insights_ready", "failed"] = "created"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class TemplateRecord(BaseModel):
    model_config = _strict_model()

    id: str
    name: str
    category: TemplateCategory | None = None
    original_path: str | None = None
    profile_path: str | None = None
    brand_tokens_path: str | None = None
    layout_map_path: str | None = None
    status: Literal["created", "diagnosed", "failed"] = "created"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float

