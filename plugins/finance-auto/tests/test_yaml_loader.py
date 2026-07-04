"""Tests for the YAML report-template loader.

Covers structural validation, the ``extends`` chain resolution, the TBD
warning behaviour, and that the four shipped templates all load cleanly.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from finance_auto_backend.config import (  # noqa: E402
    YamlValidationError,
    list_templates,
    load_template,
)

SHIPPED_TEMPLATES = PLUGIN_ROOT / "templates" / "reports"


def test_shipped_templates_count() -> None:
    # Baseline = 5 W1/W2 templates; M2 Biz Stage 3 adds reclassification_*
    # YAMLs, Stage 4 adds cash_flow_*.yaml and Stage 6 adds
    # consolidation_*.yaml.  We assert at least the W1/W2 floor so this test
    # documents the minimum surface without breaking every new template add.
    assert len(list_templates(SHIPPED_TEMPLATES)) >= 5


def test_load_balance_sheet_small() -> None:
    tpl = load_template(SHIPPED_TEMPLATES / "balance_sheet_small_enterprise.yaml")
    assert tpl.template_id == "bs_se_v1"
    assert tpl.accounting_standard == "small_enterprise"
    assert tpl.sheet_kind == "balance_sheet"
    assert len(tpl.rules) >= 50
    assert any(r.reference_code == "BS_TOTAL_ASSETS" for r in tpl.rules)
    assert tpl.extends_chain == []
    assert all(w.field != "code" or "TBD" in w.message for w in tpl.warnings)


def test_load_income_statement_small() -> None:
    tpl = load_template(SHIPPED_TEMPLATES / "income_statement_small_enterprise.yaml")
    assert tpl.template_id == "pl_se_v1"
    assert tpl.sheet_kind == "income_statement"
    codes = {r.reference_code for r in tpl.rules}
    assert {"PL_REVENUE", "PL_NET_PROFIT", "PL_OPERATING_PROFIT"} <= codes


def test_load_balance_sheet_general_extends_small() -> None:
    tpl = load_template(SHIPPED_TEMPLATES / "balance_sheet_general_enterprise.yaml")
    assert tpl.template_id == "bs_ge_v1"
    assert tpl.accounting_standard == "general_enterprise"
    assert "bs_se_v1" in tpl.extends_chain
    codes = {r.reference_code for r in tpl.rules}
    assert "BS_GE_1131_FIN_RCV" in codes
    assert "BS_1001" in codes
    tbd_codes = [
        r.reference_code
        for r in tpl.rules
        if r.code is not None and r.code == "TBD"
    ]
    assert {"BS_GE_1606_ROU", "BS_GE_2241_CL", "BS_GE_2811_LL"} == set(tbd_codes)
    tbd_warnings = [w for w in tpl.warnings if w.field == "code"]
    assert len(tbd_warnings) >= 3
    assert tpl.has_tbd_lines is True


def test_load_income_statement_general_has_six_new_lines() -> None:
    tpl = load_template(SHIPPED_TEMPLATES / "income_statement_general_enterprise.yaml")
    assert tpl.template_id == "pl_ge_v1"
    new_codes = {
        "PL_GE_6117",
        "PL_GE_6101",
        "PL_GE_6111_AS",
        "PL_GE_6112",
        "PL_GE_6601_CIL",
        "PL_GE_6701",
    }
    assert new_codes <= {r.reference_code for r in tpl.rules}


def test_load_cash_flow_general_enterprise_indirect() -> None:
    """The indirect-method CF template (cf_indirect_ge_v1) must load cleanly
    so the cash_flow:general_enterprise resolver key has a backing file."""
    tpl = load_template(
        SHIPPED_TEMPLATES / "cash_flow_indirect_general_enterprise.yaml"
    )
    assert tpl.template_id == "cf_indirect_ge_v1"
    assert tpl.sheet_kind == "cash_flow"
    assert tpl.accounting_standard == "general_enterprise"
    codes = {r.reference_code for r in tpl.rules}
    assert {"CF_NET_PROFIT", "CF_OPERATING_NET", "CF_NET_CHANGE"} <= codes
    # Every manual_input rule must name a cf_* key the engine publishes.
    mi_keys = {
        r.manual_input_key
        for r in tpl.rules
        if r.data_source == "manual_input"
    }
    assert all(k and k.startswith("cf_") for k in mi_keys)


def test_invalid_data_source_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        textwrap.dedent(
            """\
            template_id: bad
            name: bad
            sheet_kind: balance_sheet
            accounting_standard: small_enterprise
            xltpl_file: x.xlsx
            version: 1
            rules:
              - reference_code: BAD_X
                target_line_no: 1
                target_label: x
                data_source: nonsense
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(YamlValidationError, match="invalid data_source"):
        load_template(bad)


def test_account_data_source_requires_balance_kind(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        textwrap.dedent(
            """\
            template_id: bad
            name: bad
            sheet_kind: balance_sheet
            accounting_standard: small_enterprise
            xltpl_file: x.xlsx
            version: 1
            rules:
              - reference_code: BAD_X
                target_line_no: 1
                target_label: x
                data_source: account
                code: "1001"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(YamlValidationError, match="balance_kind is missing"):
        load_template(bad)


def test_invalid_code_pattern_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        textwrap.dedent(
            """\
            template_id: bad
            name: bad
            sheet_kind: balance_sheet
            accounting_standard: small_enterprise
            xltpl_file: x.xlsx
            version: 1
            rules:
              - reference_code: BAD_X
                target_line_no: 1
                target_label: x
                data_source: account
                balance_kind: closing_net
                code: "has space"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(YamlValidationError, match="fails validation pattern"):
        load_template(bad)


def test_cyclic_extends_raises(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(
        textwrap.dedent(
            """\
            template_id: a
            name: a
            sheet_kind: balance_sheet
            accounting_standard: small_enterprise
            xltpl_file: x.xlsx
            version: 1
            extends: b.yaml
            rules: []
            """
        ),
        encoding="utf-8",
    )
    b.write_text(
        textwrap.dedent(
            """\
            template_id: b
            name: b
            sheet_kind: balance_sheet
            accounting_standard: small_enterprise
            xltpl_file: x.xlsx
            version: 1
            extends: a.yaml
            rules: []
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(YamlValidationError, match="Cyclic"):
        load_template(a)
