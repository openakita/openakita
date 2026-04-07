"""Centralized conversation lifecycle manager.

Manages busy-lock state transitions and ensures consistent cleanup across
all exit paths (normal completion, cancel, delete, disconnect).

Previously, busy-lock logic was scattered across chat.py (_mark_busy,
_clear_busy) and only released in _stream_chat's finally block, which
caused stale "in-progress" states when conversations were cancelled or
deleted through different code paths.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_SECONDS = 600  # 10 min auto-release


@dataclass
class BusyInfo:
    client_id: str
    start_time: float = field(default_factory=time.time)
    generation: int = 0


class ConversationLifecycleManager:
    """All conversation state transitions go through this manager.

    ``start()`` / ``finish()`` pair guarantees that busy-lock release and
    ``chat:idle`` / ``chat:busy`` broadcasts happen consistently, regardless
    of whether the exit is via normal completion, user cancel, session
    deletion, or client disconnect.

    A monotonically increasing *generation* counter prevents stale
    ``_stream_chat`` finally blocks from accidentally releasing a lock
    that was already taken over by a newer request.
    """

    def __init__(self) -> None:
        self._busy: dict[str, BusyInfo] = {}
        self._lock = asyncio.Lock()
        self._generation_counter = 0

    # ── Public API ──────────────────────────────────────────────────────

    async def start(self, conversation_id: str, client_id: str) -> tuple[BusyInfo | None, int]:
        """Mark a conversation as busy.

        Returns ``(conflict, generation)``:
        - *conflict*: existing ``BusyInfo`` if a **different** client holds
          the lock, ``None`` on success.
        - *generation*: unique ID for this busy session.  Pass it to
          ``finish()`` so that stale callers don't accidentally release a
          newer lock.
        """
        async with self._lock:
            self._expire_stale()
            existing = self._busy.get(conversation_id)
            if existing and existing.client_id != client_id:
                return existing, 0
            self._generation_counter += 1
            gen = self._generation_counter
            self._busy[conversation_id] = BusyInfo(
                client_id=client_id,
                generation=gen,
            )

        await self._broadcast(
            "chat:busy",
            {
                "conversation_id": conversation_id,
                "client_id": client_id,
            },
        )
        return None, gen

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

        await self._broadcast(
            "chat:idle",
            {
                "conversation_id": conversation_id,
            },
        )
        return True

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
