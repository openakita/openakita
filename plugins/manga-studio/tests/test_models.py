"""Phase 2.1 — manga_models.py: visual styles, voices, pricing, cost, hints."""

from __future__ import annotations

import pytest

from manga_models import (
    ALL_VOICES,
    BACKENDS,
    CHARACTER_ROLES,
    COSYVOICE_VOICES,
    DEFAULT_COST_THRESHOLD_CNY,
    DURATION_OPTIONS,
    EDGE_TTS_VOICES,
    ERROR_HINTS,
    PRICE_TABLE,
    RATIOS,
    SECONDS_PER_PANEL_OPTIONS,
    VISUAL_STYLES,
    VISUAL_STYLES_BY_ID,
    VOICES_BY_ID,
    build_catalog,
    estimate_cost,
    hint_for,
)

# ─── Visual styles ───────────────────────────────────────────────────────


def test_visual_styles_count_and_unique_ids() -> None:
    assert len(VISUAL_STYLES) == 10
    ids = [s.id for s in VISUAL_STYLES]
    assert len(set(ids)) == 10  # all unique


def test_visual_styles_required_fields() -> None:
    for s in VISUAL_STYLES:
        assert s.label_zh and s.label_en
        assert s.description_zh and s.description_en
        assert s.prompt_fragment  # used by prompt_assembler
        assert s.negative_prompt
        d = s.to_dict()
        # Sanity-check the public dict surface.
        assert d["id"] == s.id
        assert d["label"] == s.label_zh


def test_visual_styles_by_id_lookup() -> None:
    assert VISUAL_STYLES_BY_ID["shonen"].label_en == "Shonen"
    assert "ghibli" in VISUAL_STYLES_BY_ID
    assert "noir" in VISUAL_STYLES_BY_ID


# ─── Voices ──────────────────────────────────────────────────────────────


def test_edge_tts_voices_cover_role_archetypes() -> None:
    assert len(EDGE_TTS_VOICES) == 4
    for v in EDGE_TTS_VOICES:
        assert v.engine == "edge"
        assert v.is_free is True


def test_cosyvoice_voices_count_and_paid_flag() -> None:
    assert len(COSYVOICE_VOICES) == 12
    for v in COSYVOICE_VOICES:
        assert v.engine == "cosyvoice"
        assert v.is_free is False


def test_all_voices_combined_and_indexed() -> None:
    assert len(ALL_VOICES) == len(EDGE_TTS_VOICES) + len(COSYVOICE_VOICES)
    assert "zh-CN-XiaoyiNeural" in VOICES_BY_ID
    assert "longxiaochun" in VOICES_BY_ID


# ─── Aspect / duration / role / backend ──────────────────────────────────


def test_ratios_includes_vertical_first() -> None:
    assert RATIOS[0] == "9:16"  # vertical-first for short-drama platforms


def test_duration_and_panel_options_monotonic() -> None:
    assert tuple(sorted(DURATION_OPTIONS)) == DURATION_OPTIONS
    assert tuple(sorted(SECONDS_PER_PANEL_OPTIONS)) == SECONDS_PER_PANEL_OPTIONS


def test_character_roles_match_db_constraints() -> None:
    assert set(CHARACTER_ROLES) == {"main", "support", "narrator", "villain"}


def test_backends_match_db_constraints() -> None:
    assert set(BACKENDS) == {"direct", "runninghub", "comfyui_local"}


# ─── Price table (frozen) ────────────────────────────────────────────────


def test_price_table_frozen_keys() -> None:
    """Tests fail loudly if a remote price drift renames a key. We
    deliberately freeze the *shape* — not the values — so a vendor price
    update only requires editing one constant + this test."""
    assert {
        "seedance-1.0-lite-i2v",
        "seedance-1.0-lite-t2v",
        "wan2.7-image",
        "wan2.7-image-pro",
        "cosyvoice-v2",
        "qwen-vl-max",
        "edge-tts",
    } == set(PRICE_TABLE.keys())


def test_price_table_seedance_resolution_keys() -> None:
    for key in ("seedance-1.0-lite-i2v", "seedance-1.0-lite-t2v"):
        assert "480P_per_sec" in PRICE_TABLE[key]
        assert "720P_per_sec" in PRICE_TABLE[key]


# ─── Cost estimation ─────────────────────────────────────────────────────


def test_estimate_cost_default_path_returns_4_items() -> None:
    cost = estimate_cost(n_panels=10, total_duration_sec=60, story_chars=300)
    assert cost["currency"] == "CNY"
    # qwen + image + video + edge-tts = 4 lines
    assert len(cost["items"]) == 4
    names = [it["name"] for it in cost["items"]]
    assert "qwen-vl-max (script)" in names
    assert "wan2.7-image" in names
    assert any("seedance-1.0-lite-i2v" in n for n in names)
    assert "edge-tts (free)" in names


def test_estimate_cost_with_paid_tts_swaps_engine() -> None:
    cost = estimate_cost(
        n_panels=5,
        total_duration_sec=30,
        story_chars=500,
        tts_engine="cosyvoice",
    )
    names = [it["name"] for it in cost["items"]]
    assert "cosyvoice-v2 TTS" in names
    assert "edge-tts (free)" not in names


def test_estimate_cost_skip_qwen_when_disabled() -> None:
    cost = estimate_cost(
        n_panels=5,
        total_duration_sec=30,
        story_chars=200,
        use_qwen_for_script=False,
    )
    assert all("qwen" not in it["name"] for it in cost["items"])


def test_estimate_cost_720p_costs_more_than_480p() -> None:
    a = estimate_cost(n_panels=5, total_duration_sec=30, story_chars=200, resolution="480P")
    b = estimate_cost(n_panels=5, total_duration_sec=30, story_chars=200, resolution="720P")
    assert b["total"] > a["total"]


def test_estimate_cost_image_pro_costs_more() -> None:
    a = estimate_cost(
        n_panels=10,
        total_duration_sec=60,
        story_chars=200,
        image_model="wan2.7-image",
    )
    b = estimate_cost(
        n_panels=10,
        total_duration_sec=60,
        story_chars=200,
        image_model="wan2.7-image-pro",
    )
    assert b["total"] > a["total"]


def test_estimate_cost_threshold_flag() -> None:
    """A short 480P episode under the threshold; a long 720P over it."""
    # Even one Seedance second already costs ¥0.40, so to land under the
    # ¥5 default threshold we keep the episode at 5s with one panel and
    # disable the qwen script-writing line.
    cheap = estimate_cost(
        n_panels=1,
        total_duration_sec=5,
        story_chars=50,
        use_qwen_for_script=False,
        threshold=DEFAULT_COST_THRESHOLD_CNY,
    )
    expensive = estimate_cost(
        n_panels=20,
        total_duration_sec=300,
        story_chars=2000,
        resolution="720P",
        image_model="wan2.7-image-pro",
        tts_engine="cosyvoice",
        threshold=DEFAULT_COST_THRESHOLD_CNY,
    )
    assert cheap["exceeds_threshold"] is False
    assert expensive["exceeds_threshold"] is True
    assert cheap["total"] < expensive["total"]


def test_estimate_cost_unknown_video_model_raises() -> None:
    with pytest.raises(ValueError, match="unknown video_model"):
        estimate_cost(
            n_panels=1,
            total_duration_sec=5,
            story_chars=10,
            video_model="bogus-model",
        )


def test_estimate_cost_formatted_total_uses_yuan_sign() -> None:
    cost = estimate_cost(n_panels=1, total_duration_sec=5, story_chars=10)
    assert cost["formatted_total"].startswith("¥")
    assert "{:.2f}".format(cost["total"]) in cost["formatted_total"]


# ─── Error hints ─────────────────────────────────────────────────────────


def test_error_hints_required_keys_present() -> None:
    required = {
        "network",
        "timeout",
        "rate_limit",
        "auth",
        "not_found",
        "moderation",
        "moderation_face",
        "content_violation",
        "quota",
        "dependency",
        "unknown",
    }
    assert required.issubset(ERROR_HINTS.keys())


def test_error_hints_have_bilingual_payload() -> None:
    for kind, payload in ERROR_HINTS.items():
        assert payload["title_zh"] and payload["title_en"], kind
        assert payload["hints_zh"], kind
        assert payload["hints_en"], kind
        # Same number of bullet points in both languages.
        assert len(payload["hints_zh"]) == len(payload["hints_en"]), kind


def test_hint_for_falls_back_to_unknown() -> None:
    assert hint_for(None)["title_zh"] == ERROR_HINTS["unknown"]["title_zh"]
    assert hint_for("totally_made_up_kind") == ERROR_HINTS["unknown"]
    assert hint_for("network") == ERROR_HINTS["network"]


# ─── Catalog ─────────────────────────────────────────────────────────────


def test_build_catalog_shape() -> None:
    cat = build_catalog()
    assert len(cat.visual_styles) == 10
    assert cat.ratios == list(RATIOS)
    assert cat.duration_options == list(DURATION_OPTIONS)
    assert cat.seconds_per_panel_options == list(SECONDS_PER_PANEL_OPTIONS)
    assert cat.character_roles == list(CHARACTER_ROLES)
    assert cat.backends == list(BACKENDS)
    assert len(cat.voices) == len(ALL_VOICES)
    assert cat.cost_threshold == DEFAULT_COST_THRESHOLD_CNY


def test_build_catalog_voices_have_engine_field() -> None:
    cat = build_catalog()
    engines = {v["engine"] for v in cat.voices}
    assert engines == {"edge", "cosyvoice"}
