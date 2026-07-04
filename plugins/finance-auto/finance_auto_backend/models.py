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

    W3 Stage 2: ``simplified`` / ``simplified_top_n`` / ``simplify_config`` /
    ``merged_row_ids`` / ``footnote`` capture the v0.2 Part 1 §3 simplifier
    state.  ``source_rows`` keeps the **full** detail set even when
    simplification hides rows in the visible report.
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
    simplified: bool = False
    simplified_top_n: int = 0
    simplify_config: dict | None = None
    merged_row_ids: list[str] = Field(default_factory=list)
    footnote: str | None = None


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


class CellDetailRow(BaseModel):
    """One row of source detail returned by the cell-details endpoint.

    Either ``trial_balance_row_id`` is set (kept rows) OR ``is_merged`` is
    True (the synthetic "其他" row); the front-end uses the flag to render
    the row with a grey-italic style and an expandable child list."""

    trial_balance_row_id: str | None = None
    name: str
    amount: float
    is_merged: bool = False
    merged_count: int = 0
    merged_row_ids: list[str] = Field(default_factory=list)
    aux_text: str | None = None
    account_code: str | None = None


class CellDetailsResponse(BaseModel):
    report_id: str
    cell_id: str
    reference_code: str
    target_label: str
    simplified: bool
    simplify_config: dict | None
    visible_rows: list[CellDetailRow]
    full_rows: list[CellDetailRow]
    footnote: str | None


class CellSimplifyPatchRequest(BaseModel):
    enabled: bool
    strategy: Literal["top_n", "threshold", "both"] = "top_n"
    top_n: int = Field(default=10, ge=1, le=500)
    sort_by: Literal["amount_desc", "amount_abs_desc"] = "amount_desc"
    merge_label: str = "其他"
    min_threshold: float | None = None
    keep_negative_separate: bool = True
    footnote_template: str = "其他 {count} 项合计 {amount}"


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


# ---------------------------------------------------------------------------
# Cross-period validator (M1 W3 Stage 3 — v0.3 Part Biz §4)
# ---------------------------------------------------------------------------

CrossPeriodSeverity = Literal["exact", "tolerance", "warning", "error"]
"""Four severity buckets per v0.3 Part Biz §4.3:
* ``exact``     — values literally equal.
* ``tolerance`` — |delta| < ``tolerance`` (default 1元).
* ``warning``   — tolerance ≤ |delta| < ``warn_threshold`` (default 100元).
* ``error``     — |delta| ≥ ``warn_threshold`` (must fix; emits a ParseIssue).
"""


class CrossPeriodDifference(BaseModel):
    """One per-account row in a cross-period check result."""

    full_code: str
    account_name: str | None = None
    prior_closing: float = 0.0
    current_opening: float = 0.0
    delta: float = 0.0
    severity: CrossPeriodSeverity
    side: Literal["debit", "credit", "net"] = "net"
    note: str | None = None


class CrossPeriodCheckResult(BaseModel):
    """The aggregated outcome of a single CrossPeriodValidator run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    prior_period_id: str
    current_period_id: str
    prior_import_id: str
    current_import_id: str
    tolerance: float = 1.0
    warn_threshold: float = 100.0
    total_accounts: int = 0
    exact_count: int = 0
    tolerance_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    parse_issue_ids: list[str] = Field(default_factory=list)
    differences: list[CrossPeriodDifference] = Field(default_factory=list)
    status: str = "ok"
    notes: str | None = None
    version: int = 1
    created_at: str


class CrossPeriodCheckListItem(BaseModel):
    """Compact summary row used by the listing endpoint."""

    id: str
    org_id: str
    prior_period_id: str
    current_period_id: str
    total_accounts: int
    error_count: int
    warning_count: int
    created_at: str


class CrossPeriodCheckListResponse(BaseModel):
    items: list[CrossPeriodCheckListItem]
    total: int


class CrossPeriodCheckRequest(BaseModel):
    """Trigger a check.  If ``prior_import_id`` / ``current_import_id`` are
    omitted we resolve the latest successful import for each period."""

    prior_period_id: str
    current_period_id: str
    prior_import_id: str | None = None
    current_import_id: str | None = None
    tolerance: float = Field(1.0, ge=0.0)
    warn_threshold: float = Field(100.0, ge=0.0)
    emit_parse_issues: bool = True


# ---------------------------------------------------------------------------
# manual_inputs (M1 W3 Stage 4 — cash-flow supplementary fields).
# ---------------------------------------------------------------------------

ManualInputSource = Literal["manual", "vat_declaration", "learning_sample", "import"]
ManualInputValueType = Literal["cny", "text", "int", "float"]


class ManualInputPreset(BaseModel):
    """One row from ``templates/manual_inputs/*.yaml`` — describes a slot
    the UI must surface even when no value has been submitted yet."""

    key: str
    label: str
    value_type: ManualInputValueType = "cny"
    default_source: ManualInputSource = "manual"
    source_hint: str | None = None
    required_by: list[str] = Field(default_factory=list)


class ManualInputRecord(BaseModel):
    """One persisted row in ``manual_inputs``."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    period_id: str
    field_key: str
    field_label: str = ""
    value: str = ""
    value_type: ManualInputValueType = "cny"
    source: ManualInputSource = "manual"
    notes: str | None = None
    decided_by: str = "local"
    decided_at: str
    version: int = 1


class ManualInputSlot(BaseModel):
    """A unified view that merges the preset + the current persisted record
    (if any) — what the UI usually wants to render in one go."""

    key: str
    label: str
    value_type: ManualInputValueType = "cny"
    default_source: ManualInputSource = "manual"
    source_hint: str | None = None
    required_by: list[str] = Field(default_factory=list)
    record: ManualInputRecord | None = None
    filled: bool = False


class ManualInputListResponse(BaseModel):
    period_id: str
    org_id: str
    slots: list[ManualInputSlot]
    filled_count: int
    total_count: int


class ManualInputSubmitRequest(BaseModel):
    value: str
    value_type: ManualInputValueType = "cny"
    source: ManualInputSource = "manual"
    notes: str | None = None
    decided_by: str = "local"
    expected_version: int | None = None
    """Optional optimistic-lock token. Pass the ``version`` returned by a
    prior GET / PUT to detect concurrent writes — when present and the
    stored row's ``version`` differs the server returns HTTP 409 with the
    actual current version so the client can re-fetch + retry. Leaving
    the field unset preserves the old read-modify-write behaviour for
    callers that have not been updated yet."""


# ---------------------------------------------------------------------------
# M2 Biz Stage 2 — multi-auditor collaboration (v0.3 Part Biz §1)
# ---------------------------------------------------------------------------

UserRole = Literal["auditor", "manager", "partner", "admin"]
"""Four roles per Part Biz §1.1 permission matrix.

* ``auditor``  — drafts reports within assigned scope.
* ``manager``  — reviews / approves / requests changes; cross-org consolidation.
* ``partner``  — signs off final reports; un-restricted scope.
* ``admin``    — user / system administration only (no report editing).
"""

ProjectRole = Literal["lead_auditor", "reviewer", "partner_signoff"]
"""Per-assignment role; what this user *does* on a specific (org, period)."""

ReviewStatus = Literal[
    "draft",
    "pending_review",
    "reviewed",
    "pending_signoff",
    "signed_off",
    "returned",
]
"""State machine per Part Biz §1.6 sequence diagram.

``draft → pending_review → reviewed → pending_signoff → signed_off`` is the
happy path; manager / partner may ``returned`` at any pending step to send
the workflow back to the auditor for revision.
"""

CommentKind = Literal["general", "review_question", "answer", "audit_finding"]


class UserCreateRequest(BaseModel):
    """POST /users — request body."""

    user_id: str = Field(..., min_length=3, max_length=64,
                         description="Stable id; conventionally user_xxxxxxxx")
    display_name: str = Field(..., min_length=1, max_length=128)
    role: UserRole
    email: str = Field(default="", max_length=256)
    active: bool = True


class UserModel(BaseModel):
    """Persisted users row + API response."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    display_name: str
    role: UserRole
    email: str = ""
    active: bool = True
    created_at: str
    updated_at: str
    version: int = 1


class UserListResponse(BaseModel):
    users: list[UserModel]
    total: int


class AssignmentCreateRequest(BaseModel):
    """POST /orgs/{org_id}/assignments — request body."""

    user_id: str
    period_id: str | None = Field(
        default=None,
        description="None = 整账套（所有期间）",
    )
    role_in_project: ProjectRole
    assigned_by: str = "local"


class AssignmentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    org_id: str
    period_id: str | None = None
    role_in_project: ProjectRole
    assigned_at: str
    assigned_by: str = "local"
    revoked_at: str | None = None
    version: int = 1


class AssignmentListResponse(BaseModel):
    assignments: list[AssignmentModel]
    total: int


class ReviewWorkflowModel(BaseModel):
    """Persisted review_workflows row + API response."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: int
    org_id: str
    period_id: str
    report_id: str | None = None
    target_kind: Literal["report_instance", "audit_evidence", "notes"] = "report_instance"
    status: ReviewStatus
    auditor_id: str | None = None
    reviewer_id: str | None = None
    partner_id: str | None = None
    submitted_at: str | None = None
    reviewed_at: str | None = None
    signed_off_at: str | None = None
    returned_at: str | None = None
    return_reason: str | None = None
    history: list[dict] = Field(default_factory=list)
    created_at: str
    updated_at: str
    version: int = 1


class ReviewWorkflowSubmitRequest(BaseModel):
    """POST /orgs/{org_id}/reports/{report_id}/review/submit — request body.

    Caller is the auditor; ``reviewer_id`` / ``partner_id`` may be left
    empty here and filled later when the manager / partner actually
    picks up the workflow.
    """

    auditor_id: str = "local"
    reviewer_id: str | None = None
    partner_id: str | None = None
    target_kind: Literal["report_instance", "audit_evidence", "notes"] = "report_instance"


class ReviewWorkflowActionRequest(BaseModel):
    """Generic action body for approve / sign_off / request_changes."""

    actor_id: str = "local"
    note: str | None = None
    reason: str | None = None  # only used by request_changes


class CommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=8000)
    kind: CommentKind = "general"
    author_id: str = "local"
    parent_id: int | None = None
    mentions: list[str] = Field(default_factory=list)


class CommentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int | None = None
    cell_id: str | None = None
    report_id: str | None = None
    org_id: str
    parent_id: int | None = None
    kind: CommentKind = "general"
    author_id: str = "local"
    body: str
    mentions: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolved_by: str | None = None
    resolved_at: str | None = None
    created_at: str
    updated_at: str
    version: int = 1


class CommentListResponse(BaseModel):
    comments: list[CommentModel]
    total: int


# ---------------------------------------------------------------------------
# M2 Biz Stage 3 — reclassification rules (v0.3 Part Biz §3.6 / v0.1 §5.2)
# ---------------------------------------------------------------------------

ReclassificationMode = Literal["preview", "apply"]


class ReclassificationRuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    when_condition: dict = Field(
        default_factory=dict,
        description="Predicate: account_code_starts:list, balance_direction, threshold",
    )
    action: dict = Field(
        default_factory=dict,
        description="Action: move_to_account_code, reason",
    )
    active: bool = True
    priority: int = 100


class ReclassificationRuleModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_id: int
    org_id: str | None = None
    name: str
    description: str | None = None
    when_condition: dict = Field(default_factory=dict)
    action: dict = Field(default_factory=dict)
    active: bool = True
    priority: int = 100
    source_yaml: str | None = None
    created_at: str
    created_by: str = "local"
    updated_at: str
    version: int = 1


class ReclassificationRuleListResponse(BaseModel):
    rules: list[ReclassificationRuleModel]
    total: int


class ReclassificationRunRequest(BaseModel):
    period_id: str
    import_id: str | None = Field(
        default=None,
        description="覆盖默认；不传则取该 period 最新一次成功导入",
    )
    rule_ids: list[int] | None = Field(
        default=None,
        description="不传则跑该 org 所有 active 规则",
    )
    triggered_by: str = "local"


class ReclassificationRunItemModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int | None = None
    rule_name: str = ""
    source_account: str
    target_account: str
    amount: str = "0"  # Decimal as str
    direction: Literal["credit", "debit"] = "credit"
    reason: str | None = None
    matched_row_id: str | None = None
    created_at: str


class ReclassificationRunModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    org_id: str
    period_id: str
    import_id: str | None = None
    mode: ReclassificationMode
    rules_count: int = 0
    items_count: int = 0
    total_amount: str = "0"  # Decimal as str
    parse_issue_ids: list[str] = Field(default_factory=list)
    started_at: str
    finished_at: str | None = None
    triggered_by: str = "local"
    status: Literal["ok", "failed", "partial"] = "ok"
    notes: str | None = None
    items: list[ReclassificationRunItemModel] = Field(default_factory=list)
    version: int = 1
    # EX-P2-9 follow-up: the run header's ``status`` column is pinned to
    # ('ok','failed','partial') by a v9 CHECK constraint, so an undone run
    # cannot flip its own status.  The authoritative "was this run undone?"
    # signal lives in ``reclassification_history``; we surface it here so the
    # run-list API reflects the undone state immediately after POST /undo
    # instead of still rendering the run as live.
    undone_at: str | None = None
    undone_by: str | None = None


# ---------------------------------------------------------------------------
# M2 Biz Stage 6 — consolidation models (v0.3 Part Biz §2)
# ---------------------------------------------------------------------------

ConsolidationJoinMethod = Literal["full", "equity", "proportional"]
EliminationKind = Literal[
    "inter_ar_ap",
    "inter_sales",
    "inter_investment",
    "inter_profit",
    "minority_interest",
    "other",
]
ConsolidationReportKind = Literal["balance_sheet", "income_statement", "cash_flow"]


class ConsolidationGroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    parent_org_id: str
    description: str | None = None
    created_by: str = "local"


class ConsolidationGroupModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: int
    name: str
    parent_org_id: str
    description: str | None = None
    created_at: str
    created_by: str = "local"
    updated_at: str
    version: int = 1


class ConsolidationGroupListResponse(BaseModel):
    groups: list[ConsolidationGroupModel]
    total: int


class ConsolidationMemberCreateRequest(BaseModel):
    subsidiary_org_id: str
    ownership_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    join_method: ConsolidationJoinMethod = "full"
    is_parent: bool = False


class ConsolidationMemberModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    subsidiary_org_id: str
    ownership_pct: float = 100.0
    join_method: ConsolidationJoinMethod = "full"
    is_parent: bool = False
    added_at: str
    version: int = 1


class EliminationEntryCreateRequest(BaseModel):
    period_id: str
    kind: EliminationKind = "inter_ar_ap"
    debit_target: str
    credit_target: str
    amount: str = "0"  # Decimal as str
    rationale: str | None = None
    rule_key: str = ""
    is_auto: bool = False
    review_required: bool = True
    auto_match_confidence: float | None = None
    created_by: str = "local"


class EliminationEntryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    period_id: str
    kind: EliminationKind = "inter_ar_ap"
    rule_key: str = ""
    debit_target: str
    credit_target: str
    amount: str = "0"
    rationale: str | None = None
    is_auto: bool = False
    review_required: bool = True
    auto_match_confidence: float | None = None
    created_at: str
    created_by: str = "local"
    version: int = 1


class EliminationEntryListResponse(BaseModel):
    entries: list[EliminationEntryModel]
    total: int


class ConsolidationRunRequest(BaseModel):
    period_id: str
    kind: ConsolidationReportKind = "balance_sheet"
    accounting_standard: AccountingStandard | None = None
    actor_user_id: str = "local"


class ConsolidatedReportModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    consolidated_report_id: int
    group_id: int
    period_id: str
    kind: ConsolidationReportKind
    accounting_standard: str = "small_enterprise"
    status: Literal["ok", "failed", "partial"] = "ok"
    cells: list[dict] = Field(default_factory=list)
    minority_interest: str = "0"
    consolidation_meta: dict = Field(default_factory=dict)
    member_orgs_snapshot: list[dict] = Field(default_factory=list)
    elimination_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str
    generated_by: str = "local"
    version: int = 1


class ConsolidatedReportListResponse(BaseModel):
    reports: list[ConsolidatedReportModel]
    total: int


# ---------------------------------------------------------------------------
# M3 Biz Stage 1+2 — report notes registry + per-document persistence
# (schema v10 tables; see ``db/migrations/v10_notes_peer.py``).
#
# The audit (§4.2) flagged that the 5 tables introduced by M3 lacked
# Pydantic counterparts, so the route layer returned bare ``dict[str,
# Any]`` payloads.  These models give callers a stable JSON shape and
# let the OpenAPI schema show the field set.  They are intentionally
# kept as ``model_config = ConfigDict(from_attributes=True)`` so the
# existing dict-based callers (``NotesGenerator._row_to_doc`` etc.)
# can keep flowing dicts while typed callers get the strict version.
# ---------------------------------------------------------------------------

NoteTemplateFormat = Literal["markdown", "excel"]
NoteTemplateDataSource = Literal["data_driven", "narrative", "hybrid"]
NoteAccountingStandard = Literal["small_enterprise", "general_enterprise"]
NoteDocumentStatus = Literal["draft", "in_review", "finalized"]
ReportNoteKind = Literal[
    "data", "narrative", "hybrid",
    "narrative_pending_ai", "narrative_pending_user",
]


class NoteTemplateModel(BaseModel):
    """One row of ``note_templates`` (M3 Biz Stage 1 / schema v10)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    note_section: str
    note_item_code: str
    template_format: NoteTemplateFormat = "markdown"
    template_path: str
    data_source: NoteTemplateDataSource
    auto_fill_pct: int = 0
    requires_ai: bool = False
    ai_scenario_id: str | None = None
    accounting_standard: NoteAccountingStandard = "small_enterprise"
    version: int = 1
    created_at: str | None = None


class NoteDocumentModel(BaseModel):
    """One row of ``note_documents`` — owns a generation per (org, period)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: str
    period_id: str
    status: NoteDocumentStatus = "draft"
    accounting_standard: NoteAccountingStandard = "small_enterprise"
    version: int = 1
    created_at: str
    updated_at: str


class ReportNoteModel(BaseModel):
    """One row of ``report_notes`` — attaches one rendered section to a
    document, optionally referencing an ``llm_call_audit`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    template_id: int
    note_section: str
    note_item_code: str
    content: str = ""
    kind: ReportNoteKind = "data"
    ai_audit_id: int | None = None
    version: int = 1
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# M3 Biz Stage 3 — peer comparison.
# ---------------------------------------------------------------------------


class PeerBenchmarkModel(BaseModel):
    """One quartile-bench row from ``peer_benchmarks``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    industry_code: str
    metric_code: str
    metric_name: str
    period_label: str = "2024"
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    sample_size: int = 0
    source: str = "seed"
    accounting_standard: NoteAccountingStandard = "small_enterprise"
    version: int = 1
    created_at: str | None = None


class PeerComparisonResultModel(BaseModel):
    """One row of ``peer_comparison_results`` — caches one run of the
    PeerComparisonService for an (org, period, industry)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: str
    period_id: str
    industry_code: str
    metrics: list[dict] = Field(default_factory=list)
    """Decoded ``metrics_json`` payload — list of per-metric quartile
    assessments.  Kept as ``list[dict]`` so the schema stays open while
    Sibling B's S5 evolves the per-metric shape."""
    ai_summary: str = ""
    ai_audit_id: int | None = None
    version: int = 1
    created_at: str
