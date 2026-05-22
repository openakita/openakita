"""Centralized conversation lifecycle manager.

Manages busy-lock state transitions and ensures consistent cleanup across
all exit paths (normal completion, cancel, delete, disconnect).

Previously, busy-lock logic was scattered across chat.py (_mark_busy,
_clear_busy) and only released in _stream_chat's finally block, which
caused stale "in-progress" states when conversations were cancelled or
deleted through different code paths.

v1.27.14 (plan: conversation concurrency v1.28, S1.2):
- ``BusyInfo`` gained a ``turn_id`` field, populated when the request has
  a turn-level idempotency key (S1.6 TurnRegistry).
- ``start()`` returns a richer :class:`StartResult` that distinguishes
  same-client takeover (INTERRUPT/STEER) from different-client conflict
  (always REJECT) and carries the active :class:`DoubleTextingPolicy`.
- For backwards compatibility, :class:`StartResult` is iterable as the
  legacy ``(conflict, generation)`` tuple so older call-sites keep
  working without modification.  Callers are encouraged to read named
  attributes (``conflict``, ``generation``, ``took_over``,
  ``policy_applied``, ``queued_after_generation``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from .double_texting import DoubleTextingPolicy

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_SECONDS = 600  # 10 min auto-release


@dataclass
class BusyInfo:
    client_id: str
    start_time: float = field(default_factory=time.time)
    generation: int = 0
    # v1.27.14: optional per-turn idempotency key (S1.6).  None when the
    # caller did not supply one or for legacy endpoints.
    turn_id: str | None = None


@dataclass
class StartResult:
    """Result of :meth:`ConversationLifecycleManager.start`.

    Iterable as ``(conflict, generation)`` for legacy callers.

    Attributes:
        conflict: Existing :class:`BusyInfo` when the conversation is held
            by a **different** client (REJECT) or by the same client under
            REJECT/QUEUE policy.  ``None`` on success (lock acquired) or
            when QUEUE callers should wait then retry.
        generation: Unique generation of the new lock; ``0`` when conflict
            blocked acquisition.
        took_over: Previous :class:`BusyInfo` that was implicitly replaced
            because the same client preempted (INTERRUPT / STEER).  ``None``
            otherwise.  Used by callers to broadcast ``took_over_generation``
            in ``chat:busy`` events (S1.7 frontend takeover banner).
        policy_applied: The policy that was honoured (REJECT/QUEUE/INTERRUPT/
            STEER).  Identical to caller's ``policy`` argument; lifecycle
            does not downgrade policies internally (feature-flag downgrade
            is the caller's responsibility, see ``resolve_policy``).
        queued_after_generation: For QUEUE policy with same-client conflict,
            the generation of the in-flight task the caller should wait
            on before retrying.  ``None`` otherwise.
    """

    conflict: BusyInfo | None
    generation: int
    took_over: BusyInfo | None = None
    policy_applied: DoubleTextingPolicy = DoubleTextingPolicy.REJECT
    queued_after_generation: int | None = None
    # v1.27.15 (S2 P1-4): STEER means "let the old turn keep running,
    # just inject this new message into its working context".  When
    # set, ``conflict`` is also set (old holder is still the lock
    # holder); ``generation`` is 0.  The caller is responsible for
    # calling ``Agent.insert_user_message`` and NOT opening a new SSE
    # stream — they should ack the user and let them re-subscribe via
    # ``GET /api/chat/resume`` to follow the (still-running) old turn.
    steered: bool = False

    def __iter__(self) -> Iterator[object]:
        """Tuple unpacking back-compat: ``conflict, gen = await start(...)``."""
        yield self.conflict
        yield self.generation


class ConversationLifecycleManager:
    """All conversation state transitions go through this manager.

    ``start()`` / ``finish()`` pair guarantees that busy-lock release and
    ``chat:idle`` / ``chat:busy`` broadcasts happen consistently, regardless
    of whether the exit is via normal completion, user cancel, session
    deletion, or client disconnect.

    A monotonically increasing *generation* counter prevents stale
    ``_stream_chat`` finally blocks from accidentally releasing a lock
    that was already taken over by a newer request.

    .. note::

       This class is a pure mechanism.  Policy resolution (header > channel
       > default) and feature-flag downgrade live in
       :func:`openakita.api.routes.double_texting.resolve_policy`.  The
       lifecycle manager only honours the policy it is handed.
    """

    def __init__(self) -> None:
        self._busy: dict[str, BusyInfo] = {}
        self._lock = asyncio.Lock()
        self._generation_counter = 0
        # FIX 3 (v1.27.14 follow-up): per-conversation idle events to power
        # :meth:`wait_for_idle` (QUEUE policy at HTTP layer).  Created lazily
        # on first ``wait_for_idle`` call, ``set()`` from ``finish()``.
        # Keyed by conversation_id; entries are cleaned up when the next
        # ``start()`` succeeds on that conversation.
        self._idle_events: dict[str, asyncio.Event] = {}

    # ── Public API ──────────────────────────────────────────────────────

    async def start(
        self,
        conversation_id: str,
        client_id: str,
        *,
        policy: DoubleTextingPolicy = DoubleTextingPolicy.REJECT,
        turn_id: str | None = None,
    ) -> StartResult:
        """Mark a conversation as busy under the given ``policy``.

        Returns a :class:`StartResult`.  For legacy callers that still
        unpack ``conflict, gen = await start(...)``, ``StartResult.__iter__``
        yields the same two values.

        Semantics by policy (same-client overlap only; cross-client overlap
        always returns ``conflict``):

        - **REJECT**: same-client overlap → ``conflict`` set, ``generation=0``.
        - **QUEUE**: same-client overlap → ``conflict`` set,
          ``generation=0``, ``queued_after_generation`` = current holder's
          generation.  Caller awaits ``settled_event`` (S1.5) then retries.
        - **INTERRUPT** / **STEER**: same-client overlap → lock acquired,
          ``took_over`` = previous :class:`BusyInfo`, new generation issued.
          Caller is responsible for actually cancelling the in-flight task
          (see ``agent._preempt_or_queue``, S1.4).
        """
        async with self._lock:
            self._expire_stale()
            existing = self._busy.get(conversation_id)

            if existing and existing.client_id != client_id:
                return StartResult(
                    conflict=existing,
                    generation=0,
                    policy_applied=policy,
                )

            took_over: BusyInfo | None = None
            if existing and existing.client_id == client_id:
                if policy is DoubleTextingPolicy.REJECT:
                    return StartResult(
                        conflict=existing,
                        generation=0,
                        policy_applied=policy,
                    )
                if policy is DoubleTextingPolicy.QUEUE:
                    return StartResult(
                        conflict=existing,
                        generation=0,
                        policy_applied=policy,
                        queued_after_generation=existing.generation,
                    )
                if policy is DoubleTextingPolicy.STEER:
                    # v1.27.15 (S2 P1-4): unique among the four policies,
                    # STEER does NOT acquire the lock.  Return a "steered"
                    # signal so the HTTP layer can hand the new message
                    # off to ``Agent.insert_user_message`` (which appends
                    # to ``TaskState.pending_user_inserts`` for the
                    # ReAct loop to drain on the next post-tool tick).
                    # We don't bump generation, don't broadcast chat:busy,
                    # don't touch _idle_events — the old turn IS still
                    # running.
                    return StartResult(
                        conflict=existing,
                        generation=0,
                        policy_applied=policy,
                        steered=True,
                    )
                took_over = existing

            self._generation_counter += 1
            gen = self._generation_counter
            self._busy[conversation_id] = BusyInfo(
                client_id=client_id,
                generation=gen,
                turn_id=turn_id,
            )
            # FIX 3 (v1.27.14 b-cut): wake & drop the stale idle Event.
            #
            # Why ``set`` first then ``pop`` (instead of plain ``pop``)?
            # Consider this race:
            #
            #   1. A starts gen=1; A's other tab calls wait_for_idle
            #      (target_generation=1) → creates idle_events[conv]=ev →
            #      releases lock → blocks on ev.wait().
            #   2. A's third tab now fires INTERRUPT → here we are.
            #
            # If we just ``pop`` ev without setting it, the waiter from
            # step (1) blocks until ``timeout`` because finish() will
            # never see it.  By ``set`` ing it first, the waiter wakes
            # up; ``wait_for_idle`` returns True; the caller retries
            # ``start`` and either takes over (same-client INTERRUPT) or
            # gets a fresh 409 (cross-client), but is never stranded.
            ev_old = self._idle_events.pop(conversation_id, None)
            if ev_old is not None:
                ev_old.set()

        await self._broadcast(
            "chat:busy",
            {
                "conversation_id": conversation_id,
                "client_id": client_id,
                "generation": gen,
                "took_over_generation": took_over.generation if took_over else None,
            },
        )
        return StartResult(
            conflict=None,
            generation=gen,
            took_over=took_over,
            policy_applied=policy,
        )

    async def finish(
        self,
        conversation_id: str,
        generation: int | None = None,
    ) -> bool:
        """Release busy-lock and broadcast ``chat:idle``.

        *generation* guard: if provided, only releases when it matches the
        current lock.  This prevents a late-running ``_stream_chat`` finally
        from clearing a lock that was already handed to a newer request.

        When *generation* is ``None`` the lock is released unconditionally
        (used by explicit cancel / delete operations).

        Returns ``True`` if the lock was actually released.
        """
        async with self._lock:
            existing = self._busy.get(conversation_id)
            if existing is None:
                return False
            if generation is not None and existing.generation != generation:
                logger.debug(
                    "[Lifecycle] finish() skipped: generation mismatch "
                    "conv=%s current=%d requested=%d",
                    conversation_id,
                    existing.generation,
                    generation,
                )
                return False
            del self._busy[conversation_id]
            # FIX 3: signal any QUEUE-waiting caller that the conversation
            # is now idle.  We notify under the lock so a racing start()
            # cannot recreate _busy[conversation_id] between us deleting
            # the lock and waiters resuming.
            ev = self._idle_events.get(conversation_id)
            if ev is not None:
                ev.set()

        await self._broadcast(
            "chat:idle",
            {
                "conversation_id": conversation_id,
            },
        )
        return True

    async def wait_for_idle(
        self,
        conversation_id: str,
        *,
        target_generation: int | None = None,
        timeout: float = 30.0,
    ) -> bool:
        """Wait until ``conversation_id`` is idle (or already is).

        v1.27.14 follow-up (FIX 3): backs QUEUE-policy at the HTTP layer.
        After :meth:`start` returns a conflict with
        ``queued_after_generation``, the caller awaits this method,
        then retries :meth:`start`.

        Args:
            conversation_id: the conversation whose busy-lock to wait on.
            target_generation: if provided, only return when the
                in-flight generation has progressed past this value
                (i.e. the original holder has finished).  ``None``
                returns as soon as the conversation is idle.
            timeout: max seconds to block.

        Returns:
            ``True`` if idle within the timeout, ``False`` on timeout.
        """
        async with self._lock:
            existing = self._busy.get(conversation_id)
            if existing is None:
                return True
            if target_generation is not None and existing.generation != target_generation:
                # The conversation has already moved on past our target —
                # whoever we wanted to wait on is no longer the holder.
                return True
            ev = self._idle_events.get(conversation_id)
            if ev is None:
                ev = asyncio.Event()
                self._idle_events[conversation_id] = ev

        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        finally:
            # Best-effort cleanup so the dict doesn't grow forever on
            # abandoned waits; safe because start() also pops on its way in.
            async with self._lock:
                if conversation_id in self._idle_events:
                    cur = self._busy.get(conversation_id)
                    if cur is None:
                        self._idle_events.pop(conversation_id, None)

    async def get_busy_status(
        self,
        conversation_id: str = "",
    ) -> dict:
        """Query busy state — powers ``GET /api/chat/busy``."""
        async with self._lock:
            self._expire_stale()
            if conversation_id:
                info = self._busy.get(conversation_id)
                if info:
                    return {
                        "busy": True,
                        "conversation_id": conversation_id,
                        "client_id": info.client_id,
                        "since": info.start_time,
                    }
                return {"busy": False, "conversation_id": conversation_id}
            return {
                "busy_conversations": [
                    {
                        "conversation_id": cid,
                        "client_id": info.client_id,
                        "since": info.start_time,
                    }
                    for cid, info in self._busy.items()
                ],
            }

    # ── Internal ────────────────────────────────────────────────────────

    async def _broadcast(self, event: str, data: dict) -> None:
        try:
            from .websocket import broadcast_event

            await broadcast_event(event, data)
        except Exception:
            pass

    def _expire_stale(self) -> None:
        """Remove entries older than BUSY_TIMEOUT_SECONDS.  Caller holds ``self._lock``."""
        now = time.time()
        stale = [k for k, v in self._busy.items() if now - v.start_time > BUSY_TIMEOUT_SECONDS]
        for k in stale:
            logger.info("[Lifecycle] Auto-releasing stale busy lock: conv=%s", k)
            del self._busy[k]


# ── Module-level singleton ──────────────────────────────────────────────

_instance: ConversationLifecycleManager | None = None


def get_lifecycle_manager() -> ConversationLifecycleManager:
    """Return the singleton ``ConversationLifecycleManager``."""
    global _instance
    if _instance is None:
        _instance = ConversationLifecycleManager()
    return _instance
