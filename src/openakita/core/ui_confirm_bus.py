"""C9b: Cross-cutting state for SSE security_confirm flows.

This module isolates the **UI confirmation state machine** from the v1
``PolicyEngine`` so that:

1. ``reasoning_engine`` (yields ``security_confirm`` SSE), ``gateway``
   (renders IM cards / waits for text fallback), and ``api/routes/config``
   (``/api/chat/security-confirm`` POST endpoint) all share a single
   global event/decision/pending registry without depending on
   ``core/policy.py`` (which is being phased out in C8b).

2. ``policy.py`` no longer exposes any UI confirm facade methods (C8b-3).
   Production callers (CLI, web, IM adapters) call
   ``policy_v2.confirm_resolution.apply_resolution`` for resolve-with-side-effects
   or use ``get_ui_confirm_bus()`` directly for prepare/wait. The bus stays
   minimal — no allowlist writes here, by design.

3. The bus is a **module-level singleton** — it survives ``reset_policy_engine``
   automatically, fixing the C7 audit-2 regression where the v1 engine
   reset would lose pending UI confirms (previously patched by manual
   field-by-field copy in ``reset_policy_engine``; that copy is now
   redundant and removed in C9b).

State owned by the bus
======================

- ``_events: dict[str, asyncio.Event]`` — wakeup primitive per ``confirm_id``.
- ``_decisions: dict[str, str]`` — "allow_once" / "deny" / etc., set by
  ``resolve()`` and read by ``wait_for_resolution()``.
- ``_pending: dict[str, dict]`` — sidecar payload (tool_name, params,
  session_id, needs_sandbox, created_at) registered by ``store_pending()``;
  consumed by ``resolve()`` and returned to the caller so
  ``policy_v2.confirm_resolution.apply_resolution`` can decide whether to
  also write to ``SessionAllowlistManager`` / ``UserAllowlistManager``
  based on the user's choice.

Coupling note
=============

C8b-3: the bus is fully decoupled from any allowlist manager. ``resolve()``
just wakes the waiter and returns the pending dict; the side-effect of
"this user choice should be remembered for the session/forever" is the
job of ``policy_v2.confirm_resolution.apply_resolution``, which the
production resolve callsites all funnel through.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# Default TTL for pending UI confirms; ``configure_ttl`` lets the policy
# engine push the user-configured ``confirmation.confirm_ttl`` value in.
_DEFAULT_TTL_SECONDS: int = 300


class UIConfirmBus:
    """Single source of truth for SSE security confirm events."""

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, str] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._ttl_seconds = ttl_seconds

    # ----- TTL configuration ------------------------------------------------

    def configure_ttl(self, ttl_seconds: int) -> None:
        """Push the policy engine's configured TTL into the bus.

        Called by ``PolicyEngine.__init__`` so the bus's pending-confirm
        garbage-collection matches the user-configured ``confirm_ttl``.
        """
        if ttl_seconds and ttl_seconds > 0:
            self._ttl_seconds = int(ttl_seconds)

    # ----- Pending sidecar (used by IM card renderer + decision replay) ----

    def store_pending(
        self,
        tool_id: str,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
        needs_sandbox: bool = False,
    ) -> None:
        """Register the sidecar payload for a confirm_id about to be SSE'd.

        Must be called by the SSE producer **before** the event is yielded,
        so the resolver (gateway IM card click / web modal POST) can pop
        it back out.
        """
        self._cleanup_expired()
        self._pending[tool_id] = {
            "tool_name": tool_name,
            "params": params,
            "created_at": time.time(),
            "session_id": session_id,
            "needs_sandbox": needs_sandbox,
        }

    def list_pending(self) -> list[dict[str, Any]]:
        """Diagnostic accessor (SecurityView debug panel / tests)."""
        return [{"id": k, **v} for k, v in self._pending.items()]

    def cleanup_session(self, session_id: str) -> None:
        """Drop all pending confirms tied to the given session."""
        to_remove = [
            k for k, v in self._pending.items() if v.get("session_id") == session_id
        ]
        for k in to_remove:
            self._pending.pop(k, None)

    def _cleanup_expired(self) -> None:
        """Garbage-collect pending confirms older than ``_ttl_seconds``."""
        now = time.time()
        expired = [
            k
            for k, v in self._pending.items()
            if now - v.get("created_at", 0) > self._ttl_seconds
        ]
        for k in expired:
            self._pending.pop(k, None)

    # ----- Event wait/resolve cycle -----------------------------------------

    def prepare(self, confirm_id: str) -> None:
        """Register the wakeup ``asyncio.Event`` for ``confirm_id``.

        **Idempotent** — if an event is already registered for this id and
        no decision has been written yet, reuse it. This was added in C8a
        because ``reasoning_engine`` and ``gateway`` (IM path) both call
        ``prepare`` for the same confirm_id; replacing the event would
        orphan the first waiter.
        """
        if not confirm_id:
            return
        existing = self._events.get(confirm_id)
        if existing is not None and confirm_id not in self._decisions:
            return
        self._events[confirm_id] = asyncio.Event()
        self._decisions.pop(confirm_id, None)

    def cleanup(self, confirm_id: str) -> None:
        """Drop both the event and the resolved decision for an id."""
        if not confirm_id:
            return
        self._events.pop(confirm_id, None)
        self._decisions.pop(confirm_id, None)

    def resolve(self, confirm_id: str, decision: str) -> dict[str, Any] | None:
        """Wake any waiter and record the decision.

        Returns the popped pending sidecar (with normalized ``decision`` and
        effective ``needs_sandbox``), or ``None`` if no pending was
        registered (the SSE was never emitted, or already resolved).

        Callers usually call ``policy_v2.confirm_resolution.apply_resolution``
        instead of this method directly — that helper threads the returned
        dict into ``SessionAllowlistManager`` / ``UserAllowlistManager``
        based on the user's choice.
        """
        pending = self._pending.pop(confirm_id, None)

        # Normalize legacy values
        if decision == "allow":
            decision = "allow_once"

        # Ensure waiter wakes regardless of whether a pending sidecar
        # existed (gateway / API may resolve a confirm whose pending
        # was already GC'd, but a wait_for_resolution coroutine could
        # still be parked on the event).
        if confirm_id in self._events and confirm_id not in self._decisions:
            self._decisions[confirm_id] = decision
            ev = self._events.get(confirm_id)
            if ev is not None:
                ev.set()

        if pending is None:
            return None

        needs_sandbox = pending.get("needs_sandbox", False)
        if decision == "sandbox":
            needs_sandbox = True

        return {
            **pending,
            "decision": decision,
            "needs_sandbox": needs_sandbox,
        }

    async def wait_for_resolution(self, confirm_id: str, timeout: float) -> str:
        """Block until ``resolve(confirm_id, ...)`` is called or ``timeout`` hits.

        Timeout falls back to ``"deny"`` (and synthesizes a ``resolve("deny")``
        so any orphan waiters / sidecar entries are also cleaned).
        """
        if not confirm_id:
            return "deny"
        ev = self._events.get(confirm_id)
        if ev is None:
            return self._decisions.get(confirm_id, "deny")
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except TimeoutError:
            if confirm_id not in self._decisions:
                # Auto-deny on timeout. Important: don't bypass ``resolve()``
                # — call it so the pending sidecar is also popped.
                self.resolve(confirm_id, "deny")
        return self._decisions.get(confirm_id, "deny")


# ---------------------------------------------------------------------------
# Module-level singleton accessor (survives PolicyEngine reset)
# ---------------------------------------------------------------------------

_global_bus: UIConfirmBus | None = None


def get_ui_confirm_bus() -> UIConfirmBus:
    """Lazy global singleton."""
    global _global_bus
    if _global_bus is None:
        _global_bus = UIConfirmBus()
    return _global_bus


def reset_ui_confirm_bus() -> None:
    """Test-only reset hook. Production code should never call this — the
    bus surviving ``reset_policy_engine`` is intentional (a UI confirm
    in flight when the user saves a config change should still wake up).
    """
    global _global_bus
    _global_bus = None


__all__ = [
    "UIConfirmBus",
    "get_ui_confirm_bus",
    "reset_ui_confirm_bus",
]
