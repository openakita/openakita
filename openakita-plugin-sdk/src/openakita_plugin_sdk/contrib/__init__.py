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

from .cost_estimator import CostBreakdown, CostEstimator, CostPreview
from .delivery_promise import DeliveryPromise, validate_cuts
from .env_any_loader import EnvAnyEntry, load_env_any
from .errors import ErrorCoach, ErrorPattern, RenderedError
from .intent_verifier import IntentSummary, IntentVerifier
from .prompt_optimizer import PromptOptimizer
from .provider_score import ProviderScore, score_providers
from .quality_gates import GateResult, GateStatus, QualityGates
from .render_pipeline import RenderPipeline, build_render_pipeline
from .slideshow_risk import SlideshowRisk, evaluate_slideshow_risk
from .storage_stats import StorageStats, collect_storage_stats
from .task_manager import BaseTaskManager, TaskRecord, TaskStatus
from .ui_events import UIEventEmitter, strip_plugin_event_prefix
from .vendor_client import BaseVendorClient, VendorError

__all__ = [
    "BaseTaskManager",
    "BaseVendorClient",
    "CostBreakdown",
    "CostEstimator",
    "CostPreview",
    "DeliveryPromise",
    "EnvAnyEntry",
    "ErrorCoach",
    "ErrorPattern",
    "GateResult",
    "GateStatus",
    "IntentSummary",
    "IntentVerifier",
    "PromptOptimizer",
    "ProviderScore",
    "QualityGates",
    "RenderedError",
    "RenderPipeline",
    "SlideshowRisk",
    "StorageStats",
    "TaskRecord",
    "TaskStatus",
    "UIEventEmitter",
    "VendorError",
    "build_render_pipeline",
    "collect_storage_stats",
    "evaluate_slideshow_risk",
    "load_env_any",
    "score_providers",
    "strip_plugin_event_prefix",
    "validate_cuts",
]
