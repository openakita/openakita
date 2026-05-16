"""Smoke tests for happyhorse_models.py — modes, voices, pricing, cost."""

from __future__ import annotations

import pytest
from happyhorse_models import (
    AUDIO_LIMITS,
    COSYVOICE_VOICES,
    EDGE_VOICES,
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    PRICE_TABLE,
    SYSTEM_VOICES,
    VOICES_BY_ID,
    build_catalog,
    check_audio_duration,
    estimate_cost,
)


def test_twelve_modes_present():
    expected = {
        "t2v",
        "i2v",
        "i2v_end",
        "video_extend",
        "r2v",
        "video_edit",
        "photo_speak",
        "video_relip",
        "video_reface",
        "pose_drive",
        "avatar_compose",
        "long_video",
    }
    assert {m.id for m in MODES} == expected
    assert set(MODES_BY_ID.keys()) == expected


def test_voice_catalog_unique_ids():
    assert len(SYSTEM_VOICES) == len(VOICES_BY_ID)
    assert len(SYSTEM_VOICES) >= len(COSYVOICE_VOICES) + len(EDGE_VOICES) - 1


def test_price_table_has_happyhorse_and_wan_models():
    assert "happyhorse-1.0-t2v" in PRICE_TABLE
    assert "happyhorse-1.0-i2v" in PRICE_TABLE
    assert "happyhorse-1.0-r2v" in PRICE_TABLE
    assert any(k.startswith("wan2.6") for k in PRICE_TABLE)


def test_estimate_cost_t2v_returns_positive_total():
    preview = estimate_cost(
        "t2v",
        {"model": "happyhorse-1.0-t2v", "duration": 5, "resolution": "720P"},
    )
    assert preview["total"] > 0
    assert preview["formatted_total"].startswith("¥")
    assert preview["currency"] == "CNY"


def test_estimate_cost_unknown_mode_raises():
    with pytest.raises(ValueError):
        estimate_cost("not-a-mode", {})


def test_check_audio_duration_too_short():
    """photo_speak's lower bound is 0.5s, so anything below must fail."""
    err = check_audio_duration("photo_speak", 0.1)
    assert err is not None
    assert "0.5" in err or "时长" in err


def test_check_audio_duration_within_range():
    err = check_audio_duration("photo_speak", 5.0)
    assert err is None


def test_audio_limits_dict_only_for_relevant_modes():
    assert "photo_speak" in AUDIO_LIMITS
    assert "video_relip" in AUDIO_LIMITS


def test_error_hints_cover_known_kinds():
    for kind in ("auth", "client", "server", "quota", "network", "timeout"):
        assert kind in ERROR_HINTS


def test_build_catalog_smoke():
    cat = build_catalog()
    assert len(cat.modes) == 12
    assert cat.cost_threshold > 0
    assert isinstance(cat.default_models, dict)
    assert all(isinstance(v, str) for v in cat.default_models.values())


# ─── Bug 1 regression — TTS engine normalization ─────────────────────


@pytest.mark.parametrize(
    "engine_alias",
    ["cosyvoice", "cosyvoice-v2", "CosyVoice", "cosyvoice_v2", "qwen-tts"],
)
def test_cosyvoice_aliases_bill_at_cosyvoice_v2_rate(engine_alias):
    """Regression for issue #480: any cosyvoice alias must produce a
    non-zero TTS subtotal in long_video / digital-human cost previews.

    The pre-fix bug folded ``"cosyvoice"`` into the edge-tts (free)
    bucket so the user saw ¥0 for paid cosyvoice synthesis and the
    cost-approval gate never triggered.
    """
    preview = estimate_cost(
        "photo_speak",
        {"model": "wan2.2-s2v", "resolution": "480P", "tts_engine": engine_alias},
        audio_duration_sec=10.0,
        text_chars=5000,
    )
    tts_items = [it for it in preview["items"] if "TTS" in it["name"]]
    assert tts_items, f"engine={engine_alias!r}: no TTS line found"
    tts = tts_items[0]
    assert tts["name"] == "cosyvoice-v2 TTS"
    assert tts["unit_price"] == pytest.approx(
        PRICE_TABLE["cosyvoice-v2"]["per_10k_chars"]
    )
    assert tts["subtotal"] > 0, (
        f"engine={engine_alias!r}: cosyvoice was billed as free"
    )


@pytest.mark.parametrize("engine_alias", ["edge", "edge-tts", "EDGE"])
def test_edge_aliases_bill_as_free(engine_alias):
    preview = estimate_cost(
        "photo_speak",
        {"model": "wan2.2-s2v", "resolution": "480P", "tts_engine": engine_alias},
        audio_duration_sec=10.0,
        text_chars=5000,
    )
    tts_items = [it for it in preview["items"] if "TTS" in it["name"]]
    assert tts_items
    assert tts_items[0]["name"] == "edge-tts TTS"
    assert tts_items[0]["subtotal"] == 0


# ─── Bug 2 regression — official price table cross-check (2026-05) ───
#
# Source: https://help.aliyun.com/zh/model-studio/model-pricing
# These freeze the *current* CN-tier official prices so a future remote
# drift trips the CI before the user gets a billing-shock.


@pytest.mark.parametrize(
    "model_id,key,expected",
    [
        # HappyHorse 1.0 family
        ("happyhorse-1.0-t2v", "720P_per_sec", 0.90),
        ("happyhorse-1.0-t2v", "1080P_per_sec", 1.60),
        ("happyhorse-1.0-i2v", "720P_per_sec", 0.90),
        ("happyhorse-1.0-r2v", "1080P_per_sec", 1.60),
        ("happyhorse-1.0-video-edit", "720P_per_sec", 0.90),
        # Wan 2.6 standard (was 0.70/1.20 → corrected to official 0.60/1.00)
        ("wan2.6-t2v", "720P_per_sec", 0.60),
        ("wan2.6-t2v", "1080P_per_sec", 1.00),
        ("wan2.6-i2v", "720P_per_sec", 0.60),
        ("wan2.6-r2v", "1080P_per_sec", 1.00),
        # Wan 2.6 flash (now audio-tiered)
        ("wan2.6-i2v-flash", "audio-true_720P_per_sec", 0.30),
        ("wan2.6-i2v-flash", "audio-true_1080P_per_sec", 0.50),
        ("wan2.6-i2v-flash", "audio-false_720P_per_sec", 0.15),
        ("wan2.6-i2v-flash", "audio-false_1080P_per_sec", 0.25),
        ("wan2.6-r2v-flash", "audio-false_720P_per_sec", 0.15),
        # Wan 2.7 i2v (was 0.85/1.50 → corrected to official 0.60/1.00)
        ("wan2.7-i2v", "720P_per_sec", 0.60),
        ("wan2.7-i2v", "1080P_per_sec", 1.00),
        # Digital human (unchanged from official)
        ("wan2.2-s2v-detect", "per_image", 0.004),
        ("wan2.2-s2v", "480P_per_sec", 0.50),
        ("wan2.2-s2v", "720P_per_sec", 0.90),
        # videoretalk (was 0.30 → corrected to official 0.08)
        ("videoretalk", "per_sec", 0.08),
        ("wan2.2-animate-mix", "wan-std_per_sec", 0.60),
        ("wan2.2-animate-mix", "wan-pro_per_sec", 1.20),
        ("wan2.2-animate-move", "wan-std_per_sec", 0.40),
        ("wan2.2-animate-move", "wan-pro_per_sec", 0.60),
        # Image gen
        ("wan2.7-image", "per_image", 0.20),
        ("wan2.7-image-pro", "per_image", 0.50),
        # TTS (was 0.20 → corrected to official 2.00; 10× undercount)
        ("cosyvoice-v2", "per_10k_chars", 2.00),
        ("edge-tts", "per_10k_chars", 0.00),
    ],
)
def test_price_table_matches_official_dashscope_cn(model_id, key, expected):
    """Freeze the official 2026-05 CN-tier unit prices.

    See https://help.aliyun.com/zh/model-studio/model-pricing — when this
    test fails the official page changed; update the table and the
    parameter list in tandem so the cost preview never silently drifts.
    """
    assert PRICE_TABLE[model_id][key] == pytest.approx(expected), (
        f"{model_id}[{key}] drifted from official price"
    )


def test_videoretalk_cost_uses_corrected_price():
    """video_relip used to bill at ¥0.30/s — must now be ¥0.08/s."""
    preview = estimate_cost(
        "video_relip",
        {"model": "videoretalk"},
        audio_duration_sec=10.0,
    )
    vr = [it for it in preview["items"] if it["name"] == "videoretalk"]
    assert vr, "videoretalk line missing"
    assert vr[0]["unit_price"] == pytest.approx(0.08)
    assert vr[0]["subtotal"] == pytest.approx(0.80)


def test_wan26_t2v_cost_uses_corrected_price():
    preview = estimate_cost(
        "t2v",
        {"model": "wan2.6-t2v", "resolution": "1080P", "duration": 5},
    )
    line = preview["items"][0]
    assert line["unit_price"] == pytest.approx(1.00)
    assert line["subtotal"] == pytest.approx(5.00)


def test_wan27_i2v_cost_uses_corrected_price():
    preview = estimate_cost(
        "i2v",
        {"model": "wan2.7-i2v", "resolution": "720P", "duration": 5},
    )
    line = preview["items"][0]
    assert line["unit_price"] == pytest.approx(0.60)
    assert line["subtotal"] == pytest.approx(3.00)


def test_wan26_flash_picks_audio_true_tier_when_url_supplied():
    """Flash variants halve cost when silent; cost preview must pick the
    right tier from ``params['driving_audio_url']`` / ``params['audio']``.
    """
    with_audio = estimate_cost(
        "i2v",
        {
            "model": "wan2.6-i2v-flash",
            "resolution": "720P",
            "duration": 5,
            "driving_audio_url": "https://example.com/bg.mp3",
        },
    )
    silent = estimate_cost(
        "i2v",
        {
            "model": "wan2.6-i2v-flash",
            "resolution": "720P",
            "duration": 5,
            "audio": False,
        },
    )
    assert with_audio["items"][0]["unit_price"] == pytest.approx(0.30)
    assert silent["items"][0]["unit_price"] == pytest.approx(0.15)
    assert silent["items"][0]["subtotal"] < with_audio["items"][0]["subtotal"]


def test_wan26_flash_picks_audio_true_when_audio_flag_true():
    preview = estimate_cost(
        "i2v",
        {
            "model": "wan2.6-i2v-flash",
            "resolution": "1080P",
            "duration": 5,
            "audio": True,
        },
    )
    assert preview["items"][0]["unit_price"] == pytest.approx(0.50)


def test_cosyvoice_v2_price_at_official_10x_higher():
    """Regression: a 10k-char script used to bill ¥0.20; the official
    price is ¥2.00 — must now report the correct ¥2.00 (10× higher)."""
    preview = estimate_cost(
        "photo_speak",
        {"model": "wan2.2-s2v", "resolution": "480P", "tts_engine": "cosyvoice-v2"},
        audio_duration_sec=10.0,
        text_chars=10000,
    )
    tts = [it for it in preview["items"] if "TTS" in it["name"]][0]
    assert tts["unit_price"] == pytest.approx(2.00)
    assert tts["subtotal"] == pytest.approx(2.00)
