"""Feature flag registry for safe progressive rollout.

A lightweight, dependency-free registry for runtime feature toggles. Defaults
keep the new "v2" behavior on so existing improvements are picked up
automatically; setting an environment variable like ``OPENAKITA_FF_DISABLE``
to a comma-separated list of flag names lets ops downgrade to legacy behavior
without redeploying.

Why not extend ``Settings``? ``pydantic-settings`` parses .env on import; a
typed mistake there can crash boot. This module never raises and never blocks
boot — it just returns ``True``/``False``.
"""

from __future__ import annotations

import logging
import os
from threading import RLock

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, bool] = {
    # PR-A1
    "grep_safety_v1": True,
    # PR-A2
    "risk_authorized_intent_v2": True,
    # PR-A3
    "memory_delete_by_query_v1": True,
    # PR-B1
    "profile_whitelist_v2": True,
    # PR-B2
    "memory_session_scope_v1": True,
    # PR-B3
    "memory_rule_session_scope_v1": True,
    # PR-C1
    "openai_content_recovery_v2": True,
    # PR-C2
    "openai_recovery_no_cooldown_v1": True,
    # PR-C3
    "openai_empty_response_dump_v1": True,
    # PR-D1
    "history_db_merge_v1": True,
    # PR-D2
    "session_backfill_on_start_v1": True,
    # PR-E1
    "im_error_format_v1": True,
    # PR-F1
    "tauri_health_heartbeat_v1": True,
    # P1-6/7
    "scheduler_metadata_cleanup_v1": True,
    # P2-2
    "llm_inflight_loop_aware_v1": True,
}


_lock = RLock()
_overrides: dict[str, bool] = {}


def _parse_csv_env(name: str) -> set[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


_DISABLED_AT_IMPORT: set[str] = _parse_csv_env("OPENAKITA_FF_DISABLE")
_ENABLED_AT_IMPORT: set[str] = _parse_csv_env("OPENAKITA_FF_ENABLE")


def is_enabled(name: str) -> bool:
    """Return whether a feature flag is on. Unknown flags are treated as off."""
    with _lock:
        if name in _overrides:
            return _overrides[name]
    if name in _DISABLED_AT_IMPORT:
        return False
    if name in _ENABLED_AT_IMPORT:
        return True
    return _DEFAULTS.get(name, False)


def set_flag(name: str, value: bool) -> None:
    """Override a flag at runtime (test harness / admin API)."""
    with _lock:
        _overrides[name] = bool(value)
    logger.info("[FeatureFlags] %s set to %s", name, value)


def clear_overrides() -> None:
    """Reset all runtime overrides (used by tests)."""
    with _lock:
        _overrides.clear()


def known_flags() -> dict[str, bool]:
    """Return a snapshot of all known flags with their effective state."""
    return {name: is_enabled(name) for name in _DEFAULTS}
