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

from .cost_estimator import CostBreakdown, CostEstimator, CostPreview, to_human_units
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
    FFmpegError,
    FFmpegResult,
    ffprobe_json,
    ffprobe_json_sync,
    resolve_binary,
    run_ffmpeg,
    run_ffmpeg_sync,
)
from .intent_verifier import IntentSummary, IntentVerifier
from .llm_json_parser import (
    parse_llm_json,
    parse_llm_json_array,
    parse_llm_json_object,
)
from .prompt_optimizer import PromptOptimizer
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

__all__ = [
    "BaseTaskManager",
    "BaseVendorClient",
    "COST_TRANSLATION_MAP",
    "CostBreakdown",
    "CostEstimator",
    "CostPreview",
    "CostTemplate",
    "DEFAULT_AV_EXTENSIONS",
    "DEFAULT_IMAGE_EXTENSIONS",
    "DEFAULT_PREVIEW_EXTENSIONS",
    "DEP_CATALOG",
    "DEP_CATALOG_BY_ID",
    "DeliveryPromise",
    "DepStatus",
    "DependencyGate",
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
    "FFMPEG",
    "FFmpegError",
    "FFmpegResult",
    "GateResult",
    "GateStatus",
    "InstallEvent",
    "InstallMethod",
    "IntentSummary",
    "IntentVerifier",
    "PromptOptimizer",
    "ProviderScore",
    "QualityGates",
    "RenderedError",
    "RenderPipeline",
    "ReviewIssue",
    "ReviewReport",
    "ReviewThresholds",
    "SlideshowRisk",
    "StorageStats",
    "SystemDependency",
    "TaskRecord",
    "TaskStatus",
    "UIEventEmitter",
    "VendorError",
    "WHISPER_CPP",
    "YT_DLP",
    "add_upload_preview_route",
    "build_preview_url",
    "build_render_pipeline",
    "collect_storage_stats",
    "current_platform",
    "evaluate_slideshow_risk",
    "ffprobe_json",
    "ffprobe_json_sync",
    "get_cost_template",
    "load_env_any",
    "parse_llm_json",
    "parse_llm_json_array",
    "parse_llm_json_object",
    "register_cost_template",
    "resolve_binary",
    "review_audio",
    "review_image",
    "review_source",
    "review_video",
    "run_ffmpeg",
    "run_ffmpeg_sync",
    "score_providers",
    "strip_plugin_event_prefix",
    "to_human_units",
    "translate_cost",
    "validate_cuts",
]
