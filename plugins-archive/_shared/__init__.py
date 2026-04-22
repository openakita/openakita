"""plugins-archive/_shared — vendored helpers for archived plugins.

This package collects helper modules that were previously published as
``openakita_plugin_sdk.contrib`` but are now consumed *only* by the
plugins under ``plugins-archive/`` (no longer first-class citizens).

Why it lives here
-----------------

The SDK has gone back to its "minimal plugin shell" positioning, so all
opinionated scaffolding has been removed from the SDK package surface.
Archive plugins still need this code to keep running, but they own it
collectively now — no Python project depends on this directory.

Loading
-------

Each archive plugin's ``plugin.py`` (or its imported sibling modules)
adds ``plugins-archive/`` to ``sys.path`` at import time so that
``from _shared import X`` resolves.  See any plugin's first few lines for
the canonical bootstrap snippet.

Status
------

* Not part of the SDK's public API.
* Not covered by main CI.
* Will not receive new features — bug fixes only when an archive plugin
  is actively being moved back to first-class.
"""

from __future__ import annotations

from .cost_estimator import CostBreakdown, CostEstimator, CostPreview, to_human_units
from .errors import ErrorCoach, ErrorPattern, RenderedError
from .ffmpeg import (
    AUTO_GRADE_PRESETS,
    DEFAULT_GRADE_CLAMP_PCT,
    FFmpegError,
    FFmpegResult,
    GradeStats,
    auto_color_grade_filter,
    ffprobe_json,
    ffprobe_json_sync,
    get_grade_preset,
    list_grade_presets,
    resolve_binary,
    run_ffmpeg,
    run_ffmpeg_sync,
    sample_signalstats,
    sample_signalstats_sync,
)
from .intent_verifier import EvalResult, IntentSummary, IntentVerifier
from .llm_json_parser import (
    parse_llm_json,
    parse_llm_json_array,
    parse_llm_json_object,
)
from .provider_score import ProviderScore, score_providers
from .quality_gates import GateResult, GateStatus, QualityGates
from .render_pipeline import RenderPipeline, build_render_pipeline
from .slideshow_risk import SlideshowRisk, evaluate_slideshow_risk
from .source_review import (
    ReviewIssue,
    ReviewReport,
    ReviewThresholds,
    review_audio,
    review_image,
    review_source,
    review_video,
)
from .storage_stats import StorageStats, collect_storage_stats
from .task_manager import BaseTaskManager, TaskRecord, TaskStatus
from .ui_events import UIEventEmitter, strip_plugin_event_prefix
from .upload_preview import (
    DEFAULT_AV_EXTENSIONS,
    DEFAULT_IMAGE_EXTENSIONS,
    DEFAULT_PREVIEW_EXTENSIONS,
    add_upload_preview_route,
    build_preview_url,
)
from .vendor_client import (
    ERROR_KIND_AUTH,
    ERROR_KIND_CLIENT,
    ERROR_KIND_MODERATION,
    ERROR_KIND_NETWORK,
    ERROR_KIND_NOT_FOUND,
    ERROR_KIND_RATE_LIMIT,
    ERROR_KIND_SERVER,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNKNOWN,
    BaseVendorClient,
    VendorError,
)
from .verification import (
    BADGE_GREEN,
    BADGE_RED,
    BADGE_YELLOW,
    KIND_DATE,
    KIND_NUMBER,
    KIND_OTHER,
    KIND_PERSON,
    KIND_PLACE,
    KIND_QUOTE,
    KIND_URL,
    LowConfidenceField,
    Verification,
    merge_verifications,
    render_verification_badge,
)

__all__ = [
    "AUTO_GRADE_PRESETS",
    "BADGE_GREEN",
    "BADGE_RED",
    "BADGE_YELLOW",
    "BaseTaskManager",
    "BaseVendorClient",
    "CostBreakdown",
    "CostEstimator",
    "CostPreview",
    "DEFAULT_AV_EXTENSIONS",
    "DEFAULT_GRADE_CLAMP_PCT",
    "DEFAULT_IMAGE_EXTENSIONS",
    "DEFAULT_PREVIEW_EXTENSIONS",
    "ERROR_KIND_AUTH",
    "ERROR_KIND_CLIENT",
    "ERROR_KIND_MODERATION",
    "ERROR_KIND_NETWORK",
    "ERROR_KIND_NOT_FOUND",
    "ERROR_KIND_RATE_LIMIT",
    "ERROR_KIND_SERVER",
    "ERROR_KIND_TIMEOUT",
    "ERROR_KIND_UNKNOWN",
    "ErrorCoach",
    "ErrorPattern",
    "EvalResult",
    "FFmpegError",
    "FFmpegResult",
    "GateResult",
    "GateStatus",
    "GradeStats",
    "IntentSummary",
    "IntentVerifier",
    "KIND_DATE",
    "KIND_NUMBER",
    "KIND_OTHER",
    "KIND_PERSON",
    "KIND_PLACE",
    "KIND_QUOTE",
    "KIND_URL",
    "LowConfidenceField",
    "ProviderScore",
    "QualityGates",
    "RenderPipeline",
    "RenderedError",
    "ReviewIssue",
    "ReviewReport",
    "ReviewThresholds",
    "SlideshowRisk",
    "StorageStats",
    "TaskRecord",
    "TaskStatus",
    "UIEventEmitter",
    "VendorError",
    "Verification",
    "add_upload_preview_route",
    "auto_color_grade_filter",
    "build_preview_url",
    "build_render_pipeline",
    "collect_storage_stats",
    "evaluate_slideshow_risk",
    "ffprobe_json",
    "ffprobe_json_sync",
    "get_grade_preset",
    "list_grade_presets",
    "merge_verifications",
    "parse_llm_json",
    "parse_llm_json_array",
    "parse_llm_json_object",
    "render_verification_badge",
    "resolve_binary",
    "review_audio",
    "review_image",
    "review_source",
    "review_video",
    "run_ffmpeg",
    "run_ffmpeg_sync",
    "sample_signalstats",
    "sample_signalstats_sync",
    "score_providers",
    "strip_plugin_event_prefix",
    "to_human_units",
]
