"""V2 organisation SSE stream endpoint.

``GET /api/v2/orgs-spec/{id}/stream`` is a Server-Sent Events channel
backed by the org's long-lived
:class:`~openakita.runtime.stream.StreamBus` (built lazily by
:mod:`openakita.runtime.stream_registry`). Each event is one
ADR-0006 record. 404 when ``runtime_v2_enabled`` is off or the
org is unknown. The dispatch path is wired to the org-level bus
in P-RC-3.

v25 RC-3 fix: the handler now subscribes to every channel in
:data:`~openakita.runtime.stream.STANDARD_CHANNELS` except the
noisy ``debug`` channel (which the frontend can opt back in via
a future query-param). Previously the handler only subscribed to
``progress_ledger`` / ``messages`` / ``lifecycle`` / ``tasks``,
which silently dropped every ``updates`` (delegation_result),
``checkpoints`` (checkpoint_written), and ``debug`` event the
supervisor actually emits. See
``_v22_biz/_root_cause_analysis.md`` §RC-3 and
``_v25_biz/v25_phenomena_report.md`` D7.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from openakita.config import settings
from openakita.orgs import OrgNotFound
from openakita.runtime.stream import STANDARD_CHANNELS, StreamEvent
from openakita.runtime.stream_registry import (
    get_or_create_org_stream_bus,
    mark_subscriber_attached,
    mark_subscriber_lost,
)

__all__ = ["DEFAULT_SSE_CHANNELS", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs-spec", tags=["v2:组织流"])


#: Channels exposed by default. Computed at import time from
#: :data:`STANDARD_CHANNELS` (the canonical ADR-0006 set the
#: supervisor actually emits to) minus the high-volume ``debug``
#: channel, which is opt-in for diagnostic frontends only. The
#: subscription set is sorted to keep the tuple deterministic for
#: tests and OpenAPI snapshots.
DEFAULT_SSE_CHANNELS: tuple[str, ...] = tuple(
    sorted(ch for ch in STANDARD_CHANNELS if ch != "debug")
)


def _serialize_event(event: StreamEvent) -> str:
    """Render ``event`` as one SSE record (event-name = channel).

    The ``data`` JSON drops the channel field (already on the
    ``event:`` line) and adds a ``ts`` mirror of the emitted
    timestamp.
    """
    body = event.to_jsonable()
    body.pop("channel", None)
    body["ts"] = body.get("emitted_at")
    return f"event: {event.channel}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


async def _event_stream(request: Request, org_id: str) -> AsyncIterator[str]:
    """Yield SSE-formatted strings until the client disconnects.

    v25 RC-3 fix: the generator no longer fabricates an
    ``lifecycle/sse_connected`` first event. The "connection is
    live" signal is now carried by the HTTP ``200`` + the
    ``text/event-stream`` content type alone (browsers raise
    ``EventSource.onopen`` for this), and an initial
    ``retry: 3000`` SSE directive plus the first idle ``: ping``
    SSE comment keep the response body flushing through proxies
    that buffer until they see bytes. The synthetic first event
    used to mask the RC-3 channel-coverage gap by always
    satisfying "first event received" assertions even when no
    real supervisor event ever flowed.
    """
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
        # The ``retry`` directive sets the client-side reconnect
        # delay. It is the first chunk so reverse proxies that
        # buffer the response until they see any body bytes
        # (gunicorn / nginx without ``X-Accel-Buffering: no``)
        # release the headers immediately.
        yield "retry: 3000\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
            except TimeoutError:
                # SSE comment line ("``:`` SP <text>"); per the
                # HTML spec these are dropped by the EventSource
                # parser so clients see no event, but the bytes
                # keep the TCP connection warm and force the
                # framework to flush any buffered chunks.
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

    v22 fix (audit v10 §19 "SSE org not found"): the org-existence
    probe used to call :func:`get_default_store().get` -- the legacy
    ``JsonOrgStore`` backed by ``data/orgs_v2.json``. After Sprint-9
    every ``POST /api/v2/orgs/from-template`` mints orgs through
    :class:`~openakita.orgs.manager.OrgManager` (``data/orgs/<id>/
    org.json``) and never writes ``orgs_v2.json``, so every freshly
    minted org's SSE stream 404'd. We now look the org up through
    ``request.app.state.org_manager`` so both routes see the live
    org registry the rest of the v2 surface writes to.
    """
    if not getattr(settings, "runtime_v2_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="runtime v2 is disabled",
        )
    # Local import: ``orgs_v2_runtime`` is loaded after this module
    # during the side-effect import chain in ``server.py`` -- a
    # module-level import would either circular-import or pin a
    # mid-init module reference. The lazy import is exercised once
    # per SSE handshake (already an IO-bound op), so the cost is
    # negligible.
    from .orgs_v2_runtime import _get_manager

    manager = _get_manager(request)
    try:
        org = manager.get(org_id)
    except (FileNotFoundError, KeyError, OrgNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"org {org_id} not found",
        ) from exc
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"org {org_id} not found",
        )

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
