"""v2 ``OrgCommandService`` (P-RC-9 P9.4).

Replaces v1 ``openakita.orgs.command_service.OrgCommandService``
(963 LOC, 24 methods, ``OrgRuntime``-coupled) with a
Protocol-typed surface decoupled from the runtime via injected
Protocols (ADR-0011). Implements
:class:`openakita.orgs.node_scheduler.CommandDispatcher`
so P9.3 NodeScheduler can call ``service.dispatch`` without
circular imports.

Two architecturally-significant deltas vs v1:

1. ``self._runtime._has_active_delegations`` reach-in
   replaced by an injected :class:`CommandRuntimeProtocol`
   surface (4 awaitables + 3 sync accessors).
2. ``threading.Lock`` becomes ``asyncio.Lock`` (G-RC-9.2
   Nit-4 lock-type ruling). ``submit`` becomes async to
   align with the lock.

ADR refs: ADR-0011 (Protocol-typed decomposition); ADR-0012
(no shim under v1); ADR-0013 (wall-clock SLA asserted at the
service-plus-runtime integration level in P9.4e, not inside
this file -- the service is a pass-through to
``CommandRuntimeProtocol.send_command``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any, Protocol, runtime_checkable

from .command_models import (
    OrgCommandConflict,
    OrgCommandError,
    OrgCommandRequest,
    OrgOutputScope,
    new_command_id,
)

__all__ = [
    "BrainProtocol",
    "ChannelGatewayProtocol",
    "CommandRuntimeProtocol",
    "EventEmitterProtocol",
    "OrgCommandConflict",
    "OrgCommandError",
    "OrgCommandService",
    "OrgCommandServiceProtocol",
    "OrgLookupProtocol",
    "SessionManagerProtocol",
    "get_command_service",
    "set_command_service",
]

logger = logging.getLogger(__name__)

# v1 ``_CMD_TTL`` (3600 s) lifted verbatim. Running commands get 2x TTL
# for graceful shutdown (matches v1 ``_purge_old_commands`` body).
_CMD_TTL = 3600


# ---------------------------------------------------------------------------
# Public service Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class OrgCommandServiceProtocol(Protocol):
    """Public surface of every ``OrgCommandService`` impl.

    P9.4 ships :class:`OrgCommandService` as the only impl;
    P9.5+ may add a recording variant for integration tests.
    """

    async def dispatch(self, org_id: str, node_id: str, prompt: str) -> dict[str, Any]: ...
    async def submit(self, request: OrgCommandRequest) -> dict[str, Any]: ...
    def get_status(self, org_id: str, command_id: str) -> dict[str, Any] | None: ...
    async def cancel(self, org_id: str, command_id: str) -> dict[str, Any] | None: ...
    def subscribe_summary(
        self, command_id: str, *, surface: str = ..., target: str = ...
    ) -> asyncio.Queue[dict[str, Any]]: ...
    def unsubscribe_summary(
        self, command_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None: ...
    async def publish_summary(self, command_id: str, event: dict[str, Any]) -> None: ...
    def find_command_for_event(
        self, org_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None: ...
    def mark_delivered(self, command_id: str, *, surface: str, target: str, event: str) -> None: ...
    @property
    def commands(self) -> dict[str, dict[str, Any]]: ...
    def bridge_session_chat_id(self, org_id: str, target_node_id: str | None) -> str: ...


# ---------------------------------------------------------------------------
# Injected Protocols (ADR-0011 cross-subsystem boundary)
# ---------------------------------------------------------------------------


@runtime_checkable
class OrgLookupProtocol(Protocol):
    """Read-only org / node lookup.

    Replaces v1 ``self._runtime.get_org(org_id)`` reach-in.
    Returned object is duck-typed: callers touch ``.status``,
    ``.get_node(node_id)``, and ``.get_root_nodes()``. v1
    ``Organization`` + v2 ``OrgManager`` (P9.5) both satisfy
    structurally.
    """

    def get_org(self, org_id: str) -> Any | None: ...


@runtime_checkable
class CommandRuntimeProtocol(Protocol):
    """Runtime surface ``OrgCommandService`` needs (ADR-0011).

    Replaces every v1 ``self._runtime.<x>`` reach-in: 4
    awaitables + 3 sync accessors. ``has_active_delegations``
    exposes v1's leaked ``_has_active_delegations`` private.
    """

    async def send_command(
        self,
        org_id: str,
        target_node_id: str | None,
        prompt: str,
        *,
        command_id: str,
    ) -> dict[str, Any]: ...

    async def cancel_user_command(
        self,
        org_id: str,
        command_id: str,
        *,
        cancel_reason: str | None = None,
    ) -> dict[str, Any]: ...

    def has_active_delegations(self, org_id: str, root_node_id: str) -> bool: ...

    def get_command_tracker_snapshot(
        self, org_id: str, command_id: str
    ) -> dict[str, Any] | None: ...

    def get_event_store(self, org_id: str) -> Any: ...

    def get_inbox(self, org_id: str) -> Any: ...


@runtime_checkable
class SessionManagerProtocol(Protocol):
    """Minimal session manager surface for bridge persistence.

    v1 ``SessionManager`` satisfies this structurally so P9.8
    caller migration is one import line.
    """

    def get_session(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        create_if_missing: bool = ...,
    ) -> Any | None: ...

    def mark_dirty(self) -> None: ...


@runtime_checkable
class ChannelGatewayProtocol(Protocol):
    """Minimal channel gateway surface for IM forward dispatch.

    Replaces v1 ``from openakita.main import get_message_gateway``
    reach-in inside ``_dispatch_forwards``. v1 ``MessageGateway``
    satisfies the Protocol structurally.
    """

    async def send_text_reliably(
        self,
        *,
        channel: str,
        chat_id: str,
        text: str,
        record_to_session: bool = ...,
        user_id: str = ...,
        thread_id: str | None = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> bool: ...


@runtime_checkable
class EventEmitterProtocol(Protocol):
    """Minimal websocket / lifecycle event emitter.

    Replaces v1 ``websocket.broadcast_event/fire_event``
    reach-ins. v1 callables wrap into this shape with no
    behavioural drift.
    """

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None: ...

    def fire(self, event: str, payload: dict[str, Any]) -> None: ...


@runtime_checkable
class BrainProtocol(Protocol):
    """Minimal LLM frontend for ADR-0013 wall-clock SLA tests.

    P9.4e uses a one-method ``MockBrain`` so the wall-clock
    budget is dominated by the cancel pipeline, not the LLM
    mock. Production runtime uses :class:`SupervisorBrain` (3
    methods); this Protocol is SLA-tests-only.
    """

    async def respond(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Service implementation (scaffold; P9.4b/b2 land bodies)
# ---------------------------------------------------------------------------


class OrgCommandService:
    """Submit, track, cancel, and observe commands for any org.

    Construct with the five injected Protocols; only
    ``runtime`` + ``lookup`` are required for ``dispatch``.
    The four optional ones (session_manager / gateway /
    emitter) make those side effects no-ops when None, matching
    v1's degraded-mode behaviour.

    Concurrency: ``asyncio.Lock`` (G-RC-9.2 Nit-4 lock-type
    ruling). ``submit`` acquires ``self._lock`` before
    mutating ``self._commands`` / ``self._running_by_root``;
    ``cancel`` performs atomic single-key dict ops without
    the lock (safe under asyncio's single-thread invariant).
    """

    def __init__(
        self,
        runtime: CommandRuntimeProtocol,
        *,
        lookup: OrgLookupProtocol | None = None,
        session_manager: SessionManagerProtocol | None = None,
        gateway: ChannelGatewayProtocol | None = None,
        emitter: EventEmitterProtocol | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._runtime = runtime
        # v1 ``OrgRuntime`` exposes ``get_org`` + the runtime
        # methods, so callers passing a single instance for both
        # get v1-equivalent behaviour when ``lookup`` is omitted.
        self._lookup: OrgLookupProtocol = lookup if lookup is not None else runtime  # type: ignore[assignment]
        self._session_manager = session_manager
        self._gateway = gateway
        self._emitter = emitter
        self._commands: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._running_by_root: dict[tuple[str, str], str] = {}
        self._summary_subscribers: dict[
            str,
            list[
                tuple[
                    asyncio.Queue[dict[str, Any]],
                    asyncio.AbstractEventLoop,
                    str,
                    str,
                ]
            ],
        ] = {}
        # Sprint-2 P0-2 (audit v2 §5 F1-new): per-command outcome cache
        # populated by event-bus subscriptions so ``get_status`` and the
        # background ``_run_minimal`` finaliser can reflect the real
        # ``agent_run_failed`` / ``agent_run_finished`` result instead of
        # the stale "submitted -> done" flip ``runtime.send_command``
        # alone produces. Keyed by ``command_id``; values are a small
        # dict ``{"event", "reason", "error", "node_id", "ts"}``.
        self._command_outcomes: dict[str, dict[str, Any]] = {}
        # Sprint-3 P0-2 (audit v3 §5.3): per-command inflight task map.
        # Pre-fix the cancel endpoint accepted the request, recorded the
        # ``user_command_cancelled`` event, but had no asyncio handle on
        # the running task, so the underlying ``Brain.messages_create_async``
        # kept burning tokens until the natural completion. We now stash
        # the ``asyncio.Task`` created by ``_schedule_run`` so ``cancel``
        # can call ``task.cancel()`` and have ``CancelledError`` propagate
        # all the way down to the httpx request, closing the LLM
        # connection. Tasks are popped in the ``_run_minimal`` finaliser
        # (success / failure / cancelled all converge there) so the dict
        # never leaks across commands.
        self._inflight_tasks: dict[str, asyncio.Task[Any]] = {}
        # Sprint-5 P0-2 (audit v5 §5.2 + v15 §6.2.4 B6.4): secondary
        # index keyed by org_id so ``cancel_all_for_org`` can stop every
        # in-flight task for an org in O(set size) without scanning the
        # whole command dict. Pre-fix the user pressed "stop org" and
        # the spec flipped to STOPPED but per-command tasks kept burning
        # tokens (3 sprints of v* audits flagged this; Sprint-4 §3
        # explicitly punted to this commit). Populated lock-step with
        # ``_inflight_tasks`` in ``_schedule_run`` / ``_run_minimal`` /
        # ``_purge_old_commands`` / ``cancel_all_for_org``.
        self._inflight_by_org: dict[str, set[str]] = {}
        # Sprint-5 unexpected-finding #2 (audit v5 §5.3): watchdog task
        # that scans ``_inflight_tasks`` periodically and cancels any
        # command whose wall-clock elapsed exceeds the org's
        # ``watchdog_stuck_threshold_s``. The task is opt-in (caller
        # must call :meth:`start_watchdog`) so legacy callers (contract
        # / parity tests that construct :class:`OrgCommandService`
        # without an event loop running for the full default budget)
        # keep working unchanged. Default poll interval is 30 s; tests
        # can override via :meth:`configure_watchdog`.
        #
        # Sprint-8 P0-A (v19 audit ``_orgs_business_capability_audit_v8.md``
        # §2 + §8.1): the v19 B-module test ran 19/25 cases before
        # crashing at L4.5 with a 14/19 done rate. Root-cause analysis
        # showed the slow-task tail can legitimately reach 160-180 s on
        # multi-node orgs (L3.4 was still ``running`` at the test wait
        # cap of 180 s, then reaped by the watchdog much later). The
        # legacy 1800 s (30 min) default left genuinely stuck commands
        # holding the inflight slot for half an hour while burning
        # tokens, which is far longer than the longest legitimate run
        # observed across v13-v19 audits (~3 min). Sprint-8 tightens
        # the default to **600 s (10 min)** so a stuck LLM gets reaped
        # within a 5x safety margin of the slowest legit task. Tests
        # that rely on millisecond budgets continue to inject a custom
        # value via ``configure_watchdog(default_threshold_secs=...)``;
        # production deployments that want the legacy 30 min envelope
        # set ``watchdog_stuck_threshold_s=1800`` on the org spec.
        self._watchdog_task: asyncio.Task[None] | None = None
        self._watchdog_poll_interval_secs: float = 30.0
        self._watchdog_default_threshold_secs: float = 600.0
        self._event_bus = event_bus
        if event_bus is not None:
            self._wire_event_bus(event_bus)

    # ------------------------------------------------------------------
    # Event-bus wiring (Sprint-2 P0-2 -- audit v2 §5 F1-new)
    # ------------------------------------------------------------------

    # Names of events the executor emits during the per-node agent run.
    # We pre-list them so subscription is explicit and we do not have to
    # rely on a wildcard ``add_tap`` (some bus impls only support the
    # named-subscriber surface).
    #
    # Sprint-3 P0-2 (audit v3 §5.3) adds ``agent_run_cancelled`` so a
    # user-initiated cancel surfaces in the outcome cache + ``event_ref``
    # snapshot as a *distinct* terminal state instead of being either
    # silently absent or mis-classified as ``agent_run_failed``.
    _AGENT_RUN_EVENT_NAMES: tuple[str, ...] = (
        "agent_run_started",
        "agent_run_finished",
        "agent_run_failed",
        "agent_run_cancelled",
    )

    def _wire_event_bus(self, event_bus: Any) -> None:
        """Subscribe :meth:`_handle_agent_event` to the executor's events.

        Failures here log + return: the v1 contract is "service must
        not refuse to start because the event bus is missing"; in that
        case ``get_status`` simply continues to read the legacy
        ``_run_minimal``-only state, which is still strictly better
        than the pre-Sprint-2 silence.

        Sprint-3 P0-2: each subscription captures the event name in a
        closure so the handler does not have to re-derive it from the
        payload shape. The pre-Sprint-3 shape-based inference confused
        ``agent_run_cancelled`` (which carries ``reason="user_cancel"``)
        with ``agent_run_failed``; routing by the real event name makes
        the outcome cache unambiguous.
        """

        subscribe = getattr(event_bus, "subscribe", None)
        if not callable(subscribe):
            logger.warning(
                "[OrgCmd] event_bus has no subscribe(); "
                "command status reconciliation disabled"
            )
            return
        for name in self._AGENT_RUN_EVENT_NAMES:
            try:
                subscribe(name, self._make_event_handler(name))
            except Exception:  # noqa: BLE001 -- bus must not block service init
                logger.exception(
                    "[OrgCmd] failed to subscribe to event %r; reconciliation degraded",
                    name,
                )

    def _make_event_handler(self, event_name: str) -> Any:
        """Return a sync ``(payload) -> None`` closure that forwards
        ``(event_name, payload)`` to :meth:`_handle_agent_event`.

        Factored out so the wiring loop stays single-line and so tests
        that exercise the handler directly (``test_command_status_
        reconciliation``) can still call ``_handle_agent_event`` with
        a single ``payload`` arg via the legacy back-compat path.
        """

        def _h(payload: dict[str, Any]) -> None:
            self._handle_agent_event(payload, event_name=event_name)

        return _h

    def _handle_agent_event(
        self,
        payload: dict[str, Any],
        *,
        event_name: str | None = None,
    ) -> None:
        """Cache the latest agent-run outcome for a command id.

        Idempotent: handlers may fire multiple times during a single
        run (started -> finished, started -> failed). We always keep
        the latest payload so a started+failed sequence resolves to
        ``failed`` and a started+finished sequence resolves to
        ``finished``. The handler is sync (the bus accepts both sync
        and async handlers); callers in this service are sync too,
        so no event-loop hop is needed.

        When ``event_name`` is provided (the new ``_make_event_handler``
        path) we record it verbatim. When it is missing (legacy direct
        callers / Sprint-2 tests) we fall back to the payload-shape
        inference Sprint-2 shipped with -- preserving back-compat with
        ``test_command_status_reconciliation.py`` which calls this
        method via ``bus.emit`` -> single-arg subscription.
        """

        if not isinstance(payload, dict):
            return
        command_id = payload.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            return
        if event_name is None:
            # Legacy shape-based inference (Sprint-2 back-compat).
            if "reason" in payload or "error" in payload:
                event_name = "agent_run_failed"
            elif "output_len" in payload:
                event_name = "agent_run_finished"
            else:
                event_name = "agent_run_started"
        prior = self._command_outcomes.get(command_id) or {}
        new_outcome: dict[str, Any] = {
            "event": event_name,
            "reason": payload.get("reason"),
            "error": payload.get("error"),
            "node_id": payload.get("node_id"),
            "output_len": payload.get("output_len"),
            "ts": time.time(),
        }
        # Sprint-5 P0-2 / unexpected-finding #2: preserve the
        # ``cancelled_by`` (and watchdog quantities) the seed write in
        # ``cancel_all_for_org`` / ``_watchdog_tick`` deposited, unless
        # the inbound payload carries an explicit value. Without this
        # the natural ``agent_run_cancelled`` event from the executor
        # would clobber our marker the instant it arrives, and the
        # events.jsonl reader could no longer distinguish stop-org /
        # watchdog cancels from user-initiated cancels.
        for key in ("cancelled_by", "elapsed_s", "threshold_s"):
            value = payload.get(key)
            if value is None:
                value = prior.get(key)
            if value is not None:
                new_outcome[key] = value
        self._command_outcomes[command_id] = new_outcome

    # ------------------------------------------------------------------
    # Accessors (parity gate -- byte-for-byte view of v1 internals)
    # ------------------------------------------------------------------

    @property
    def commands(self) -> dict[str, dict[str, Any]]:
        """Live view of in-flight + recently-completed commands.

        Mutating the returned dict is undefined; v1 callers
        treated it as read-only and v2 keeps that contract.
        """
        return self._commands

    def bridge_session_chat_id(self, org_id: str, target_node_id: str | None) -> str:
        """Deterministic chat-id used by the desktop-session bridge.

        Byte-for-byte mirror of v1; existing desktop sessions
        rely on this storage-layout prefix.
        """
        if target_node_id:
            return f"org_{org_id}_node_{target_node_id}"
        return f"org_{org_id}"

    # ------------------------------------------------------------------
    # CommandDispatcher boundary (P9.3 NodeScheduler)
    # ------------------------------------------------------------------

    async def dispatch(self, org_id: str, node_id: str, prompt: str) -> dict[str, Any]:
        """Implements :class:`CommandDispatcher` for NodeScheduler.

        Thin pass-through to ``send_command``. Scheduled
        commands are runtime-internal (no user waits, no
        tracking record); the signature matches v1
        ``OrgRuntime.send_command`` byte-for-byte modulo
        ``command_id``, which is minted here because the
        schedule loop has no UI id to thread.
        """
        return await self._runtime.send_command(
            org_id,
            node_id,
            prompt,
            command_id=new_command_id(),
        )

    # ------------------------------------------------------------------
    # User-facing verbs (dispatch table is here so future verbs extend
    # without touching the if/elif chain v1 grew over time).
    # ------------------------------------------------------------------

    async def submit(self, request: OrgCommandRequest) -> dict[str, Any]:
        """Submit a user command for ``request.org_id``.

        Byte-for-byte parity with v1 ``submit`` modulo:

        * v1 is sync; v2 is async (asyncio.Lock alignment).
        * v1 ``uuid.uuid4().hex[:12]`` becomes
          ``new_command_id`` (Nit-1 monotonic mint).

        Behaviour: validates the org is running, resolves the
        root node, conflict-checks per-root, records the
        command in ``self._commands`` + ``self._running_by_root``,
        then schedules ``_run`` as a background task. Returns
        the v1 dict shape ``{"command_id", "status",
        "root_node_id"}`` so REST callers see no shape drift.
        """
        content = (request.content or "").strip()
        if not content:
            raise OrgCommandError("content is required")
        # Defensive normalization (exploratory v12 §10.1 second guard).
        # The REST endpoint now defaults ``output_scope`` to ``INTERNAL``
        # via the Pydantic schema, but other internal callers (IM
        # gateway, CLI, parity harness) build ``OrgCommandRequest`` by
        # hand. If any of them slips a ``None`` through, fall back to
        # ``INTERNAL`` instead of crashing on ``.value``.
        if request.output_scope is None:
            request.output_scope = OrgOutputScope.INTERNAL

        org = self._require_org_running(request.org_id)
        if request.target_node_id and not org.get_node(request.target_node_id):
            raise OrgCommandError(f"Node not found: {request.target_node_id}")
        root_node_id = self._resolve_command_root_id(org, request.target_node_id)
        if not root_node_id:
            raise OrgCommandError("Organization has no root nodes")

        self._purge_old_commands()
        command_id = new_command_id()
        root_key = (request.org_id, root_node_id)
        now = time.time()
        run_content = content
        if request.continue_previous:
            run_content = self._build_continue_content(
                request.org_id,
                root_node_id,
                content,
            )

        async with self._lock:
            existing_id = self._running_by_root.get(root_key)
            existing = self._commands.get(existing_id or "")
            if existing and existing.get("status") == "running":
                if not request.replace_existing:
                    raise OrgCommandConflict(
                        "组织上有命令正在执行，请稍后或显式取消/替换。",
                        command_id=existing_id or "",
                    )
                existing["cancel_requested_by_user"] = True
                existing["cancel_requested_at"] = now

            self._commands[command_id] = {
                "command_id": command_id,
                "org_id": request.org_id,
                "root_node_id": root_node_id,
                "target_node_id": request.target_node_id,
                "status": "running",
                "phase": "running",
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
                "finished_at": None,
                "origin_surface": request.origin_surface.value,
                "output_scope": request.output_scope.value,
                "source": request.source.to_dict(),
                "delivered_to": [],
                "continue_previous": request.continue_previous,
                "forward_to": [ft.to_dict() for ft in request.forward_to],
            }
            self._running_by_root[root_key] = command_id

        # NOTE: P9.4b2 wires the bridge / blackboard mirror + the
        # background ``_run`` task. For P9.4b the command is
        # recorded and the cancel path works; the runtime is
        # invoked synchronously here so callers can still
        # observe ``status="done"``. This keeps P9.4b under the
        # 350 LOC ceiling.
        run_request = OrgCommandRequest(
            org_id=request.org_id,
            content=run_content,
            target_node_id=request.target_node_id,
            source=request.source,
            origin_surface=request.origin_surface,
            output_scope=request.output_scope,
            replace_existing=request.replace_existing,
            continue_previous=request.continue_previous,
            forward_to=list(request.forward_to),
        )
        self._schedule_run(
            run_request,
            command_id,
            root_node_id,
            replace_existing_id=existing_id if request.replace_existing else None,
        )
        return {
            "command_id": command_id,
            "status": "running",
            "root_node_id": root_node_id,
        }

    def get_status(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        """Live status snapshot for ``command_id``.

        Byte-for-byte parity with v1: ``cmd[*]`` direct fields
        + tracker-snapshot overlay via
        :class:`CommandRuntimeProtocol`. Read-only, no lock --
        v1 contract: the caller may see a snapshot one event
        older than live state.

        Sprint-2 P0-2 overlay: when a matching ``agent_run_failed`` /
        ``agent_run_finished`` event has fired, surface its
        ``event_ref`` + (for failures) the reason / error string so
        callers can distinguish a real success from the legacy
        "always 200 with phase=done" lie the v13 audit flagged.
        """
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None
        try:
            live = self._runtime.get_command_tracker_snapshot(org_id, command_id)
        except Exception:
            live = None
        phase = cmd.get("phase") or cmd["status"]
        if cmd["status"] == "running":
            if live:
                phase = live.get("phase") or phase
            try:
                es = self._runtime.get_event_store(org_id)
                for ev in es.query(event_type="command_phase", limit=20) or []:
                    data = ev.get("data") or {}
                    if data.get("command_id") == command_id:
                        phase = data.get("phase") or phase
                        break
            except Exception:
                pass
        result: dict[str, Any] = {
            "command_id": cmd["command_id"],
            "status": cmd["status"],
            "phase": phase,
            "root_node_id": cmd.get("root_node_id", ""),
            "result": cmd["result"],
            "error": cmd["error"],
            "elapsed_s": round(time.time() - cmd["created_at"], 1),
            "cancel_requested_by_user": bool(cmd.get("cancel_requested_by_user")),
            "origin_surface": cmd.get("origin_surface"),
            "output_scope": cmd.get("output_scope"),
        }
        outcome = self._command_outcomes.get(command_id)
        if outcome is not None:
            event_ref = outcome.get("event")
            if event_ref:
                result["event_ref"] = event_ref
            if event_ref == "agent_run_failed" and not result.get("error"):
                # Mirror the persisted error onto the live snapshot the
                # frontend reads. ``_run_minimal`` already does this for
                # finalised commands; this branch covers the read-while-
                # running window before the finaliser flips ``cmd``.
                reason = outcome.get("reason")
                error = outcome.get("error")
                rendered = " ".join(s for s in (reason, error) if s).strip()
                if rendered:
                    result["error"] = rendered
            # Sprint-3 P0-2: surface ``phase=cancelled`` while the
            # ``_run_minimal`` finaliser is still unwinding past the
            # cancel point. The cmd dict will catch up shortly, but
            # the SSE stream and pollers may sample this snapshot in
            # the meantime and we want them to see the real terminal
            # state immediately.
            if event_ref == "agent_run_cancelled" and result["status"] == "running":
                result["status"] = "cancelled"
                result["phase"] = "cancelled"
        if live:
            result.update(_live_snapshot_view(live))
        elif isinstance(cmd.get("result"), dict):
            cr = cmd["result"]
            result.update(
                {
                    "warning": cr.get("warning"),
                    "stopped_by_watchdog": bool(cr.get("stopped_by_watchdog")),
                    "cancelled_by_user": bool(cr.get("cancelled_by_user")),
                }
            )
        return result

    async def cancel(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        """Cancel an in-flight command.

        Byte-for-byte parity with v1: ``None`` on missing /
        wrong-org; ``{"ok": True, "already_done": True}`` on
        terminal; otherwise the runtime cancel
        + ``cancel_requested_by_user`` flag + the cancelled
        IM forward via :class:`ChannelGatewayProtocol`. The
        broadcast goes through :class:`EventEmitterProtocol`
        (no-op when emitter is None -- v1 degraded-mode
        equivalence).

        Sprint-3 P0-2 (audit v3 §5.3): in addition to flipping the
        tracker state via :class:`CommandRuntimeProtocol`, we now also
        call ``task.cancel()`` on the inflight ``_run_minimal`` task so
        ``CancelledError`` propagates down to the LLM ``httpx`` request
        and the connection is closed. Pre-fix this method only emitted
        the ``user_command_cancelled`` event; the LLM stayed running
        until natural completion 60-180 s later, billing real tokens
        even though the UI button was already greyed out.
        """
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None
        if cmd.get("status") != "running":
            return {"ok": True, "command_id": command_id, "already_done": True}
        # Sprint-3 P0-2: cancel the asyncio task *before* awaiting the
        # runtime cancel. Two reasons:
        # 1. ``task.cancel()`` is synchronous: it schedules a
        #    ``CancelledError`` to fire at the next await, which keeps
        #    the cancel signal in flight even if ``runtime.cancel_user_command``
        #    blocks (rare, but possible if the lookup is slow).
        # 2. The runtime cancel is async and may itself raise; we want
        #    ``task.cancel()`` to have already landed so a subsequent
        #    exception on the runtime path does not strand a running
        #    LLM call.
        task = self._inflight_tasks.get(command_id)
        if task is not None and not task.done():
            task.cancel()
        result = await self._runtime.cancel_user_command(org_id, command_id)
        self._update_command_state(
            command_id,
            cancel_requested_by_user=True,
            cancel_requested_at=time.time(),
        )
        if self._emitter is not None:
            try:
                await self._emitter.broadcast(
                    "org:command_cancelled",
                    {
                        "org_id": org_id,
                        "command_id": command_id,
                        "by": "user",
                        "cancelled_roots": result.get("cancelled_roots", []),
                    },
                )
            except Exception:
                logger.debug(
                    "[OrgCmd] broadcast org:command_cancelled failed",
                    exc_info=True,
                )
        await self._dispatch_forwards(
            org_id,
            command_id,
            "cancelled",
            "用户在指挥台对该任务强制取消，正在执行的子节点应该停止。",
        )
        return {
            "ok": True,
            "command_id": command_id,
            "cancelled_roots": result.get("cancelled_roots", []),
        }

    # ------------------------------------------------------------------
    # Sprint-6 P0-2: cancel-source bridge (RCA _v17_p1_rca.md §2.5)
    # ------------------------------------------------------------------

    def get_cancel_source(self, command_id: str) -> str | None:
        """Return the ``cancelled_by`` source stored in the outcome cache.

        The Sprint-5 commit pre-seeded
        ``_command_outcomes[cid]["cancelled_by"]`` in
        :meth:`cancel_all_for_org` (``stop_org``) and the watchdog
        (``watchdog``) but the ``agent_run_cancelled`` event the
        executor emits on ``CancelledError`` hard-coded
        ``reason="user_cancel"`` -- the cache marker never reached
        disk. Sprint-6 P0-2 wires the executor to consult this
        accessor before emitting so events.jsonl carries the source
        verbatim. Returns ``None`` when the outcome is missing or
        carries no source (user-initiated cancels fall through and
        keep the legacy ``user_cancel`` reason).

        Sprint-7 P0-A (audit v7 §1.2 + §5 finding 5): the source string
        was previously interpolated as ``stop_org:<reason>`` by the
        :func:`api.server._on_stop_org_cancel_inflight` shim, which
        produced ``stop_org:stop`` compound values on disk. The shim
        now passes the literal ``"stop_org"`` to keep the taxonomy at
        exactly three values: ``user_cancel``, ``stop_org``,
        ``watchdog``.
        """

        outcome = self._command_outcomes.get(command_id)
        if not isinstance(outcome, dict):
            return None
        source = outcome.get("cancelled_by")
        if isinstance(source, str) and source:
            return source
        return None

    # ------------------------------------------------------------------
    # Sprint-5 P0-2: org-wide cancel + watchdog
    # ------------------------------------------------------------------

    async def cancel_all_for_org(
        self, org_id: str, *, reason: str = "stop_org"
    ) -> list[str]:
        """Cancel every in-flight command for one org. Returns cid list.

        Sprint-5 P0-2 (audit ``_orgs_business_capability_audit_v5.md``
        §5.2 #1 + v15 §6.2.4 B6.4): pre-fix ``POST /api/v2/orgs/<id>/stop``
        flipped the org state machine to STOPPED but per-command
        ``_inflight_tasks`` kept the LLM connection open until natural
        completion. We now offer this org-scoped cancel hook so the
        lifecycle ``on_stop_org`` callback can wire propagation through
        :class:`OrgLifecycleManager`.

        Behaviour mirrors :meth:`cancel`: ``task.cancel()`` is the
        synchronous side-effect; the ``_run_minimal`` ``CancelledError``
        branch (Sprint-3) writes the outcome + event. Here we *also*
        seed ``_command_outcomes`` with ``cancelled_by=stop_org`` so
        downstream filters can tell user-cancel apart from org-stop
        cancel even after the event arrives.

        The method is async only because the caller (lifecycle manager)
        is async; the body itself does no awaiting beyond best-effort
        runtime ``cancel_user_command`` calls (those may await an
        internal lock and we want to fire one per command to keep the
        runtime tracker / dispatcher in sync).
        """

        cids = list(self._inflight_by_org.get(org_id, set()))
        if not cids:
            return []
        logger.info(
            "[OrgCmd] stop-org cancelling %d in-flight commands (org=%s, reason=%s)",
            len(cids),
            org_id,
            reason,
        )
        for cid in cids:
            task = self._inflight_tasks.get(cid)
            if task is not None and not task.done():
                task.cancel()
            # Seed the outcome cache so ``get_status`` overlays
            # ``phase=cancelled`` immediately even before
            # ``_run_minimal`` catches the CancelledError. Mark the
            # source so events.jsonl readers can distinguish
            # user-initiated from org-stop-initiated cancels.
            self._command_outcomes[cid] = {
                "event": "agent_run_cancelled",
                "reason": reason,
                "error": None,
                "node_id": None,
                "output_len": None,
                "ts": time.time(),
                "cancelled_by": reason,
            }
            # Best-effort: also notify the runtime layer so the
            # dispatch tracker / chain accounting agrees with us.
            # Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5): pass the
            # cancel source through so the dispatch sibling can
            # stamp ``cancelled_by`` on the events.jsonl payload
            # instead of always writing ``user_cancel``. Without this
            # the v17 audit saw 0/5 stop-org cases tagged with
            # ``cancelled_by=stop_org`` on disk (single-plane Sprint-5
            # fix; outcome cache only).
            try:
                await self._runtime.cancel_user_command(
                    org_id, cid, cancel_reason=reason
                )
            except Exception:  # noqa: BLE001 -- runtime cancel is best-effort here
                logger.debug(
                    "[OrgCmd] runtime cancel_user_command raised during stop-org "
                    "(org=%s cid=%s)",
                    org_id,
                    cid,
                    exc_info=True,
                )
        return cids

    def configure_watchdog(
        self,
        *,
        poll_interval_secs: float | None = None,
        default_threshold_secs: float | None = None,
    ) -> None:
        """Tweak the watchdog timing knobs before :meth:`start_watchdog`.

        Sprint-5 unexpected-finding #2 (audit v5 §5.3): the v16 audit's
        ``B5 failure injection`` saw 4/6 cases time out because the
        LLM happily burnt 600 s+ on a recursive / sleep-style prompt.
        The org spec already exposes ``watchdog_stuck_threshold_s``
        but **nothing was reading it**: the watchdog loop never ran.
        We now ship a real loop that scans ``_inflight_tasks`` and
        cancels stuck commands.

        Sprint-8 P0-A (v19 audit §2 + §8.1): the production default
        is **600 s** (was 1800 s in Sprint-5 through Sprint-7). The
        two knobs let tests run a 2 s budget against a 0.1 s poll
        instead of the production 30 s / 600 s defaults.
        """

        if poll_interval_secs is not None:
            self._watchdog_poll_interval_secs = max(0.05, float(poll_interval_secs))
        if default_threshold_secs is not None:
            self._watchdog_default_threshold_secs = max(
                1.0, float(default_threshold_secs)
            )

    def start_watchdog(self) -> bool:
        """Spawn the periodic stuck-task scanner. Returns ``False`` if already running.

        Idempotent: a second call while the task is alive returns
        ``False`` and leaves the live task untouched.
        """

        if self._watchdog_task is not None and not self._watchdog_task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop yet (test fixture instantiating service eagerly
            # outside an async context). Defer; the caller can retry
            # after the loop is up.
            return False
        self._watchdog_task = loop.create_task(
            self._watchdog_loop(), name="openakita-orgs-command-watchdog"
        )
        logger.info(
            "[OrgCmd] command watchdog started (poll=%.1fs, default_threshold=%.0fs)",
            self._watchdog_poll_interval_secs,
            self._watchdog_default_threshold_secs,
        )
        return True

    async def stop_watchdog(self, *, timeout: float = 2.0) -> None:
        """Cancel the watchdog task. Idempotent / safe at shutdown."""

        task = self._watchdog_task
        self._watchdog_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=max(0.1, timeout))
        except (asyncio.CancelledError, TimeoutError):
            pass
        except Exception:  # noqa: BLE001
            logger.debug("[OrgCmd] watchdog stop raised", exc_info=True)

    async def _watchdog_loop(self) -> None:
        """Periodic scan: cancel commands that exceed their per-org budget.

        The loop swallows all per-iteration exceptions so a transient
        bug (lookup raising, malformed spec) cannot poison the loop --
        the next tick re-tries. Cancel is sticky: a command we cancel
        gets ``cancelled_by=watchdog`` so the v17 audit can distinguish
        wall-clock kills from user / stop-org cancels.
        """

        try:
            while True:
                await asyncio.sleep(self._watchdog_poll_interval_secs)
                try:
                    self._watchdog_tick()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "[OrgCmd] watchdog tick raised; continuing",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            return

    def _watchdog_tick(self) -> None:
        """One scan over the in-flight task table."""

        now = time.time()
        # Snapshot the keys so we can mutate the dict via cancel
        # propagation without "dict changed size during iteration".
        for cid in list(self._inflight_tasks.keys()):
            task = self._inflight_tasks.get(cid)
            if task is None or task.done():
                continue
            cmd = self._commands.get(cid)
            if cmd is None:
                continue
            created_at = float(cmd.get("created_at") or 0.0)
            if created_at <= 0:
                continue
            org_id = cmd.get("org_id")
            threshold = self._resolve_watchdog_threshold(org_id)
            if threshold <= 0:
                # Org explicitly disabled watchdog.
                continue
            elapsed = now - created_at
            if elapsed < threshold:
                continue
            logger.warning(
                "[OrgCmd] watchdog killing stuck command (org=%s cid=%s "
                "elapsed=%.1fs threshold=%.0fs)",
                org_id,
                cid,
                elapsed,
                threshold,
            )
            task.cancel()
            # Seed the outcome cache with a watchdog-specific marker so
            # ``get_status`` immediately reflects the kill (the
            # ``_run_minimal`` CancelledError branch will catch up and
            # overwrite ``cancelled_by`` to ``user`` by default; the
            # outcome cache write here keeps the explicit
            # ``cancelled_by=watchdog`` source for the events.jsonl
            # reader to pick up).
            self._command_outcomes[cid] = {
                "event": "agent_run_watchdog_killed",
                "reason": "watchdog_stuck_threshold_exceeded",
                "error": None,
                "node_id": None,
                "output_len": None,
                "ts": now,
                "cancelled_by": "watchdog",
                "elapsed_s": round(elapsed, 1),
                "threshold_s": int(threshold),
            }
            # Emit the explicit event for events.jsonl (best-effort).
            #
            # Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.7.1 + §2.5):
            # include ``cancelled_by="watchdog"`` on the on-disk
            # payload so events.jsonl readers can attribute the kill
            # to the watchdog even before the
            # follow-on ``agent_run_cancelled`` arrives. Pre-Sprint-6
            # the field lived only in the outcome cache, so any
            # external auditor scanning the JSONL had no way to
            # tell apart a watchdog kill from a user / stop-org
            # cancel (both later events used the same hard-coded
            # ``user_cancel`` reason).
            if self._event_bus is not None:
                emit = getattr(self._event_bus, "emit", None)
                if callable(emit):
                    try:
                        coro = emit(
                            "agent_run_watchdog_killed",
                            {
                                "org_id": org_id,
                                "command_id": cid,
                                "elapsed_s": round(elapsed, 1),
                                "threshold_s": int(threshold),
                                "cancelled_by": "watchdog",
                            },
                        )
                        if asyncio.iscoroutine(coro):
                            # Schedule fire-and-forget so the watchdog
                            # tick stays synchronous; we are already
                            # inside the event loop because the loop
                            # itself awaited us.
                            try:
                                asyncio.get_running_loop().create_task(coro)
                            except RuntimeError:
                                # No running loop -- drop the emit.
                                pass
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "[OrgCmd] watchdog event emit raised", exc_info=True
                        )

    def _resolve_watchdog_threshold(self, org_id: Any) -> float:
        """Pull ``watchdog_stuck_threshold_s`` off the org spec.

        Returns the v2 service-level default when the spec has the
        watchdog disabled / threshold missing / the lookup raises
        (best-effort -- a watchdog must never crash the service).
        Returns ``0`` (skip cancel) iff the org explicitly disabled
        the watchdog.
        """

        if not isinstance(org_id, str) or not org_id:
            return self._watchdog_default_threshold_secs
        try:
            org = self._lookup.get_org(org_id)
        except Exception:  # noqa: BLE001
            return self._watchdog_default_threshold_secs
        if org is None:
            return self._watchdog_default_threshold_secs
        if not bool(getattr(org, "watchdog_enabled", True)):
            return 0.0
        threshold = getattr(org, "watchdog_stuck_threshold_s", None)
        try:
            value = float(threshold)
        except (TypeError, ValueError):
            value = self._watchdog_default_threshold_secs
        if value <= 0:
            value = self._watchdog_default_threshold_secs
        return value

    # ------------------------------------------------------------------
    # Private helpers (parity with v1; lifted as-is unless ADR-0011 forces
    # a Protocol-routed rewrite)
    # ------------------------------------------------------------------

    def _require_org_running(self, org_id: str):  # noqa: ANN202 -- duck-typed
        """Resolve the org via :class:`OrgLookupProtocol` + status-gate.

        Mirrors v1 ``_require_org_running`` byte-for-byte
        modulo the lookup boundary. Raises
        :class:`OrgCommandError` (org missing) or
        :class:`OrgCommandConflict` (org paused / archived / not
        yet active).
        """
        org = self._lookup.get_org(org_id)
        if not org:
            raise OrgCommandError("Organization not found")
        status = getattr(org, "status", None)
        status_value = getattr(status, "value", None) or str(status)
        # v1 imports OrgStatus from openakita.orgs.models; v2 stays
        # decoupled by string-matching the enum values (which are
        # part of the v1 / v2 parity contract anyway).
        if status_value in {"active", "running"}:
            return org
        if status_value == "paused":
            raise OrgCommandConflict(
                "组织当前已暂停，请先恢复组织后再下发指令。",
                command_id="",
            )
        if status_value == "archived":
            raise OrgCommandConflict(
                "组织已归档，无法下发指令。",
                command_id="",
            )
        raise OrgCommandConflict(
            f"组织尚未启动。当前状态: {status_value}",
            command_id="",
        )

    def _resolve_command_root_id(self, org, target_node_id: str | None) -> str:  # noqa: ANN001
        """Pick the root node id to bill the command against.

        ``target_node_id`` wins if supplied; otherwise we use
        the first root. v1 ``_resolve_command_root_id`` parity.
        """
        if target_node_id:
            return target_node_id
        roots = org.get_root_nodes() or []
        return roots[0].id if roots else ""

    def _purge_old_commands(self) -> None:
        """Drop terminal commands older than ``_CMD_TTL`` from memory.

        Synchronous because v1 calls it from sync ``submit``.
        The asyncio lock is non-reentrant so v2 uses a plain
        dict-comprehension instead of ``async with self._lock``
        here -- the mutation happens only inside the
        ``submit``-owned lock or before the first ``await``,
        so the dict cannot be observed mid-mutation.
        """
        now = time.time()
        stale = [
            cid
            for cid, cmd in self._commands.items()
            if (cmd["status"] in ("done", "error") and now - cmd["created_at"] > _CMD_TTL)
            or (cmd["status"] == "running" and now - cmd["created_at"] > _CMD_TTL * 2)
        ]
        for cid in stale:
            cmd = self._commands.pop(cid, None)
            if cmd:
                self._running_by_root.pop(
                    (cmd.get("org_id"), cmd.get("root_node_id")),
                    None,
                )
            # Sprint-2 P0-2: keep ``_command_outcomes`` aligned with
            # ``_commands`` so the per-process outcome cache cannot
            # grow unbounded once a command has aged past TTL.
            self._command_outcomes.pop(cid, None)
            # Sprint-3 P0-2: same hygiene for the inflight-task map so
            # a never-finalised task entry (e.g. an asyncio leak) is
            # cleared on the next ``submit`` instead of pinning the
            # coroutine across the TTL window.
            stale_task = self._inflight_tasks.pop(cid, None)
            if stale_task is not None and not stale_task.done():
                stale_task.cancel()
            # Sprint-5 P0-2: same hygiene for the by-org index. We do
            # not know the org_id from the pop above (we popped first),
            # so look it up from the previously-popped ``cmd``.
            if cmd:
                stale_org = cmd.get("org_id")
                if isinstance(stale_org, str):
                    org_cids = self._inflight_by_org.get(stale_org)
                    if org_cids is not None:
                        org_cids.discard(cid)
                        if not org_cids:
                            self._inflight_by_org.pop(stale_org, None)

    def _update_command_state(
        self,
        command_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Patch a command record in-place. v1 parity.

        Sprint-3 P0-2 (audit v3 §5.3): ``cancelled`` is now a recognised
        terminal status (alongside ``done`` / ``error``). Pre-fix the
        ``_run_minimal`` cancel branch wrote ``status="cancelled"`` but
        ``phase`` stayed on whatever the snapshot last carried, leaving
        ``GET /commands/{cid}`` reporting ``phase=running, status=cancelled``
        (UI shows a spinner with a strikethrough). Including ``cancelled``
        in the same auto-mirror set as ``done`` / ``error`` keeps the
        public snapshot self-consistent for the new terminal state.
        """
        cmd = self._commands.get(command_id)
        if cmd is None:
            return None
        if status is not None:
            cmd["status"] = status
            if phase is None and status in ("done", "error", "cancelled"):
                cmd["phase"] = status
        if phase is not None:
            cmd["phase"] = phase
        for k, v in fields.items():
            cmd[k] = v
        cmd["updated_at"] = time.time()
        return cmd

    def _build_continue_content(self, org_id: str, root_node_id: str, content: str) -> str:
        """Augment a new command with recent context after cancellation.

        v1 ``_build_continue_content`` lifted with one structural
        change: the blackboard / project-store accessors go
        through ``CommandRuntimeProtocol`` instead of reaching
        into the v1 runtime. P9.4b2 may further split this if
        LOC pressure persists; for P9.4b we keep parity.
        """
        last_cmd = self._find_recent_previous_command(org_id, root_node_id)
        sections: list[str] = []
        if last_cmd:
            result = last_cmd.get("result")
            result_text = ""
            if isinstance(result, dict):
                result_text = str(result.get("result") or result.get("error") or "")[:1200]
            elif result:
                result_text = str(result)[:1200]
            sections.append(
                "\n".join(
                    [
                        f"- previous command: {last_cmd.get('command_id')}",
                        f"- status: {last_cmd.get('status')} / {last_cmd.get('phase')}",
                        f"- cancelled by user: {bool(last_cmd.get('cancel_requested_by_user'))}",
                        f"- partial result: {result_text or '(none)'}",
                    ]
                )
            )
        # v1 also stitches in blackboard summary + unfinished
        # project tasks via runtime reach-ins. The protocoled
        # versions land in P9.4b2 together with the gateway /
        # emitter wiring; for P9.4b the trimmed-context path is
        # enough to satisfy the contract test (v2 just returns
        # less context than v1 when blackboard/project_store are
        # not injected -- documented in the docstring).
        context = "\n\n".join(s for s in sections if s.strip()) or "(no context)"
        return (
            "[continue cancelled task]\n"
            "This is a NEW command, not a resumed command_id. Read the "
            "history below, then continue from where the cancellation "
            "left off without redoing finished work.\n\n"
            f"{context}\n\n[new user instruction]\n{content}"
        )

    def _find_recent_previous_command(
        self, org_id: str, root_node_id: str
    ) -> dict[str, Any] | None:
        """Look up the most recent terminal command on a root. v1 parity."""
        candidates = [
            cmd
            for cmd in self._commands.values()
            if cmd.get("org_id") == org_id
            and cmd.get("root_node_id") == root_node_id
            and cmd.get("status") != "running"
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda c: float(c.get("finished_at") or c.get("updated_at") or 0),
            reverse=True,
        )
        return candidates[0]

    def _schedule_run(
        self,
        request: OrgCommandRequest,
        command_id: str,
        root_node_id: str,
        *,
        replace_existing_id: str | None = None,
    ) -> None:
        """Schedule the background ``_run`` coroutine.

        P9.4b ships the **minimal** scheduler:
        ``asyncio.create_task`` against the running loop.
        The full v1 flow (``run_coroutine_threadsafe`` +
        ``_broadcast_done`` + ``publish_summary`` +
        ``_push_root_task_complete`` + bridges +
        ``_dispatch_forwards`` fan-out) lands in P9.4b2.
        The minimal scheduler is enough for the P9.4d
        contract + P9.4e SLA tests.

        Sprint-3 P0-1 (audit v3 §5.2 / §4.5): when the caller does not
        pin a specific node (``request.target_node_id is None``) we now
        forward the resolved ``root_node_id`` (an entry node such as
        ``producer`` for ``aigc-video-studio``) to
        :meth:`CommandRuntimeProtocol.send_command` instead of the
        unresolved ``None``. Pre-fix the executor's ``ProfileResolver``
        received ``node_id=None``, so ``_extract_node`` matched no
        ``OrgNode`` and the system prompt fell back to the literal
        "node `None` (role: worker)" string that the v14 LLM-debug
        audit flagged (68/79 files had ``context.node_id=null``).

        Sprint-3 P0-2 (audit v3 §5.3): the created task is stored in
        ``self._inflight_tasks[command_id]`` so ``cancel`` can call
        ``task.cancel()`` and have ``CancelledError`` propagate through
        ``runtime.send_command`` -> ``agent_dispatch`` ->
        ``executor.activate_and_run`` -> ``Brain.messages_create_async``
        -> ``httpx`` so the in-flight LLM HTTP request actually gets
        torn down (real token-burn stops).
        """

        async def _run_minimal() -> None:
            # Sprint-3 P0-1: prefer the resolved root over an unset
            # target so the executor's ProfileResolver receives a real
            # node id. The ``request.target_node_id or root_node_id``
            # pattern keeps an explicit caller-supplied node winning,
            # which matters for scheduler-driven sub-task dispatch.
            effective_target = request.target_node_id or root_node_id
            try:
                if replace_existing_id:
                    try:
                        await self._runtime.cancel_user_command(
                            request.org_id,
                            replace_existing_id,
                        )
                    except Exception:
                        pass
                result = await self._runtime.send_command(
                    request.org_id,
                    effective_target,
                    request.content,
                    command_id=command_id,
                )
                # Sprint-2 P0-2: ``runtime.send_command`` returns
                # ``"submitted"`` regardless of the actual agent run
                # outcome (the agent dispatch callback fires inside
                # send_command itself but its result never bubbles back
                # via the return value -- only via the event bus).
                # Consult the per-command outcome cache populated by the
                # event-bus subscription before flipping the public
                # status. Pre-fix this finaliser unconditionally wrote
                # ``status=done, error=null`` even when events.jsonl
                # showed ``agent_run_failed`` (audit v2 §5 F1-new).
                outcome = self._command_outcomes.get(command_id)
                if outcome is not None and outcome.get("event") == "agent_run_failed":
                    rendered_error = " ".join(
                        s
                        for s in (
                            outcome.get("reason") or "",
                            outcome.get("error") or "",
                        )
                        if s
                    ).strip() or "agent run failed"
                    self._update_command_state(
                        command_id,
                        status="error",
                        phase="error",
                        result=result,
                        error=rendered_error,
                        event_ref="agent_run_failed",
                        finished_at=time.time(),
                    )
                elif outcome is not None and outcome.get("event") == "agent_run_cancelled":
                    # Sprint-3 P0-2: the executor emitted the cancel
                    # event but the task itself completed normally
                    # (e.g. the agent run wrapped the cancel into a
                    # graceful early-return). Reflect cancelled state
                    # without crashing the finaliser.
                    self._update_command_state(
                        command_id,
                        status="cancelled",
                        phase="cancelled",
                        result=result,
                        error=None,
                        event_ref="agent_run_cancelled",
                        finished_at=time.time(),
                    )
                else:
                    self._update_command_state(
                        command_id,
                        status="done",
                        phase="done",
                        result=result,
                        event_ref=(outcome or {}).get("event") if outcome else None,
                        finished_at=time.time(),
                    )
            except asyncio.CancelledError:
                # Sprint-3 P0-2 (audit v3 §5.3): the user pressed cancel
                # and ``cancel`` -> ``task.cancel()`` injected
                # ``CancelledError`` at the LLM await point. Flip the
                # public snapshot to ``cancelled`` *before* re-raising
                # so ``GET /commands/{cid}`` immediately returns
                # ``phase=cancelled, event_ref=agent_run_cancelled``
                # (pre-fix it kept saying ``phase=running`` until the
                # LLM happened to finish ~1-3 minutes later, while
                # token meter kept ticking).
                self._update_command_state(
                    command_id,
                    status="cancelled",
                    phase="cancelled",
                    error=None,
                    event_ref="agent_run_cancelled",
                    finished_at=time.time(),
                    cancelled_by_user=True,
                )
                raise
            except Exception as exc:
                self._update_command_state(
                    command_id,
                    status="error",
                    phase="error",
                    error=str(exc),
                    finished_at=time.time(),
                )
            finally:
                root_key = (request.org_id, root_node_id)
                if self._running_by_root.get(root_key) == command_id:
                    self._running_by_root.pop(root_key, None)
                # Sprint-3 P0-2: clear the inflight task entry so the
                # dict cannot grow unbounded and so a stale ``Task``
                # reference cannot accidentally re-cancel a recycled
                # command_id.
                self._inflight_tasks.pop(command_id, None)
                # Sprint-5 P0-2: keep the by-org index aligned with
                # ``_inflight_tasks``. Both are mutated only on this
                # event loop so a discard-after-pop is safe.
                org_cids = self._inflight_by_org.get(request.org_id)
                if org_cids is not None:
                    org_cids.discard(command_id)
                    if not org_cids:
                        self._inflight_by_org.pop(request.org_id, None)

        loop = asyncio.get_running_loop()
        task = loop.create_task(_run_minimal())
        # Sprint-3 P0-2: record the task so ``cancel`` can reach it.
        # We register before any first ``await`` so the cancel-while-
        # still-pending race window is closed (the dict is mutated
        # synchronously in the same event-loop tick as ``submit``).
        self._inflight_tasks[command_id] = task
        # Sprint-5 P0-2: same pre-await registration for the by-org
        # secondary index so ``cancel_all_for_org`` reaches the task
        # even if the user fires ``POST /stop`` within the same tick
        # as ``submit``.
        self._inflight_by_org.setdefault(request.org_id, set()).add(command_id)

    async def _dispatch_forwards(
        self,
        org_id: str,
        command_id: str,
        kind: str,
        text: str,
    ) -> None:
        """Mirror a final outcome to extra IM destinations.

        P9.4b ships the **gated no-op**: when
        ``self._gateway`` is None (v1
        ``get_message_gateway() is None`` branch) the
        method returns immediately. Full body lands in
        P9.4b2.
        """
        if self._gateway is None:
            return

    # ------------------------------------------------------------------
    # Fan-out / observability
    # ------------------------------------------------------------------

    def subscribe_summary(
        self,
        command_id: str,
        *,
        surface: str = "unknown",
        target: str = "",
    ) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to summary events for ``command_id``.

        Captures the *current* event loop at subscribe time so
        :meth:`publish_summary` can hop threads if the event
        fires from a worker (v1 contract).
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._summary_subscribers.setdefault(command_id, []).append(
            (queue, asyncio.get_running_loop(), surface, target)
        )
        cmd = self._commands.get(command_id)
        if cmd and cmd.get("status") in {"done", "error"}:
            event: dict[str, Any] = {
                "type": "org_command_done",
                "org_id": cmd.get("org_id", ""),
                "command_id": command_id,
            }
            if cmd.get("status") == "done":
                event["result"] = cmd.get("result")
            else:
                event["error"] = cmd.get("error") or "Command failed"
            queue.put_nowait(event)
        return queue

    async def publish_summary(self, command_id: str, event: dict[str, Any]) -> None:
        """Fan out a summary event to every subscriber.

        Records each delivery on the command's ``delivered_to``
        list (parity with v1 mark_delivered + publish_summary
        ordering). ``asyncio.QueueFull`` is swallowed -- a slow
        subscriber must not block siblings (v1 contract).
        """
        for queue, loop, surface, target in list(self._summary_subscribers.get(command_id, [])):
            try:
                self.mark_delivered(
                    command_id,
                    surface=surface,
                    target=target,
                    event=str(event.get("type") or event.get("event") or ""),
                )
                if loop is asyncio.get_running_loop():
                    queue.put_nowait(event)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except asyncio.QueueFull:
                pass

    def find_command_for_event(self, org_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Look up the command record matching an event payload.

        Direct command_id match wins; otherwise (legacy events
        without an explicit id) returns the lone running
        command if exactly one exists. Mirrors v1.
        """
        command_id = str(data.get("command_id") or "")
        if command_id:
            cmd = self._commands.get(command_id)
            if cmd and cmd.get("org_id") == org_id:
                return cmd
        running = [
            cmd
            for cmd in self._commands.values()
            if cmd.get("org_id") == org_id and cmd.get("status") == "running"
        ]
        if len(running) == 1:
            return running[0]
        return None

    def mark_delivered(
        self,
        command_id: str,
        *,
        surface: str,
        target: str,
        event: str,
    ) -> None:
        """Mark a summary event as delivered to a surface. v1 parity."""
        cmd = self._commands.get(command_id)
        if not cmd:
            return
        delivered = cmd.setdefault("delivered_to", [])
        delivered.append(
            {
                "surface": surface,
                "target": target,
                "event": event,
                "ts": time.time(),
            }
        )

    def unsubscribe_summary(
        self,
        command_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Unsubscribe a previously-subscribed queue. v1 parity."""
        subscribers = self._summary_subscribers.get(command_id)
        if not subscribers:
            return
        for item in list(subscribers):
            if item[0] is queue:
                with suppress(ValueError):
                    subscribers.remove(item)
                break
        if not subscribers:
            self._summary_subscribers.pop(command_id, None)

    # ------------------------------------------------------------------
    # Forward dispatch (mirror final outcome to IM gateways)
    # ------------------------------------------------------------------

    async def _dispatch_forwards(
        self,
        org_id: str,
        command_id: str,
        kind: str,
        text: str,
    ) -> None:
        """Mirror a final outcome to extra IM destinations.

        ``kind`` is one of ``done`` / ``error`` / ``cancelled``;
        ``text`` is the human-readable body already trimmed by
        the caller. When ``self._gateway`` is None the method
        is a fast no-op (v1's degraded-mode equivalence). Each
        per-target send is best-effort: one channel failure
        must not affect siblings or the desktop flow.
        """
        if self._gateway is None:
            return
        cmd = self._commands.get(command_id)
        if not cmd:
            return
        targets_raw = cmd.get("forward_to") or []
        if not targets_raw:
            return
        prefix = {
            "done": "✅ 组织任务已完成",
            "error": "❌ 组织任务失败",
            "cancelled": "🛑 组织任务已被取消",
        }.get(kind, "📣 组织任务更新")
        body = (text or "").strip()
        if len(body) > 1500:
            body = body[:1500].rstrip() + "…"
        msg = f"{prefix}\n(command_id: {command_id}, org: {org_id})\n\n{body}"
        delivered: list[dict[str, Any]] = []
        for raw in targets_raw:
            if not isinstance(raw, dict):
                continue
            channel = str(raw.get("channel") or "")
            chat_id = str(raw.get("chat_id") or "")
            if not channel or not chat_id:
                continue
            thread_id = raw.get("thread_id") or None
            try:
                ok = await self._gateway.send_text_reliably(
                    channel=channel,
                    chat_id=chat_id,
                    text=msg,
                    record_to_session=False,
                    user_id="system",
                    thread_id=thread_id,
                    metadata={
                        "org_id": org_id,
                        "command_id": command_id,
                        "forward_kind": kind,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "[OrgCmd] forward to %s/%s failed for command %s: %s",
                    channel,
                    chat_id,
                    command_id,
                    exc,
                )
                ok = False
            delivered.append(
                {
                    "channel": channel,
                    "chat_id": chat_id,
                    "kind": kind,
                    "ok": bool(ok),
                    "ts": time.time(),
                }
            )
        if delivered:
            cmd_now = self._commands.get(command_id)
            if cmd_now is not None:
                existing = list(cmd_now.get("forward_log") or [])
                existing.extend(delivered)
                cmd_now["forward_log"] = existing[-50:]


# ---------------------------------------------------------------------------
# Local helpers (kept at module scope so the service body stays compact)
# ---------------------------------------------------------------------------


def _live_snapshot_view(live: dict[str, Any]) -> dict[str, Any]:
    """Project a runtime tracker snapshot into ``get_status``.

    14 keys, byte-for-byte parity with v1 ``get_status``
    fallback values. Lifted as a helper so the v2 method body
    stays single-pass.
    """
    return {
        "root_node_id": live.get("root_node_id") or "",
        "tracker_state": live.get("tracker_state"),
        "root_chain_id": live.get("root_chain_id", ""),
        "open_chains": live.get("open_chains", []),
        "open_chain_count": live.get("open_chain_count", 0),
        "open_subtree_chains": live.get("open_subtree_chains", []),
        "blockers": live.get("blockers", []),
        "blocker_summary": live.get("blocker_summary", ""),
        "busy_nodes": live.get("busy_nodes", []),
        "pending_mailbox": live.get("pending_mailbox", []),
        "root_status": live.get("root_status", ""),
        "last_progress_elapsed_s": live.get("last_progress_elapsed_s"),
        "warned_stuck": live.get("warned_stuck", False),
        "stopped_by_watchdog": live.get("auto_stopped", False),
        "cancelled_by_user": live.get("user_cancelled", False),
    }


# ---------------------------------------------------------------------------
# Module singleton (back-compat with v1 ``get_command_service`` callers)
# ---------------------------------------------------------------------------


_service_instance: OrgCommandService | None = None


def set_command_service(service: OrgCommandService | None) -> None:
    """Install the module-level service singleton.

    Byte-for-byte mirror of v1 ``set_command_service`` so P9.8
    caller migration is a one-import change.
    """
    global _service_instance
    _service_instance = service


def get_command_service() -> OrgCommandService | None:
    """Read the module-level service singleton."""
    return _service_instance
