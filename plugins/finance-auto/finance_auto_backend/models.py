"""Pydantic models for finance-auto (M1 W1 — five core entities).

Naming kept close to the v0.1 main design doc §4 and the v0.2 Part 1 §1.3
``aux_mode`` field.  Field-level docstrings call out where each attribute is
covered in the design.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums (kept as Literal types so the OpenAPI schema is human-readable
# without forcing every consumer to import an enum class).
# ---------------------------------------------------------------------------

AuxMode = Literal["full", "light", "none"]
"""v0.2 Part 1 §1.3 — controls how aux dimensions (项目/部门/客户) are stored.

* ``full``  — every aux value gets its own row in the future ``aux_*`` tables
              (M1 W2+).  Most enterprise installs default to this.
* ``light`` — aux values are inlined as ``aux_text`` on the balance row only;
              no separate dimension tables.  Small businesses / 个体工商户.
* ``none``  — drop aux text entirely; useful for personal book keeping demos.
"""

Standard = Literal["cas", "small", "other"]
"""会计准则代码：``cas``=企业会计准则、``small``=小企业会计准则、``other``=其他."""

PeriodKind = Literal["year", "month", "quarter"]

BalanceSide = Literal["debit", "credit"]

ImportStatus = Literal["pending", "ok", "failed"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Organization (账套)
# ---------------------------------------------------------------------------


class OrganizationCreate(BaseModel):
    """Request body for ``POST /orgs`` — what the user sends."""

    name: str = Field(..., min_length=1, max_length=128, description="账套显示名")
    code: str = Field(..., min_length=1, max_length=64, description="账套唯一编码")
    industry: str = Field(default="general", description="行业 / 业态")
    standard: Standard = Field(default="cas", description="会计准则代码")
    aux_mode: AuxMode = Field(default="full", description="v0.2 Part1 §1.3 辅助核算模式")
    erp_source: str | None = Field(default=None, description="原 ERP 来源: 用友/金蝶/通用/None")
    fiscal_start: str | None = Field(default=None, description="会计期间起始日 YYYY-MM-DD")

    @field_validator("code")
    @classmethod
    def _code_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("code must not be empty after stripping")
        return v


class Organization(BaseModel):
    """Persisted ``organizations`` row — also the response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    industry: str
    standard: Standard
    aux_mode: AuxMode
    erp_source: str | None
    fiscal_start: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_create(cls, payload: OrganizationCreate) -> Organization:
        now = _utcnow_iso()
        return cls(
            id=_new_id("org"),
            name=payload.name,
            code=payload.code,
            industry=payload.industry,
            standard=payload.standard,
            aux_mode=payload.aux_mode,
            erp_source=payload.erp_source,
            fiscal_start=payload.fiscal_start,
            created_at=now,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Accounting period
# ---------------------------------------------------------------------------


class AccountingPeriod(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    period_id: str = Field(..., description="2025-FY / 2025-01 / 2025-Q1 etc.")
    period_kind: PeriodKind = "year"
    start_date: str | None = None
    end_date: str | None = None
    is_closed: bool = False
    created_at: str

    @classmethod
    def new(
        cls,
        *,
        org_id: str,
        period_id: str,
        period_kind: PeriodKind = "year",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> AccountingPeriod:
        return cls(
            id=_new_id("prd"),
            org_id=org_id,
            period_id=period_id,
            period_kind=period_kind,
            start_date=start_date,
            end_date=end_date,
            is_closed=False,
            created_at=_utcnow_iso(),
        )


# ---------------------------------------------------------------------------
# Account (chart of accounts row)
# ---------------------------------------------------------------------------


class Account(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    parent_code: str = Field(..., description="4-digit normalized parent code (1001 etc.)")
    child_code: str | None = None
    full_code: str = Field(..., description="parent[.child] joined")
    name: str
    balance_side: BalanceSide = "debit"
    category: str | None = None
    is_active: bool = True
    created_at: str

    @classmethod
    def new(
        cls,
        *,
        org_id: str,
        parent_code: str,
        child_code: str | None,
        name: str,
        balance_side: BalanceSide = "debit",
        category: str | None = None,
    ) -> Account:
        full_code = parent_code if not child_code else f"{parent_code}.{child_code}"
        return cls(
            id=_new_id("acc"),
            org_id=org_id,
            parent_code=parent_code,
            child_code=child_code,
            full_code=full_code,
            name=name,
            balance_side=balance_side,
            category=category,
            is_active=True,
            created_at=_utcnow_iso(),
        )


# ---------------------------------------------------------------------------
# Trial balance import (header) + rows
# ---------------------------------------------------------------------------


class TrialBalanceImport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    period_id: str
    source_file: str
    file_size: int
    file_sha256: str | None
    parser_used: str | None
    row_count: int
    status: ImportStatus
    error_message: str | None
    uploaded_at: str
    parsed_at: str | None

    @classmethod
    def pending(
        cls,
        *,
        org_id: str,
        period_id: str,
        source_file: str,
        file_size: int,
        file_sha256: str | None,
    ) -> TrialBalanceImport:
        return cls(
            id=_new_id("imp"),
            org_id=org_id,
            period_id=period_id,
            source_file=source_file,
            file_size=file_size,
            file_sha256=file_sha256,
            parser_used=None,
            row_count=0,
            status="pending",
            error_message=None,
            uploaded_at=_utcnow_iso(),
            parsed_at=None,
        )


class TrialBalanceRow(BaseModel):
    """One parsed line item from a trial-balance file.

    Field naming follows the canonical Chinese 余额表 column order:
    raw_code → parent/child split → 期初(借/贷) → 本期(借/贷) → 期末(借/贷).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    import_id: str
    org_id: str
    period_id: str
    row_index: int

    raw_code: str | None
    parent_code: str
    child_code: str | None
    full_code: str
    account_name: str | None
    aux_text: str | None = None

    opening_debit: float = 0.0
    opening_credit: float = 0.0
    period_debit: float = 0.0
    period_credit: float = 0.0
    closing_debit: float = 0.0
    closing_credit: float = 0.0


# ---------------------------------------------------------------------------
# Response envelopes (used by routes)
# ---------------------------------------------------------------------------


class OrgListResponse(BaseModel):
    organizations: list[Organization]
    total: int


class ImportListResponse(BaseModel):
    imports: list[TrialBalanceImport]
    total: int


class RowListResponse(BaseModel):
    rows: list[TrialBalanceRow]
    total: int
    limit: int
    offset: int


class UploadResponse(BaseModel):
    import_id: str
    row_count: int
    parser_used: str
    status: ImportStatus
    error_message: str | None = None
    parse_issues_detected: int = Field(
        default=0,
        description="W3 Stage 1: 解析阶段命中的异常条数（含 auto_applied）",
    )
    parse_issues_must_fix: int = Field(
        default=0,
        description="W3 Stage 1: 其中 severity=must_fix 的条数",
    )
    parse_issues_auto_applied: int = Field(
        default=0,
        description="W3 Stage 1: 命中 learning_sample 已自动处理的条数",
    )


# ---------------------------------------------------------------------------
# Report instance + per-cell trace (M1 W2 Stage 4)
# ---------------------------------------------------------------------------


SheetKind = Literal[
    "balance_sheet",
    "income_statement",
    "owners_equity",
    "cash_flow",
]
"""Statutory statements supported by the report-generation pipeline."""

AccountingStandard = Literal["small_enterprise", "general_enterprise"]
"""Two YAML-template-backed standards.  Maps to ``Organization.standard``
via ``cas`` -> general_enterprise, ``small`` -> small_enterprise."""


class ReportCell(BaseModel):
    """One line in a generated statement.

    The ``source_rows`` field is the cell-level traceability layer the design
    document calls out as essential for audit-trail UX: it carries the IDs of
    every ``trial_balance_rows`` row that fed the cell's value.  Aggregations
    span all matching rows; section headers / formulas leave it empty.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    report_id: str
    reference_code: str
    target_line_no: int = 0
    target_label: str
    indent_level: int = 0
    data_source: str
    code: str | None = None
    value: float = 0.0
    sign: int = 1
    is_total: bool = False
    is_tbd: bool = False
    formula: str | None = None
    notes: str | None = None
    source_rows: list[str] = Field(default_factory=list)


class ReportInstance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    period_id: str
    sheet_kind: SheetKind
    accounting_standard: AccountingStandard
    template_id: str
    template_version: int = 1
    status: Literal["ok", "failed"] = "ok"
    cell_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    source_import_id: str | None = None
    backend_used: str | None = None
    output_path: str | None = None
    generated_at: str

    @classmethod
    def new(
        cls,
        *,
        org_id: str,
        period_id: str,
        sheet_kind: SheetKind,
        accounting_standard: AccountingStandard,
        template_id: str,
        template_version: int,
        source_import_id: str | None,
    ) -> ReportInstance:
        return cls(
            id=_new_id("rep"),
            org_id=org_id,
            period_id=period_id,
            sheet_kind=sheet_kind,
            accounting_standard=accounting_standard,
            template_id=template_id,
            template_version=template_version,
            source_import_id=source_import_id,
            generated_at=_utcnow_iso(),
        )


class ReportListResponse(BaseModel):
    reports: list[ReportInstance]
    total: int


class ReportDetailResponse(BaseModel):
    report: ReportInstance
    cells: list[ReportCell]


# ---------------------------------------------------------------------------
# VAT declaration (M1 W2 Stage 5)
# ---------------------------------------------------------------------------


class VatDeclarationModel(BaseModel):
    """Persisted VAT declaration row (also the API response)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    declaration_period: str
    province: str | None
    dialect: str
    confidence: float
    output_vat: float
    input_vat: float
    prev_credit: float
    tax_payable: float
    surtax_total: float
    raw_fields: dict[str, float]
    warnings: list[str] = Field(default_factory=list)
    source_file: str | None
    file_sha256: str | None
    uploaded_at: str


class VatDeclarationListResponse(BaseModel):
    declarations: list[VatDeclarationModel]
    total: int


# ---------------------------------------------------------------------------
# Audit templates (M1 W2 Stage 6)
# ---------------------------------------------------------------------------


class AuditTemplate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    file_path: str
    file_sha256: str | None
    file_size: int
    placeholder_count: int
    unknown_placeholder_count: int
    placeholder_report: dict
    uploaded_at: str

    @property
    def is_strict_clean(self) -> bool:
        return self.unknown_placeholder_count == 0


class AuditTemplateListResponse(BaseModel):
    templates: list[AuditTemplate]
    total: int


class AuditTemplateRenderRequest(BaseModel):
    report_id: str = Field(..., description="用于注入 cells 上下文的报表实例 ID")
    strict: bool = Field(
        default=True,
        description="True 时拒绝渲染含 unknown 占位符的模板；False 跳过未知占位符",
    )


class ReportGenerateRequest(BaseModel):
    period_id: str = Field(..., description="生成报表所基于的会计期间")
    accounting_standard: AccountingStandard | None = Field(
        default=None,
        description="覆盖账套默认准则；不传则使用 Organization.standard 推断",
    )
    source_import_id: str | None = Field(
        default=None,
        description="指定使用某次导入的余额表；不传则取该 (org, period) 最新一次成功导入",
    )


# ---------------------------------------------------------------------------
# ParseIssue + LearningSample (M1 W3 Stage 1 — v0.2 Part 1 §2)
# ---------------------------------------------------------------------------

IssueType = Literal[
    "unknown_code",
    "name_ambiguity",
    "direction_anomaly",
    "debit_credit_imbalance",
    "field_missing",
    "format_corrupt",
    "cross_period_mismatch",
]
"""Seven issue families.  The first six come from W3 Stage 1's L1 detector,
the seventh is appended by W3 Stage 3's CrossPeriodValidator (v0.3 Part Biz
§4.3)."""

IssueSeverity = Literal["must_fix", "suggested", "ignorable"]
"""``must_fix`` blocks report generation; ``suggested`` warns only;
``ignorable`` is recorded for audit trail without UI surfacing."""

UserDecisionKind = Literal["apply_ai", "manual_fix", "skip", "ignore_as_other"]


class ParseIssue(BaseModel):
    """One row in ``parse_issues``.

    Field-level encryption: ``original_data`` may carry account names / aux
    text which are PII — the route layer wraps the payload in the same
    KeyManager pipeline the trial-balance rows use (see
    ``encryption.PARSE_ISSUE_PII_FIELDS``).  The model itself stays a flat
    Pydantic class so the API response keeps a stable JSON shape.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    period_id: str
    import_id: str
    row_index: int
    sheet_name: str = ""
    column_name: str = ""
    issue_type: IssueType
    severity: IssueSeverity
    pattern_signature: str = ""
    original_data: dict = Field(default_factory=dict)
    ai_suggestion: dict | None = None
    ai_confidence: float | None = None
    ai_consent_id: int | None = None
    user_decision: UserDecisionKind | None = None
    user_decision_payload: dict = Field(default_factory=dict)
    user_decided_at: str | None = None
    user_decided_by: str = ""
    applied_to_learning: bool = False
    learning_sample_id: str | None = None
    auto_applied: bool = False
    auto_applied_source: str | None = None
    version: int = 1
    created_at: str


class LearningSample(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str | None = None
    pattern_type: IssueType
    pattern_signature: str
    action: dict = Field(default_factory=dict)
    confidence: float = 1.0
    hit_count: int = 0
    last_used_at: str | None = None
    auto_apply: bool = False
    source_decision_id: str
    created_at: str


class ParseIssueListResponse(BaseModel):
    issues: list[ParseIssue]
    total: int
    pending: int
    must_fix_pending: int


class LearningSampleListResponse(BaseModel):
    samples: list[LearningSample]
    total: int


class ParseIssueDecisionRequest(BaseModel):
    decision: UserDecisionKind
    payload: dict = Field(default_factory=dict)
    decided_by: str = "local"


class ParseIssueLearnRequest(BaseModel):
    auto_apply: bool = False
    share_globally: bool = False
    confidence: float = Field(1.0, ge=0.0, le=1.0)
