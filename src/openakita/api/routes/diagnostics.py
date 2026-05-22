"""Read-only diagnostics endpoints (plan: conversation concurrency v1.28, FIX 6).

Currently exposes the v1.27.14 conversation-concurrency telemetry counters
(:mod:`openakita.core.conversation_metrics`) and the in-flight turn registry
(:mod:`openakita.api.routes.turn_registry`).  Admin uses these to verify
which policy branches are firing in production (preempt / queue /
abandon / takeover) and tune ``settings.preempt_settle_timeout_ms`` /
``double_texting_per_channel`` accordingly.

All endpoints are GET-only and return JSON; no authentication beyond the
global ``api_token`` / desktop-token guard that wraps the whole API
surface.  They surface metadata that is also visible in the server log,
so they are safe to expose on the same LAN profile as the chat API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from openakita.core.conversation_metrics import snapshot as conversation_metrics_snapshot

from .turn_registry import get_turn_registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/diagnostics/conversation_metrics")
async def get_conversation_metrics() -> dict:
    """Snapshot of the in-process v1.27.14 concurrency counters.

    Counter names (see ``openakita.core.conversation_metrics``):

    * ``preempt`` — INTERRUPT / STEER policy actually cancelled an
      in-flight task. Labels: ``policy``, ``channel``.
    * ``queue`` — QUEUE policy waited for a prior task to settle.
      Labels: ``channel``.
    * ``settled_timeout`` — wait_until_settled exceeded
      ``settings.preempt_settle_timeout_ms``. Labels: ``policy``,
      ``channel``.
    * ``abandon`` — old task was marked abandoned after settled_timeout.
      Labels: ``policy``, ``channel``.
    * ``takeover`` — HTTP lifecycle.start returned ``took_over``
      (INTERRUPT path succeeded). Labels: ``channel``.
    * ``illegal_reasoning_entry`` — strict-mode state-machine violation
      (S5 wiring; pre-S5 always 0). Labels: ``source``.
    """
    return {
        "counters": conversation_metrics_snapshot(),
    }


@router.get("/api/diagnostics/turn_registry")
async def get_turn_registry_snapshot() -> dict:
    """In-flight + recently-finished turns (60s TTL).

    Helps debug "client says it sent the message but UI shows nothing":
    look for the ``turn_id`` here; if status is ``in_flight`` the request
    is still being processed; if ``succeeded`` / ``failed`` is present
    the request finished but the client likely hasn't replayed the SSE
    yet.
    """
    registry = get_turn_registry()
    return {
        "turns": await registry.snapshot(),
    }


__all__ = ["router"]
