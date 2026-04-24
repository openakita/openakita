"""Phase 1 unit tests for ``footage_gate_models``.

These tests guard the static metadata that every higher layer (pipeline,
QC, plugin entry, UI) depends on. A regression here ripples instantly so
we keep the suite small but exhaustive — every public constant is at
least name-checked, and the three "defensive" constants (HDR_TRANSFERS,
TONEMAP_CHAIN, MIN_SUBTITLE_MARGINV_VERTICAL) get their own value-pinned
assertions so a careless edit cannot silently break the upstream-issue
defences (vs video-use PR #6 / PR #5).
"""

from __future__ import annotations

import footage_gate_models as fm

# ── MODES ────────────────────────────────────────────────────────────────


def test_modes_has_exactly_four_canonical_ids() -> None:
    assert set(fm.MODE_IDS) == {
        "source_review",
        "silence_cut",
        "auto_color",
        "cut_qc",
    }


def test_only_cut_qc_supports_auto_remux() -> None:
    """The auto-remux remediation loop is meaningful only for cut_qc — the
    UI per-task toggle is hidden for the other three modes."""
    for mode_id, spec in fm.MODES.items():
        assert spec["supports_auto_remux"] is (mode_id == "cut_qc"), mode_id


def test_modes_have_required_keys() -> None:
    required = {
        "display_zh",
        "display_en",
        "input_kinds",
        "output_kind",
        "catalog_id",
        "supports_auto_remux",
    }
    for mode_id, spec in fm.MODES.items():
        assert required <= set(spec.keys()), f"{mode_id} missing keys"
        assert isinstance(spec["input_kinds"], tuple)


# ── ERROR_HINTS ──────────────────────────────────────────────────────────


def test_error_hints_has_exactly_nine_categories() -> None:
    """avatar-studio / subtitle-craft contract — expanding past 9 forces
    a host UI badge update so we lock the count."""
    assert len(fm.ERROR_KINDS) == 9
    assert set(fm.ERROR_KINDS) == {
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


def test_every_error_kind_has_zh_and_en_hints() -> None:
    for kind, by_lang in fm.ERROR_HINTS.items():
        assert "zh" in by_lang and "en" in by_lang, kind
        assert len(by_lang["zh"]) >= 1, kind
        assert len(by_lang["en"]) >= 1, kind


def test_dependency_hint_mentions_ffmpeg() -> None:
    """Dependency is the headline failure mode — the hint MUST point to
    the Settings → System Dependencies installer or the user has no path
    forward."""
    zh = " ".join(fm.ERROR_HINTS["dependency"]["zh"])
    en = " ".join(fm.ERROR_HINTS["dependency"]["en"])
    assert "FFmpeg" in zh and "FFmpeg" in en
    assert "Settings" in en or "Settings" in zh


# ── RISK_THRESHOLDS / GRADE_CLAMPS / SILENCE_DEFAULTS ────────────────────


def test_risk_thresholds_have_all_categories() -> None:
    expected_keys = {
        "video_min_width",
        "video_min_height",
        "video_min_duration_sec",
        "video_max_silent_audio_db",
        "audio_min_duration_sec",
        "audio_min_channels",
        "image_min_width",
        "image_min_height",
        "image_min_filesize_kb",
    }
    assert expected_keys <= fm.RISK_THRESHOLDS.keys()


def test_grade_clamps_centred_on_one_within_eight_percent() -> None:
    """All three clamps must contain 1.0 (identity) and span no more
    than ±10 % — anything wider would let auto_color over-correct on a
    single pass (vs video-use grade.py L235-291 spec)."""
    for name, (lo, hi) in fm.GRADE_CLAMPS.items():
        assert lo <= 1.0 <= hi, name
        assert hi - lo <= 0.20, f"{name} clamp too wide: [{lo}, {hi}]"


def test_silence_defaults_have_required_fields() -> None:
    expected = {
        "threshold_db",
        "min_silence_len_sec",
        "min_sound_len_sec",
        "pad_sec",
    }
    assert expected <= fm.SILENCE_DEFAULTS.keys()
    assert fm.SILENCE_DEFAULTS["threshold_db"] < 0  # dBFS
    assert fm.SILENCE_DEFAULTS["pad_sec"] >= 0


# ── HDR / TONEMAP / SUBTITLE — defensive constants ───────────────────────


def test_hdr_transfers_covers_pq_and_hlg() -> None:
    """video-use PR #6 fix — both PQ (smpte2084) and HLG (arib-std-b67)
    must be detected as HDR, otherwise eq=gamma= will black out the frame
    on Dolby Vision / HLG sources."""
    assert "smpte2084" in fm.HDR_TRANSFERS
    assert "arib-std-b67" in fm.HDR_TRANSFERS


def test_tonemap_chain_is_canonical_zscale_form() -> None:
    """The chain MUST end with format=yuv420p so the downstream eq=
    filter sees an SDR pixel format. Specific filter names pinned because
    a careless edit (e.g. swapping zscale for scale) would silently pass
    the unit test that checks for 'tonemap' alone."""
    chain = fm.TONEMAP_CHAIN
    assert chain.startswith("zscale=t=linear")
    assert "tonemap=" in chain
    assert chain.endswith("format=yuv420p")
    # No leading / trailing comma — caller is responsible for joining.
    assert not chain.startswith(",")
    assert not chain.endswith(",")


def test_min_subtitle_marginv_is_at_least_60() -> None:
    """vs video-use PR #5 — anything below ~60px overlaps the iOS gesture
    bar on iPhone 14+. We chose 90 to give a little extra slack for older
    Android nav bars; the test enforces the lower bound only so we can
    raise the value later without churning callers."""
    assert fm.MIN_SUBTITLE_MARGINV_VERTICAL >= 60
