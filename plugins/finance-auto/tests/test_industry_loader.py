"""Unit tests for the W3 Stage 5 industry overrides loader."""

from __future__ import annotations

from finance_auto_backend.config.industry_loader import (
    deep_merge,
    effective_config,
    list_industries,
    load_overlay,
    merge_manual_input_presets,
)
from finance_auto_backend.config.manual_inputs_loader import cash_flow_aux_presets


def test_deep_merge_dicts_recursively() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    overlay = {"a": {"c": 20, "e": 5}, "f": 6}
    out = deep_merge(base, overlay)
    assert out == {"a": {"b": 1, "c": 20, "e": 5}, "d": 3, "f": 6}


def test_deep_merge_scalar_overlay_wins() -> None:
    assert deep_merge(1, 2) == 2
    assert deep_merge("base", "overlay") == "overlay"
    assert deep_merge(True, False) is False


def test_deep_merge_list_overlay_wins_outright() -> None:
    out = deep_merge([1, 2, 3], [9, 10])
    assert out == [9, 10]


def test_deep_merge_overlay_none_preserves_base() -> None:
    assert deep_merge({"a": 1}, None) == {"a": 1}


def test_list_industries_returns_shipped_three_plus_general() -> None:
    items = list_industries()
    industries = {i["industry"] for i in items}
    assert {"manufacturing", "restaurant", "tech_service"} <= industries


def test_restaurant_overlay_sets_light_aux_mode_and_simplify_defaults() -> None:
    overlay = load_overlay("restaurant")
    assert overlay["org_defaults"]["aux_mode"] == "light"
    bs1122 = overlay["report_overrides"]["balance_sheet"]["BS_1122"]
    assert bs1122["simplify"]["enabled"] is True
    assert bs1122["simplify"]["top_n"] == 10
    assert bs1122["simplify"]["merge_label"] == "其他客户"


def test_effective_config_for_general_returns_base_only() -> None:
    base = {"org_defaults": {"aux_mode": "full"}}
    eff = effective_config(base=base, industry="general")
    assert eff == base


def test_effective_config_for_restaurant_pulls_in_overlay() -> None:
    base = {"org_defaults": {"aux_mode": "full", "currency": "CNY"}}
    eff = effective_config(base=base, industry="restaurant")
    assert eff["org_defaults"]["aux_mode"] == "light"      # overlay wins
    assert eff["org_defaults"]["currency"] == "CNY"        # base preserved
    assert "report_overrides" in eff                        # added by overlay


def test_manual_input_overlay_appends_industry_specific_keys() -> None:
    base = cash_flow_aux_presets()
    merged = merge_manual_input_presets(base, industry="manufacturing")
    keys = {p.key for p in merged}
    # Original 7 + 2 new manufacturing keys
    assert "depreciation_expense" in keys
    assert "amortization_low_value" in keys
    # No data loss
    assert {p.key for p in base} <= keys


def test_unknown_industry_is_a_no_op() -> None:
    assert load_overlay("unknown_industry") == {}
    assert load_overlay(None) == {}
