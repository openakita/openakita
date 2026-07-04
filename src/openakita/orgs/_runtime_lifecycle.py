"""``_runtime_lifecycle.py`` -- v2 OrgRuntime lifecycle sibling (P9.6d).

Lifts the org start / stop / restart / health surface out of
v1 ``OrgRuntime`` (v1 methods absorbed: ``start``,
``shutdown``, ``start_org``, ``stop_org``, ``delete_org``,
``reset_org``, ``pause_org``, ``resume_org``,
``_activate_org``, ``_deactivate_org``,
``_stop_org_services``, ``_cancel_org_tasks``,
``mark_org_stopped``, ``is_org_recently_stopped``,
``_soft_stop_org`` -- approximately 18 v1 methods, ~500 LOC).

The v2 implementation collapses these into a focused
:class:`OrgLifecycleManager` that operates on **state +
emitter callbacks** -- the heavy logic (cancelling in-flight
tasks, draining mailboxes, recovering pending tasks) stays
delegated to the dispatch / node-lifecycle siblings via
injected callbacks. The result is a clean state machine
that the OrgRuntime singleton wires up at ``start()`` time.

This commit lands the lifecycle scaffolding + state machine;
``OrgRuntime`` integration (wiring CommandService /
NodeScheduler / Watchdog / IdleProbeLoop start / stop calls)
rides P9.6beta when those siblings have real bodies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import time
from typing import Any

from .runtime import EventBusProtocol, RuntimeStateProtocol

_LOGGER = logging.getLogger(__name__)

# Org state machine constants -- parity with v1
# ``openakita.orgs.models.OrgStatus`` semantics.
STATE_CREATED = "CREATED"
STATE_ACTIVE = "ACTIVE"
STATE_PAUSED = "PAUSED"
STATE_STOPPED = "STOPPED"
STATE_DELETED = "DELETED"

# Recently-stopped grace window (v1 ``is_org_recently_stopped``
# returns True for orgs stopped within 15 minutes).
_RECENTLY_STOPPED_WINDOW_SECS = 15 * 60

_VALID_TRANSITIONS: dict[str, set[str]] = {
    STATE_CREATED: {STATE_ACTIVE, STATE_DELETED},
    STATE_ACTIVE: {STATE_PAUSED, STATE_STOPPED, STATE_DELETED},
    STATE_PAUSED: {STATE_ACTIVE, STATE_STOPPED, STATE_DELETED},
    STATE_STOPPED: {STATE_ACTIVE, STATE_DELETED},
    STATE_DELETED: set(),  # terminal
}


class IllegalOrgTransition(RuntimeError):
    """Raised when a state transition violates the table above."""


class OrgLifecycleManager:
    """State-machine + DI-callback orchestrator for org lifecycle.

    Constructor args:

    * ``state`` -- :class:`RuntimeStateProtocol` backing
      store (defaults to ``runtime.py``''s
      ``_InMemoryRuntimeState`` if the OrgRuntime wires it).
    * ``event_bus`` -- :class:`EventBusProtocol` for
      lifecycle event emission (org_started /
      org_stopped / org_deleted / org_paused / org_resumed).
    * ``on_start_org`` -- async callback the manager
      awaits after the transition lands (lets dispatch +
      node-lifecycle siblings spin up tasks).
    * ``on_stop_org`` -- async callback awaited before the
      transition lands (lets siblings drain mailboxes /
      cancel in-flight work).
    """

    def __init__(
        self,
        state: RuntimeStateProtocol,
        event_bus: EventBusProtocol,
        *,
        on_start_org: Callable[[str], Awaitable[None]] | None = None,
        on_stop_org: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._state = state
        self._event_bus = event_bus
        self._on_start_org = on_start_org
        self._on_stop_org = on_stop_org
        self._recently_stopped: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def set_on_stop_org(
        self, callback: Callable[[str, str], Awaitable[None]] | None
    ) -> None:
        """Late-bind the stop-org callback after construction.

        Sprint-5 P0-2 (audit ``_orgs_business_capability_audit_v5.md``
        §5.2 #1): the composition root in ``api/server.py`` constructs
        :class:`OrgRuntime` (which builds this lifecycle manager) *before*
        the :class:`OrgCommandService` exists, but the
        ``stop_org_propagates_cancel`` wiring needs a reference to the
        command service so it can iterate its ``_inflight_by_org`` index.
        Exposing a setter avoids re-ordering the lifespan, which would
        ripple into the executor / dispatch wiring.

        The callback follows the constructor contract: async,
        ``(org_id, reason) -> None``, exceptions are swallowed by the
        caller (``stop_org``).
        """

        if callback is not None and not callable(callback):
            raise TypeError("on_stop_org must be callable when provided")
        self._on_stop_org = callback

    # ------------------------------------------------------------------
    # Transition primitives (private; called by the public verbs below)
    # ------------------------------------------------------------------

    def _check_transition(self, current: str | None, target: str) -> None:
        """Raise IllegalOrgTransition if ``current -> target`` is
        not in :data:`_VALID_TRANSITIONS`. ``current=None`` is
        treated as a fresh create -> ACTIVE path.
        """

        if current is None:
            if target not in {STATE_ACTIVE, STATE_DELETED}:
                raise IllegalOrgTransition(f"cannot create-and-transition new org to {target!r}")
            return
        if current == target:  # idempotent
            return
        if target not in _VALID_TRANSITIONS.get(current, set()):
            raise IllegalOrgTransition(f"illegal org transition: {current!r} -> {target!r}")

    async def _emit_lifecycle(self, event: str, org_id: str, **extra: Any) -> None:
        payload = {"org_id": org_id, "at": time(), **extra}
        await self._event_bus.emit(event, payload)
        await self._event_bus.broadcast_ws(event, payload)

    # ------------------------------------------------------------------
    # Public verbs
    # ------------------------------------------------------------------

    async def start_org(self, org_id: str) -> bool:
        """Transition org -> ACTIVE; invoke on_start_org callback."""

        async with self._lock:
            current = self._state.get_org_state(org_id)
            self._check_transition(current, STATE_ACTIVE)
            if current == STATE_ACTIVE:
                return True  # idempotent; do not re-fire callback / event
            ok = await self._state.transition_org_state(org_id, STATE_ACTIVE)
        if not ok:
            return False
        if self._on_start_org is not None:
            try:
                await self._on_start_org(org_id)
            except Exception:  # noqa: BLE001 (v1 parity)
                _LOGGER.exception("on_start_org callback raised (org=%s)", org_id)
        await self._emit_lifecycle("org_started", org_id)
        return True

    async def stop_org(self, org_id: str, *, reason: str = "stop") -> bool:
        """Force-stop: drain in-flight work, then land the STOPPED terminal.

        test17 issue A: stop is a FORCE operation and is deliberately NOT bound
        by the normal ``_check_transition`` table. An org that was loaded from
        disk but never (re)activated in this runtime process (e.g. right after a
        backend restart) has ``get_org_state() is None``; the strict table has
        no ``None``/``CREATED`` -> ``STOPPED`` edge, so the old code raised
        ``IllegalOrgTransition`` ("cannot create-and-transition new org to
        'STOPPED'") and the stop button failed -- leaving the running command
        uncancellable. Stopping must ALWAYS be able to cancel in-flight work and
        land a terminal state; only an already-``DELETED`` org (terminal, gone)
        is refused. The drain callback runs first and unconditionally, so a
        running command is cancelled even when the state map is empty.
        """

        if self._on_stop_org is not None:
            try:
                await self._on_stop_org(org_id, reason)
            except Exception:  # noqa: BLE001 (v1 parity: never block stop)
                _LOGGER.exception("on_stop_org callback raised (org=%s)", org_id)
        async with self._lock:
            current = self._state.get_org_state(org_id)
            if current == STATE_DELETED:
                return False  # terminal / gone -- nothing to stop
            already_stopped = current == STATE_STOPPED
            ok = (
                True
                if already_stopped
                else await self._state.transition_org_state(
                    org_id, STATE_STOPPED, reason=reason
                )
            )
            self._recently_stopped[org_id] = time()
        # Emit only on a real transition so an idempotent double-stop stays quiet.
        if ok and not already_stopped:
            await self._emit_lifecycle("org_stopped", org_id, reason=reason)
        return ok

    async def pause_org(self, org_id: str) -> bool:
        async with self._lock:
            current = self._state.get_org_state(org_id)
            self._check_transition(current, STATE_PAUSED)
            ok = await self._state.transition_org_state(org_id, STATE_PAUSED)
        if ok:
            await self._emit_lifecycle("org_paused", org_id)
        return ok

    async def resume_org(self, org_id: str) -> bool:
        async with self._lock:
            current = self._state.get_org_state(org_id)
            self._check_transition(current, STATE_ACTIVE)
            ok = await self._state.transition_org_state(org_id, STATE_ACTIVE)
        if ok:
            await self._emit_lifecycle("org_resumed", org_id)
        return ok

    async def restart_org(self, org_id: str, *, reason: str = "restart") -> bool:
        """Stop + start in one verb; idempotent w.r.t. already-stopped."""

        if self._state.is_org_active(org_id):
            await self.stop_org(org_id, reason=reason)
        return await self.start_org(org_id)

    async def delete_org(self, org_id: str) -> bool:
        """Permanently transition -> DELETED (terminal)."""

        async with self._lock:
            current = self._state.get_org_state(org_id)
            self._check_transition(current, STATE_DELETED)
            ok = await self._state.transition_org_state(org_id, STATE_DELETED)
        if ok:
            self._recently_stopped.pop(org_id, None)
            await self._emit_lifecycle("org_deleted", org_id)
        return ok

    # ------------------------------------------------------------------
    # Health-check + recently-stopped query (v1 parity)
    # ------------------------------------------------------------------

    def health_check(self, org_id: str) -> dict[str, Any]:
        """Compact view of the org''s lifecycle state for status readers."""

        current = self._state.get_org_state(org_id)
        return {
            "org_id": org_id,
            "state": current,
            "is_active": current == STATE_ACTIVE,
            "is_recently_stopped": self.is_org_recently_stopped(org_id),
            "at": time(),
        }

    def is_org_recently_stopped(self, org_id: str) -> bool:
        """Parity with v1 ``OrgRuntime.is_org_recently_stopped``."""

        stopped_at = self._recently_stopped.get(org_id)
        if stopped_at is None:
            return False
        return (time() - stopped_at) <= _RECENTLY_STOPPED_WINDOW_SECS

    def mark_org_stopped(self, org_id: str) -> None:
        """Parity with v1 ``OrgRuntime.mark_org_stopped``: stamp the grace window."""

        self._recently_stopped[org_id] = time()


__all__ = [
    "IllegalOrgTransition",
    "OrgLifecycleManager",
    "STATE_ACTIVE",
    "STATE_CREATED",
    "STATE_DELETED",
    "STATE_PAUSED",
    "STATE_STOPPED",
]
