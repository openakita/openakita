"""Unit tests for the YAML-driven report generator.

The tests build a minimal in-memory list of TrialBalanceLine entries and
verify:

* a small-enterprise BS YAML produces the right cells from real data,
* the formula evaluator handles ACCOUNT, ACCOUNT_GROUP, LINE, SUM_LINES,
* TBD codes propagate as ``is_tbd=True`` cells with a warning,
* unsupported AST nodes are rejected with a warning instead of executing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from finance_auto_backend.config import load_template  # noqa: E402
from finance_auto_backend.report_generator import (  # noqa: E402
    TrialBalanceLine,
    _eval_formula,
    generate_report,
)


def _line(
    *,
    code: str,
    name: str = "测试",
    closing_debit: float = 0,
    closing_credit: float = 0,
    period_debit: float = 0,
    period_credit: float = 0,
) -> TrialBalanceLine:
    parent = code.split(".", 1)[0]
    child = code.split(".", 1)[1] if "." in code else None
    return TrialBalanceLine(
        id=f"row_{code.replace('.', '_')}",
        full_code=code,
        parent_code=parent,
        child_code=child,
        account_name=name,
        opening_debit=0.0,
        opening_credit=0.0,
        period_debit=period_debit,
        period_credit=period_credit,
        closing_debit=closing_debit,
        closing_credit=closing_credit,
    )


SHIPPED = PLUGIN_ROOT / "templates" / "reports"


def test_generate_balance_sheet_small_basic() -> None:
    template = load_template(SHIPPED / "balance_sheet_small_enterprise.yaml")
    lines = [
        _line(code="1001", name="库存现金", closing_debit=10000),
        _line(code="1002", name="银行存款", closing_debit=200000),
        _line(code="1122.客户A", name="客户A", closing_debit=500000),
        _line(code="1122.客户B", name="客户B", closing_credit=15000),
        _line(code="1601", name="固定资产", closing_debit=1000000),
        _line(code="1602", name="累计折旧", closing_credit=200000),
        _line(code="2202", name="应付账款", closing_credit=300000),
        _line(code="4001", name="实收资本", closing_credit=1000000),
        _line(code="4104", name="未分配利润", closing_credit=195000),
    ]
    gen = generate_report(
        template=template,
        org_id="org_x",
        period_id="2025-FY",
        accounting_standard="small_enterprise",
        balance_lines=lines,
        source_import_id="imp_x",
    )

    assert gen.instance.cell_count == len(template.rules)
    by_code = {c.reference_code: c for c in gen.cells}
    assert by_code["BS_1001"].value == pytest.approx(210000.0)
    assert by_code["BS_1122"].value == pytest.approx(500000.0)
    # The 15k credit balance is *not* part of BS_1122 because subaccount_debit_positive
    # only sums positive subaccount nets.
    assert by_code["BS_1601_NET"].value == pytest.approx(800000.0)
    assert by_code["BS_TOTAL_ASSETS"].value > 0
    assert by_code["BS_TOTAL_ASSETS"].source_rows == []
    assert "row_1001" in by_code["BS_1001"].source_rows


def test_indirect_cash_flow_warnings_are_quiet() -> None:
    """间接法现金流量表：无 manual_input 时不应刷屏。

    - 模板里 manual_input 规则带 manual_input_key → 不再产生 "code is missing"。
    - 折旧/摊销行改为 manual_input(cf_dep_amort) → 不再有 "unbound identifier"。
    - 未录入的补充项被聚合为单条温和提示，而非逐项刷屏。
    """
    template = load_template(SHIPPED / "cash_flow_indirect_general_enterprise.yaml")
    gen = generate_report(
        template=template,
        org_id="org_x",
        period_id="2025-FY",
        accounting_standard="general_enterprise",
        balance_lines=[],
        source_import_id=None,
        manual_input_values=None,  # nothing persisted yet
    )
    assert not any("code is missing" in w for w in gen.warnings), gen.warnings
    assert not any("unbound identifier" in w for w in gen.warnings), gen.warnings
    pending = [w for w in gen.warnings if "补充项待" in w]
    assert len(pending) == 1, gen.warnings
    # The single condensed line covers the manual-input lines that fell back
    # to 0; per-line "is not yet filled" noise must be gone.
    assert not any("is not yet filled" in w for w in gen.warnings), gen.warnings


def test_indirect_cash_flow_no_warnings_when_filled() -> None:
    """补充项全部录入后，间接法现金流量表零告警。"""
    template = load_template(SHIPPED / "cash_flow_indirect_general_enterprise.yaml")
    manual = {
        r.manual_input_key: 1.0
        for r in template.rules
        if r.data_source == "manual_input" and r.manual_input_key
    }
    gen = generate_report(
        template=template,
        org_id="org_x",
        period_id="2025-FY",
        accounting_standard="general_enterprise",
        balance_lines=[],
        source_import_id=None,
        manual_input_values=manual,
    )
    assert gen.warnings == [], gen.warnings


def test_generate_general_enterprise_emits_tbd() -> None:
    template = load_template(SHIPPED / "balance_sheet_general_enterprise.yaml")
    gen = generate_report(
        template=template,
        org_id="org_x",
        period_id="2025-FY",
        accounting_standard="general_enterprise",
        balance_lines=[],
        source_import_id=None,
    )
    by_code = {c.reference_code: c for c in gen.cells}
    assert by_code["BS_GE_1606_ROU"].is_tbd is True
    assert by_code["BS_GE_2241_CL"].is_tbd is True
    assert by_code["BS_GE_2811_LL"].is_tbd is True
    assert any("TBD" in w for w in gen.warnings)


def test_eval_formula_basic_arithmetic() -> None:
    warnings: list[str] = []
    sources: list[str] = []
    val = _eval_formula(
        "{{ 100 + 200 * 3 }}",
        balance_lines=[],
        line_values={},
        line_no_values={},
        warnings=warnings,
        source_collector=sources,
    )
    assert val == pytest.approx(700.0)
    assert warnings == []


def test_eval_formula_account_group() -> None:
    lines = [
        _line(code="1401", closing_debit=1000),
        _line(code="1402", closing_debit=2000),
        _line(code="1471", closing_credit=500),
    ]
    warnings: list[str] = []
    sources: list[str] = []
    val = _eval_formula(
        "{{ ACCOUNT_GROUP(['1401','1402'],'closing_net') - ACCOUNT('1471','closing_net') }}",
        balance_lines=lines,
        line_values={},
        line_no_values={},
        warnings=warnings,
        source_collector=sources,
    )
    assert val == pytest.approx(3500.0)
    assert "row_1401" in sources
    assert "row_1471" in sources
    assert warnings == []


def test_eval_formula_rejects_attribute_access() -> None:
    warnings: list[str] = []
    sources: list[str] = []
    val = _eval_formula(
        "{{ ACCOUNT.__class__.__bases__ }}",
        balance_lines=[],
        line_values={},
        line_no_values={},
        warnings=warnings,
        source_collector=sources,
    )
    assert val == 0.0
    assert any("unsupported" in w for w in warnings)


def test_eval_formula_rejects_lambda() -> None:
    warnings: list[str] = []
    sources: list[str] = []
    val = _eval_formula(
        "{{ (lambda: 99)() }}",
        balance_lines=[],
        line_values={},
        line_no_values={},
        warnings=warnings,
        source_collector=sources,
    )
    assert val == 0.0
    assert warnings


def test_eval_formula_line_and_sum_lines() -> None:
    warnings: list[str] = []
    sources: list[str] = []
    val = _eval_formula(
        "{{ LINE('A') + SUM_LINES(1, 3) }}",
        balance_lines=[],
        line_values={"A": 10.0},
        line_no_values={1: 2.0, 2: 3.0, 3: 5.0, 4: 100.0},
        warnings=warnings,
        source_collector=sources,
    )
    assert val == pytest.approx(20.0)
