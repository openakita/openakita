"""V2 organisation SSE stream endpoint.

``GET /api/v2/orgs/{id}/stream`` is a Server-Sent Events channel
backed by the org's long-lived
:class:`~openakita.runtime.stream.StreamBus` (built lazily by
:mod:`openakita.runtime.stream_registry`). Each event is one
ADR-0006 record; channels delivered are ``progress_ledger`` /
``messages`` / ``lifecycle`` / ``tasks``. 404 when
``runtime_v2_enabled`` is off or the org is unknown. The
dispatch path is wired to the org-level bus in P-RC-3; this
commit ships the route + tests so the frontend has a stable
contract to render against.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from openakita.config import settings
from openakita.runtime.orgs import OrgNotFound, get_default_store
from openakita.runtime.stream import StreamEvent, Subscription
from openakita.runtime.stream_registry import get_or_create_org_stream_bus

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs", tags=["v2:组织流"])


#: Channels exposed by default. Noisy telemetry (``values`` /
#: ``updates`` / ``debug`` / ``checkpoints``) is omitted; the
#: frontend ProgressLedgerTimeline only needs the four below.
DEFAULT_SSE_CHANNELS: tuple[str, ...] = (
    "progress_ledger",
    "messages",
    "lifecycle",
    "tasks",
)


def _serialize_event(event: StreamEvent) -> str:
    """Render ``event`` as one SSE record (event-name = channel).

    The ``data`` JSON drops the channel field (already on the
    ``event:`` line) and adds a ``ts`` mirror of the emitted
    timestamp.
    """
    body = event.to_jsonable()
    body.pop("channel", None)
    body["ts"] = body.get("emitted_at") or datetime.now(UTC).isoformat()
    return f"event: {event.channel}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


async def _event_stream(request: Request, org_id: str) -> AsyncIterator[str]:
    """Yield SSE-formatted strings until the client disconnects."""
    bus = get_or_create_org_stream_bus(org_id)

    # Attach the Subscription manually before the first yield so
    # the bus has us in its subscriber list before any byte goes
    # out. ``StreamBus.subscribe`` defers the append until the
    # iterator is first stepped, which races the SSE handshake.
    sub = Subscription(
        channels=frozenset(DEFAULT_SSE_CHANNELS),
        queue=asyncio.Queue(maxsize=bus._max_queue),
        drain_on_close=False,
    )
    async with bus._lock:
        bus._subscriptions.append(sub)

    # The try/finally must wrap every yield so ``aclose`` reliably
    # detaches the subscription regardless of which yield point
    # the generator was paused at when it was closed.
    try:
        yield "retry: 3000\n\n"
        initial = StreamEvent(
            channel="lifecycle",
            event_id="sse_connected",
            command_id="",
            org_id=org_id,
            superstep=0,
            emitted_at=datetime.now(UTC),
            type="sse_connected",
            payload={"org_id": org_id},
        )
        yield _serialize_event(initial)

        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield _serialize_event(event)
    except asyncio.CancelledError:
        pass
    finally:
        async with bus._lock:
            if sub in bus._subscriptions:
                bus._subscriptions.remove(sub)
        sub.closed.set()


@router.get("/{org_id}/stream", summary="SSE stream of v2 supervisor progress for one org")
async def stream_org_progress(request: Request, org_id: str) -> StreamingResponse:
    """SSE endpoint backed by the org's long-lived StreamBus."""
    if not getattr(settings, "runtime_v2_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="runtime v2 is disabled",
        )
    try:
        get_default_store().get(org_id)
    except OrgNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"org {org_id} not found",
        ) from exc

    return StreamingResponse(
        _event_stream(request, org_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
