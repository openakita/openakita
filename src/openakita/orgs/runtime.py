"""OrgRuntime v2 Protocol + default-backend layer (P-RC-9 P9.6a0).

This is the **largest** of ADR-0011''s six Protocol-typed
subsystems. The v1 ``src/openakita/orgs/runtime.py`` is 6 355
LOC across 132 methods on a single ``OrgRuntime`` class; the
v2 rewrite splits the responsibility across ``runtime.py``
(this file: 3 NEW Protocols + 3 default in-memory backends +
[P9.6a] the ``OrgRuntime`` skeleton + ``CommandRuntimeProtocol``
surface) plus 7 sibling underscore-prefixed modules under
``runtime/orgs/`` (each <= 500 LOC per ADR-0014).

This commit (P9.6a0) lands the Protocol + default-backend
layer:

* Three NEW Protocols (each <= 5 methods per ADR-0011
  granularity ceiling):

  - :class:`RuntimeStateProtocol` (4 methods) -- org + node
    state machine ops (start / stop / get / is_active).
  - :class:`NodeLifecycleProtocol` (5 methods) -- per-node
    status transitions + message routing hook.
  - :class:`EventBusProtocol` (4 methods) -- pub / sub /
    broadcast for org + node lifecycle events.

* Default in-memory backends for the three new Protocols
  (sufficient for the unit / parity / contract suites and for
  smoke runs; production wiring composes the same Protocols
  with persistent / WebSocket-bridged backends).

The ``OrgRuntime`` class itself lands in P9.6a (next commit),
composing the 6 reused Protocols (from P9.1 / P9.3 / P9.4 /
P9.5) + these 3 new Protocols + implementing
``CommandRuntimeProtocol`` (P9.4 contract). Subsequent siblings
(``_runtime_event_bus.py`` P9.6b, ``_runtime_watchdog.py`` P9.6c,
``_runtime_lifecycle.py`` P9.6d) ride this turn; the heavy
siblings + parity + contract + G-RC-9.6 mini-gate ride
P9.6beta / P9.6gamma.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .blackboard import BlackboardBackendProtocol
from .command_service import OrgCommandServiceProtocol, OrgLookupProtocol
from .manager import OrgLifecycleEmitterProtocol, OrgPersistenceProtocol
from .node_scheduler import NodeSchedulerProtocol

if TYPE_CHECKING:  # pragma: no cover -- forward ref only
    from ._runtime_dispatch import CommandDispatchManager

_LOGGER = logging.getLogger(__name__)

# H3 / H4 (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
# callback signatures wired through ``OrgRuntime.__init__`` into the
# dispatch sibling. They live here (not in ``_runtime_dispatch``) so
# the runtime composition root can name them in keyword arguments
# without dragging the dispatch import into the public surface.
_AgentDispatchCb = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]
_ChainCancelCb = Callable[[str, str, str], Awaitable[None]]
_EventTap = Callable[[str, dict[str, Any]], Any]


# =====================================================================
# Three new Protocols (P9.6; each <= 5 methods per ADR-0011)
# =====================================================================


@runtime_checkable
class RuntimeStateProtocol(Protocol):
    """Org + node state machine surface (4 methods).

    Implementations track per-org running / paused / stopped
    states and the per-node IDLE / BUSY / ERROR transitions
    that drive the lifecycle + watchdog siblings.

    Default backend: :class:`_InMemoryRuntimeState` (this file).
    Production may swap in a SQLite-backed implementation
    once P-RC-10 hygiene runs land.
    """

    async def transition_org_state(
        self, org_id: str, target: str, *, reason: str | None = None
    ) -> bool: ...

    async def transition_node_state(
        self, org_id: str, node_id: str, target: str, *, reason: str | None = None
    ) -> bool: ...

    def get_org_state(self, org_id: str) -> str | None: ...

    def is_org_active(self, org_id: str) -> bool: ...


@runtime_checkable
class NodeLifecycleProtocol(Protocol):
    """Per-node lifecycle surface (5 methods).

    Implementations own the node status field on the
    ``Organization`` snapshot + the inbound message routing
    hook the messenger calls into.

    Default backend: :class:`_InMemoryNodeLifecycle`. Production
    composes with :class:`RuntimeStateProtocol` for the
    transition primitives.
    """

    async def set_node_status(
        self, org_id: str, node_id: str, new_status: str, *, reason: str | None = None
    ) -> None: ...

    def get_node_status(self, org_id: str, node_id: str) -> str | None: ...

    async def on_node_message(self, org_id: str, node_id: str, msg: Any) -> None: ...

    def register_node(self, org_id: str, node_id: str) -> None: ...

    def deregister_node(self, org_id: str, node_id: str) -> None: ...


@runtime_checkable
class EventBusProtocol(Protocol):
    """Pub / sub surface for org + node lifecycle (4 methods).

    Implementations fan events out to in-process subscribers
    (:meth:`subscribe` / :meth:`unsubscribe`) and to the
    WebSocket bridge (:meth:`broadcast_ws`). Default backend:
    :class:`_InMemoryEventBus`.
    """

    async def emit(self, event: str, payload: dict[str, Any]) -> None: ...

    async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None: ...

    def subscribe(self, event: str, handler: Callable[[dict[str, Any]], Any]) -> None: ...

    def unsubscribe(self, event: str, handler: Callable[[dict[str, Any]], Any]) -> None: ...


# =====================================================================
# Default in-memory backends (P9.6a; sufficient for unit / parity tests)
# =====================================================================


class _InMemoryRuntimeState:
    """Dict-backed :class:`RuntimeStateProtocol` (default).

    Parity-faithful to v1 ``OrgRuntime`` semantics: an org is
    "active" iff a ``start_org`` transition succeeded since
    the last ``stop_org``; node statuses default to ``IDLE``.
    """

    def __init__(self) -> None:
        self._org_states: dict[str, str] = {}
        self._node_states: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def transition_org_state(
        self, org_id: str, target: str, *, reason: str | None = None
    ) -> bool:
        async with self._lock:
            self._org_states[org_id] = target
        return True

    async def transition_node_state(
        self, org_id: str, node_id: str, target: str, *, reason: str | None = None
    ) -> bool:
        async with self._lock:
            self._node_states[(org_id, node_id)] = target
        return True

    def get_org_state(self, org_id: str) -> str | None:
        return self._org_states.get(org_id)

    def is_org_active(self, org_id: str) -> bool:
        return self._org_states.get(org_id) == "ACTIVE"


class _InMemoryNodeLifecycle:
    """Dict-backed :class:`NodeLifecycleProtocol` (default)."""

    def __init__(self, state: RuntimeStateProtocol | None = None) -> None:
        self._state = state
        self._registered: set[tuple[str, str]] = set()
        self._statuses: dict[tuple[str, str], str] = {}

    async def set_node_status(
        self, org_id: str, node_id: str, new_status: str, *, reason: str | None = None
    ) -> None:
        self._statuses[(org_id, node_id)] = new_status
        if self._state is not None:
            await self._state.transition_node_state(org_id, node_id, new_status, reason=reason)

    def get_node_status(self, org_id: str, node_id: str) -> str | None:
        return self._statuses.get((org_id, node_id))

    async def on_node_message(self, org_id: str, node_id: str, msg: Any) -> None:
        # P9.6a: default backend is a sink; production wiring overrides via
        # _runtime_node_lifecycle.py (P9.6beta).
        return None

    def register_node(self, org_id: str, node_id: str) -> None:
        self._registered.add((org_id, node_id))
        self._statuses.setdefault((org_id, node_id), "IDLE")

    def deregister_node(self, org_id: str, node_id: str) -> None:
        self._registered.discard((org_id, node_id))
        self._statuses.pop((org_id, node_id), None)


class _InMemoryEventBus:
    """In-process :class:`EventBusProtocol` (default).

    H4 fix (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
    in addition to the per-event-name pub/sub surface required by
    :class:`EventBusProtocol`, this default backend now exposes a
    wildcard "tap" surface (:meth:`add_tap` / :meth:`remove_tap`)
    so the runtime composition root can plug bridges that observe
    every event regardless of name (persist to ``OrgEventStore``,
    forward to per-org ``StreamBus``). Taps are isolated by
    try/except so a failing sink cannot poison the dispatch loop.
    The named subscriber surface is unchanged for back-compat with
    existing P9.6gamma contract tests.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[dict[str, Any]], Any]]] = defaultdict(list)
        self._taps: list[_EventTap] = []

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in list(self._subs.get(event, ())):
            res = handler(payload)
            if asyncio.iscoroutine(res):
                await res
        for tap in list(self._taps):
            try:
                res = tap(event, payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 -- taps must not poison dispatch
                _LOGGER.warning(
                    "event-bus tap raised for event=%r; sink isolated", event, exc_info=True
                )

    async def broadcast_ws(self, event: str, data: dict[str, Any]) -> None:
        # P9.6a: default backend is a no-op; production wiring overrides
        # via _runtime_event_bus.py (P9.6b lands real WS bridging).
        return None

    def subscribe(self, event: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._subs[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    def add_tap(self, tap: _EventTap) -> None:
        """Register a wildcard observer that sees every emitted event.

        Tap signature: ``(event_name: str, payload: dict) -> None |
        Awaitable[None]``. The bus catches and logs any exception so a
        failing sink cannot block other taps or the named subscribers.
        H4 hook for OrgEventStore / StreamBus forwarding.
        """

        self._taps.append(tap)

    def remove_tap(self, tap: _EventTap) -> None:
        if tap in self._taps:
            self._taps.remove(tap)


# =====================================================================
# OrgRuntime -- P9.6a scaffold (bodies ride P9.6alpha-d + P9.6beta)
# =====================================================================


class OrgRuntime:
    """v2 OrgRuntime -- charter subsystem #6 of ADR-0011.

    **Implements** :class:`CommandRuntimeProtocol` (the P9.4
    contract :class:`OrgCommandService` consumes -- closes the
    P9.4 dependency loop).

    **Composes** (DI via ``__init__``) the 6 reused Protocols
    + 3 new Protocols listed in the module docstring. The
    skeleton + ``__init__`` land in P9.6a; the 4 sibling
    managers land in P9.6alpha-d (event-bus / watchdog /
    lifecycle) and P9.6beta-e/f/g/h (dispatch / agent
    pipeline / node lifecycle / plugin assets). P9.6i wires
    :class:`CommandDispatchManager` into ``__init__`` so the
    4 :class:`CommandRuntimeProtocol` methods are real
    delegations (no more ``NotImplementedError``).
    """

    def __init__(
        self,
        *,
        # Reused Protocols (composition from prior P9.x):
        lookup: OrgLookupProtocol,
        persistence: OrgPersistenceProtocol,
        lifecycle_emitter: OrgLifecycleEmitterProtocol,
        command_service: OrgCommandServiceProtocol | None = None,
        node_scheduler: NodeSchedulerProtocol | None = None,
        blackboard_backend: BlackboardBackendProtocol | None = None,
        # New Protocols (P9.6; defaults to in-memory backends):
        state: RuntimeStateProtocol | None = None,
        node_lifecycle: NodeLifecycleProtocol | None = None,
        event_bus: EventBusProtocol | None = None,
        # P9.6beta -- the dispatch manager that backs the 4
        # CommandRuntimeProtocol methods. Defaults to a
        # locally-constructed in-process dispatch sibling.
        dispatch: CommandDispatchManager | None = None,
        # H3 / H4 (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
        # composition-root hooks the API server lifespan plugs in so the
        # AgentPipelineExecutor actually fires per dispatch and so the
        # in-memory bus events get forwarded to OrgEventStore / StreamBus.
        # All optional + default-None to keep every existing OrgRuntime
        # callsite (contract / parity / api wiring tests) working.
        agent_dispatch: _AgentDispatchCb | None = None,
        chain_cancel: _ChainCancelCb | None = None,
    ) -> None:
        self._lookup = lookup
        self._persistence = persistence
        self._lifecycle_emitter = lifecycle_emitter
        self._command_service = command_service
        self._node_scheduler = node_scheduler
        self._blackboard_backend = blackboard_backend
        self._state: RuntimeStateProtocol = state if state is not None else _InMemoryRuntimeState()
        self._node_lifecycle: NodeLifecycleProtocol = (
            node_lifecycle if node_lifecycle is not None else _InMemoryNodeLifecycle(self._state)
        )
        self._event_bus: EventBusProtocol = (
            event_bus if event_bus is not None else _InMemoryEventBus()
        )
        self._agent_dispatch = agent_dispatch
        self._chain_cancel = chain_cancel
        # P9.6beta -- compose the dispatch sibling so the
        # 4 CommandRuntimeProtocol methods below have a
        # real backing manager (no more NotImplementedError).
        # The agent-pipeline / node-lifecycle / plugin-asset
        # managers are reachable via ``openakita.orgs``
        # exports and get wired into the runtime by the
        # composition root (P9.6gamma will exercise this via
        # parity fixtures + contract tests).
        from ._runtime_dispatch import CommandDispatchManager  # local import: avoid cycle

        self._dispatch: CommandDispatchManager = (
            dispatch
            if dispatch is not None
            else CommandDispatchManager(
                command_service=self._command_service,
                lookup=self._lookup,
                event_bus=self._event_bus,
                agent_dispatch=agent_dispatch,
                chain_cancel=chain_cancel,
            )
        )
        # smoke-B5 -- compose the lifecycle sibling so the
        # B34-B37 router endpoints (POST /{id}/start /stop /pause /resume)
        # have real backing methods.  Without this, the dispatch route
        # in ``orgs_v2_runtime_dispatch._call_lifecycle`` returned 503
        # ``OrgRuntime.start_org not wired`` because ``getattr(rt,
        # 'start_org', None)`` resolved to None.
        from ._runtime_lifecycle import OrgLifecycleManager  # local import: avoid cycle

        self._lifecycle: OrgLifecycleManager = OrgLifecycleManager(
            state=self._state,
            event_bus=self._event_bus,
        )
        # Per-org accessors backing the OrgLookupProtocol +
        # CommandRuntimeProtocol surfaces. Populated lazily by
        # the lifecycle sibling (P9.6d).
        self._event_stores: dict[str, Any] = {}
        self._inboxes: dict[str, Any] = {}
        self._watchdog_tasks: dict[str, asyncio.Task[None]] = {}
        self._idle_probe_tasks: dict[str, asyncio.Task[None]] = {}

        # H4 fix (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
        # bridge the in-process dispatch event-bus to two long-lived
        # sinks the rest of the API expects to see populated:
        #
        # * ``OrgEventStore`` (per-org JSONL at
        #   ``data/orgs/<id>/logs/events.jsonl``) -- backs
        #   ``GET /api/v2/orgs/{id}/{events,activity,audit-log}``.
        # * Per-org ``StreamBus`` (built lazily by
        #   ``runtime/stream_registry.py``) -- backs
        #   ``GET /api/v2/orgs-spec/{id}/stream`` (SSE).
        #
        # Pre-fix both sinks were idle for every command (24 mint orgs
        # all had 0-line events.jsonl; the SSE stream only emitted
        # ``: ping``). Duck-typed against ``add_tap`` so injected
        # bus implementations that don't support wildcard observation
        # silently skip the bridge instead of raising.
        register_tap = getattr(self._event_bus, "add_tap", None)
        if callable(register_tap):
            register_tap(self._persist_event_tap)
            register_tap(self._stream_event_tap)

    # ------------------------------------------------------------------
    # OrgLookupProtocol delegation (Protocol satisfied via composition)
    # ------------------------------------------------------------------

    def get_org(self, org_id: str) -> Any:
        return self._lookup.get_org(org_id)

    # ------------------------------------------------------------------
    # CommandRuntimeProtocol -- 6 stub methods (P9.6beta fills bodies)
    # ------------------------------------------------------------------

    async def send_command(
        self,
        org_id: str,
        target_node_id: str,
        content: str,
        *,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """v1 ``OrgRuntime.send_command`` parity (delegates to dispatch sibling P9.6e).

        H2 fix (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
        accept the optional ``command_id`` kwarg and forward it to the
        dispatch sibling so the OrgCommandService-minted id stays
        attached to the tracker. ``None`` preserves the legacy
        submit-or-mint fallback for callsites (node-scheduler /
        contract tests) that do not pre-mint an id.
        """

        return await self._dispatch.send_command(
            org_id,
            target_node_id,
            content,
            command_id=command_id,
        )

    async def cancel_user_command(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        """v1 ``OrgRuntime.cancel_user_command`` parity (delegates to dispatch sibling P9.6e)."""

        return await self._dispatch.cancel_user_command(org_id, command_id)

    def has_active_delegations(self, org_id: str, root_node_id: str) -> bool:
        """v1 ``OrgRuntime._has_active_delegations`` parity (delegates to dispatch sibling P9.6e)."""

        return self._dispatch.has_active_delegations(org_id, root_node_id)

    def get_command_tracker_snapshot(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        """v1 ``OrgRuntime.get_command_tracker_snapshot`` parity (delegates to dispatch sibling P9.6e)."""

        return self._dispatch.get_command_tracker_snapshot(org_id, command_id)

    # ------------------------------------------------------------------
    # Lifecycle verbs (smoke-B5 wire-up) -- delegate to OrgLifecycleManager
    # ------------------------------------------------------------------

    async def start_org(self, org_id: str) -> dict[str, Any]:
        """Transition org -> ACTIVE (B34).

        Returns a v1-shape envelope ``{'status': 'active', 'ok': bool}``
        so the API layer's ``_to_dict`` shim is a no-op.  Raises
        :class:`ValueError` on illegal transitions (mapped to HTTP 400
        by ``_call_lifecycle`` in the dispatch route).
        """
        from ._runtime_lifecycle import IllegalOrgTransition  # local import

        try:
            ok = await self._lifecycle.start_org(org_id)
        except IllegalOrgTransition as exc:
            raise ValueError(str(exc)) from exc
        return {"ok": ok, "status": self._state.get_org_state(org_id) or "unknown"}

    async def stop_org(self, org_id: str, *, reason: str = "stop") -> dict[str, Any]:
        """Transition org -> STOPPED (B35)."""
        from ._runtime_lifecycle import IllegalOrgTransition  # local import

        try:
            ok = await self._lifecycle.stop_org(org_id, reason=reason)
        except IllegalOrgTransition as exc:
            raise ValueError(str(exc)) from exc
        return {"ok": ok, "status": self._state.get_org_state(org_id) or "unknown"}

    async def pause_org(self, org_id: str) -> dict[str, Any]:
        """Transition org -> PAUSED (B36)."""
        from ._runtime_lifecycle import IllegalOrgTransition  # local import

        try:
            ok = await self._lifecycle.pause_org(org_id)
        except IllegalOrgTransition as exc:
            raise ValueError(str(exc)) from exc
        return {"ok": ok, "status": self._state.get_org_state(org_id) or "unknown"}

    async def resume_org(self, org_id: str) -> dict[str, Any]:
        """Transition org -> ACTIVE from PAUSED (B37)."""
        from ._runtime_lifecycle import IllegalOrgTransition  # local import

        try:
            ok = await self._lifecycle.resume_org(org_id)
        except IllegalOrgTransition as exc:
            raise ValueError(str(exc)) from exc
        return {"ok": ok, "status": self._state.get_org_state(org_id) or "unknown"}

    def set_on_stop_org(self, callback: Any) -> None:
        """Sprint-5 P0-2 passthrough: late-bind the stop-org callback.

        The :class:`OrgLifecycleManager` already exposes the setter; this
        wrapper hides the private ``_lifecycle`` attribute from the
        composition root, which keeps the v1 ``OrgRuntime`` shape clean
        and lets us evolve the lifecycle owner without touching every
        caller. See :meth:`OrgLifecycleManager.set_on_stop_org`.
        """

        self._lifecycle.set_on_stop_org(callback)

    # ------------------------------------------------------------------
    # Sprint-5 ex-finding cleanup (audit v5 §5.2 #5): three node-query
    # endpoints (``GET nodes/{id}/{thinking,prompt-preview,status}``)
    # used to surface 503 / AttributeError because v2 OrgRuntime had no
    # implementations. We add safe placeholder methods so the frontend
    # panel can render an empty / informational view instead of crashing
    # while the real implementations land alongside the NodeStatusController
    # subsystem (tracked as P9.7gamma in the runtime roadmap).
    # ------------------------------------------------------------------

    def get_node_thinking(self, org_id: str, node_id: str) -> dict[str, Any]:
        """Best-effort thinking timeline (Sprint-5 stub).

        Returns recent ``command_phase`` / ``subtask_assigned`` events
        for ``node_id`` from the per-org event store. Empty list when
        the store is missing or empty; no AttributeError ever again.
        """

        events: list[dict[str, Any]] = []
        try:
            store = self.get_event_store(org_id)
            if store is not None and hasattr(store, "query"):
                for ev in store.query(limit=50) or []:
                    if not isinstance(ev, dict):
                        continue
                    data = ev.get("data") or ev.get("payload") or {}
                    if not isinstance(data, dict):
                        continue
                    if data.get("node_id") == node_id:
                        events.append(ev)
        except Exception:  # noqa: BLE001
            pass
        return {
            "org_id": org_id,
            "node_id": node_id,
            "thinking": events,
            "implementation": "sprint5_stub",
        }

    def preview_node_prompt(self, org_id: str, node_id: str) -> dict[str, Any]:
        """Render the system prompt the node would receive (Sprint-5 stub).

        Reuses :class:`ProfileResolver` from the agent pipeline so the
        previewed prompt matches what ``_BrainBackedNodeAgent.run`` will
        feed the brain. When the spec / lookup is unavailable returns
        a structured ``prompt=None`` payload (not a 500) so the frontend
        panel can show an "n/a" state.
        """

        prompt_text: str | None = None
        try:
            from ._default_agent_builder import _persona_system_prompt
            from ._runtime_agent_pipeline import ProfileResolver

            resolver = ProfileResolver(lookup=self._lookup)
            spec = resolver.resolve(org_id=org_id, node_id=node_id)
            if spec is not None:
                prompt_text = _persona_system_prompt(spec, depth=0)
        except Exception:  # noqa: BLE001
            prompt_text = None
        return {
            "org_id": org_id,
            "node_id": node_id,
            "prompt": prompt_text,
            "implementation": "sprint5_stub",
        }

    def get_node_status_snapshot(self, org_id: str, node_id: str) -> dict[str, Any]:
        """Compact per-node status (Sprint-5 stub).

        Returns ``running`` when the node has any in-flight tracker
        snapshot via the dispatch sibling; ``idle`` otherwise. The
        ``is_active`` / ``recently_stopped`` flags piggy-back on the
        lifecycle manager so the panel can also reflect org-state.
        """

        is_active = False
        recently_stopped = False
        try:
            is_active = bool(self._state.is_org_active(org_id))
        except Exception:  # noqa: BLE001
            pass
        try:
            recently_stopped = bool(self._lifecycle.is_org_recently_stopped(org_id))
        except Exception:  # noqa: BLE001
            pass
        status = "active" if is_active else "idle"
        return {
            "org_id": org_id,
            "node_id": node_id,
            "status": status,
            "is_active": is_active,
            "recently_stopped": recently_stopped,
            "implementation": "sprint5_stub",
        }

    def register_event_store(self, org_id: str) -> Any:
        """Eagerly mint an :class:`OrgEventStore` for ``org_id``.

        Idempotent -- returns the existing store if one is already
        wired.  Exposed so the create / import / from-template paths
        (or tests) can pre-warm before any event is emitted; routine
        callers can rely on :meth:`get_event_store` to lazy-mint on
        first access (smoke-5-sse fix; see ``tmp_p10/_5_sse_triage.md``).
        """
        existing = self._event_stores.get(org_id)
        if existing is not None:
            return existing
        from ._runtime_event_store import OrgEventStore  # local: avoid cycle

        jsonl: Any = None
        get_dir = getattr(self._lookup, "get_org_dir", None)
        if callable(get_dir):
            try:
                jsonl = Path(get_dir(org_id)) / "logs" / "events.jsonl"
            except Exception:  # noqa: BLE001 (parity with v1 swallow)
                jsonl = None
        store = OrgEventStore(org_id, jsonl_path=jsonl)
        self._event_stores[org_id] = store
        return store

    def get_event_store(self, org_id: str) -> Any:
        """Return the registered event store, or lazily mint one for known orgs.

        Mint runtime orgs (created via ``POST /api/v2/orgs/from-template``)
        used to land on disk under ``data/orgs/<id>/`` without ever
        registering an event store on the singleton -- so every
        downstream ``/events`` / ``/activity`` / ``/audit-log`` route
        404'd.  We now lazy-mint on first access when the org is known
        to the :class:`OrgLookupProtocol` backing this runtime; genuinely
        missing org ids still return ``None`` so the route's 404 path is
        preserved (see ``tests/api/contracts/test_orgs_v2_contracts_state.py::test_b45_events_404_when_no_store``).
        """
        cached = self._event_stores.get(org_id)
        if cached is not None:
            return cached
        try:
            known = self._lookup.get_org(org_id)
        except Exception:  # noqa: BLE001 (lookup failure -> behave like miss)
            known = None
        if not known:
            return None
        return self.register_event_store(org_id)

    def get_inbox(self, org_id: str) -> Any:
        return self._inboxes.get(org_id)

    # ------------------------------------------------------------------
    # H4 event-bus bridges (see ``__init__`` docstring + audit §3.2 P0)
    # ------------------------------------------------------------------

    def _persist_event_tap(self, event_name: str, payload: dict[str, Any]) -> None:
        """Persist every dispatch event onto the org's :class:`OrgEventStore`.

        Idempotently lazy-mints the per-org store via
        :meth:`register_event_store`. Best-effort: any I/O / lookup
        failure logs a warning and returns; the dispatch loop must
        never see the exception.
        """

        if not isinstance(payload, dict):
            return
        org_id = payload.get("org_id")
        if not isinstance(org_id, str) or not org_id:
            return
        try:
            store = self.register_event_store(org_id)
            record = dict(payload)
            record.setdefault("type", event_name)
            store.append(record)
        except Exception as exc:  # noqa: BLE001 -- bridge must not poison dispatch
            _LOGGER.warning(
                "OrgRuntime persist tap failed for event=%r org=%s: %s",
                event_name,
                org_id,
                exc,
            )

    async def _stream_event_tap(self, event_name: str, payload: dict[str, Any]) -> None:
        """Forward every dispatch event to the org's :class:`StreamBus`.

        Emits on the ``lifecycle`` channel (one of the four channels
        the v2 SSE route subscribes to by default; see
        ``api/routes/orgs_v2_stream.py``). Imports the registry
        lazily because ``openakita.runtime`` pulls a chunk of the
        IM stack that we don't need at module import time.
        """

        if not isinstance(payload, dict):
            return
        org_id = payload.get("org_id")
        if not isinstance(org_id, str) or not org_id:
            return
        try:
            from openakita.runtime.stream_registry import (
                get_or_create_org_stream_bus,
            )

            stream_bus = get_or_create_org_stream_bus(org_id)
            await stream_bus.emit(
                "lifecycle",
                event_name,
                dict(payload),
                command_id=str(payload.get("command_id") or ""),
                org_id=org_id,
            )
        except Exception as exc:  # noqa: BLE001 -- bridge must not poison dispatch
            _LOGGER.warning(
                "OrgRuntime stream tap failed for event=%r org=%s: %s",
                event_name,
                org_id,
                exc,
            )


def get_runtime() -> OrgRuntime | None:
    """Return the process-wide :class:`OrgRuntime` singleton.

    P9.6a returns ``None``; the factory wiring lives in the
    lifecycle sibling (P9.6d) which sets the singleton on
    first ``start()``.
    """

    return _RUNTIME_SINGLETON


_RUNTIME_SINGLETON: OrgRuntime | None = None
