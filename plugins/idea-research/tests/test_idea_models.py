"""Unit tests for idea-research data models (§5 / §6.4 / §6.5 / §15)."""

from __future__ import annotations

import time

import pytest
from idea_models import (
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    PERSONAS,
    PERSONAS_BY_ID,
    PERSONAS_BY_NAME,
    PRICE_TABLE,
    PROMPTS,
    RANKER_WEIGHTS,
    TrendItem,
    compute_interaction_rate,
    compute_time_decay,
    estimate_cost,
    get_mode,
    get_persona,
    hint_for,
    score_trend_item,
)

# ---- §5 MODES ---------------------------------------------------------------


def test_modes_have_four_canonical_entries():
    assert {m.id for m in MODES} == {
        "radar_pull",
        "breakdown_url",
        "compare_accounts",
        "script_remix",
    }


def test_get_mode_returns_or_raises():
    assert get_mode("radar_pull").id == "radar_pull"
    with pytest.raises(KeyError):
        get_mode("nope")


def test_modes_have_default_input_dicts():
    for m in MODES:
        assert isinstance(m.default_input, dict)
        assert m.label_zh and m.label_en


def test_modes_index_matches_list():
    assert {m.id: m for m in MODES} == MODES_BY_ID


# ---- §13.1 PERSONAS ---------------------------------------------------------


def test_personas_default_count_is_twelve():
    assert len(PERSONAS) == 12
    ids = {p.id for p in PERSONAS}
    assert ids == set(PERSONAS_BY_ID)
    names = {p.name for p in PERSONAS}
    assert names == set(PERSONAS_BY_NAME)


def test_persona_system_prompts_render_template_fully():
    """Plan §13.1.B requires no leftover placeholders in the rendered prompt."""

    for p in PERSONAS:
        assert "{name}" not in p.system_prompt
        assert "{audience}" not in p.system_prompt
        assert "{tone}" not in p.system_prompt
        assert p.system_prompt.startswith("你扮演 ")
        assert "你最擅长：" in p.system_prompt


@pytest.mark.parametrize(
    "lookup",
    ["xhs_ops", "小红书运营专家", "douyin_director", "抖音爆款编导"],
)
def test_get_persona_supports_id_or_name(lookup):
    assert get_persona(lookup) is not None


def test_get_persona_unknown_returns_none():
    assert get_persona("does-not-exist") is None


# ---- §7.3 PROMPTS -----------------------------------------------------------


def test_prompts_table_has_four_required_entries():
    assert set(PROMPTS) == {
        "STRUCTURE_PROMPT",
        "COMMENT_SUMMARY_PROMPT",
        "PERSONA_TAKEAWAYS_PROMPT",
        "SCRIPT_REMIX_PROMPT",
    }


def test_structure_prompt_mentions_required_fields():
    text = PROMPTS["STRUCTURE_PROMPT"]
    for placeholder in (
        "{title}",
        "{author}",
        "{duration}",
        "{platform}",
        "{transcript_segments_json}",
        "{frames_descriptions_json}",
    ):
        assert placeholder in text


def test_script_remix_prompt_mentions_mdrm_section():
    text = PROMPTS["SCRIPT_REMIX_PROMPT"]
    assert "{mdrm_inspirations_json}" in text
    assert "{my_persona}" in text
    assert "{num_variants}" in text


# ---- §6.5 RANKER + scoring --------------------------------------------------


def test_compute_interaction_rate_handles_zeros_safely():
    assert compute_interaction_rate(
        like=None, comment=None, share=None, view=None
    ) == pytest.approx(0.0)
    assert compute_interaction_rate(like=10, comment=2, share=1, view=100) == pytest.approx(
        (10 + 6 + 5) / 100
    )


def test_compute_time_decay_monotonically_decreases():
    fresh = compute_time_decay(fetched_at=1000, publish_at=1000)
    one_h = compute_time_decay(fetched_at=1000 + 3600, publish_at=1000)
    assert fresh == pytest.approx(1.0)
    assert 0.0 < one_h < fresh


def test_score_trend_item_boosted_by_keywords_and_mdrm():
    base = TrendItem(
        id="x",
        platform="bilibili",
        external_id="ext",
        external_url="https://b23.tv/x",
        title="AI 编辑器革命：Cursor 评测",
        like_count=1000,
        comment_count=50,
        share_count=10,
        view_count=10_000,
        publish_at=int(time.time()) - 3600,
        fetched_at=int(time.time()),
    )
    s_no_kw = score_trend_item(base, [])
    s_kw = score_trend_item(base, ["Cursor"])
    s_mdrm = score_trend_item(
        TrendItem(**{**base.__dict__, "mdrm_hits": ["hk1", "hk2"]}),
        ["Cursor"],
    )
    assert s_kw > s_no_kw
    assert s_mdrm > s_kw


def test_ranker_weights_have_required_keys():
    assert {
        "interaction_exp",
        "time_decay_half_life_h",
        "keyword_match_coeff",
        "mdrm_hit_coeff",
        "platform",
    } <= set(RANKER_WEIGHTS)
    assert "bilibili" in RANKER_WEIGHTS["platform"]


# ---- §5 estimate_cost -------------------------------------------------------


def test_estimate_cost_radar_is_zero():
    assert estimate_cost("radar_pull", {})["cost_cny"] == 0.0


def test_estimate_cost_breakdown_default_is_positive():
    out = estimate_cost("breakdown_url", {})
    assert out["cost_cny"] > 0
    assert {"asr", "vlm_frames", "structure_llm", "comments_llm", "persona_llm"} <= set(
        out["breakdown"]
    )


def test_estimate_cost_breakdown_long_video_uses_cloud_asr():
    short = estimate_cost(
        "breakdown_url",
        {"duration_seconds_estimate": 60, "num_frames_estimate": 10},
    )
    long = estimate_cost(
        "breakdown_url",
        {"duration_seconds_estimate": 7200, "num_frames_estimate": 10},
    )
    assert long["breakdown"]["asr"] > short["breakdown"]["asr"]


def test_estimate_cost_unknown_mode_returns_zero():
    out = estimate_cost("does_not_exist", {})
    assert out["cost_cny"] == 0.0
    assert "unknown_mode" in out["breakdown"]


def test_price_table_has_minimum_entries():
    for k in ("qwen-vl-max", "qwen-max", "qwen-plus", "paraformer-v2"):
        assert k in PRICE_TABLE


# ---- §15 ERROR_HINTS --------------------------------------------------------


def test_error_hints_have_eleven_categories_with_bilingual_text():
    expected = {
        "network",
        "timeout",
        "auth",
        "quota",
        "moderation",
        "rate_limit",
        "dependency",
        "format",
        "unknown",
        "cookies_expired",
        "crawler_blocked",
    }
    assert set(ERROR_HINTS) == expected
    for kind, hint in ERROR_HINTS.items():
        assert "zh" in hint and "en" in hint, kind
        assert hint["zh"] and hint["en"]


def test_hint_for_falls_back_to_unknown():
    assert hint_for("does-not-exist") == ERROR_HINTS["unknown"]
    assert hint_for("auth") == ERROR_HINTS["auth"]
