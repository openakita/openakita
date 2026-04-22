"""Tests for openakita_plugin_sdk.contrib.cost_translation."""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import (
    COST_TRANSLATION_MAP,
    CostTemplate,
    get_cost_template,
    register_cost_template,
    to_human_units,
    translate_cost,
)


def test_to_human_units_is_re_exported_from_package() -> None:
    """Regression for the missing __init__ export (audit B6)."""
    assert callable(to_human_units)
    assert "元" in to_human_units(3.5, "CNY")


def test_translate_cost_known_plugin_includes_units_money_runtime() -> None:
    label = translate_cost("seedance-video", cost=3.0, currency="CNY",
                           units=5, unit_label=None)
    assert "5 秒视频" in label
    assert "元" in label
    assert "30-60 秒" in label
    assert label.count(" / ") == 2


def test_translate_cost_unknown_plugin_falls_back_to_money_only() -> None:
    label = translate_cost("nonexistent-plugin-xyz", cost=2.0, currency="CNY")
    assert "元" in label
    assert " / " not in label  # no units, no runtime


def test_translate_cost_with_explicit_unit_label_overrides_template() -> None:
    label = translate_cost("seedance-video", cost=1.0, currency="CNY",
                           units=3, unit_label="次试拍")
    assert "3 次试拍" in label
    assert "秒视频" not in label


def test_translate_cost_integer_unit_renders_without_decimal() -> None:
    label = translate_cost("tongyi-image", cost=0.4, currency="CNY",
                           units=4)
    assert "4 张图片" in label
    assert "4.0" not in label


def test_translate_cost_fractional_unit_renders_with_one_decimal() -> None:
    label = translate_cost("highlight-cutter", cost=0.0, currency="CNY",
                           units=2.5)
    assert "2.5 分钟视频" in label


def test_register_cost_template_overrides_existing() -> None:
    original = COST_TRANSLATION_MAP.get("seedance-video")
    try:
        new_tpl = CostTemplate(
            unit_label_zh="秒高清片", per_unit_hint="x", runtime_hint="y",
        )
        register_cost_template("seedance-video", new_tpl)
        assert get_cost_template("seedance-video") == new_tpl
        label = translate_cost("seedance-video", cost=1.0, units=2)
        assert "2 秒高清片" in label
        assert "y" in label
    finally:
        # Restore so this test does not leak across tests
        if original is not None:
            COST_TRANSLATION_MAP["seedance-video"] = original


def test_register_cost_template_validates_input() -> None:
    with pytest.raises(ValueError):
        register_cost_template("", CostTemplate(unit_label_zh="x"))
    with pytest.raises(TypeError):
        register_cost_template("foo", "not a template")  # type: ignore[arg-type]


def test_translate_cost_custom_translator_overrides_money_format() -> None:
    label = translate_cost(
        "seedance-video", cost=3.0, currency="CNY", units=5,
        translator=lambda c, cur: f"自定义 {c} {cur}",
    )
    assert "自定义 3.0 CNY" in label


def test_translate_cost_credit_currency() -> None:
    label = translate_cost("seedance-video", cost=120.0, currency="credit",
                           units=10)
    assert "credits" in label


def test_translate_cost_negative_units_omitted() -> None:
    label = translate_cost("seedance-video", cost=1.0, currency="CNY",
                           units=-1)
    assert "秒视频" not in label  # negative units should not render
    assert "元" in label


def test_all_builtin_templates_render_without_error() -> None:
    """Smoke test: every builtin template must work for translate_cost."""
    for pid in list(COST_TRANSLATION_MAP.keys()):
        out = translate_cost(pid, cost=1.0, currency="CNY", units=1)
        assert out
