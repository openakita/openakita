"""Static-metadata guards for finpulse_models.

These tests are deliberately cheap — they confirm the canonical mode
IDs, error categories, and source registry stay aligned with the plan
so any accidental rename / drop surfaces on the first test pass.
"""

from __future__ import annotations

from finpulse_models import (
    DEFAULT_CRONS,
    ERROR_HINTS,
    ERROR_KINDS,
    MODE_IDS,
    MODES,
    SCORE_THRESHOLDS,
    SESSIONS,
    SOURCE_DEFS,
    SOURCE_IDS,
)


def test_modes_cover_plan_v1() -> None:
    assert set(MODE_IDS) == {"ingest", "daily_brief", "hot_radar", "ask_news"}
    for mode_id, meta in MODES.items():
        assert "display_zh" in meta
        assert "display_en" in meta
        assert mode_id in MODE_IDS


def test_daily_brief_has_three_sessions() -> None:
    assert MODES["daily_brief"]["sessions"] == ("morning", "noon", "evening")
    assert SESSIONS == ("morning", "noon", "evening")
    for session in SESSIONS:
        assert session in DEFAULT_CRONS
        assert DEFAULT_CRONS[session]


def test_error_kinds_are_the_canonical_nine() -> None:
    assert set(ERROR_KINDS) == {
        "network",
        "timeout",
        "auth",
        "quota",
        "rate_limit",
        "dependency",
        "moderation",
        "not_found",
        "unknown",
    }
    for kind in ERROR_KINDS:
        hints = ERROR_HINTS[kind]
        assert hints["zh"], f"{kind}: zh hints empty"
        assert hints["en"], f"{kind}: en hints empty"


def test_source_registry_has_eight_prime_finance_plus_rss_and_newsnow() -> None:
    # Eight prime finance sources + generic RSS aggregator + NewsNow = 10.
    # NewsNow is enabled by default too now that the plugin pins the
    # public aggregator URL and throttles it via ``newsnow.min_interval_s``.
    assert len(SOURCE_IDS) == 10
    expected_enabled_by_default = {
        "wallstreetcn",
        "cls",
        "xueqiu",
        "eastmoney",
        "pbc_omo",
        "nbs",
        "fed_fomc",
        "sec_edgar",
        "rss_generic",
        "newsnow",
    }
    for source_id in expected_enabled_by_default:
        assert SOURCE_DEFS[source_id]["default_enabled"] is True, source_id


def test_score_thresholds_monotonic() -> None:
    values = [
        SCORE_THRESHOLDS["noise"],
        SCORE_THRESHOLDS["low"],
        SCORE_THRESHOLDS["routine"],
        SCORE_THRESHOLDS["important"],
        SCORE_THRESHOLDS["critical"],
    ]
    assert values == sorted(values)
    assert SCORE_THRESHOLDS["critical"] <= 10.0
    assert SCORE_THRESHOLDS["noise"] >= 0.0
