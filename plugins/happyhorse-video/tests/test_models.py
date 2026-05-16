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
