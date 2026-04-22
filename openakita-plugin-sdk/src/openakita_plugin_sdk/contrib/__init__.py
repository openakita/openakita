"""openakita_plugin_sdk.contrib — shared scaffolding for AI media plugins.

This subpackage extracts the common backend pieces every AI video / image /
audio plugin needs (task DB, vendor HTTP client, error coach, cost preview,
intent verifier, prompt optimizer, quality gates, render pipeline, etc.).

Design rules:

- **Pure Python, no extra mandatory deps** beyond what the SDK already pins
  (`pydantic`, stdlib).  ``aiosqlite`` and ``httpx`` are imported lazily so
  plugins that do not touch DB / HTTP keep working.
- **Every public class / function is fully type-annotated** and works in
  ``Plugin 2.0`` plugins without touching host internals.
- **Backwards compatible**: nothing here changes existing PluginAPI.  All
  features are opt-in by importing from ``openakita_plugin_sdk.contrib``.
- **No multi-path**: each helper has *one* canonical home — never copy this
  code into individual plugins, always import.

See ``docs/contrib.md`` for the full reference.
"""

from __future__ import annotations

from .agent_loop_config import (
    DEFAULT_AGENT_LOOP_CONFIG,
    DEFAULT_CONTEXT_OVERFLOW_MARKERS,
    DEFAULT_RETRY_STATUS_CODES,
    AgentLoopConfig,
)
from .checkpoint import Checkpoint, restore_from_snapshot, take_checkpoint
from .cost_estimator import CostBreakdown, CostEstimator, CostPreview, to_human_units
from .cost_tracker import (
    Adjustment,
    ApprovalRequired,
    CostEntry,
    CostSnapshot,
    CostSummary,
    CostTracker,
    DuplicateReservation,
    InsufficientBudget,
    ReservationNotFound,
)
from .cost_translation import (
    COST_TRANSLATION_MAP,
    CostTemplate,
    register_cost_template,
    translate_cost,
)
from .cost_translation import (
    get_template as get_cost_template,
)
from .delivery_promise import DeliveryPromise, validate_cuts
from .dep_catalog import CATALOG as DEP_CATALOG
from .dep_catalog import CATALOG_BY_ID as DEP_CATALOG_BY_ID
from .dep_catalog import FFMPEG, WHISPER_CPP, YT_DLP
from .dep_gate import (
    DependencyGate,
    DepStatus,
    InstallEvent,
    InstallMethod,
    SystemDependency,
    current_platform,
)
from .env_any_loader import EnvAnyEntry, load_env_any
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
from .parallel_executor import (
    ParallelResult,
    ParallelSummary,
    run_parallel,
)
from .parallel_executor import (
    summarize as summarize_parallel,
)
from .prompt_optimizer import PromptOptimizer
from .prompts import (
    PromptNotFound,
    list_prompts,
    load_prompt,
    render_prompt,
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
from .tool_result import ToolResult
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
    "Adjustment",
    "AgentLoopConfig",
    "ApprovalRequired",
    "BADGE_GREEN",
    "BADGE_RED",
    "BADGE_YELLOW",
    "BaseTaskManager",
    "BaseVendorClient",
    "COST_TRANSLATION_MAP",
    "Checkpoint",
    "DEFAULT_AGENT_LOOP_CONFIG",
    "DEFAULT_CONTEXT_OVERFLOW_MARKERS",
    "DEFAULT_GRADE_CLAMP_PCT",
    "DEFAULT_RETRY_STATUS_CODES",
    "CostBreakdown",
    "CostEntry",
    "CostEstimator",
    "CostPreview",
    "CostSnapshot",
    "CostSummary",
    "CostTemplate",
    "CostTracker",
    "DEFAULT_AV_EXTENSIONS",
    "DEFAULT_IMAGE_EXTENSIONS",
    "DEFAULT_PREVIEW_EXTENSIONS",
    "DEP_CATALOG",
    "DEP_CATALOG_BY_ID",
    "DeliveryPromise",
    "DepStatus",
    "DependencyGate",
    "DuplicateReservation",
    "ERROR_KIND_AUTH",
    "ERROR_KIND_CLIENT",
    "ERROR_KIND_MODERATION",
    "ERROR_KIND_NETWORK",
    "ERROR_KIND_NOT_FOUND",
    "ERROR_KIND_RATE_LIMIT",
    "ERROR_KIND_SERVER",
    "ERROR_KIND_TIMEOUT",
    "ERROR_KIND_UNKNOWN",
    "EnvAnyEntry",
    "ErrorCoach",
    "ErrorPattern",
    "EvalResult",
    "FFMPEG",
    "FFmpegError",
    "FFmpegResult",
    "GateResult",
    "GateStatus",
    "GradeStats",
    "InstallEvent",
    "InstallMethod",
    "InsufficientBudget",
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
    "ParallelResult",
    "ParallelSummary",
    "PromptNotFound",
    "PromptOptimizer",
    "ProviderScore",
    "QualityGates",
    "RenderedError",
    "RenderPipeline",
    "ReservationNotFound",
    "ReviewIssue",
    "ReviewReport",
    "ReviewThresholds",
    "SlideshowRisk",
    "StorageStats",
    "SystemDependency",
    "TaskRecord",
    "TaskStatus",
    "ToolResult",
    "UIEventEmitter",
    "VendorError",
    "Verification",
    "WHISPER_CPP",
    "YT_DLP",
    "add_upload_preview_route",
    "auto_color_grade_filter",
    "build_preview_url",
    "build_render_pipeline",
    "collect_storage_stats",
    "current_platform",
    "evaluate_slideshow_risk",
    "ffprobe_json",
    "ffprobe_json_sync",
    "get_cost_template",
    "get_grade_preset",
    "list_grade_presets",
    "list_prompts",
    "load_env_any",
    "load_prompt",
    "merge_verifications",
    "parse_llm_json",
    "parse_llm_json_array",
    "parse_llm_json_object",
    "register_cost_template",
    "render_prompt",
    "render_verification_badge",
    "resolve_binary",
    "restore_from_snapshot",
    "review_audio",
    "review_image",
    "review_source",
    "review_video",
    "run_ffmpeg",
    "run_ffmpeg_sync",
    "run_parallel",
    "sample_signalstats",
    "sample_signalstats_sync",
    "score_providers",
    "strip_plugin_event_prefix",
    "summarize_parallel",
    "take_checkpoint",
    "to_human_units",
    "translate_cost",
    "validate_cuts",
]
