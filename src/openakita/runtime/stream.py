"""Multi-channel stream bus for v2 runtime observability.

Implements ADR-0006: every supervisor / node component emits typed
events on one of eight named channels. Subscribers can subscribe to a
subset, get a bounded queue, and never block producers — when a
subscriber is too slow, the *oldest* event in their queue is dropped
(documented in ADR-0006). A drop counter is exposed for both the
front-end and tests.

This module deliberately avoids Pydantic. The hot path is "emit one
event"; the validation surface lives in :func:`StreamEvent.validate`,
called only when ``StreamBus(strict=True)``. Production callers can
disable strict mode for throughput; tests turn it on to fail fast.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "StreamBus",
    "StreamEvent",
    "ChannelName",
    "STANDARD_CHANNELS",
    "Subscription",
]

logger = logging.getLogger(__name__)

ChannelName = str


#: The canonical channel set defined in ADR-0006.
STANDARD_CHANNELS: frozenset[str] = frozenset(
    {
        "values",
        "updates",
        "tasks",
        "checkpoints",
        "messages",
        "debug",
        "progress_ledger",
        "lifecycle",
    }
)


# ---------------------------------------------------------------------------
# Event envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamEvent:
    """A single bus event with the ADR-0006 envelope.

    All fields are JSON-serialisable. ``payload`` is intentionally
    typed as ``dict[str, Any]`` to keep emitters lightweight; the
    channel-scoped event type enumeration lives next to the channel
    that owns the type.
    """

    channel: ChannelName
    event_id: str
    command_id: str
    org_id: str
    superstep: int
    emitted_at: datetime
    type: str
    payload: dict[str, Any]
    correlation_id: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "event_id": self.event_id,
            "command_id": self.command_id,
            "org_id": self.org_id,
            "superstep": self.superstep,
            "emitted_at": self.emitted_at.isoformat(),
            "type": self.type,
            "payload": dict(self.payload),
            "correlation_id": self.correlation_id,
        }

    def validate(self) -> None:
        """Defensive structural check; raises ``ValueError`` on bad data."""
        if not self.channel or not isinstance(self.channel, str):
            raise ValueError("StreamEvent.channel must be a non-empty string")
        if not self.type or not isinstance(self.type, str):
            raise ValueError("StreamEvent.type must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("StreamEvent.payload must be a dict")
        if not isinstance(self.superstep, int) or self.superstep < 0:
            raise ValueError("StreamEvent.superstep must be a non-negative int")


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


@dataclass
class Subscription:
    """A bounded subscriber channel with oldest-drop backpressure.

    Owned by ``StreamBus``. Held by callers via the async iterator
    returned from :meth:`StreamBus.subscribe`; closing the iterator
    detaches the subscription from the bus.

    Attributes:
        drain_on_close:
            When ``True`` (the default in P-RC-2 and beyond), the
            owning :class:`StreamBus` waits for this subscription's
            queue to drain to zero pending items before signalling
            its ``closed`` event in :meth:`StreamBus.close`. Callers
            that prefer the original eager-close behaviour
            (close fires immediately, in-flight events are lost)
            pass ``drain_on_close=False`` to
            :meth:`StreamBus.subscribe`. Closes G-RC-1 residual
            risk #1.
    """

    channels: frozenset[str]
    queue: asyncio.Queue[StreamEvent]
    dropped: int = 0
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    drain_on_close: bool = True

    def matches(self, channel: str) -> bool:
        return channel in self.channels


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class StreamBus:
    """In-process multi-channel pub/sub.

    Producers call :meth:`emit` (or :meth:`emit_many`); consumers call
    :meth:`subscribe` to receive an async iterator of
    :class:`StreamEvent`. The bus is single-process and intentionally
    simple — it has no networking, no persistence, and no cross-runtime
    distribution. Events are persisted by the :mod:`event_store`
    module, not here.

    Args:
        max_queue_size: bounded queue size per subscriber. When the
            queue is full and a new event arrives for that subscriber,
            the *oldest* event is discarded (oldest-drop backpressure)
            so the subscriber always sees the freshest tail. Default
            256 follows ADR-0006.
        strict: when ``True``, every emitted event is validated before
            fan-out. Tests should set this to ``True``.
    """

    def __init__(self, *, max_queue_size: int = 256, strict: bool = False) -> None:
        self._subscriptions: list[Subscription] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue_size
        self._strict = strict
        self._total_emitted = 0
        self._total_dropped = 0
        # P-RC-3 T3: once True, subscribe() raises and emit() is a
        # silent no-op. Set inside close() before drain begins so a
        # re-entrant close() returns promptly and late publishers
        # cannot land events after shutdown.
        self._closed = False

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        *channels: str,
        drain_on_close: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Subscribe to one or more channels and yield events.

        Usage:
            async for event in bus.subscribe("updates", "checkpoints"):
                ...

        The async iterator stops cleanly when the subscriber's task is
        cancelled or when :meth:`close` is called on the bus.

        Args:
            channels: one or more channel names to listen on.
            drain_on_close: when ``True`` (default), :meth:`close` waits
                up to the close-time timeout for this subscription's
                queue to drain to zero before unblocking the consumer.
                Pass ``False`` to opt into eager-close
                semantics (in-flight events are lost on close).
        """
        if not channels:
            raise ValueError("subscribe() requires at least one channel name")
        if self._closed:
            raise RuntimeError("StreamBus is closed")
        for ch in channels:
            if ch not in STANDARD_CHANNELS and not ch.startswith("custom."):
                logger.debug(
                    "StreamBus: subscribing to non-standard channel %r "
                    "(allowed: STANDARD_CHANNELS or custom.* prefix)",
                    ch,
                )
        sub = Subscription(
            channels=frozenset(channels),
            queue=asyncio.Queue(maxsize=self._max_queue),
            drain_on_close=drain_on_close,
        )
        async with self._lock:
            self._subscriptions.append(sub)
        try:
            while not sub.closed.is_set():
                getter = asyncio.create_task(sub.queue.get())
                close_wait = asyncio.create_task(sub.closed.wait())
                done, pending = await asyncio.wait(
                    {getter, close_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if getter in done:
                    yield getter.result()
                else:
                    return
        finally:
            async with self._lock:
                if sub in self._subscriptions:
                    self._subscriptions.remove(sub)
            sub.closed.set()

    async def close(self, *, drain_timeout: float = 2.0) -> None:
        """Close every subscription so consumers exit their loops.

        For subscriptions that opted in (``drain_on_close=True``,
        the default in P-RC-2 and beyond), this method first waits
        up to ``drain_timeout`` seconds for their queues to reach
        zero pending items before signalling the close event. This
        eliminates the post-supervisor drain race that P-RC-1
        commit 7 had to mitigate with a 10x ``asyncio.sleep(0)``
        workaround in ``channels/gateway.py``.

        Subscriptions that pass ``drain_on_close=False`` retain the
        original eager-close semantics: their close event fires
        immediately and any events still queued are discarded when
        the consumer exits.

        On timeout, a warning is logged and the close still
        proceeds -- pathological consumers must never be able to
        wedge a bus shutdown.
        """
        # Re-entrancy: a second close() is a no-op.
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            subs = list(self._subscriptions)
            self._subscriptions.clear()
        await self._wait_until_drained(subs, timeout=drain_timeout)
        for sub in subs:
            sub.closed.set()

    async def _wait_until_drained(
        self,
        subs: list[Subscription],
        *,
        timeout: float,
    ) -> None:
        """Block until every drain-eligible subscription's queue is empty.

        Eager subscriptions (``drain_on_close=False``) are skipped so
        the eager-close path is unaffected. Returns early on
        timeout; callers that care log a warning when this happens.
        """
        eligible = [s for s in subs if s.drain_on_close]
        if not eligible:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while True:
            if all(s.queue.empty() for s in eligible):
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                pending = sum(s.queue.qsize() for s in eligible)
                logger.warning(
                    "StreamBus.close: drain timed out with %d event(s) still "
                    "queued across %d subscriber(s); proceeding to close",
                    pending,
                    len(eligible),
                )
                return
            # Yield once per micro-tick. ``asyncio.sleep(0)`` re-enters
            # the scheduler so consumer ``queue.get()`` tasks get a turn
            # before we re-check ``empty()``. The deadline guarantees we
            # never spin.
            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit(
        self,
        channel: str,
        type: str,
        payload: dict[str, Any],
        *,
        command_id: str = "",
        org_id: str = "",
        superstep: int = 0,
        correlation_id: str | None = None,
    ) -> StreamEvent:
        """Emit a single event. Returns the constructed event.

        ``command_id`` and ``org_id`` default to empty strings so quick
        diagnostic emits from leaf modules need not plumb the ids; the
        supervisor and node runtime always supply them in production.
        """
        event = StreamEvent(
            channel=channel,
            event_id=uuid.uuid4().hex[:16],
            command_id=command_id,
            org_id=org_id,
            superstep=superstep,
            emitted_at=datetime.now(UTC),
            type=type,
            payload=payload,
            correlation_id=correlation_id,
        )
        if self._strict:
            event.validate()
        await self._fanout(event)
        return event

    async def emit_many(self, events: list[StreamEvent]) -> None:
        """Fan out a pre-built batch of events.

        Used by the supervisor when emitting a coherent multi-channel
        bundle (e.g. ``tasks`` + ``progress_ledger`` for a single turn).
        """
        for ev in events:
            if self._strict:
                ev.validate()
            await self._fanout(ev)

    async def _fanout(self, event: StreamEvent) -> None:
        """Push ``event`` to every matching subscriber."""
        if self._closed:
            logger.debug(
                "StreamBus._fanout: dropping %s event on closed bus",
                event.channel,
            )
            return
        # Snapshot the subscriber list so emitters never block on
        # subscribe / unsubscribe lock contention.
        async with self._lock:
            subs = [s for s in self._subscriptions if s.matches(event.channel)]
        self._total_emitted += 1
        for sub in subs:
            self._push_with_drop(sub, event)

    def _push_with_drop(self, sub: Subscription, event: StreamEvent) -> None:
        """Insert ``event`` into ``sub.queue`` with oldest-drop policy."""
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = sub.queue.get_nowait()
                sub.dropped += 1
                self._total_dropped += 1
            except asyncio.QueueEmpty:
                pass
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Should be unreachable: we just drained one slot. Be
                # defensive anyway, drop the new event.
                sub.dropped += 1
                self._total_dropped += 1

    # ------------------------------------------------------------------
    # Public subscription surface (P-RC-3 T5)
    #
    # ``api/routes/orgs_v2_stream.py`` previously reached into
    # ``self._lock`` / ``self._subscriptions`` / ``self._max_queue``
    # to attach a manually-built ``Subscription`` before the SSE
    # handshake. These small helpers expose the same primitives via
    # a documented surface so the route does not depend on private
    # attributes.
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        """True once :meth:`close` has been called."""
        return self._closed

    def subscription_capacity(self) -> int:
        """Return the per-subscriber bounded queue size."""
        return self._max_queue

    def make_subscription(
        self,
        channels: tuple[str, ...] | frozenset[str],
        *,
        drain_on_close: bool = True,
    ) -> Subscription:
        """Build a fresh :class:`Subscription` bound to this bus.

        The returned subscription is NOT yet attached; callers must
        feed it to :meth:`register_subscription` (or close it via
        :meth:`detach_subscription`).
        """
        return Subscription(
            channels=frozenset(channels),
            queue=asyncio.Queue(maxsize=self._max_queue),
            drain_on_close=drain_on_close,
        )

    async def register_subscription(self, sub: Subscription) -> None:
        """Attach a caller-built :class:`Subscription` to the bus.

        Raises ``RuntimeError`` if the bus is already closed.
        """
        if self._closed:
            raise RuntimeError("StreamBus is closed")
        async with self._lock:
            self._subscriptions.append(sub)

    async def detach_subscription(self, sub: Subscription) -> None:
        """Remove ``sub`` from the bus and signal its close event."""
        async with self._lock:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)
        sub.closed.set()

    @property
    def subscriber_count(self) -> int:
        """Snapshot of currently attached subscribers (race-prone; do not loop on it)."""
        return len(self._subscriptions)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    @property
    def total_emitted(self) -> int:
        return self._total_emitted

    @property
    def total_dropped(self) -> int:
        return self._total_dropped

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": len(self._subscriptions),
            "total_emitted": self._total_emitted,
            "total_dropped": self._total_dropped,
            "per_subscriber_drops": [s.dropped for s in self._subscriptions],
        }

    def to_jsonable(self) -> dict[str, Any]:
        """Serialise basic stats. Used by the runtime debug endpoint."""
        return self.stats()
