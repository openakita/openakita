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
