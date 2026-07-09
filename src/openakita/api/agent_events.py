"""Shared Agent management WebSocket event helpers."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


def _fire_agent_event(event: str, payload: dict[str, Any]) -> None:
    try:
        from openakita.api.routes.websocket import fire_event

        fire_event(event, payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AgentEvents] Failed to emit %s: %s", event, exc)


def emit_agent_profiles_changed(
    action: str,
    *,
    profile_id: str | None = None,
    profile_ids: Sequence[str] | None = None,
) -> None:
    payload: dict[str, Any] = {"action": action}
    if profile_id:
        payload["profile_id"] = profile_id
    if profile_ids:
        payload["profile_ids"] = list(profile_ids)
    _fire_agent_event("agents:profiles_changed", payload)


def emit_agent_categories_changed(
    action: str,
    *,
    category_id: str | None = None,
    profile_id: str | None = None,
    profile_ids: Sequence[str] | None = None,
) -> None:
    payload: dict[str, Any] = {"action": action}
    if category_id:
        payload["category_id"] = category_id
    if profile_id:
        payload["profile_id"] = profile_id
    if profile_ids:
        payload["profile_ids"] = list(profile_ids)
    _fire_agent_event("agents:categories_changed", payload)
