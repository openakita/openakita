"""Phase 1 — avatar_models pure-data + cost / hint coverage."""

from __future__ import annotations

import pytest
from avatar_models import (
    AUDIO_LIMITS,
    DEFAULT_COST_THRESHOLD_CNY,
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    PRICE_TABLE,
    SYSTEM_VOICES,
    VOICES_BY_ID,
    build_catalog,
    check_audio_duration,
    estimate_cost,
    hint_for,
)

# ─── Catalog shape ───────────────────────────────────────────────────────


def test_modes_count_and_endpoints() -> None:
    assert len(MODES) == 5
    expected = {"photo_speak", "video_relip", "video_reface", "avatar_compose", "pose_drive"}
    assert {m.id for m in MODES} == expected
    for m in MODES:
        assert m.dashscope_endpoint
        assert m.label_zh and m.label_en
        assert m.cost_strategy
    assert MODES_BY_ID["photo_speak"].dashscope_endpoint == "submit_s2v"
    assert MODES_BY_ID["video_reface"].dashscope_endpoint == "submit_animate_mix"
    assert MODES_BY_ID["pose_drive"].dashscope_endpoint == "submit_animate_move"


def test_voices_catalog_12_cosyvoice_v2() -> None:
    assert len(SYSTEM_VOICES) == 12
    ids = {v.id for v in SYSTEM_VOICES}
    assert "longxiaochun" in ids and "longwan" in ids
    for v in SYSTEM_VOICES:
        assert v.is_system is True
        assert v.gender in {"female", "male", "neutral"}
        assert VOICES_BY_ID[v.id] is v


def test_build_catalog_payload_serialisable() -> None:
    cat = build_catalog()
    assert len(cat.modes) == 5
    assert len(cat.voices) == 12
    assert "480P" in cat.resolutions and "720P" in cat.resolutions
    assert "wan-std" in cat.animate_mix_modes and "wan-pro" in cat.animate_mix_modes
    assert cat.cost_threshold == DEFAULT_COST_THRESHOLD_CNY


# ─── Price table is frozen (drift-detection) ─────────────────────────────


def test_price_table_frozen_keys() -> None:
    """Tests pin the published unit-price keys so a remote drift is loud."""
    assert PRICE_TABLE["wan2.2-s2v-detect"]["per_image"] == 0.004
    assert PRICE_TABLE["wan2.2-s2v"]["480P_per_sec"] == 0.50
    assert PRICE_TABLE["wan2.2-s2v"]["720P_per_sec"] == 0.90
    assert PRICE_TABLE["videoretalk"]["per_sec"] == 0.30
    assert PRICE_TABLE["wan2.2-animate-mix"]["wan-std_per_sec"] == 0.60
    assert PRICE_TABLE["wan2.2-animate-mix"]["wan-pro_per_sec"] == 1.20
    assert PRICE_TABLE["wan2.2-animate-move"]["wan-std_per_sec"] == 0.40
    assert PRICE_TABLE["wan2.2-animate-move"]["wan-pro_per_sec"] == 0.60
    assert PRICE_TABLE["wan2.5-i2i-preview"]["per_image"] == 0.20
    assert PRICE_TABLE["wan2.7-image"]["per_image"] == 0.20
    assert PRICE_TABLE["wan2.7-image-pro"]["per_image"] == 0.50
    assert PRICE_TABLE["cosyvoice-v2"]["per_10k_chars"] == 0.20


# ─── estimate_cost — happy paths × 4 modes ───────────────────────────────


def test_estimate_cost_photo_speak_480p_5s() -> None:
    p = estimate_cost(
        "photo_speak",
        {"resolution": "480P"},
        audio_duration_sec=5.0,
        text_chars=120,
    )
    # 0.004 (detect) + 0.50*5 (s2v) + 0.20*(120/10000) ≈ 0.004 + 2.50 + 0.0024
    assert p["currency"] == "CNY"
    assert p["total"] == pytest.approx(2.51, abs=0.005)
    assert p["formatted_total"].startswith("¥")
    assert all(it["subtotal"] >= 0 for it in p["items"])
    assert any("s2v" in it["name"] for it in p["items"])
    assert p["exceeds_threshold"] is False


def test_estimate_cost_photo_speak_720p_drives_higher_per_sec() -> None:
    low = estimate_cost("photo_speak", {"resolution": "480P"}, audio_duration_sec=10.0)
    high = estimate_cost("photo_speak", {"resolution": "720P"}, audio_duration_sec=10.0)
    assert high["total"] > low["total"]


def test_estimate_cost_video_relip_uses_audio_duration() -> None:
    p = estimate_cost(
        "video_relip",
        {"video_duration_sec": 30.0},
        audio_duration_sec=8.0,
    )
    # videoretalk billed by audio not video → 0.30 * 8 = 2.40
    relip_item = next(it for it in p["items"] if it["name"] == "videoretalk")
    assert relip_item["units"] == 8.0
    assert relip_item["subtotal"] == pytest.approx(2.40, abs=0.005)


def test_estimate_cost_video_reface_pro_more_expensive() -> None:
    std = estimate_cost("video_reface", {"video_duration_sec": 5.0, "mode_pro": False})
    pro = estimate_cost("video_reface", {"video_duration_sec": 5.0, "mode_pro": True})
    assert pro["total"] == pytest.approx(2 * std["total"], abs=0.005)
    assert pro["total"] == pytest.approx(6.00, abs=0.005)
    assert pro["exceeds_threshold"] is True


def test_estimate_cost_avatar_compose_scales_with_ref_images() -> None:
    p1 = estimate_cost(
        "avatar_compose",
        {"ref_image_count": 1, "resolution": "480P"},
        audio_duration_sec=4.0,
    )
    p3 = estimate_cost(
        "avatar_compose",
        {"ref_image_count": 3, "resolution": "480P"},
        audio_duration_sec=4.0,
    )
    assert p3["total"] > p1["total"]
    # 0.20 * 3 - 0.20 * 1 = 0.40 delta, plus identical s2v + detect
    assert p3["total"] - p1["total"] == pytest.approx(0.40, abs=0.005)


def test_estimate_cost_avatar_compose_with_qwen_vl_adds_item() -> None:
    p = estimate_cost(
        "avatar_compose",
        {"ref_image_count": 2, "use_qwen_vl": True, "qwen_token_estimate": 1000},
        audio_duration_sec=3.0,
    )
    assert any("qwen-vl-max" in it["name"] for it in p["items"])


def test_estimate_cost_pose_drive_std_and_pro() -> None:
    std = estimate_cost("pose_drive", {"video_duration_sec": 10.0, "mode_pro": False})
    pro = estimate_cost("pose_drive", {"video_duration_sec": 10.0, "mode_pro": True})
    assert std["total"] == pytest.approx(4.00, abs=0.005)
    assert pro["total"] == pytest.approx(6.00, abs=0.005)
    assert pro["total"] > std["total"]
    item_std = next(it for it in std["items"] if "animate-move" in it["name"])
    assert item_std["unit_price"] == 0.40
    item_pro = next(it for it in pro["items"] if "animate-move" in it["name"])
    assert item_pro["unit_price"] == 0.60


def test_estimate_cost_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        estimate_cost("not_a_mode", {})


def test_audio_limits_known_modes() -> None:
    """wan2.2-s2v has a hard 20s cap; videoretalk is 2-120s; others n/a."""
    assert "photo_speak" in AUDIO_LIMITS
    assert AUDIO_LIMITS["photo_speak"].max_sec <= 20
    assert "avatar_compose" in AUDIO_LIMITS
    assert AUDIO_LIMITS["avatar_compose"].max_sec <= 20
    assert AUDIO_LIMITS["video_relip"].max_sec >= 100
    assert AUDIO_LIMITS["video_relip"].min_sec >= 1.5
    assert "video_reface" not in AUDIO_LIMITS
    assert "pose_drive" not in AUDIO_LIMITS


def test_check_audio_duration_blocks_oversized_for_s2v() -> None:
    """The user-reported case: 21s audio must be flagged before submission."""
    msg = check_audio_duration("photo_speak", 21.0)
    assert msg is not None
    assert "wan2.2-s2v" in msg
    assert "20" in msg or "19.5" in msg
    msg2 = check_audio_duration("avatar_compose", 25.0)
    assert msg2 is not None
    assert "wan2.2-s2v" in msg2


def test_check_audio_duration_passes_in_range() -> None:
    assert check_audio_duration("photo_speak", 10.0) is None
    assert check_audio_duration("video_relip", 30.0) is None
    assert check_audio_duration("avatar_compose", 5.0) is None


def test_check_audio_duration_blocks_undersized_for_videoretalk() -> None:
    msg = check_audio_duration("video_relip", 1.0)
    assert msg is not None
    assert "videoretalk" in msg


def test_check_audio_duration_no_op_for_modes_without_audio() -> None:
    """video_reface / pose_drive are video-driven, not audio-driven."""
    assert check_audio_duration("video_reface", 999.0) is None
    assert check_audio_duration("pose_drive", 999.0) is None
    assert check_audio_duration("photo_speak", None) is None


def test_cost_preview_no_milk_tea_in_formatted_total() -> None:
    """User explicitly rejected the milk-tea translator; verify it stays gone."""
    p = estimate_cost("photo_speak", {"resolution": "480P"}, audio_duration_sec=10.0)
    assert p["formatted_total"].startswith("¥")
    forbidden = ("奶茶", "milk", "tea", "咖啡", "杯")
    for word in forbidden:
        assert word not in p["formatted_total"]
        for it in p["items"]:
            assert word not in it["name"]
            assert word not in it.get("note", "")


# ─── Error hints — 9 kinds, bilingual ────────────────────────────────────


def test_error_hints_full_coverage() -> None:
    expected = {
        "network",
        "timeout",
        "rate_limit",
        "auth",
        "not_found",
        "moderation",
        "quota",
        "dependency",
        "unknown",
    }
    assert set(ERROR_HINTS) == expected
    for kind, hint in ERROR_HINTS.items():
        assert hint["title_zh"] and hint["title_en"], f"{kind} missing title"
        assert len(hint["hints_zh"]) >= 1
        assert len(hint["hints_en"]) >= 1
        assert all(isinstance(s, str) and s for s in hint["hints_zh"])
        assert all(isinstance(s, str) and s for s in hint["hints_en"])


def test_hint_for_falls_back_to_unknown() -> None:
    assert hint_for("auth")["title_en"] == ERROR_HINTS["auth"]["title_en"]
    assert hint_for("nonexistent")["title_en"] == ERROR_HINTS["unknown"]["title_en"]
    assert hint_for(None)["title_en"] == ERROR_HINTS["unknown"]["title_en"]
