"""Unit tests for the W3 Stage 1 parse-issue detector.

The detector is a pure function operating on ``ParsedRow`` lists, so these
tests don't need the FastAPI service or any DB.
"""

from __future__ import annotations

from finance_auto_backend.parsers.xls_parser import ParsedRow
from finance_auto_backend.validators.parse_issue_detector import (
    detect_parse_issues,
    make_pattern_signature,
)


def _row(
    row_index: int = 0,
    raw_code: str = "1001",
    parent_code: str = "1001",
    child_code: str | None = None,
    account_name: str | None = "库存现金",
    opening_debit: float = 0.0,
    opening_credit: float = 0.0,
    period_debit: float = 0.0,
    period_credit: float = 0.0,
    closing_debit: float = 0.0,
    closing_credit: float = 0.0,
    aux_text: str | None = None,
) -> ParsedRow:
    return ParsedRow(
        row_index=row_index,
        raw_code=raw_code,
        parent_code=parent_code,
        child_code=child_code,
        full_code=parent_code if not child_code else f"{parent_code}.{child_code}",
        account_name=account_name,
        aux_text=aux_text,
        opening_debit=opening_debit,
        opening_credit=opening_credit,
        period_debit=period_debit,
        period_credit=period_credit,
        closing_debit=closing_debit,
        closing_credit=closing_credit,
    )


def test_unknown_account_class_detected() -> None:
    rows = [_row(parent_code="8888", raw_code="8888", account_name="未知挂账", closing_debit=100)]
    issues = detect_parse_issues(rows)
    assert any(i.issue_type == "unknown_code" for i in issues)


def test_normal_row_no_issues() -> None:
    rows = [
        _row(parent_code="1001", account_name="库存现金",
             opening_debit=100, closing_debit=100),
        _row(parent_code="2202", account_name="应付账款",
             opening_credit=200, closing_credit=200),
    ]
    issues = detect_parse_issues(rows)
    assert issues == []


def test_direction_anomaly_credit_on_asset() -> None:
    rows = [_row(parent_code="1001", account_name="库存现金", closing_credit=500)]
    issues = detect_parse_issues(rows)
    types = {i.issue_type for i in issues}
    assert "direction_anomaly" in types


def test_missing_account_name_for_nonzero_row() -> None:
    rows = [_row(parent_code="1001", account_name=None, closing_debit=300)]
    issues = detect_parse_issues(rows)
    assert any(i.issue_type == "field_missing" for i in issues)


def test_debit_credit_imbalance_flagged() -> None:
    rows = [
        _row(parent_code="1001", account_name="库存现金",
             opening_debit=100, closing_debit=500,
             period_debit=200, period_credit=0),
    ]
    issues = detect_parse_issues(rows)
    assert any(i.issue_type == "debit_credit_imbalance" for i in issues)


def test_ambiguous_name_for_known_code() -> None:
    rows = [_row(parent_code="1221", account_name="应收-某客户保证金-2026", closing_debit=12000)]
    issues = detect_parse_issues(rows)
    types = {i.issue_type for i in issues}
    # 1221 其他应收 包含「应收」关键字 → 不触发；测一个未在 hint 列表的
    rows2 = [_row(parent_code="6602", account_name="abc-xyz", closing_debit=100)]
    issues2 = detect_parse_issues(rows2)
    assert any(i.issue_type == "name_ambiguity" for i in issues2)


def test_format_corrupt_full_width() -> None:
    rows = [_row(parent_code="1001", raw_code="１００１", account_name="库存现金",
                 closing_debit=100)]
    issues = detect_parse_issues(rows)
    assert any(i.issue_type == "format_corrupt" for i in issues)


def test_pattern_signature_stability() -> None:
    s1 = make_pattern_signature("unknown_code",
                                {"parent_code": "8888", "account_name": "未知挂账"})
    s2 = make_pattern_signature("unknown_code",
                                {"parent_code": "8888", "account_name": " 未知挂账 "})
    # whitespace normalization keeps the two equivalent
    assert s1 == s2


def test_bidirectional_code_not_flagged_for_direction() -> None:
    # 2221 应交税费 is bidirectional — debit OR credit is OK
    rows = [_row(parent_code="2221", account_name="应交税费", closing_debit=100)]
    issues = detect_parse_issues(rows)
    assert not any(i.issue_type == "direction_anomaly" for i in issues)


def test_must_fix_severity_for_unknown_code() -> None:
    rows = [_row(parent_code="9999", raw_code="9999", account_name="挂账户", closing_debit=1)]
    issues = detect_parse_issues(rows)
    must_fix = [i for i in issues if i.severity == "must_fix"]
    assert any(i.issue_type == "unknown_code" for i in must_fix)
