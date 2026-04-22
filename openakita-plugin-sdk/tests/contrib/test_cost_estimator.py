"""Tests for openakita_plugin_sdk.contrib.cost_estimator."""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import CostEstimator
from openakita_plugin_sdk.contrib.cost_estimator import to_human_units


def test_basic_two_lines_sums_correctly() -> None:
    est = CostEstimator(currency="CNY", retry_multiplier=1.10, safety_margin=0.15)
    est.add("Seedance 2.0 720p", units=5, unit_label="s", unit_price=0.20)  # 1.00
    est.add("Seedance audio",     units=5, unit_label="s", unit_price=0.04)  # 0.20
    p = est.build(confidence="high")
    assert p.low == pytest.approx(1.20)
    # high = 1.20 * 1.10 * 1.15 = 1.518
    assert p.high == pytest.approx(1.20 * 1.10 * 1.15)
    assert p.confidence == "high"
    assert p.currency == "CNY"
    assert p.human_label  # not empty


def test_empty_returns_low_confidence() -> None:
    p = CostEstimator().build()
    assert p.low == 0.0 and p.high == 0.0
    assert p.confidence == "low"


def test_invalid_multipliers_raise() -> None:
    with pytest.raises(ValueError):
        CostEstimator(retry_multiplier=0.5)
    with pytest.raises(ValueError):
        CostEstimator(safety_margin=-0.1)


def test_negative_units_raise() -> None:
    est = CostEstimator()
    with pytest.raises(ValueError):
        est.add("x", units=-1, unit_label="s", unit_price=1.0)


def test_sample_label_picks_matching_subtotal() -> None:
    est = CostEstimator()
    est.add("a", units=1, unit_label="x", unit_price=1.0)
    est.add("b", units=2, unit_label="x", unit_price=3.0)
    p = est.build(sample_label="a")
    assert p.sample_cost == pytest.approx(1.0)


def test_to_dict_round_trip() -> None:
    est = CostEstimator()
    est.add("a", units=1, unit_label="x", unit_price=1.0)
    est.note("excludes upload fees")
    d = est.build().to_dict()
    assert "low" in d and "high" in d and "breakdown" in d
    assert d["notes"] == ["excludes upload fees"]


def test_human_translator_known_currencies() -> None:
    assert "元" in to_human_units(5.0, "CNY")
    assert "$" in to_human_units(5.0, "USD")
    assert "credits" in to_human_units(50.0, "credit")


def test_reset_clears_items() -> None:
    est = CostEstimator()
    est.add("a", units=1, unit_label="x", unit_price=1.0)
    est.reset()
    assert est.build().low == 0.0
