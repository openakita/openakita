"""V2 organisation SSE stream endpoint.

``GET /api/v2/orgs-spec/{id}/stream`` is a Server-Sent Events channel
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
from openakita.orgs import OrgNotFound, get_default_store
from openakita.runtime.stream import StreamEvent
from openakita.runtime.stream_registry import (
    get_or_create_org_stream_bus,
    mark_subscriber_attached,
    mark_subscriber_lost,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs-spec", tags=["v2:组织流"])


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

    # Attach via the public API (P-RC-3 T5) so this route does not
    # reach into bus._lock / bus._subscriptions / bus._max_queue.
    # Eager-close (drain_on_close=False) matches the previous
    # behaviour: an SSE consumer that disconnects must release
    # immediately without holding bus.close() on a drained queue.
    sub = bus.make_subscription(
        DEFAULT_SSE_CHANNELS,
        drain_on_close=False,
    )
    await bus.register_subscription(sub)
    mark_subscriber_attached(org_id)

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
        await bus.detach_subscription(sub)
        mark_subscriber_lost(org_id)


def _build_streaming_response(request: Request, org_id: str) -> StreamingResponse:
    """Validate the org + return the long-poll :class:`StreamingResponse`.

    Shared by both the legacy ``/api/v2/orgs-spec/{id}/stream`` route
    and the Sprint-9 alias ``/api/v2/orgs/{id}/events/stream`` so
    the two surfaces are byte-for-byte equivalent.
    """
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


@router.get("/{org_id}/stream", summary="SSE stream of v2 supervisor progress for one org")
async def stream_org_progress(request: Request, org_id: str) -> StreamingResponse:
    """SSE endpoint backed by the org's long-lived StreamBus.

    Legacy path under ``/api/v2/orgs-spec/`` -- the frontend's
    ``apps/setup-center/src/api/v2Stream.ts`` was wired to this URL
    in P-RC-3. Kept verbatim so older clients keep working.

    Newer callers should prefer the Sprint-9 alias
    ``GET /api/v2/orgs/{id}/events/stream`` mounted on
    :mod:`orgs_v2_runtime` -- same body, conventional location next
    to the rest of the runtime verbs.
    """
    return _build_streaming_response(request, org_id)
