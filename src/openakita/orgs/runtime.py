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
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .blackboard import BlackboardBackendProtocol
from .command_service import OrgCommandServiceProtocol, OrgLookupProtocol
from .manager import OrgLifecycleEmitterProtocol, OrgPersistenceProtocol
from .node_scheduler import NodeSchedulerProtocol

if TYPE_CHECKING:  # pragma: no cover -- forward ref only
    from ._runtime_agent_host import NodeToolHost
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


def _pick_event_field(
    ev: dict[str, Any], nested: dict[str, Any], keys: tuple[str, ...]
) -> Any:
    """First non-empty value across ``ev`` then ``nested`` for any of ``keys``.

    Helper for :meth:`OrgRuntime.get_node_thinking`: agent-pipeline events
    stamp meaningful fields either at the top level or under a nested
    ``data``/``payload`` mapping depending on the producer, so the timeline
    projection probes both.
    """

    for k in keys:
        v = ev.get(k)
        if v in (None, "", []):
            v = nested.get(k)
        if v not in (None, "", []):
            return v
    return None


def _args_preview_brief(raw: Any, *, limit: int = 60) -> str:
    """Condense a tool ``args_preview`` blob into a short, human line.

    The raw value is usually a JSON object string like
    ``{"path": "report.md", "content": "..."}``. For the activity feed we
    only want a glanceable hint, so we surface the most meaningful single
    argument (path / target / query / command / url) when the blob parses
    as JSON, otherwise we just clip the raw string. Never raises.
    """

    if not raw:
        return ""
    s = str(raw).strip()
    try:
        import json as _json

        obj = _json.loads(s)
        if isinstance(obj, dict):
            for key in (
                "path",
                "file_path",
                "dst",
                "destination",
                "dir_path",
                "query",
                "command",
                "url",
                "pattern",
            ):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    v = val.strip()
                    return v if len(v) <= limit else v[: limit - 1] + "…"
    except (ValueError, TypeError):
        pass
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _describe_recent_event(
    etype: str, ev: dict[str, Any], last_assigned: dict[str, str]
) -> str:
    """Build a Chinese, content-bearing description for one activity line.

    UI feedback: the canvas activity feed previously showed only an
    action + node name (``▶执行 主编`` / ``✓完成 视觉设计``) because
    ``recent_tasks`` set ``task = content_preview`` and the lifecycle
    events (``agent_run_started`` / ``agent_run_finished``) carry no
    ``content_preview``. This derives a "做了什么" snippet from fields the
    events already persist — no fabricated data:

    * ``subtask_assigned``     -> the delegated instruction (content_preview)
    * ``agent_run_started``    -> the task it just picked up (last assignment)
    * ``agent_run_finished``   -> 产出字数 + 交付文件名
    * ``node_tool_called``     -> 工具名 + 入参摘要
    * ``node_tool_completed``  -> 工具名 + 返回字数

    Returns ``""`` (rendered as an empty cell, never raw English) when no
    meaningful content is available.
    """

    if etype == "subtask_assigned":
        return str(ev.get("content_preview") or "")
    if etype == "agent_run_started":
        node = str(ev.get("node_id") or "")
        assigned = last_assigned.get(node) or ""
        return f"受理任务：{assigned}" if assigned else ""
    if etype == "agent_run_finished":
        bits: list[str] = []
        try:
            out = int(ev.get("output_len") or 0)
        except (TypeError, ValueError):
            out = 0
        if out > 0:
            bits.append(f"产出 {out} 字")
        art = ev.get("artifact_path") or ""
        if art:
            try:
                name = PureWindowsPath(str(art)).name
                if name:
                    bits.append(f"交付 {name}")
            except (ValueError, OSError):
                pass
        return "，".join(bits)
    if etype == "node_tool_called":
        tool = str(ev.get("tool_name") or "工具")
        brief = _args_preview_brief(ev.get("args_preview"))
        return f"{tool}（{brief}）" if brief else f"{tool}"
    if etype == "node_tool_completed":
        tool = str(ev.get("tool_name") or "工具")
        try:
            rlen = int(ev.get("result_len") or 0)
        except (TypeError, ValueError):
            rlen = 0
        return f"{tool} 完成，返回 {rlen} 字" if rlen > 0 else f"{tool} 完成"
    if etype == "node_thinking":
        think = str(ev.get("thinking") or "").strip()
        if think:
            return f"思考：{think[:60]}…" if len(think) > 60 else f"思考：{think}"
        return ""
    return str(ev.get("content_preview") or "")


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
        # Sprint-9 removed ``self._watchdog_tasks`` -- the wall-clock
        # CommandWatchdog has been replaced by the supervisor's
        # StallDetector + max_turns cap (see
        # ``runtime/supervisor.py`` + ``orgs/command_service.py``).
        self._idle_probe_tasks: dict[str, asyncio.Task[None]] = {}
        # Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): per-process
        # :class:`NodeToolHost` slot. We deliberately keep it as a
        # single-slot ref (not a per-org dict) because the Sprint-6
        # minimum-viable host re-uses the main desktop Agent's
        # handler_registry -- there is exactly one such registry in
        # the process regardless of how many orgs run. Per-org
        # workspace / memory isolation is reserved for Sprint-7+
        # (see ``_runtime_agent_host`` module docstring).
        self._node_tool_host: NodeToolHost | None = None

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
        # B4/B5/B6 contract bridge sinks (set by the composition root via
        # ``set_contract_sinks``). When wired, ``_contract_event_tap``
        # turns the dispatch event stream into ProjectStore tasks +
        # OrgBlackboard facts/resources so the kanban / blackboard /
        # deliverable UI panels are populated from real runs.
        self._contract_project_store: Any = None
        self._contract_blackboard: Any = None
        self._contract_project_by_org: dict[str, str] = {}

        register_tap = getattr(self._event_bus, "add_tap", None)
        if callable(register_tap):
            register_tap(self._persist_event_tap)
            register_tap(self._stream_event_tap)
            # B2: bridge v2 dispatch events onto the legacy ``org:*`` WS
            # channel so the React node graph animates in real time.
            register_tap(self._ws_event_tap)
            # B4/B5/B6: project/task + blackboard/resource persistence.
            register_tap(self._contract_event_tap)

    def set_contract_sinks(self, *, project_store: Any = None, blackboard: Any = None) -> None:
        """Wire the per-org ProjectStore / OrgBlackboard registries.

        The composition root calls this once after constructing the
        ``OrgScoped*`` registries so the contract bridge tap can persist
        structured product data (projects/tasks, blackboard facts and
        deliverable resources) keyed by the org on each event.
        """
        self._contract_project_store = project_store
        self._contract_blackboard = blackboard

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

    async def cancel_user_command(
        self,
        org_id: str,
        command_id: str,
        *,
        cancel_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """v1 ``OrgRuntime.cancel_user_command`` parity (delegates to dispatch sibling P9.6e).

        Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5): pass through the
        explicit cancel source so the dispatch sibling can stamp
        events.jsonl with ``cancelled_by`` (stop_org / watchdog /
        user_cancel) instead of always emitting the hardcoded
        ``user_cancel`` Sprint-5 wrote.
        """

        return await self._dispatch.cancel_user_command(
            org_id, command_id, cancel_reason=cancel_reason
        )

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

    # ------------------------------------------------------------------
    # Sprint-6 P0-1: NodeToolHost wiring (RCA _v17_p1_rca.md §1.5)
    # ------------------------------------------------------------------

    def set_node_tool_host(self, host: NodeToolHost | None) -> None:
        """Bind a :class:`NodeToolHost` for use by per-node agents.

        Late-binding: the API server lifespan installs the host once
        ``app.state.agent`` (the desktop ``Agent``) is fully wired.
        Prior to that the runtime falls back to the empty
        ``default_handler_registry`` path the Sprint-5 commit shipped
        with, so the lifespan-race window keeps its v17 observable
        instead of crashing (RCA §1.5.4 rollback strategy).
        """

        # Dispose the previous host (if any) so the source agent's
        # handler_registry can be garbage-collected on rebinds.
        prior = self._node_tool_host
        if prior is not None and prior is not host:
            try:
                prior.dispose()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "NodeToolHost.dispose raised during rebind",
                    exc_info=True,
                )
        self._node_tool_host = host

    def get_node_tool_host(self) -> NodeToolHost | None:
        """Return the currently-bound :class:`NodeToolHost`, if any."""

        return self._node_tool_host

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
        """Per-node thinking / activity timeline for the monitor panel.

        UI issue #5/#6: this used to return ``{"thinking": [<raw events>]}``
        but the frontend (and the B31 contract) read ``data.timeline`` whose
        items must be shaped ``{type: "event", event_type, data, timestamp}``
        (or ``type: "message"``). The old key + raw shape meant the "思维链"
        panel was ALWAYS empty even after a full multi-node run. We now:

        * return the ``timeline`` key the panel expects,
        * include every event acted by ``node_id`` AND every delegation where
          it is the dispatching parent or the dispatched child (so a node's
          chain shows both "I was asked X" and "I handed Y to Z"),
        * project the raw top-level fields onto the small set of ``data`` keys
          that already have Chinese ``DATA_KEY_LABELS`` translations so each
          row renders readable text instead of a bare colored dot.
        """

        timeline: list[dict[str, Any]] = []
        try:
            store = self.get_event_store(org_id)
            if store is not None and hasattr(store, "query"):
                for ev in store.query(limit=300) or []:
                    if not isinstance(ev, dict):
                        continue
                    # A4 fix: dispatch / agent-pipeline events stamp ``node_id``
                    # at the TOP level (see ``_runtime_agent_pipeline_executor``
                    # -> ``OrgEventStore.append``); legacy producers nest it.
                    nested = ev.get("data") or ev.get("payload") or {}
                    nested = nested if isinstance(nested, dict) else {}
                    ev_node = ev.get("node_id") or nested.get("node_id")
                    parent = ev.get("parent_node_id") or nested.get("parent_node_id")
                    child = ev.get("child_node_id") or nested.get("child_node_id")
                    if node_id not in (ev_node, parent, child):
                        continue
                    etype = ev.get("type") or ev.get("event_type") or ""
                    # 图4: ``node_run_delta`` are high-frequency transient STREAM
                    # frames (hundreds per node run) — they belong in the live
                    # 编排过程 timeline, NOT the monitor's discrete 思维链. The
                    # reasoning they carry is consolidated into the single
                    # ``node_thinking`` event below, so skip the raw deltas here
                    # to keep the panel clean (one reasoning row, not 200+).
                    if etype == "node_run_delta":
                        continue
                    # Project onto already-translated ``DATA_KEY_LABELS`` keys.
                    data: dict[str, Any] = {}
                    preview = _pick_event_field(
                        ev, nested, ("content_preview", "content", "instruction")
                    )
                    if preview is not None:
                        data["task"] = preview
                    result_prev = _pick_event_field(
                        ev, nested, ("result_preview", "result", "summary")
                    )
                    if result_prev is not None:
                        data["result_preview"] = result_prev
                    # 图4: ``node_thinking`` events carry the node's reasoning so
                    # the 思维链 panel shows "what it thought about", not just
                    # actions. Reuse the already-translated ``thinking`` label.
                    thinking_txt = _pick_event_field(ev, nested, ("thinking",))
                    if thinking_txt is not None:
                        data["thinking"] = thinking_txt
                    if child:
                        data["to"] = child
                    if parent and parent != node_id:
                        data["from"] = parent
                    for key in ("reason", "exit_reason", "tool", "tool_name", "status"):
                        val = _pick_event_field(ev, nested, (key,))
                        if val is not None:
                            data[key if key != "exit_reason" else "reason"] = val
                    out_len = ev.get("output_len", nested.get("output_len"))
                    if isinstance(out_len, int) and out_len > 0:
                        data["result_preview"] = data.get(
                            "result_preview", ""
                        ) or f"（输出 {out_len} 字）"
                    artifact = ev.get("artifact_path") or nested.get("artifact_path")
                    if artifact:
                        data["filename"] = str(artifact).replace("\\", "/").rsplit("/", 1)[-1]
                    timeline.append(
                        {
                            "type": "event",
                            "event_type": etype,
                            "node_id": ev_node,
                            "data": data,
                            "timestamp": ev.get("ts") or ev.get("at") or ev.get("timestamp"),
                        }
                    )
        except Exception:  # noqa: BLE001
            pass
        return {
            "org_id": org_id,
            "node_id": node_id,
            # ``timeline`` is the canonical key (matches B31 contract +
            # frontend); keep ``thinking`` as a back-compat mirror for any
            # older reader that still expects the Sprint-5 shape.
            "timeline": timeline,
            "thinking": timeline,
            "count": len(timeline),
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

    # ------------------------------------------------------------------
    # B1: org-level status snapshot + node-status mutators (P9.7 wiring).
    # The /_p97/health probe expects ``get_status_snapshot`` /
    # ``set_node_status`` / ``freeze_node`` to be callable before it
    # reports the runtime subsystem "wired"; the GET /{id}/status route
    # also depends on ``get_status_snapshot``.
    # ------------------------------------------------------------------

    def get_status_snapshot(self, org_id: str) -> dict[str, Any] | None:
        """Compact org-level status envelope for ``GET /{id}/status``.

        Reuses :meth:`get_stats` (single source of truth for the node
        roster + live status buckets) and adds the org-level lifecycle
        state. Returns ``None`` for an unknown org so the route 404s.
        """
        stats = self.get_stats(org_id)
        if stats is None:
            return None
        try:
            org_state = self._state.get_org_state(org_id)
        except Exception:  # noqa: BLE001
            org_state = None
        return {
            "org_id": org_id,
            "name": stats.get("name", org_id),
            "state": org_state,
            "is_active": stats.get("is_active", False),
            "health": stats.get("health", "healthy"),
            "node_count": stats.get("node_count", 0),
            "node_stats": stats.get("node_stats", {}),
            "nodes": stats.get("per_node", []),
        }

    async def set_node_status(
        self, org_id: str, node_id: str, new_status: str, *, reason: str | None = None
    ) -> str | None:
        """Set a node's live status via the lifecycle backend.

        Returns the prior status string (best-effort). Also mirrors the
        change onto the legacy ``org:node_status`` WebSocket event so the
        node graph reflects manual freezes / resumes immediately.
        """
        prior: str | None = None
        try:
            prior = self._node_lifecycle.get_node_status(org_id, node_id)
        except Exception:  # noqa: BLE001
            prior = None
        try:
            await self._node_lifecycle.set_node_status(
                org_id, node_id, new_status, reason=reason
            )
        except TypeError:
            # Some lifecycle backends are sync / take no reason kwarg.
            res = self._node_lifecycle.set_node_status(org_id, node_id, new_status)
            if asyncio.iscoroutine(res):
                await res
        await self._broadcast_ws_safe(
            "org:node_status",
            {"org_id": org_id, "node_id": node_id, "status": new_status},
        )
        return prior

    async def freeze_node(self, org_id: str, node_id: str, *, reason: str | None = None) -> str | None:
        """Freeze a node (status -> ``frozen``)."""
        return await self.set_node_status(org_id, node_id, "frozen", reason=reason or "freeze")

    def get_stats(self, org_id: str) -> dict[str, Any] | None:
        """A1 fix: real org runtime statistics for the dashboard.

        Pre-fix ``OrgRuntime`` had no ``get_stats`` at all, so
        ``GET /api/v2/orgs/{id}/stats`` returned 503
        ``runtime_method:get_stats not wired`` and the data-screen
        dashboard fell back to its ``loadError`` ("看板无法加载") view.

        The payload mirrors the shape the React ``OrgDashboard``
        consumes (``node_stats`` / ``per_node`` / ``department_workload``
        / ``recent_tasks`` / KPI counters). Everything is derived from
        already-persisted data: the org spec (node roster), the live
        node-lifecycle status map, and the per-org event store. Returns
        ``None`` for an unknown org so the route's 404 path is kept.
        """
        org = self.get_org(org_id)
        if org is None:
            return None

        def _attr(obj: Any, name: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        nodes = list(_attr(org, "nodes", []) or [])
        buckets = {"idle": 0, "busy": 0, "error": 0, "frozen": 0, "waiting": 0}
        per_node: list[dict[str, Any]] = []
        dept_wl: dict[str, dict[str, int]] = {}
        for n in nodes:
            nid = _attr(n, "id", "") or ""
            role = _attr(n, "role_title", "") or nid
            dept = _attr(n, "department", "") or ""
            # Prefer the live lifecycle status; fall back to the spec's
            # static status field for nodes that never activated.
            status = "idle"
            try:
                live = self._node_lifecycle.get_node_status(org_id, nid)
                status = str(live).lower() if live else str(_attr(n, "status", "idle") or "idle").lower()
            except Exception:  # noqa: BLE001
                status = str(_attr(n, "status", "idle") or "idle").lower()
            # Normalise lifecycle vocab onto the 5 dashboard buckets.
            if status in ("running", "active", "working"):
                status = "busy"
            elif status in ("offline", "stopped"):
                status = "idle"
            if status not in buckets:
                status = "idle"
            buckets[status] += 1
            per_node.append(
                {"id": nid, "role_title": role, "department": dept, "status": status}
            )
            slot = dept_wl.setdefault(dept or "—", {"total": 0, "busy": 0})
            slot["total"] += 1
            if status == "busy":
                slot["busy"] += 1

        recent_tasks: list[dict[str, Any]] = []
        completed = 0
        total_events = 0
        try:
            store = self.get_event_store(org_id)
            if store is not None and hasattr(store, "query"):
                evts = store.query(limit=200) or []
                total_events = len(evts)
                _type_map = {
                    "subtask_assigned": "task_delegated",
                    "agent_run_started": "node_activated",
                    "agent_run_finished": "task_completed",
                    "agent_run_failed": "task_rejected",
                    "agent_run_cancelled": "task_cancelled",
                    # UI feedback: surface tool steps in the feed so each
                    # line carries "做了什么" (tool name + args + result).
                    "node_tool_called": "tool_called",
                    "node_tool_completed": "tool_completed",
                }
                # Track the most recent assignment per node so a bare
                # ``agent_run_started`` (which carries no content) can be
                # described with the task that node just picked up.
                last_assigned: dict[str, str] = {}
                for ev in evts:
                    if not isinstance(ev, dict):
                        continue
                    etype = ev.get("type") or ev.get("event_type") or ""
                    if etype == "agent_run_finished":
                        completed += 1
                    if etype == "subtask_assigned":
                        cid = str(ev.get("child_node_id") or "")
                        if cid:
                            last_assigned[cid] = str(ev.get("content_preview") or "")
                    mapped = _type_map.get(etype)
                    if mapped is None:
                        continue
                    ts = ev.get("ts") or ev.get("at") or 0
                    try:
                        ts_ms = float(ts) * 1000.0 if float(ts) < 1e12 else float(ts)
                    except (TypeError, ValueError):
                        ts_ms = 0
                    recent_tasks.append(
                        {
                            "type": mapped,
                            "from": ev.get("parent_node_id") or ev.get("node_id") or "",
                            "to": ev.get("child_node_id")
                            or (ev.get("node_id") if ev.get("parent_node_id") else ""),
                            "task": _describe_recent_event(etype, ev, last_assigned),
                            "t": ts_ms,
                        }
                    )
                recent_tasks = recent_tasks[-30:]
                recent_tasks.reverse()
        except Exception:  # noqa: BLE001
            pass

        # Best-effort recent blackboard slice (only when the backend is
        # wired; B1 attaches it -- before that this stays []).
        recent_bb: list[dict[str, Any]] = []
        bb = self._blackboard_backend
        if bb is not None:
            try:
                entries = bb.query(limit=10) if hasattr(bb, "query") else []
                for e in entries or []:
                    d = e.to_dict() if hasattr(e, "to_dict") else e
                    if isinstance(d, dict):
                        recent_bb.append(d)
            except Exception:  # noqa: BLE001
                recent_bb = []

        err = buckets["error"]
        node_count = len(nodes)
        if err and node_count and err / node_count >= 0.5:
            health = "critical"
        elif err:
            health = "warning"
        elif buckets["busy"]:
            health = "attention"
        else:
            health = "healthy"

        uptime_s = 0.0
        created = _attr(org, "created_at", None)
        if isinstance(created, str) and created:
            try:
                from datetime import UTC, datetime

                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                uptime_s = max(0.0, datetime.now(UTC).timestamp() - dt.timestamp())
            except (ValueError, TypeError):
                uptime_s = 0.0

        try:
            is_active = bool(self._state.is_org_active(org_id))
        except Exception:  # noqa: BLE001
            is_active = False

        return {
            "org_id": org_id,
            "name": _attr(org, "name", org_id),
            "health": health,
            "is_active": is_active,
            "node_count": node_count,
            "node_stats": buckets,
            "per_node": per_node,
            "department_workload": dept_wl,
            "recent_tasks": recent_tasks,
            "recent_blackboard": recent_bb,
            "anomalies": [],
            "total_tasks_completed": _attr(org, "total_tasks_completed", 0) or completed,
            "total_messages_exchanged": _attr(org, "total_messages_exchanged", 0) or total_events,
            "pending_messages": 0,
            "pending_approvals": 0,
            "uptime_s": uptime_s,
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

    async def _broadcast_ws_safe(self, event: str, data: dict[str, Any]) -> None:
        """Best-effort legacy ``org:*`` WebSocket broadcast.

        Imports the API broadcaster lazily (the runtime layer must not
        hard-depend on the FastAPI layer) and swallows every failure so
        the dispatch loop is never poisoned by a missing/closed socket.
        """
        try:
            from openakita.api.routes.websocket import broadcast_event

            await broadcast_event(event, data)
        except Exception:  # noqa: BLE001 -- WS is informational
            _LOGGER.debug("OrgRuntime WS broadcast failed for %r", event, exc_info=True)

    async def _ws_event_tap(self, event_name: str, payload: dict[str, Any]) -> None:
        """B2 bridge: translate v2 dispatch/executor events into the legacy
        ``org:*`` WebSocket events the React node graph + chat panel listen
        for, and keep the per-node live status in sync.

        Pre-fix the agent-pipeline executor only emitted ``agent_run_*`` /
        ``subtask_assigned`` onto the in-memory bus (persisted + streamed
        over SSE), but never onto the ``org:*`` WS channel the node graph
        animates from -- so a running org looked frozen ("处理中…" with no
        node movement, 图2). This tap closes that gap without touching the
        executor: it observes the same events the persist/stream taps do.
        """
        if not isinstance(payload, dict):
            return
        org_id = payload.get("org_id")
        if not isinstance(org_id, str) or not org_id:
            return
        node_id = payload.get("node_id")
        parent = payload.get("parent_node_id")
        child = payload.get("child_node_id")
        preview = payload.get("content_preview")
        try:
            if event_name == "agent_run_started" and node_id:
                # Reflect "busy" so both the graph and get_stats see it.
                try:
                    await self._node_lifecycle.set_node_status(org_id, node_id, "busy")
                except Exception:  # noqa: BLE001
                    pass
                await self._broadcast_ws_safe(
                    "org:node_status",
                    {
                        "org_id": org_id,
                        "node_id": node_id,
                        "status": "busy",
                        "current_task": preview or "",
                    },
                )
            elif event_name in ("agent_run_finished", "agent_run_failed", "agent_run_cancelled") and node_id:
                # Root-node completion semantics: the level-0 root/主编 orchestrates
                # the WHOLE command across multiple supervisor turns (它先派单、下游
                # 逐级回流、最后整合汇报). Its FIRST agent_run_finished is NOT the end
                # of the command -- the supervisor may run more turns (integration /
                # final synthesis) afterwards. Idling the root here made the node
                # graph show 主编"空闲/已完成" while下级仍在工作 (与设计不符). Keep the
                # root "busy/进行中" until the command actually converges; the
                # authoritative idle is applied once by ``_reset_busy_nodes_to_idle``
                # from :meth:`emit_command_done`. Non-root nodes idle as before.
                is_root_node = False
                if event_name == "agent_run_finished":
                    try:
                        org = self.get_org(org_id)
                        node = org.get_node(node_id) if org is not None else None
                        is_root_node = node is not None and getattr(node, "level", None) == 0
                    except Exception:  # noqa: BLE001
                        is_root_node = False
                if not is_root_node:
                    try:
                        await self._node_lifecycle.set_node_status(org_id, node_id, "idle")
                    except Exception:  # noqa: BLE001
                        pass
                    await self._broadcast_ws_safe(
                        "org:node_status",
                        {"org_id": org_id, "node_id": node_id, "status": "idle"},
                    )
                if event_name == "agent_run_finished":
                    finished_incomplete = bool(payload.get("incomplete"))
                    await self._broadcast_ws_safe(
                        "org:task_complete",
                        {
                            "org_id": org_id,
                            "node_id": node_id,
                            "incomplete": finished_incomplete,
                        },
                    )
                    # Audit fix: the node graph animates ``org:task_delivered``
                    # (产出回流连线) but v2 never emitted it — only delegation
                    # (org:task_delegated) lit up, so the "下游产出回流到上级"
                    # half of the flow was invisible. When a child finishes, fire
                    # a delivery animation back along its reporting edge to the
                    # parent so the round-trip (派单→交付) is visible end to end.
                    # Quality gate: an incomplete output is NOT a delivery, so we
                    # suppress the 产出回流 animation (test7 RCA: "失败也显示交付").
                    if parent and not finished_incomplete:
                        await self._broadcast_ws_safe(
                            "org:task_delivered",
                            {
                                "org_id": org_id,
                                "from_node": node_id,
                                "to_node": parent,
                            },
                        )
            elif event_name == "subtask_assigned":
                await self._broadcast_ws_safe(
                    "org:task_delegated",
                    {
                        "org_id": org_id,
                        "from_node": parent or node_id or "",
                        "to_node": child or node_id or "",
                        "content": preview or "",
                    },
                )
            elif event_name == "file_output_registered":
                # test11 P2: a node just wrote / delivered a file. Mirror it onto
                # the command center as a live downloadable card (过程+最终文件)
                # so the user sees deliverables appear during the run, not only
                # after a refresh. Reuse the ``resource`` shape the chat panel
                # already renders for blackboard resources.
                fpath = str(payload.get("path") or "")
                if fpath:
                    fname = fpath.replace("\\", "/").rsplit("/", 1)[-1]
                    fsize = payload.get("size_bytes")
                    await self._broadcast_ws_safe(
                        "org:file_output_registered",
                        {
                            "org_id": org_id,
                            "node_id": node_id or "",
                            "command_id": payload.get("command_id") or "",
                            "memory_type": "resource",
                            "filename": fname,
                            "file_path": fpath,
                            "path": fpath,
                            "file_size": fsize,
                            "size": fsize,
                        },
                    )
            elif event_name in ("command_done", "org_command_done"):
                # Forward status/result/error so the command center can render
                # the final receipt straight from the WS event instead of
                # waiting on a follow-up poll (item 2: command_done 即时下发).
                done_payload: dict[str, Any] = {
                    "org_id": org_id,
                    "command_id": payload.get("command_id") or "",
                }
                if payload.get("status"):
                    done_payload["status"] = payload.get("status")
                if payload.get("result") is not None:
                    done_payload["result"] = payload.get("result")
                if payload.get("error"):
                    done_payload["error"] = payload.get("error")
                await self._broadcast_ws_safe("org:command_done", done_payload)
        except Exception:  # noqa: BLE001 -- bridge must not poison dispatch
            _LOGGER.debug("OrgRuntime ws tap failed for %r", event_name, exc_info=True)

    # ------------------------------------------------------------------
    # B4/B5/B6 contract bridge: dispatch events -> projects/tasks +
    # blackboard facts/resources. Synchronous file-backed stores are
    # cheap to write; the whole tap is isolated (failures logged, never
    # re-raised) so it can never poison the dispatch loop.
    # ------------------------------------------------------------------

    def _ensure_org_project(self, ps: Any, org_id: str) -> str | None:
        """Return the per-org working project id, creating it once."""
        cached = self._contract_project_by_org.get(org_id)
        if cached is not None:
            return cached
        # Reuse an existing working project if one was created earlier
        # (process restart resilience).
        try:
            for proj in ps.list_projects():
                pid = proj.get("id") if isinstance(proj, dict) else getattr(proj, "id", None)
                pname = proj.get("name") if isinstance(proj, dict) else getattr(proj, "name", "")
                if pid and pname == "\u7ec4\u7ec7\u534f\u4f5c\u770b\u677f":
                    self._contract_project_by_org[org_id] = pid
                    return pid
        except Exception:  # noqa: BLE001
            pass
        try:
            from .project_models import OrgProject, ProjectStatus, ProjectType

            proj = OrgProject(
                org_id=org_id,
                name="\u7ec4\u7ec7\u534f\u4f5c\u770b\u677f",
                description="\u7531\u7f16\u6392\u8fd0\u884c\u81ea\u52a8\u767b\u8bb0\u7684\u4efb\u52a1\u770b\u677f",
                project_type=ProjectType.TEMPORARY,
                status=ProjectStatus.ACTIVE,
            )
            created = ps.create_project(proj)
            pid = created.get("id") if isinstance(created, dict) else getattr(created, "id", None)
            if pid:
                self._contract_project_by_org[org_id] = pid
            return pid
        except Exception:  # noqa: BLE001
            _LOGGER.debug("contract: ensure_project failed for %s", org_id, exc_info=True)
            return None

    def ensure_command_project(
        self, org_id: str, command_id: str, root_node_id: str | None, content: str
    ) -> None:
        """UI issue #8: create the project + a root task the INSTANT a command is
        submitted, so the "项目" page shows work immediately instead of only after
        the first delegation (or after completion). The per-node subtask tap then
        hangs delegated subtasks under the same project; this root task is keyed
        by ``chain_id == command_id`` so :meth:`finalize_command_project` can flip
        it to delivered when the command converges. Idempotent + best-effort: a
        missing project store or a duplicate submit must never break submission.
        """
        ps_registry = self._contract_project_store
        if ps_registry is None or not command_id:
            return
        try:
            ps = ps_registry.for_org(org_id)
            pid = self._ensure_org_project(ps, org_id)
            if not pid:
                return
            if ps.find_task_by_chain(command_id) is not None:
                return  # idempotent: already created for this command
            from .project_models import ProjectTask, TaskStatus

            title = (content or "").strip().replace("\n", " ")
            ps.add_task(
                pid,
                ProjectTask(
                    project_id=pid,
                    title=(title[:80] or "用户指令"),
                    description=(content or "")[:2000],
                    status=TaskStatus.IN_PROGRESS,
                    assignee_node_id=root_node_id or "",
                    chain_id=command_id,
                    depth=0,
                    progress_pct=0,
                ),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("contract: ensure_command_project failed", exc_info=True)

    def finalize_command_project(self, org_id: str, command_id: str, *, ok: bool = True) -> None:
        """Flip the submit-time root task to delivered/rejected on convergence so
        the project board reflects completion (UI issue #8: 完成时应显示已完成/100%).

        Also the canonical "command converged" hook where the FINAL 主编 PDF is
        rendered (图3): a multi-turn root re-integrates, so we render from the
        LAST-recorded root deliverable here rather than the first root finish,
        guaranteeing the pdf matches the final .md. Scheduling is best-effort —
        if no event loop is running or no root artifact was recorded, we simply
        skip the pdf and the .md is still delivered."""
        # 图3 final-PDF: schedule the render of the FINAL root deliverable (only
        # on a successful convergence) before the project-store guard below, so
        # the pdf is produced even in setups without a project store wired.
        rec = None
        if command_id:
            store = getattr(self, "_root_final_artifact", None)
            # Always pop (cleanup) so a cancelled/errored command can't leak the
            # recorded artifact; only RENDER on a successful convergence.
            rec = store.pop(command_id, None) if isinstance(store, dict) else None
            if ok and rec:
                root_node_id, final_md = rec
                try:
                    import asyncio as _asyncio

                    loop = _asyncio.get_running_loop()
                    loop.create_task(
                        self._maybe_render_root_pdf(
                            org_id=org_id,
                            command_id=command_id,
                            node_id=root_node_id,
                            artifact_path=final_md,
                            bb_registry=self._contract_blackboard,
                        )
                    )
                except RuntimeError:
                    # No running loop (sync test / odd call site): skip the pdf,
                    # the final .md remains the delivered artifact.
                    _LOGGER.debug("contract: no loop for final pdf render", exc_info=True)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("contract: schedule final pdf failed", exc_info=True)

        ps_registry = self._contract_project_store
        if ps_registry is None or not command_id:
            return
        try:
            ps = ps_registry.for_org(org_id)
            task = ps.find_task_by_chain(command_id)
            if task is None:
                return
            pid = getattr(task, "project_id", None) or (
                task.get("project_id") if isinstance(task, dict) else None
            )
            tid = getattr(task, "id", None) or (task.get("id") if isinstance(task, dict) else None)
            if not pid or not tid:
                return
            from .project_models import TaskStatus

            updates: dict[str, Any] = {
                "status": TaskStatus.DELIVERED if ok else TaskStatus.REJECTED,
                "progress_pct": 100 if ok else 0,
            }
            if ok and rec:
                _, final_path = rec
                final_file = Path(str(final_path))
                try:
                    final_size = final_file.stat().st_size
                except OSError:
                    final_size = 0
                updates["file_attachments"] = [
                    {
                        "filename": final_file.name,
                        "file_path": str(final_file),
                        "file_size": final_size,
                    }
                ]
            ps.update_task(pid, tid, updates)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("contract: finalize_command_project failed", exc_info=True)

    async def emit_command_done(
        self,
        org_id: str,
        command_id: str,
        *,
        status: str = "done",
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """Emit a terminal ``command_done`` event onto the event bus (item 2).

        Pre-fix the v2 command path NEVER emitted ``command_done`` — the
        command center learned a command had converged only by polling
        ``GET /commands/{id}``. Routing the terminal state through the bus
        means all three taps fire exactly once:

        * :meth:`_persist_event_tap` -> appends to the per-org
          ``OrgEventStore`` (events.jsonl) so the event is queryable.
        * :meth:`_stream_event_tap` -> SSE ``lifecycle`` channel.
        * :meth:`_ws_event_tap` -> legacy ``org:command_done`` WS broadcast
          carrying status/result/error so the UI renders the receipt live.

        Idempotent: a per-command guard set ensures a second call (e.g. a
        retry, or both the happy + synthetic-failure paths) is a no-op, so
        the polling fallback stays compatible without producing duplicates.
        """
        if not org_id or not command_id:
            return
        done_set = getattr(self, "_command_done_emitted", None)
        if done_set is None:
            done_set = set()
            self._command_done_emitted = done_set
        if command_id in done_set:
            return
        done_set.add(command_id)
        payload: dict[str, Any] = {
            "org_id": org_id,
            "command_id": command_id,
            "status": status,
        }
        if result is not None:
            payload["result"] = result
        if error:
            payload["error"] = error
        try:
            await self._event_bus.emit("command_done", payload)
        except Exception:  # noqa: BLE001 -- terminal emit must not crash finalize
            _LOGGER.debug(
                "emit_command_done failed (org=%s cmd=%s)", org_id, command_id, exc_info=True
            )
        # 图3 convergence: when a command terminates, no node should remain
        # "进行中". A node can be left busy if its terminal agent_run_* event was
        # dropped (fan-out race / mid-run restart). Reset every still-busy node
        # to idle and broadcast it so the graph + timeline converge instead of
        # showing a permanent spinner.
        try:
            await self._reset_busy_nodes_to_idle(org_id)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("reset busy nodes failed (org=%s)", org_id, exc_info=True)

    async def _reset_busy_nodes_to_idle(self, org_id: str) -> None:
        """Flip any non-idle node of ``org_id`` back to idle + broadcast it.

        Idempotent and best-effort. Used at command convergence so the node
        graph never shows a node stuck "busy"/"error" after the command is
        done (the live agent_run_finished path already idles nodes; this is the
        safety net for dropped terminal events)."""
        org = self.get_org(org_id)
        if org is None:
            return
        nodes = getattr(org, "nodes", None)
        if isinstance(nodes, Mapping):
            node_ids = [str(k) for k in nodes]
        elif nodes:
            node_ids = [str(getattr(n, "id", "") or "") for n in nodes]
        else:
            node_ids = []
        for nid in node_ids:
            if not nid:
                continue
            try:
                cur = self._node_lifecycle.get_node_status(org_id, nid)
            except Exception:  # noqa: BLE001
                cur = None
            if cur is None:
                continue
            if str(cur).lower() in ("busy", "error"):
                try:
                    await self._node_lifecycle.set_node_status(org_id, nid, "idle")
                except Exception:  # noqa: BLE001
                    pass
                await self._broadcast_ws_safe(
                    "org:node_status",
                    {"org_id": org_id, "node_id": nid, "status": "idle"},
                )

    @staticmethod
    def _iso_now() -> str:
        """Current UTC time as an ISO-8601 string (P4 Gantt timestamp source)."""
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    def _resolve_node_department(self, org_id: str, node_id: str | None) -> str:
        """Best-effort node -> department name (empty when unknown)."""
        if not node_id:
            return ""
        try:
            org = self.get_org(org_id)
            node = org.get_node(node_id) if org is not None else None
            return str(getattr(node, "department", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    async def _publish_process_log(
        self,
        org_id: str,
        *,
        node_id: str | None,
        content: str,
        tags: list[str],
        org_level: bool = False,
    ) -> None:
        """P3: write a live process record to the blackboard (node + department
        tiers, plus org tier for org-significant events) and broadcast
        ``org:blackboard_update`` so the panel refreshes in real time."""
        bb_registry = self._contract_blackboard
        if bb_registry is None:
            return
        try:
            bb = bb_registry.for_org(org_id)
        except Exception:  # noqa: BLE001
            return
        wrote = False
        if node_id:
            try:
                bb.write_node(node_id, content, tags=tags)
                wrote = True
            except Exception:  # noqa: BLE001
                _LOGGER.debug("process-log node write failed", exc_info=True)
            dept = self._resolve_node_department(org_id, node_id)
            if dept:
                try:
                    bb.write_department(dept, content, source_node=node_id, tags=tags)
                    wrote = True
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("process-log dept write failed", exc_info=True)
        if org_level or not node_id:
            try:
                bb.write_org(content, source_node=node_id or org_id, tags=tags)
                wrote = True
            except Exception:  # noqa: BLE001
                _LOGGER.debug("process-log org write failed", exc_info=True)
        if wrote:
            await self._broadcast_ws_safe(
                "org:blackboard_update", {"org_id": org_id, "node_id": node_id or ""}
            )

    async def _publish_process_event(
        self,
        event_name: str,
        org_id: str,
        *,
        node_id: str | None,
        parent: str | None,
        child: str | None,
        preview: str,
        payload: dict[str, Any],
    ) -> None:
        """Map an orchestration event to a live, tier-aware blackboard record.

        Completion (``agent_run_finished`` ok) is intentionally left to the
        existing deliverable-fact path below; here we cover the PROCESS events
        (派单/审阅/退回/上报/异常) that previously left the blackboard empty
        mid-run."""
        reason = str(payload.get("reason") or "").strip()
        if event_name == "subtask_assigned" and child:
            who = parent or "上级"
            snippet = (preview[:60] + "…") if len(preview) > 60 else preview
            await self._publish_process_log(
                org_id,
                node_id=child,
                content=f"📋 {who} 派单 → {child}：{snippet or '(无摘要)'}",
                tags=["process", "dispatch"],
                org_level=True,
            )
        elif event_name == "agent_run_started" and node_id:
            await self._publish_process_log(
                org_id,
                node_id=node_id,
                content=f"▶ 节点 {node_id} 开始执行任务",
                tags=["process", "started"],
            )
        elif event_name == "node_review_passed" and node_id:
            await self._publish_process_log(
                org_id,
                node_id=node_id,
                content=f"✅ {parent or '上级'} 评审通过：{node_id} 的产出",
                tags=["process", "review", "passed"],
            )
        elif event_name == "node_rework_requested":
            target = child or node_id
            await self._publish_process_log(
                org_id,
                node_id=target,
                content=f"↩ {parent or '上级'} 退回重做 {target}：{reason or '产出未达要求'}",
                tags=["process", "rework"],
                org_level=True,
            )
        elif event_name == "node_review_escalated":
            target = child or node_id
            await self._publish_process_log(
                org_id,
                node_id=target,
                content=(
                    f"⤴ {target} 多次重做仍未通过，已上报上级处理：{reason or '产出未达要求'}"
                ),
                tags=["process", "review", "escalated"],
                org_level=True,
            )
        elif event_name == "node_tool_failed" and node_id:
            tool = str(payload.get("tool_name") or "工具")
            err = str(payload.get("error") or "").strip()
            await self._publish_process_log(
                org_id,
                node_id=node_id,
                content=f"⚠ 节点 {node_id} 调用 {tool} 异常：{err or '执行失败'}",
                tags=["process", "anomaly", "tool"],
            )
        elif event_name == "agent_run_failed" and node_id:
            await self._publish_process_log(
                org_id,
                node_id=node_id,
                content=f"✖ 节点 {node_id} 运行失败：{reason or '未知原因'}",
                tags=["process", "anomaly", "failed"],
                org_level=True,
            )

    async def _contract_event_tap(self, event_name: str, payload: dict[str, Any]) -> None:
        ps_registry = self._contract_project_store
        bb_registry = self._contract_blackboard
        if ps_registry is None and bb_registry is None:
            return
        if not isinstance(payload, dict):
            return
        org_id = payload.get("org_id")
        if not isinstance(org_id, str) or not org_id:
            return
        node_id = payload.get("node_id")
        parent = payload.get("parent_node_id")
        child = payload.get("child_node_id")
        preview = (payload.get("content_preview") or "").strip()
        # chain_id / parent_chain_id let us rebuild the exact delegation
        # tree (see ``_runtime_agent_pipeline_executor``); both are
        # additive event fields so older producers (no chain) fall back
        # to the flat node-based mapping below.
        chain_id = payload.get("chain_id") or None
        parent_chain_id = payload.get("parent_chain_id") or None
        command_id = str(payload.get("command_id") or "")
        ev_depth = int(payload.get("depth") or 0)
        try:
            # P3 (黑板=全组织实时分级日志): publish a live, tier-aware process
            # record for the orchestration-significant events so the blackboard
            # panel shows who is doing what (派单/审阅/退回/异常) in real time at
            # the 组织/部门/节点 tiers -- not just end-of-run completion facts.
            # test11 root cause: the blackboard only ever logged "节点X完成交付"
            # at the org tier, so during a run the panel read "暂无记录".
            if bb_registry is not None:
                await self._publish_process_event(
                    event_name,
                    org_id,
                    node_id=node_id,
                    parent=parent,
                    child=child,
                    preview=preview,
                    payload=payload,
                )
            if event_name == "subtask_assigned" and ps_registry is not None and child:
                # B5: register the delegated subtask as a project task.
                ps = ps_registry.for_org(org_id)
                pid = self._ensure_org_project(ps, org_id)
                if pid:
                    from .project_models import ProjectTask, TaskStatus

                    # Resolve the precise parent task via the dispatcher's
                    # chain id; falls back to None (root task) when the
                    # parent chain isn't registered yet / event lacks it.
                    parent_task_id = None
                    if parent_chain_id:
                        try:
                            ptask = ps.find_task_by_chain(parent_chain_id)
                            if ptask is not None:
                                parent_task_id = getattr(ptask, "id", None)
                        except Exception:  # noqa: BLE001
                            parent_task_id = None
                    task = ProjectTask(
                        project_id=pid,
                        title=(preview[:80] or f"{parent or '?'} -> {child}"),
                        description=preview,
                        status=TaskStatus.IN_PROGRESS,
                        assignee_node_id=child,
                        delegated_by=parent,
                        chain_id=chain_id,
                        parent_task_id=parent_task_id,
                        depth=ev_depth,
                        # P4 (甘特图): stamp the dispatch time so the project
                        # timeline can draw a real start->end bar per subtask.
                        started_at=self._iso_now(),
                    )
                    ps.add_task(pid, task)
            elif event_name == "agent_run_finished" and node_id:
                output_len = int(payload.get("output_len") or 0)
                artifact_path = payload.get("artifact_path")
                # Quality gate (test7 RCA 2026-06): an output that failed the
                # completion check (raw thinking / mid-iteration stub / empty)
                # carries ``incomplete=True`` and no artifact_path. It must NOT
                # be marked delivered nor registered as a downloadable
                # deliverable resource — instead we leave the task open (so the
                # supervisor re-routes) and publish a transparency note.
                incomplete = bool(payload.get("incomplete"))
                if incomplete:
                    quality_reason = str(payload.get("quality_reason") or "incomplete")
                    if bb_registry is not None:
                        try:
                            bb_registry.publish(
                                org_id,
                                (
                                    f"\u8282\u70b9 {node_id} \u7684\u4ea7\u51fa\u672a"
                                    f"\u901a\u8fc7\u5b8c\u6210\u5ea6\u6821\u9a8c"
                                    f"\uff08{quality_reason}\uff09\uff0c\u672a\u767b"
                                    f"\u8bb0\u4e3a\u4ea4\u4ed8\u7269\uff0c\u9700\u91cd"
                                    f"\u505a\u6216\u4e0a\u62a5\u4e0a\u7ea7\u3002"
                                ),
                                source_node=node_id,
                                tags=["incomplete"],
                            )
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug(
                                "contract: incomplete note publish failed", exc_info=True
                            )
                    return
                # Resolve the artifact name/size ONCE so every surface
                # (task card / blackboard panel / command-center file card)
                # shares an identical, download-ready contract. The three
                # React consumers historically expect DIFFERENT key names:
                #   * ProjectTask.file_attachments -> {filename,file_path,file_size}
                #     (FileAttachmentCard's native ``FileAttachment`` shape)
                #   * OrgBlackboardPanel entry.attachments -> {filename,path,size_bytes}
                #   * OrgChatPanel ``org:blackboard_update`` -> filename + file_path|path
                # so we emit BOTH path spellings + both size spellings to
                # keep all three rendering the same downloadable file.
                art_name = Path(str(artifact_path)).name if artifact_path else ""
                art_size = 0
                if artifact_path:
                    try:
                        art_size = Path(str(artifact_path)).stat().st_size
                    except OSError:
                        art_size = 0
                # B5: close out this run's task. Prefer the EXACT task
                # by chain id (precise tree); fall back to the node's
                # most recent in-progress task for chain-less producers.
                if ps_registry is not None:
                    ps = ps_registry.for_org(org_id)
                    try:
                        open_tasks: list[dict[str, Any]] = []
                        if chain_id:
                            open_tasks = [
                                t
                                for t in ps.all_tasks(chain_id=chain_id)
                                if t.get("status") == "in_progress"
                            ]
                        if not open_tasks:
                            open_tasks = [
                                t
                                for t in ps.all_tasks(assignee=node_id)
                                if t.get("status") == "in_progress"
                                # The submit-time command task is closed only by
                                # finalize_command_project(). A root node can
                                # finish several supervisor turns before the
                                # command converges, so node-based fallback must
                                # not mistake a stage result for final delivery.
                                and (
                                    not command_id
                                    or str(t.get("chain_id") or "") != command_id
                                )
                            ]
                        if open_tasks:
                            t = open_tasks[-1]
                            # P4 (甘特图): the DELIVERED transition auto-stamps
                            # ``delivered_at`` in update_task -> the timeline bar
                            # gets a real end (``completed_at`` stays reserved for
                            # the user 验收/accepted transition).
                            updates: dict[str, Any] = {
                                "status": "delivered",
                                "progress_pct": 100,
                            }
                            if artifact_path:
                                updates["file_attachments"] = [
                                    {
                                        "filename": art_name,
                                        "file_path": str(artifact_path),
                                        "file_size": art_size,
                                    }
                                ]
                            from .project_models import TaskStatus

                            updates["status"] = TaskStatus.DELIVERED
                            ps.update_task(t.get("project_id"), t.get("id"), updates)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("contract: task close failed", exc_info=True)
                # B4 + B6: record a blackboard fact + downloadable resource.
                if bb_registry is not None and (output_len or artifact_path):
                    attachments = None
                    if artifact_path:
                        # ``path`` + ``size_bytes`` is the canonical blackboard
                        # attachment shape (OrgBlackboardPanel reads exactly
                        # those keys); ``file_path``/``file_size`` are added as
                        # aliases so a raw FileAttachmentCard also works.
                        attachments = [
                            {
                                "filename": art_name,
                                "path": str(artifact_path),
                                "file_path": str(artifact_path),
                                "size_bytes": art_size,
                                "file_size": art_size,
                            }
                        ]
                    content = (
                        f"\u8282\u70b9 {node_id} \u5b8c\u6210\u4ea4\u4ed8"
                        f"\uff08{output_len} \u5b57\uff09"
                    )
                    try:
                        bb_registry.publish(
                            org_id,
                            content,
                            source_node=node_id,
                            tags=["deliverable"],
                            attachments=attachments,
                        )
                        # Cross-session replay fix (2026-06): the org-tier
                        # ``publish`` above is the only durable completion record
                        # the blackboard kept, so ``/memory?scope=node`` was empty
                        # after a restart (``memory/nodes/*.jsonl`` never written by
                        # the deliverable path; only the flaky P3 live process-log
                        # touched it). Mirror the completion FACT into the NODE and
                        # DEPARTMENT tiers so every delivery leaves a durable,
                        # disk-backed node-level record that replays across sessions.
                        try:
                            bb = bb_registry.for_org(org_id)
                            bb.write_node(
                                node_id,
                                content,
                                tags=["deliverable"],
                                attachments=attachments,
                            )
                            dept = self._resolve_node_department(org_id, node_id)
                            if dept:
                                bb.write_department(
                                    dept,
                                    content,
                                    source_node=node_id,
                                    tags=["deliverable"],
                                    attachments=attachments,
                                )
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug(
                                "contract: node/dept deliverable mirror failed",
                                exc_info=True,
                            )
                        # B6: the command-center timeline renders a file card
                        # only when the update advertises ``memory_type=resource``
                        # + filename + path. Without these fields OrgChatPanel
                        # fell through to a plain "blackboard updated" line and
                        # the deliverable was never downloadable from chat.
                        ws_payload: dict[str, Any] = {
                            "org_id": org_id,
                            "node_id": node_id,
                        }
                        if artifact_path:
                            ws_payload.update(
                                {
                                    "memory_type": "resource",
                                    "filename": art_name,
                                    "file_path": str(artifact_path),
                                    "path": str(artifact_path),
                                    "file_size": art_size,
                                    "size": art_size,
                                }
                            )
                        await self._broadcast_ws_safe(
                            "org:blackboard_update",
                            ws_payload,
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("contract: blackboard publish failed", exc_info=True)
                # 图3: the polished PDF is the FINAL 主编 report. A multi-turn
                # root keeps re-integrating, so the FIRST root finish is NOT the
                # final version. Instead of rendering here, just REMEMBER the
                # most-recent root (.md) deliverable for this command; the PDF is
                # rendered once at convergence (``finalize_command_project``)
                # from this last-recorded artifact, guaranteeing pdf == final md.
                if (
                    artifact_path
                    and str(artifact_path).endswith(".md")
                    and output_len > 120
                    and payload.get("artifact_role") == "final"
                ):
                    try:
                        org = self.get_org(org_id)
                        node = org.get_node(node_id) if org is not None else None
                        if node is not None and getattr(node, "level", None) == 0:
                            cid = str(payload.get("command_id") or chain_id or "")
                            if cid:
                                store = getattr(self, "_root_final_artifact", None)
                                if store is None:
                                    store = {}
                                    self._root_final_artifact = store
                                store[cid] = (node_id, str(artifact_path))
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("contract: record root artifact failed", exc_info=True)
        except Exception:  # noqa: BLE001 -- bridge must not poison dispatch
            _LOGGER.debug("OrgRuntime contract tap failed for %r", event_name, exc_info=True)

    async def _maybe_render_root_pdf(
        self,
        *,
        org_id: str,
        command_id: str,
        node_id: str,
        artifact_path: str,
        bb_registry: Any,
    ) -> None:
        """Render the root/主编 node's markdown deliverable to a PDF once.

        Only fires for the org's level-0 root node, at most once per command
        (so Chromium launches are bounded). Registers the PDF as a downloadable
        blackboard resource and emits ``org:blackboard_update(resource)`` so the
        command center shows a downloadable file card next to the .md.
        """
        # Resolve root-ness: only the level-0 node's own deliverable becomes the
        # final PDF report.
        try:
            org = self.get_org(org_id)
            node = org.get_node(node_id) if org is not None else None
            if node is None or getattr(node, "level", None) != 0:
                return
        except Exception:  # noqa: BLE001
            return
        if not command_id:
            return
        done = getattr(self, "_final_pdf_commands", None)
        if done is None:
            done = set()
            self._final_pdf_commands = done
        if command_id in done:
            return
        from pathlib import Path as _Path

        try:
            md_body = _Path(artifact_path).read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return
        if not md_body.strip():
            return
        pdf_path = str(_Path(artifact_path).with_suffix(".pdf"))
        from ._runtime_pdf import render_markdown_to_pdf

        rendered = await render_markdown_to_pdf(
            markdown_body=md_body,
            out_path=pdf_path,
            title="任务交付报告",
            meta=f"由根节点 {node_id} 汇总交付 · OpenAkita 组织编排",
        )
        if not rendered:
            return
        done.add(command_id)
        try:
            size = _Path(rendered).stat().st_size
        except OSError:
            size = 0
        name = _Path(rendered).name
        # Item 3: persist a PDF event so the history/activity REBUILD path can
        # backfill the final PDF download card after a page reload. The .md
        # deliverables already land in the event store via agent_run_finished,
        # but the PDF is rendered post-convergence and was only ever published
        # to the blackboard + live WS — invisible to a remount. Recording it as
        # a queryable event (carrying command_id + artifact_path) lets the
        # command center reattach the card identically to the live render.
        try:
            store = self.register_event_store(org_id)
            store.append(
                {
                    "type": "final_report_pdf",
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "artifact_path": rendered,
                    "output_len": size,
                }
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("final pdf event persist failed", exc_info=True)
        if bb_registry is not None:
            try:
                bb_registry.publish(
                    org_id,
                    f"主编已汇总并交付最终报告（PDF）：{name}",
                    source_node=node_id,
                    tags=["deliverable", "final_report", "pdf"],
                    attachments=[
                        {
                            "filename": name,
                            "path": rendered,
                            "file_path": rendered,
                            "size_bytes": size,
                            "file_size": size,
                        }
                    ],
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("contract: final pdf blackboard publish failed", exc_info=True)
        await self._broadcast_ws_safe(
            "org:blackboard_update",
            {
                "org_id": org_id,
                "node_id": node_id,
                "memory_type": "resource",
                "filename": name,
                "file_path": rendered,
                "path": rendered,
                "file_size": size,
                "size": size,
            },
        )


def get_runtime() -> OrgRuntime | None:
    """Return the process-wide :class:`OrgRuntime` singleton.

    P9.6a returns ``None``; the factory wiring lives in the
    lifecycle sibling (P9.6d) which sets the singleton on
    first ``start()``.
    """

    return _RUNTIME_SINGLETON


_RUNTIME_SINGLETON: OrgRuntime | None = None
