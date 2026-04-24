"""Tests for clip_models.py — modes, presets, pricing, error hints."""

from __future__ import annotations

import pytest

from clip_models import (
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    SILENCE_PRESETS,
    SILENCE_PRESETS_BY_ID,
    CostPreview,
    estimate_cost,
    get_error_hints,
    get_mode,
    mode_to_dict,
)


class TestModes:
    def test_four_modes_defined(self):
        assert len(MODES) == 4

    def test_mode_ids_unique(self):
        ids = [m.id for m in MODES]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize(
        "mode_id",
        ["highlight_extract", "silence_clean", "topic_split", "talking_polish"],
    )
    def test_get_mode(self, mode_id: str):
        m = get_mode(mode_id)
        assert m is not None
        assert m.id == mode_id

    def test_get_mode_invalid(self):
        assert get_mode("nonexistent") is None

    def test_silence_clean_skips_transcribe_analyze(self):
        m = get_mode("silence_clean")
        assert m is not None
        assert "transcribe" in m.skip_steps
        assert "analyze" in m.skip_steps

    def test_silence_clean_no_api_key(self):
        m = get_mode("silence_clean")
        assert m is not None
        assert m.requires_api_key is False

    def test_other_modes_require_api_key(self):
        for mid in ("highlight_extract", "topic_split", "talking_polish"):
            m = get_mode(mid)
            assert m is not None
            assert m.requires_api_key is True

    def test_mode_to_dict(self):
        m = get_mode("highlight_extract")
        assert m is not None
        d = mode_to_dict(m)
        assert d["id"] == "highlight_extract"
        assert "label_zh" in d
        assert "label_en" in d
        assert isinstance(d["skip_steps"], list)


class TestSilencePresets:
    def test_three_presets(self):
        assert len(SILENCE_PRESETS) == 3

    @pytest.mark.parametrize("pid", ["conservative", "standard", "aggressive"])
    def test_preset_exists(self, pid: str):
        assert pid in SILENCE_PRESETS_BY_ID

    def test_threshold_ordering(self):
        c = SILENCE_PRESETS_BY_ID["conservative"]
        s = SILENCE_PRESETS_BY_ID["standard"]
        a = SILENCE_PRESETS_BY_ID["aggressive"]
        assert c.threshold_db < s.threshold_db < a.threshold_db

    def test_min_silence_ordering(self):
        c = SILENCE_PRESETS_BY_ID["conservative"]
        s = SILENCE_PRESETS_BY_ID["standard"]
        a = SILENCE_PRESETS_BY_ID["aggressive"]
        assert c.min_silence_sec > s.min_silence_sec > a.min_silence_sec


class TestCostEstimation:
    def test_silence_clean_is_free(self):
        cost = estimate_cost("silence_clean", 1800.0)
        assert cost.total_cny == 0.0
        assert len(cost.items) == 0

    def test_highlight_extract_cost(self):
        cost = estimate_cost("highlight_extract", 1800.0)
        assert cost.total_cny > 0
        assert len(cost.items) == 2
        apis = {it["api"] for it in cost.items}
        assert "paraformer-v2" in apis
        assert "qwen-plus" in apis

    def test_topic_split_cost(self):
        cost = estimate_cost("topic_split", 1800.0)
        assert cost.total_cny > 0
        assert len(cost.items) == 2

    def test_talking_polish_cost(self):
        cost = estimate_cost("talking_polish", 1800.0)
        assert cost.total_cny > 0
        assert len(cost.items) == 2

    def test_30min_highlight_cost_around_1_5(self):
        cost = estimate_cost("highlight_extract", 1800.0)
        assert 1.0 < cost.total_cny < 2.5

    def test_invalid_mode_returns_zero(self):
        cost = estimate_cost("nonexistent", 1800.0)
        assert cost.total_cny == 0.0

    def test_cost_preview_type(self):
        cost = estimate_cost("highlight_extract", 60.0)
        assert isinstance(cost, CostPreview)


class TestErrorHints:
    EXPECTED_KINDS = [
        "network", "timeout", "auth", "quota", "moderation",
        "dependency", "format", "duration", "unknown",
    ]

    def test_all_nine_kinds_present(self):
        assert len(ERROR_HINTS) == 9
        for kind in self.EXPECTED_KINDS:
            assert kind in ERROR_HINTS

    @pytest.mark.parametrize("kind", EXPECTED_KINDS)
    def test_hint_structure(self, kind: str):
        h = ERROR_HINTS[kind]
        assert "label_zh" in h
        assert "label_en" in h
        assert "color" in h
        assert "hints_zh" in h
        assert "hints_en" in h
        assert isinstance(h["hints_zh"], list)
        assert len(h["hints_zh"]) > 0

    def test_get_error_hints_valid(self):
        h = get_error_hints("auth")
        assert h["label_en"] == "Auth Error"

    def test_get_error_hints_unknown_fallback(self):
        h = get_error_hints("totally_invalid")
        assert h["label_en"] == "Unknown Error"
