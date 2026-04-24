# ruff: noqa: N999
"""Static metadata for the footage-gate plugin.

This module ships pure-data constants — no I/O, no runtime state, no
plugin-API dependency — so it can be unit-tested in milliseconds and
imported from any layer (pipeline, qc, plugin entry, UI smoke tests).

Sections:

- :data:`MODES` — the four canonical pipeline modes (source_review,
  silence_cut, auto_color, cut_qc) with display names and required input
  kinds.
- :data:`ERROR_HINTS` — the nine standardised ``error_kind`` categories with
  bilingual operator hints. Aligned to ``avatar-studio`` and
  ``subtitle-craft`` so the host's task-detail page can render a single
  consistent badge.
- :data:`RISK_THRESHOLDS` — ``source_review`` thresholds (resolution,
  duration, audio channels). Mirrors ``OpenMontage/lib/source_media_review.py``
  L41–187 with the ``usable_for`` field surfaced (defensive vs upstream
  Issue #44).
- :data:`GRADE_CLAMPS` — the ±8 % bounds applied to ``contrast / gamma /
  saturation`` in the ``auto_color`` pipeline. Bounds copied verbatim from
  ``video-use/helpers/grade.py`` L235–291.
- :data:`SILENCE_DEFAULTS` — RMS / padding defaults for ``silence_cut``,
  derived from CutClaw ``_compute_non_silent_intervals`` (issue #3 — we
  intentionally do NOT vendor ``aubio`` / ``madmom`` / ``librosa``).
- :data:`HDR_TRANSFERS` / :data:`TONEMAP_CHAIN` — HDR colour transfer names
  to detect at probe time, plus the ffmpeg filter-graph snippet to prepend
  to the ``eq=`` chain when the source is HDR. Defends against
  ``video-use`` PR #6 (HDR ``eq=gamma=`` regression on smpte2084 input).
- :data:`MIN_SUBTITLE_MARGINV_VERTICAL` — minimum vertical safe-zone
  ``MarginV`` for portrait outputs (vertical safe-zone defence vs
  ``video-use`` PR #5).
"""

from __future__ import annotations

from typing import Final

# ── Modes ────────────────────────────────────────────────────────────────


MODES: Final[dict[str, dict[str, object]]] = {
    "source_review": {
        "display_zh": "源素材审核",
        "display_en": "Source Review",
        "input_kinds": ("video", "audio", "image"),
        "output_kind": "report",
        "catalog_id": "C6",
        "supports_auto_remux": False,
    },
    "silence_cut": {
        "display_zh": "静默剪除",
        "display_en": "Silence Cut",
        "input_kinds": ("video", "audio"),
        "output_kind": "video",
        "catalog_id": "D2",
        "supports_auto_remux": False,
    },
    "auto_color": {
        "display_zh": "自动调色",
        "display_en": "Auto Color",
        "input_kinds": ("video",),
        "output_kind": "video",
        "catalog_id": "C1",
        "supports_auto_remux": False,
    },
    "cut_qc": {
        "display_zh": "剪辑边界 QC",
        "display_en": "Cut Boundary QC",
        "input_kinds": ("video",),
        "output_kind": "report",
        "catalog_id": "C2",
        # The only mode where the auto-remux remediation loop is meaningful
        # — the per-task UI toggle gates whether the loop runs at all.
        "supports_auto_remux": True,
    },
}

MODE_IDS: Final[tuple[str, ...]] = tuple(MODES.keys())


# ── Error categories ─────────────────────────────────────────────────────
#
# The nine canonical ``error_kind`` values. Aligned with avatar-studio /
# subtitle-craft so the host task-detail badge renders a uniform label.
# The hints are deliberately short — each one is a single sentence in zh
# and en so the UI can render them in a 2-line tooltip without truncation.


ERROR_HINTS: Final[dict[str, dict[str, list[str]]]] = {
    "network": {
        "zh": [
            "请检查网络连接",
            "若使用代理请确认 dashscope.aliyuncs.com 可达",
        ],
        "en": [
            "Check your network connection",
            "If you use a proxy, ensure dashscope.aliyuncs.com is reachable",
        ],
    },
    "timeout": {
        "zh": [
            "素材较大，请到 Settings 调高超时阈值",
            "或先剪短到 ≤ 5 分钟再重试",
        ],
        "en": [
            "Large input — raise the timeout in Settings",
            "Or trim the clip to ≤ 5 min and retry",
        ],
    },
    "rate_limit": {
        "zh": [
            "DashScope 配额超限",
            "建议关闭转写摘要选项以纯本地运行",
        ],
        "en": [
            "DashScope quota exceeded",
            "Disable the transcription option to run fully locally",
        ],
    },
    "auth": {
        "zh": [
            "请到 Settings 重新填写 API Key（仅转写摘要需要）",
            "或关闭转写摘要选项",
        ],
        "en": [
            "Re-enter the API Key in Settings (only used by the transcription option)",
            "Or disable the transcription option",
        ],
    },
    "not_found": {
        "zh": [
            "请重新上传素材",
            "若使用 EDL 模式请确认 sources 字段路径有效",
        ],
        "en": [
            "Re-upload the source media",
            "If using EDL mode, verify the sources[] paths are valid",
        ],
    },
    "moderation": {
        "zh": [
            "输入音频被识别为敏感",
            "建议关闭转写摘要选项",
        ],
        "en": [
            "Input audio flagged by content moderation",
            "Disable the transcription option to bypass",
        ],
    },
    "quota": {
        "zh": [
            "请到阿里云百炼控制台充值",
            "或关闭转写摘要选项",
        ],
        "en": [
            "Top up the DashScope balance",
            "Or disable the transcription option",
        ],
    },
    # The headline failure mode for footage-gate: FFmpeg is missing /
    # too old / built without the required filters. The Settings page's
    # one-click installer covers Windows (winget) / macOS (brew) /
    # Linux (apt|dnf), and the capability probe lives in
    # ``footage_gate_inline.system_deps.probe_ffmpeg_capabilities``.
    "dependency": {
        "zh": [
            "请到 Settings → 系统依赖 一键安装 FFmpeg",
            "或手动安装 FFmpeg 4.4+（含 signalstats / eq / subtitles / tonemap）",
        ],
        "en": [
            "Open Settings → System Dependencies and click Install FFmpeg",
            "Or install FFmpeg 4.4+ manually (signalstats / eq / subtitles / tonemap required)",
        ],
    },
    "unknown": {
        "zh": [
            "请将 task_id 反馈给开发者",
            "或截图 Tasks 详情页 metadata json",
        ],
        "en": [
            "Report the task_id to the developer",
            "Or screenshot the Tasks detail-page metadata JSON",
        ],
    },
}

ERROR_KINDS: Final[tuple[str, ...]] = tuple(ERROR_HINTS.keys())


# ── source_review thresholds ─────────────────────────────────────────────
#
# Mirrors OpenMontage lib/source_media_review.py L88-187. Each threshold
# triggers exactly one risk row in the report; ``usable_for`` is computed
# *after* all risks are evaluated so consumers can downgrade an "ok" clip
# to "audio-only" without us hard-coding the matrix.


RISK_THRESHOLDS: Final[dict[str, object]] = {
    # Video
    "video_min_width": 720,
    "video_min_height": 720,
    "video_min_duration_sec": 1.0,
    "video_max_silent_audio_db": -50.0,  # below this we flag mono / dead audio
    # Audio
    "audio_min_duration_sec": 0.5,
    "audio_min_channels": 1,  # mono is acceptable but flagged as "mono_audio"
    # Image
    "image_min_width": 512,
    "image_min_height": 512,
    "image_min_filesize_kb": 16,
}


# ── auto_color clamps ────────────────────────────────────────────────────
#
# Bounds applied AFTER the signalstats sampler computes the suggested
# correction (see footage_gate_grade.py / video-use grade.py L235-291).
# The intent is "nudge the look, never overpower it" — clamps cap the
# correction within ±8 % of identity so a single auto pass cannot push
# a clip into clipping or look hyper-saturated.


GRADE_CLAMPS: Final[dict[str, tuple[float, float]]] = {
    "contrast": (0.94, 1.08),
    "gamma": (0.94, 1.10),
    "saturation": (0.94, 1.06),
}


# ── silence_cut defaults ─────────────────────────────────────────────────
#
# Defaults match the ``D2 SilenceCutter`` catalog row. Re-implemented in
# pure ``numpy`` (footage_gate_silence.py) so the buggy ``aubio`` install
# path from CutClaw upstream issue #3 is never exercised.


SILENCE_DEFAULTS: Final[dict[str, float]] = {
    # Threshold below which a sample is considered silence (dBFS).
    "threshold_db": -45.0,
    # Minimum duration of a silent run before it is removed (seconds).
    "min_silence_len_sec": 0.15,
    # Minimum duration of a non-silent run we keep — anything shorter is
    # merged with neighbours so we do not produce sub-frame chunks.
    "min_sound_len_sec": 0.05,
    # Symmetric pad re-added to each non-silent interval so the cut does
    # not crop syllables / consonants.
    "pad_sec": 0.05,
}


# ── HDR detection + tone-map fallback (vs video-use PR #6) ───────────────
#
# Two HDR transfer characteristics surface in modern footage:
#   - ``smpte2084`` — HDR10 / Dolby Vision base layer (PQ).
#   - ``arib-std-b67`` — HLG (BBC / NHK broadcasts, mobile capture).
# Both must be tone-mapped to BT.709 SDR *before* the ``eq=`` colour-grade
# filter is applied — feeding HDR samples to ``eq=gamma=`` is exactly the
# regression that prompted video-use PR #6 (frames went black on PQ
# input, only visible on Dolby Vision capable monitors).
#
# We expose the canonical filter-graph fragment as TONEMAP_CHAIN so the
# pipeline can ``"" if not is_hdr else TONEMAP_CHAIN + ","`` it onto the
# auto-grade chain. The chain is the ``zscale + tonemap + zscale`` form
# used by FFmpeg's official HDR→SDR cookbook (works on every 4.4+ build
# compiled with libzimg, which the Settings probe verifies).


HDR_TRANSFERS: Final[tuple[str, ...]] = ("smpte2084", "arib-std-b67")

TONEMAP_CHAIN: Final[str] = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


# ── Subtitle vertical safe-zone (vs video-use PR #5) ─────────────────────
#
# When the OUTPUT is portrait (height > width), ``MarginV`` below this
# threshold means the subtitle box overlaps the device gesture-bar /
# notch on most modern phones (iOS 14+, Android 12+ three-button nav).
# The cut_qc subtitle_overlay_check raises a ``subtitle_unsafe_zone``
# issue when ``MarginV < MIN_SUBTITLE_MARGINV_VERTICAL`` on a portrait
# output and the auto-remux loop's fix bumps it to this minimum.


MIN_SUBTITLE_MARGINV_VERTICAL: Final[int] = 90


__all__ = [
    "ERROR_HINTS",
    "ERROR_KINDS",
    "GRADE_CLAMPS",
    "HDR_TRANSFERS",
    "MIN_SUBTITLE_MARGINV_VERTICAL",
    "MODE_IDS",
    "MODES",
    "RISK_THRESHOLDS",
    "SILENCE_DEFAULTS",
    "TONEMAP_CHAIN",
]
