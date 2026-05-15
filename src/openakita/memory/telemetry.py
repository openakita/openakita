"""Opt-in memory health telemetry."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def memory_health_events_enabled() -> bool:
    return os.environ.get("OPENAKITA_MEMORY_HEALTH_EVENTS", "").lower() in {"1", "true", "yes"}


def emit_memory_health_event(event: str, payload: dict[str, Any] | None = None) -> None:
    if not memory_health_events_enabled():
        return
    # Keep hotfix telemetry non-invasive: local structured log only. Existing
    # feedback upload can pick this up when the user opts in and sends a report.
    logger.info("[memory_health_event] %s %s", event, payload or {})
