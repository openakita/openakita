"""Unit tests for footage_gate_grade — adjustments, filter assembly, HDR prep."""

from __future__ import annotations

import pytest
from footage_gate_grade import (
    PRESETS,
    build_grade_filter,
    derive_adjustments,
    get_preset,
    prepare_filter_chain,
)
from footage_gate_models import GRADE_CLAMPS, TONEMAP_CHAIN


class TestPresets:
    def test_all_required_presets_present(self) -> None:
        assert set(PRESETS.keys()) >= {
            "subtle",
            "neutral_punch",
            "warm_cinematic",
            "none",
        }

    def test_get_preset_returns_string(self) -> None:
        assert isinstance(get_preset("subtle"), str)
        assert get_preset("none") == ""

    def test_get_preset_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            get_preset("not-a-real-preset")


class TestDeriveAdjustments:
    def test_dark_clip_lifts_gamma_within_clamp(self) -> None:
        adj = derive_adjustments({"y_mean": 0.30, "y_std": 0.18, "sat_mean": 0.25})
        assert adj["gamma"] >= 1.0
        cl = GRADE_CLAMPS["gamma"]
        assert cl[0] <= adj["gamma"] <= cl[1]

    def test_bright_clip_pulls_gamma_back(self) -> None:
        adj = derive_adjustments({"y_mean": 0.70, "y_std": 0.18, "sat_mean": 0.25})
        assert adj["gamma"] < 1.0

    def test_flat_clip_boosts_contrast(self) -> None:
        adj = derive_adjustments({"y_mean": 0.5, "y_std": 0.10, "sat_mean": 0.25})
        assert adj["contrast"] > 1.0

    def test_oversaturated_clip_pulls_back(self) -> None:
        adj = derive_adjustments({"y_mean": 0.5, "y_std": 0.18, "sat_mean": 0.45})
        assert adj["saturation"] < 1.0

    def test_undersaturated_clip_boosts(self) -> None:
        adj = derive_adjustments({"y_mean": 0.5, "y_std": 0.18, "sat_mean": 0.10})
        assert adj["saturation"] > 1.0

    def test_clamps_are_strictly_enforced(self) -> None:
        adj = derive_adjustments({"y_mean": 0.0, "y_std": 0.0, "sat_mean": 0.0})
        for axis, (lo, hi) in GRADE_CLAMPS.items():
            assert lo <= adj[axis] <= hi

        adj2 = derive_adjustments({"y_mean": 1.0, "y_std": 1.0, "sat_mean": 1.0})
        for axis, (lo, hi) in GRADE_CLAMPS.items():
            assert lo <= adj2[axis] <= hi


class TestBuildGradeFilter:
    def test_identity_returns_subtle_baseline(self) -> None:
        out = build_grade_filter(
            {"contrast": 1.0, "gamma": 1.0, "saturation": 1.0}
        )
        assert out == PRESETS["subtle"]

    def test_includes_contrast_when_nonidentity(self) -> None:
        out = build_grade_filter(
            {"contrast": 1.05, "gamma": 1.0, "saturation": 1.0}
        )
        assert out.startswith("eq=")
        assert "contrast=1.050" in out

    def test_includes_all_axes_when_set(self) -> None:
        out = build_grade_filter(
            {"contrast": 1.05, "gamma": 1.05, "saturation": 0.95}
        )
        assert "contrast=" in out
        assert "gamma=" in out
        assert "saturation=" in out


class TestPrepareFilterChain:
    def test_sdr_passthrough(self) -> None:
        assert prepare_filter_chain("eq=contrast=1.0", hdr_source=False) == (
            "eq=contrast=1.0"
        )

    def test_hdr_prepends_tonemap_chain(self) -> None:
        result = prepare_filter_chain("eq=contrast=1.0", hdr_source=True)
        assert result.startswith(TONEMAP_CHAIN)
        assert result.endswith("eq=contrast=1.0")
        # Single comma between chain and filter
        assert result == f"{TONEMAP_CHAIN},eq=contrast=1.0"

    def test_hdr_with_empty_filter_emits_only_tonemap(self) -> None:
        assert prepare_filter_chain("", hdr_source=True) == TONEMAP_CHAIN

    def test_sdr_with_empty_filter_returns_empty(self) -> None:
        assert prepare_filter_chain("", hdr_source=False) == ""
