"""Unit tests for the W3 Stage 3 cross-period validator (pure)."""

from __future__ import annotations

from finance_auto_backend.validators.cross_period import (
    BalanceSnapshot,
    validate_cross_period,
)


def _bs(code: str, *, closing_dr: float = 0, closing_cr: float = 0,
        opening_dr: float = 0, opening_cr: float = 0,
        name: str | None = None) -> BalanceSnapshot:
    return BalanceSnapshot(
        full_code=code,
        account_name=name,
        closing_debit=closing_dr,
        closing_credit=closing_cr,
        opening_debit=opening_dr,
        opening_credit=opening_cr,
    )


def test_perfect_carryover_marks_everything_exact() -> None:
    prior = [
        _bs("1001", closing_dr=1000, name="库存现金"),
        _bs("1002", closing_dr=5000, name="银行存款"),
        _bs("2202", closing_cr=300, name="应付账款"),
    ]
    current = [
        _bs("1001", opening_dr=1000, name="库存现金"),
        _bs("1002", opening_dr=5000, name="银行存款"),
        _bs("2202", opening_cr=300, name="应付账款"),
    ]
    res = validate_cross_period(prior=prior, current=current)
    assert res.total_accounts == 3
    assert res.exact_count == 3
    assert res.error_count == 0
    assert res.warning_count == 0


def test_tolerance_window_swallows_rounding_noise() -> None:
    prior = [_bs("1001", closing_dr=1000.00)]
    current = [_bs("1001", opening_dr=1000.50)]
    res = validate_cross_period(prior=prior, current=current, tolerance=1.0)
    assert res.tolerance_count == 1
    assert res.error_count == 0


def test_warning_grade_for_small_mismatch() -> None:
    prior = [_bs("1001", closing_dr=1000)]
    current = [_bs("1001", opening_dr=1050)]  # delta=50
    res = validate_cross_period(
        prior=prior, current=current, tolerance=1.0, warn_threshold=100.0,
    )
    assert res.warning_count == 1
    assert res.error_count == 0


def test_error_grade_for_large_mismatch() -> None:
    prior = [_bs("1001", closing_dr=10_000)]
    current = [_bs("1001", opening_dr=11_500)]  # delta=1500
    res = validate_cross_period(
        prior=prior, current=current, tolerance=1.0, warn_threshold=100.0,
    )
    assert res.error_count == 1
    must_fix = res.merged_must_fix()
    assert len(must_fix) == 1
    assert must_fix[0].delta == 1500.0


def test_missing_account_on_current_side_warning_or_error() -> None:
    prior = [_bs("9999", closing_dr=50, name="去年残余科目")]
    current = []
    res = validate_cross_period(prior=prior, current=current,
                                tolerance=1.0, warn_threshold=100.0)
    assert res.warning_count == 1  # delta=50 < 100 + missing on current side
    assert res.differences[0].note and "缺失科目" in res.differences[0].note


def test_new_account_on_current_side() -> None:
    prior = []
    current = [_bs("1601", opening_dr=20_000, name="新增固定资产")]
    res = validate_cross_period(prior=prior, current=current)
    assert res.error_count == 1
    assert res.differences[0].note and "新增科目" in res.differences[0].note


def test_mixed_population_produces_full_breakdown() -> None:
    prior = [
        _bs("1001", closing_dr=1000),
        _bs("1002", closing_dr=5000),
        _bs("2202", closing_cr=300),
    ]
    current = [
        _bs("1001", opening_dr=1000),       # exact
        _bs("1002", opening_dr=5000.40),    # tolerance
        _bs("2202", opening_cr=350),        # warning (delta=50)
        _bs("1601", opening_dr=2000),       # error (new)
    ]
    res = validate_cross_period(prior=prior, current=current)
    assert res.exact_count == 1
    assert res.tolerance_count == 1
    assert res.warning_count == 1
    assert res.error_count == 1
    assert res.total_accounts == 4
