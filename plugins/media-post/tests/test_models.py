"""Phase 1 — ``mediapost_models`` unit tests (Gate 1 per §11 Phase 1).

Coverage focus per §10.1 ``test_models.py`` row:

- 4 MODES present with stable canonical ids.
- 5 PLATFORMS + 2 ASPECTS frozen lists.
- PRICE_TABLE aligns with §4.
- ERROR_HINTS keys are exactly the 9 canonical kinds (no ``rate_limit``).
- ``estimate_cost`` formulas across every mode + cost ramp classification.
- ``MediaPostError`` clamps unknown ``kind`` to ``unknown``.
"""

from __future__ import annotations

import pytest
from mediapost_models import (
    ALLOWED_ASPECTS,
    ALLOWED_ERROR_KINDS,
    ALLOWED_MODES,
    ALLOWED_PLATFORMS,
    ASPECTS,
    COST_THRESHOLD_DANGER_CNY,
    COST_THRESHOLD_WARN_CNY,
    DEFAULT_RECOMPOSE_FPS,
    DEFAULT_VLM_BATCH_SIZE,
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    PLATFORMS,
    PRICE_TABLE,
    MediaPostError,
    aspect_to_dict,
    estimate_cost,
    get_error_hints,
    get_mode,
    map_vendor_kind_to_error_kind,
    mode_to_dict,
    platform_to_dict,
)


class TestModes:
    def test_four_modes_with_canonical_ids(self) -> None:
        assert len(MODES) == 4
        assert frozenset(
            {"cover_pick", "multi_aspect", "seo_pack", "chapter_cards"}
        ) == ALLOWED_MODES

    def test_modes_by_id_lookup(self) -> None:
        for mode_id in ALLOWED_MODES:
            assert MODES_BY_ID[mode_id].id == mode_id
            assert get_mode(mode_id) is MODES_BY_ID[mode_id]
        assert get_mode("does-not-exist") is None

    def test_chapter_cards_does_not_require_api_key(self) -> None:
        chapter = MODES_BY_ID["chapter_cards"]
        assert chapter.requires_api_key is False
        assert chapter.requires_ffmpeg is True

    def test_seo_pack_skips_extract_frames(self) -> None:
        seo = MODES_BY_ID["seo_pack"]
        assert "extract_frames" in seo.skip_steps

    def test_chapter_cards_skips_extract_and_vlm(self) -> None:
        chapter = MODES_BY_ID["chapter_cards"]
        assert "extract_frames" in chapter.skip_steps
        assert "vlm_or_seo" in chapter.skip_steps

    def test_mode_to_dict_includes_all_fields(self) -> None:
        d = mode_to_dict(MODES_BY_ID["cover_pick"])
        for required in (
            "id",
            "label_zh",
            "label_en",
            "icon",
            "description_zh",
            "description_en",
            "requires_api_key",
            "requires_ffmpeg",
            "requires_playwright",
            "skip_steps",
        ):
            assert required in d


class TestPlatforms:
    def test_five_platforms(self) -> None:
        assert len(PLATFORMS) == 5
        assert frozenset(
            {"tiktok", "bilibili", "wechat", "xiaohongshu", "youtube"}
        ) == ALLOWED_PLATFORMS

    def test_chapters_only_supported_for_bilibili_and_youtube(self) -> None:
        supports = {p.id for p in PLATFORMS if p.supports_chapters}
        assert supports == {"bilibili", "youtube"}

    def test_platform_to_dict_round_trip(self) -> None:
        d = platform_to_dict(PLATFORMS[0])
        assert d["id"] in ALLOWED_PLATFORMS
        assert d["char_limit_title"] > 0


class TestAspects:
    def test_two_aspects_v1(self) -> None:
        assert len(ASPECTS) == 2
        assert frozenset({"9:16", "1:1"}) == ALLOWED_ASPECTS

    def test_default_aspect_is_9_16(self) -> None:
        defaults = [a for a in ASPECTS if a.is_default]
        assert len(defaults) == 1
        assert defaults[0].id == "9:16"

    def test_vertical_output_dim_is_608x1080(self) -> None:
        assert aspect_to_dict([a for a in ASPECTS if a.id == "9:16"][0]) == {
            "id": "9:16",
            "label_zh": "竖版 9:16（抖音 / 视频号）",
            "label_en": "Vertical 9:16 (TikTok / WeChat)",
            "output_w": 608,
            "output_h": 1080,
            "is_default": True,
        }


class TestPriceTable:
    def test_price_table_includes_vlm_and_qwen_plus(self) -> None:
        apis = {p.api for p in PRICE_TABLE}
        assert "qwen-vl-max" in apis
        assert "qwen-plus" in apis
        assert "ffmpeg-local" in apis

    def test_local_apis_are_free(self) -> None:
        for entry in PRICE_TABLE:
            if entry.api.endswith("-local"):
                assert entry.price_cny == 0.0


class TestErrorHints:
    def test_nine_canonical_kinds(self) -> None:
        expected = {
            "network",
            "timeout",
            "auth",
            "quota",
            "moderation",
            "dependency",
            "format",
            "duration",
            "unknown",
        }
        assert frozenset(expected) == ALLOWED_ERROR_KINDS
        assert set(ERROR_HINTS.keys()) == expected

    def test_no_rate_limit_key(self) -> None:
        # Red-line §5: 429 must map to `quota`; never expose `rate_limit`.
        assert "rate_limit" not in ERROR_HINTS

    def test_each_kind_has_zh_and_en_hints(self) -> None:
        for kind, payload in ERROR_HINTS.items():
            assert payload["label_zh"], f"{kind} missing label_zh"
            assert payload["label_en"], f"{kind} missing label_en"
            assert payload["hints_zh"], f"{kind} missing hints_zh"
            assert payload["hints_en"], f"{kind} missing hints_en"

    def test_get_error_hints_unknown_fallback(self) -> None:
        assert get_error_hints("does-not-exist") is ERROR_HINTS["unknown"]
        assert get_error_hints("network") is ERROR_HINTS["network"]

    def test_vendor_kind_mapping(self) -> None:
        assert map_vendor_kind_to_error_kind("rate_limit") == "quota"
        assert map_vendor_kind_to_error_kind("not_found") == "format"
        assert map_vendor_kind_to_error_kind("server") == "network"
        assert map_vendor_kind_to_error_kind("anything-else") == "unknown"


class TestEstimateCost:
    def test_cover_pick_default_quantity(self) -> None:
        # 8 candidates / 8 batch_size = 1 batch * ¥0.08 = ¥0.08
        # In production cover_pick always runs the prefilter pass too, so
        # the realistic cost is closer to 4 * 0.08 = ¥0.32; this test only
        # locks down the formula shape so do not over-fit to that constant.
        preview = estimate_cost("cover_pick", quantity=8)
        assert preview.total_cny > 0
        assert preview.cost_kind == "ok"
        assert preview.items[0]["api"] == "qwen-vl-max"

    def test_cover_pick_more_quantity_more_cost(self) -> None:
        a = estimate_cost("cover_pick", quantity=8)
        b = estimate_cost("cover_pick", quantity=24)
        assert b.total_cny > a.total_cny

    def test_multi_aspect_cost_scales_with_duration(self) -> None:
        short = estimate_cost(
            "multi_aspect",
            duration_sec=60,
            target_aspects=["9:16"],
            recompose_fps=DEFAULT_RECOMPOSE_FPS,
        )
        long = estimate_cost(
            "multi_aspect",
            duration_sec=600,
            target_aspects=["9:16"],
            recompose_fps=DEFAULT_RECOMPOSE_FPS,
        )
        assert long.total_cny > short.total_cny

    def test_multi_aspect_30min_triggers_warn(self) -> None:
        # 30 min * 2 fps / 8 = 450 batches * ¥0.08 = ¥36 → danger band.
        preview = estimate_cost(
            "multi_aspect",
            duration_sec=30 * 60,
            target_aspects=["9:16"],
            recompose_fps=2.0,
        )
        assert preview.total_cny >= COST_THRESHOLD_WARN_CNY
        assert preview.cost_kind in {"warn", "danger"}

    def test_multi_aspect_60min_two_aspects_triggers_danger(self) -> None:
        # 60min * 2fps / 8 = 900 batches; 900 * 0.08 * (1 + 0.5) = ¥108 → danger.
        preview = estimate_cost(
            "multi_aspect",
            duration_sec=60 * 60,
            target_aspects=["9:16", "1:1"],
            recompose_fps=2.0,
        )
        assert preview.total_cny >= COST_THRESHOLD_DANGER_CNY
        assert preview.cost_kind == "danger"

    def test_seo_pack_per_platform(self) -> None:
        five = estimate_cost(
            "seo_pack",
            platforms=["tiktok", "bilibili", "wechat", "xiaohongshu", "youtube"],
        )
        one = estimate_cost("seo_pack", platforms=["tiktok"])
        assert pytest.approx(five.total_cny, abs=1e-6) == 5 * one.total_cny

    def test_chapter_cards_is_free(self) -> None:
        preview = estimate_cost("chapter_cards", chapter_count=10)
        assert preview.total_cny == 0.0
        assert preview.cost_kind == "ok"

    def test_unknown_mode_returns_empty(self) -> None:
        preview = estimate_cost("does-not-exist")
        assert preview.total_cny == 0.0
        assert preview.items == []

    def test_default_vlm_batch_size_is_eight(self) -> None:
        # Red-line §13 #6: the 8-frame batch size is locked.
        assert DEFAULT_VLM_BATCH_SIZE == 8


class TestMediaPostError:
    def test_known_kind_passes_through(self) -> None:
        err = MediaPostError("network", "boom")
        assert err.kind == "network"
        assert err.message == "boom"
        assert "[network]" in str(err)

    def test_unknown_kind_clamps_to_unknown(self) -> None:
        err = MediaPostError("not-a-real-kind", "boom")
        assert err.kind == "unknown"

    def test_empty_message_renders_kind_only(self) -> None:
        err = MediaPostError("timeout")
        assert str(err) == "[timeout]"
