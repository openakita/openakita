"""v2 ``OrgCommandService`` -- Supervisor HTTP takeover (Sprint-9).

Replaces v1 ``openakita.orgs.command_service.OrgCommandService``
(963 LOC, 24 methods, ``OrgRuntime``-coupled) with a
Protocol-typed surface decoupled from the runtime via injected
Protocols (ADR-0011). Implements
:class:`openakita.orgs.node_scheduler.CommandDispatcher`
so P9.3 NodeScheduler can call ``service.dispatch`` without
circular imports.

Architectural deltas vs v1 / vs Sprint-5..8:

1. ``self._runtime._has_active_delegations`` reach-in
   replaced by an injected :class:`CommandRuntimeProtocol`
   surface (4 awaitables + 3 sync accessors).
2. ``threading.Lock`` becomes ``asyncio.Lock`` (G-RC-9.2
   Nit-4 lock-type ruling). ``submit`` becomes async to
   align with the lock.
3. **Sprint-9 supervisor HTTP takeover**: every command now runs
   through :class:`openakita.runtime.supervisor.Supervisor` built
   by :func:`openakita.runtime.supervisor_factory.build_supervisor_for_command`.
   The Sprint-5 wall-clock ``_watchdog_loop`` is gone -- stall
   detection is now LLM-evaluated by the supervisor's
   :class:`~openakita.runtime.stall_detector.StallDetector` on
   :class:`~openakita.runtime.ledger.ProgressLedger` signals.
   Cancellation is cooperative through the supervisor's
   :class:`~openakita.runtime.cancel_token.CancellationToken`.
4. The single-root lock (``_running_by_root``) is preserved and
   gains three-branch 409 semantics: ``{}`` = refuse 409,
   ``{replace_existing: true}`` = cancel old + drain checkpoint +
   submit new, ``{continue_previous: true}`` = resume the previous
   command from its last checkpoint (falling back to
   content-concatenation when no checkpoint exists).

ADR refs: ADR-0011 (Protocol-typed decomposition); ADR-0012
(no shim under v1); ADR-0013 (wall-clock SLA tests retired with
the watchdog; Supervisor's StallDetector + max_turns cap is the
new safety net).
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .command_models import (
    OrgCommandConflict,
    OrgCommandError,
    OrgCommandRequest,
    OrgOutputScope,
    new_command_id,
)

if TYPE_CHECKING:  # pragma: no cover -- import-cycle break
    from openakita.runtime.supervisor import Supervisor

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
        executor_provider: Any | None = None,
        checkpointer_provider: Any | None = None,
        supervisor_factory: Any | None = None,
        llm_client_provider: Any | None = None,
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
        # Per-command outcome cache populated by event-bus subscriptions
        # so ``get_status`` and the background supervisor finaliser can
        # reflect the real ``agent_run_failed`` / ``agent_run_finished``
        # / ``agent_run_cancelled`` event the executor emits.
        self._command_outcomes: dict[str, dict[str, Any]] = {}
        # ``asyncio.Task`` wrapping the live :meth:`Supervisor.run` for
        # each command. Sprint-9 keeps this as bookkeeping so
        # :meth:`cancel_all_for_org` can also wait for the
        # supervisor-driven background task to actually unwind after
        # its cancel token fires.
        self._inflight_tasks: dict[str, asyncio.Task[Any]] = {}
        # Secondary index keyed by org_id so ``cancel_all_for_org`` can
        # stop every supervisor for one org without scanning the whole
        # command dict.
        self._inflight_by_org: dict[str, set[str]] = {}
        # Sprint-9: per-command :class:`Supervisor` registry. Cancel
        # reaches into this map to fire :attr:`Supervisor.cancel_token`
        # cooperatively; the supervisor unwinds, writes a final
        # cancelled checkpoint, and the finaliser drops the entry.
        self._active_supervisors: dict[str, Supervisor] = {}
        # Sprint-9: providers for the Supervisor factory. The
        # composition root (``api/server.py``) injects the live
        # :class:`AgentPipelineExecutor`; tests pass an inline mock
        # or leave them ``None`` and inject a custom
        # ``supervisor_factory`` that ignores the executor. When all
        # three are ``None`` the legacy ``runtime.send_command`` path
        # is used as a fallback so a v2 IM canary or unit-test fixture
        # that never gets to the supervisor still works.
        self._executor_provider = executor_provider
        self._checkpointer_provider = checkpointer_provider
        self._supervisor_factory = supervisor_factory
        # RC-5 S3: optional override for the org-gated supervisor LLM client.
        # ``None`` (production) lazily wraps the shared default LLM client in
        # ``GatewaySupervisorLLMClient``; tests inject a scripted fake so the
        # gray-launch wiring can be asserted without burning real tokens.
        self._llm_client_provider = llm_client_provider
        self._event_bus = event_bus
        # v22 P1: background reconcile loop for ``_running_by_root``
        # bookkeeping. Started on-demand by :meth:`start_reconcile_loop`
        # (typically from ``api/server.py`` lifespan startup) and
        # stopped by :meth:`stop_reconcile_loop`. None until started.
        self._reconcile_task: asyncio.Task[Any] | None = None
        self._reconcile_stop_event: asyncio.Event | None = None
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

        Sprint-9 supervisor takeover. The high-level flow:

        1. Validate the org is running and resolve the root node id.
        2. Resolve the three-branch lock semantics:

           * No lock conflict -> fresh submit.
           * ``request.replace_existing=True`` -> cooperatively cancel
             the existing supervisor, await its final checkpoint, then
             submit a fresh command.
           * ``request.continue_previous=True`` -> try to resume the
             previous command from its last checkpoint. When no
             checkpoint exists fall back to the legacy
             content-concatenation path.
           * Plain conflict -> raise :class:`OrgCommandConflict` (HTTP
             409 ``org_command_conflict``).
        3. Record the command bookkeeping (``_commands`` +
           ``_running_by_root`` + ``_inflight_by_org``).
        4. Build a :class:`Supervisor` via the injected factory and
           kick off ``supervisor.run()`` as a background task.

        Returns the v1-shape dict ``{"command_id", "status",
        "root_node_id"}`` so REST callers see no shape drift; the
        richer supervisor observability lands on the
        :class:`CommandRead` snapshot that :meth:`get_status` builds.
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
        resume_checkpoint_id: str | None = None
        previous_command_id: str | None = None

        # ------------------------------------------------------------------
        # Three-branch lock semantics. The lock is held for the full
        # decision tree so a second submit cannot squeak through while
        # we drain the old supervisor on replace_existing.
        # ------------------------------------------------------------------
        async with self._lock:
            existing_id = self._running_by_root.get(root_key)
            existing = self._commands.get(existing_id or "")
            if existing and existing.get("status") == "running":
                if request.replace_existing:
                    existing["cancel_requested_by_user"] = True
                    existing["cancel_requested_at"] = now
                    # Cooperative cancel: fire the supervisor's token
                    # and wait up to 5 s for it to write its final
                    # checkpoint. If the drain times out we forcibly
                    # ``task.cancel()`` so the slot is reclaimed; the
                    # supervisor's own ``_terminate`` will have to
                    # catch up on the next event loop tick.
                    await self._cooperative_cancel(
                        existing_id or "", reason="replaced"
                    )
                elif request.continue_previous:
                    # Continue-previous on a STILL-RUNNING command is
                    # ambiguous (the user is sending a follow-up while
                    # the previous one is still mid-flight). Refuse
                    # with the same 409 so the frontend can prompt for
                    # an explicit cancel-and-resume.
                    raise OrgCommandConflict(
                        "上一条命令仍在执行，无法续跑；请先取消或等待完成。",
                        command_id=existing_id or "",
                    )
                else:
                    raise OrgCommandConflict(
                        "组织上有命令正在执行，请稍后或显式取消/替换。",
                        command_id=existing_id or "",
                    )

            # Resume-from-checkpoint preflight (continue_previous on a
            # terminated command). The supervisor's resume API is
            # called *inside* the background task; here we just look
            # up which checkpoint to hand it.
            if request.continue_previous:
                resume_checkpoint_id, previous_command_id = (
                    self._lookup_resume_checkpoint(request.org_id, root_node_id)
                )
                if resume_checkpoint_id is None:
                    # No checkpoint on disk -- fall through to the
                    # legacy content-concatenation continuation so the
                    # supervisor's PassThroughBrain still has the
                    # prior context in the task string.
                    run_content = self._build_continue_content(
                        request.org_id,
                        root_node_id,
                        content,
                    )

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
                "resume_checkpoint_id": resume_checkpoint_id,
                "previous_command_id": previous_command_id,
                "forward_to": [ft.to_dict() for ft in request.forward_to],
            }
            self._running_by_root[root_key] = command_id

        # UI issue #1: record the user's instruction as a ``user_command``
        # event so the command center can rebuild the user's own command
        # bubble after a reload / org-switch / remount. Previously NOTHING
        # persisted the submitted instruction in a retrievable place, so on
        # reload only node replies survived and the user bubble vanished.
        # Best-effort: never let observability break command submission.
        try:
            es = self._runtime.get_event_store(request.org_id)
            if es is not None and hasattr(es, "append"):
                preview = (request.content or "").strip()
                source_dict = (
                    request.source.to_dict()
                    if hasattr(request.source, "to_dict")
                    else None
                )
                es.append(
                    {
                        "type": "user_command",
                        "org_id": request.org_id,
                        "command_id": command_id,
                        "node_id": root_node_id,
                        "content": request.content,
                        "content_preview": preview[:500],
                        "source": source_dict,
                        "origin_surface": request.origin_surface.value,
                    }
                )
        except Exception:  # noqa: BLE001 (observability must never break submit)
            pass

        # UI issue #8: create the project + a root task at submit time so the
        # "项目" page shows work immediately (not only after the first delegation
        # or after completion). Best-effort; never block submission.
        try:
            ensure_proj = getattr(self._runtime, "ensure_command_project", None)
            if callable(ensure_proj):
                ensure_proj(request.org_id, command_id, root_node_id, request.content or "")
        except Exception:  # noqa: BLE001
            pass

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
            resume_checkpoint_id=resume_checkpoint_id,
        )
        return {
            "command_id": command_id,
            "status": "running",
            "root_node_id": root_node_id,
            "resumed_from": resume_checkpoint_id,
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
        # Sprint-9: surface supervisor observability fields so the
        # frontend Pydantic ``CommandRead`` shape gets a stable
        # ``progress_ledger`` / ``n_stalls`` / ``n_turns`` /
        # ``last_checkpoint_id`` / ``replan_count`` snapshot. We read
        # from the live supervisor when the command is still running
        # and fall back to the persisted ``supervisor_*`` fields the
        # outcome reflection wrote at terminate time.
        supervisor = self._active_supervisors.get(command_id)
        if supervisor is not None:
            result["n_turns"] = int(getattr(supervisor.stall_detector, "n_turns", 0) or 0)
            result["n_stalls"] = int(getattr(supervisor.stall_detector, "n_stalls", 0) or 0)
            result["replan_count"] = int(getattr(supervisor, "n_replans", 0) or 0)
            result["last_checkpoint_id"] = getattr(supervisor, "last_checkpoint_id", None)
            history = list(getattr(supervisor, "history", []) or [])
            if history:
                latest = history[-1]
                to_jsonable = getattr(latest, "to_jsonable", None)
                if callable(to_jsonable):
                    try:
                        result["progress_ledger"] = to_jsonable()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            result["n_turns"] = int(cmd.get("supervisor_n_turns") or 0)
            result["replan_count"] = int(cmd.get("supervisor_n_replans") or 0)
            result["last_checkpoint_id"] = cmd.get("supervisor_last_checkpoint_id")
            result.setdefault("n_stalls", 0)
            result.setdefault("progress_ledger", None)
        return result

    async def cancel(
        self,
        org_id: str,
        command_id: str,
        *,
        reason: str = "user_cancel",
    ) -> dict[str, Any] | None:
        """Cancel an in-flight command via the supervisor's cancel token.

        Sprint-9 supervisor takeover. The new flow:

        1. ``None`` on missing / wrong-org -- unchanged.
        2. ``{"ok": True, "already_done": True}`` on terminal status --
           unchanged (this is the path the v20 audit ``B6.3 dup_cancel``
           probes: a second cancel on an already-cancelled command
           must return 200 with the same envelope, NOT 4xx).
        3. Otherwise: fire :attr:`Supervisor.cancel_token`,
           await its final checkpoint (best-effort, bounded by
           5 s), then mirror the legacy IM forward + emitter
           broadcast.

        Cancellation is now cooperative: the supervisor checks
        ``cancel_token.raise_if_cancelled()`` at every safe point and
        unwinds to ``_terminate(FinalOutcome.CANCELLED, ...)`` which
        writes a cancelled checkpoint and a final lifecycle event.
        The asyncio.Task wrapping ``supervisor.run()`` is only
        force-cancelled as a hard fallback when the cooperative drain
        does not complete in time.
        """
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None
        if cmd.get("status") != "running":
            return {"ok": True, "command_id": command_id, "already_done": True}
        # v23 RC-4 observability: capture the active supervisor's root
        # *before* the cooperative drain pops it off
        # ``_active_supervisors``. Pre-fix the response always read
        # ``cancelled_roots`` off ``runtime.cancel_user_command`` only,
        # which returns ``None`` for HTTP-submitted commands (the
        # supervisor takeover path does not register a runtime tracker
        # via ``runtime.send_command``). The empty list misled v23
        # regression triage into thinking the cancel never found a
        # supervisor; in reality the supervisor was found and its
        # cancel_token was fired -- the runtime tracker simply does
        # not exist for this path. We therefore fall back to the
        # supervisor's own root so ``GET /cancel`` callers can tell
        # "no supervisor / nothing to cancel" apart from "supervisor
        # was cancelled, runtime tracker just doesn't exist".
        active_supervisor = self._active_supervisors.get(command_id)
        supervisor_root: str | None = None
        if active_supervisor is not None:
            supervisor_root = (
                getattr(getattr(active_supervisor, "task_ledger", None), "root_node_id", None)
                or cmd.get("root_node_id")
            )
        await self._cooperative_cancel(command_id, reason=reason)
        self._update_command_state(
            command_id,
            cancel_requested_by_user=True,
            cancel_requested_at=time.time(),
        )
        # Best-effort runtime cancel so the dispatch tracker stays in
        # sync with the supervisor's terminal state; the runtime cancel
        # is no longer the authoritative cancel signal (the supervisor's
        # cancel token is).
        runtime_result: dict[str, Any] = {}
        try:
            runtime_result = await self._runtime.cancel_user_command(
                org_id, command_id, cancel_reason=reason
            ) or {}
        except Exception:
            logger.debug(
                "[OrgCmd] runtime.cancel_user_command raised after cooperative cancel",
                exc_info=True,
            )
        runtime_roots = list(runtime_result.get("cancelled_roots") or [])
        if not runtime_roots and supervisor_root:
            cancelled_roots: list[str] = [supervisor_root]
        else:
            cancelled_roots = runtime_roots
        if self._emitter is not None:
            try:
                await self._emitter.broadcast(
                    "org:command_cancelled",
                    {
                        "org_id": org_id,
                        "command_id": command_id,
                        "by": "user",
                        "cancelled_roots": cancelled_roots,
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
            "cancelled_roots": cancelled_roots,
            "reason": reason,
        }

    async def _cooperative_cancel(
        self,
        command_id: str,
        *,
        reason: str,
        timeout: float | None = None,
    ) -> None:
        """Fire the supervisor's cancel token + drain its final checkpoint.

        Synchronous side-effects:

        * Flip ``_commands[cid]["status"]`` to ``cancelling`` so a
          ``get_status`` poll between cancel-fire and supervisor
          terminate sees the right phase (v20 B6.3 dup_cancel probe).
        * Pre-seed ``_command_outcomes[cid]`` with ``cancelled_by=<reason>``
          so an events.jsonl reader can attribute the cancel even
          before the supervisor's own lifecycle event lands.

        Then ``cancel_token.cancel(reason)`` (sync) and ``await`` the
        wrapping asyncio task with a deadline. On timeout we
        ``task.cancel()`` as a hard fallback -- the task should
        already be unwinding from the cooperative signal but we
        cannot block the caller indefinitely.

        ``timeout`` defaults to ``settings.orgs_cancel_drain_budget_s``
        (v22 RCA RC-6). Explicit ``float`` values from callers still
        override; tests may pass a small budget to exercise the
        force-cancel path quickly.
        """

        effective_timeout = (
            float(self._cancel_drain_budget_s())
            if timeout is None
            else float(timeout)
        )
        supervisor = self._active_supervisors.get(command_id)
        if supervisor is not None:
            try:
                supervisor.cancel_token.cancel(reason)
            except Exception:  # noqa: BLE001 -- token API is sync + safe
                logger.debug(
                    "[OrgCmd] cancel_token.cancel raised", exc_info=True
                )
        # Pre-seed status + outcome so concurrent get_status / dup_cancel
        # see the right thing without waiting for the supervisor to
        # actually write its final checkpoint.
        cmd = self._commands.get(command_id)
        if cmd is not None and cmd.get("status") == "running":
            cmd["status"] = "cancelling"
            cmd["phase"] = "cancelling"
            cmd["updated_at"] = time.time()
        self._command_outcomes[command_id] = {
            "event": "agent_run_cancelled",
            "reason": reason,
            "error": None,
            "node_id": None,
            "output_len": None,
            "ts": time.time(),
            "cancelled_by": reason,
        }
        task = self._inflight_tasks.get(command_id)
        if task is None or task.done():
            return
        # v23 RC-4 fix: the d1275851 ``cancel_event`` bridge only reaches
        # :class:`SupervisorBrain`. The production
        # :class:`PassThroughSupervisorBrain` returns canned JSON without
        # ever calling the LLM; the real LLM call lives inside
        # :meth:`Supervisor.deliver` ->
        # :meth:`AgentPipelineExecutor.activate_and_run` ->
        # ``agent.run`` -> ``Brain.messages_create_async``, which never
        # receives ``cancel_event`` (audit
        # ``_v23_biz/_rc4_debug_notes.md``). Without an explicit
        # ``task.cancel()`` the in-flight ``httpx`` request stays
        # blocked for the full drain budget. We therefore fire
        # ``task.cancel()`` immediately when a live supervisor was
        # registered: the resulting ``CancelledError`` unwinds through
        # ``httpx`` in ~100 ms, :meth:`Supervisor.run`'s new
        # ``except CancelledError`` branch absorbs it and runs
        # ``_terminate`` so the final ``cancelled`` checkpoint is
        # written, and the surrounding ``wait_for`` re-raises
        # ``CancelledError`` (which :meth:`_schedule_run._run`'s
        # ``except`` reads ``supervisor.last_checkpoint_id`` off of
        # before clearing bookkeeping).
        if supervisor is not None:
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=max(0.1, effective_timeout))
        except TimeoutError:
            logger.warning(
                "[OrgCmd] supervisor drain timed out after %.1fs; force-cancelling cid=%s",
                effective_timeout,
                command_id,
            )
            if not task.done():
                task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.debug(
                "[OrgCmd] supervisor drain raised", exc_info=True
            )

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

        Sprint-9 supervisor takeover: fires
        :attr:`Supervisor.cancel_token` for each in-flight command
        (cooperative) instead of the legacy ``task.cancel()`` hard
        kill. Each command's drain runs in parallel via
        :func:`asyncio.gather` with a 5 s deadline; the slowest
        supervisor caps the total ``cancel_all_for_org`` wall-clock at
        the same 5 s as a single cancel.

        Pre-Sprint-9 the method seeded ``_command_outcomes`` with
        ``cancelled_by=stop_org`` so the events.jsonl reader could
        tell stop-org cancels apart from user cancels;
        :meth:`_cooperative_cancel` does the same seed now so the
        observable taxonomy stays at three values:
        ``user_cancel`` / ``stop_org`` / ``replaced``.
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
        await asyncio.gather(
            *(self._cooperative_cancel(cid, reason=reason) for cid in cids),
            return_exceptions=True,
        )
        # Best-effort runtime cancels so the dispatch tracker mirrors
        # the supervisor's terminal state.
        for cid in cids:
            try:
                await self._runtime.cancel_user_command(
                    org_id, cid, cancel_reason=reason
                )
            except Exception:  # noqa: BLE001 -- runtime cancel best-effort
                logger.debug(
                    "[OrgCmd] runtime cancel_user_command raised during stop-org "
                    "(org=%s cid=%s)",
                    org_id,
                    cid,
                    exc_info=True,
                )
        return cids

    # ------------------------------------------------------------------
    # Sprint-9 supervisor integration helpers
    # ------------------------------------------------------------------

    def get_active_supervisor(self, command_id: str) -> Supervisor | None:
        """Return the live :class:`Supervisor` for ``command_id``, or ``None``.

        Public hook for ``get_status`` overlay (live progress_ledger /
        n_stalls / n_turns / last_checkpoint_id surfaced into the
        :class:`CommandRead` response) and for unit tests that want to
        inspect the supervisor state without reaching into the
        ``_active_supervisors`` private. Returns ``None`` after the
        command has terminated and the finaliser has dropped the
        registration.
        """

        return self._active_supervisors.get(command_id)

    def _build_supervisor(
        self,
        *,
        org_id: str,
        command_id: str,
        root_node_id: str,
        task: str,
    ) -> Supervisor:
        """Construct a :class:`Supervisor` via the injected factory.

        Falls back to the module-level
        :func:`openakita.runtime.supervisor_factory.build_supervisor_for_command`
        when no factory was injected. The executor + checkpointer
        providers are pulled lazily so the construction order in
        ``api/server.py`` does not have to thread the executor into
        the service before the executor itself exists.

        When neither a factory nor an executor is available (the
        "bare service" path that legacy unit tests still hit) we
        synthesise a no-op deliver callable. The factory still wires
        a real :class:`PassThroughSupervisorBrain` + per-org sqlite
        checkpointer, but the deliver simply returns a success
        ``DelegationResult`` so the supervisor's inner loop reaches
        ``DONE`` on the second turn without trying to call the
        (missing) executor.
        """

        from openakita.runtime.supervisor_factory import (
            build_supervisor_for_command,
        )

        factory = self._supervisor_factory or build_supervisor_for_command
        executor = None
        if self._executor_provider is not None:
            try:
                executor = self._executor_provider()
            except Exception:  # noqa: BLE001 -- never crash submit
                logger.debug(
                    "[OrgCmd] executor_provider raised", exc_info=True
                )
        checkpointer = None
        if self._checkpointer_provider is not None:
            try:
                checkpointer = self._checkpointer_provider(org_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[OrgCmd] checkpointer_provider raised", exc_info=True
                )

        deliver = None
        if executor is None and self._supervisor_factory is None:
            # Bare-service fallback: legacy unit tests that build
            # ``OrgCommandService`` without an executor still drive
            # ``submit`` -> the background task should not crash.
            # Synthesise a no-op deliver so the supervisor reaches
            # DONE on the second pass-through turn.
            from openakita.runtime.checkpoint import MemoryCheckpointer
            from openakita.runtime.supervisor import DelegationResult

            async def _noop_deliver(
                speaker: str, instruction: str, progress
            ) -> DelegationResult:
                return DelegationResult(
                    success=True,
                    speaker=speaker or root_node_id,
                    message="",
                    metadata={"command_id": command_id, "noop": True},
                )

            deliver = _noop_deliver
            if checkpointer is None:
                checkpointer = MemoryCheckpointer()

        kwargs: dict[str, Any] = {
            "org_id": org_id,
            "command_id": command_id,
            "root_node_id": root_node_id,
            "task": task,
            "executor": executor,
            "checkpointer": checkpointer,
        }
        if deliver is not None:
            kwargs["deliver"] = deliver

        # RC-5 S3: org-gated LLM orchestration brain. Only the *real* submit
        # path (a live executor) opts into the gray-launch; the bare-service
        # legacy fallback above stays PassThrough. Even when we decide to
        # engage llm, the factory's ``_resolve_brain`` keeps a safe fallback
        # to PassThrough if the client/directory construction yields nothing,
        # so this branch can never crash submit. The default org (not in the
        # allowlist, global flag still ``passthrough``) injects nothing here
        # and is therefore byte-for-byte unchanged.
        if executor is not None and self._should_engage_llm_brain(org_id):
            llm_client = self._build_supervisor_llm_client(org_id)
            if llm_client is not None:
                kwargs["brain_mode"] = "llm"
                kwargs["llm_client"] = llm_client
                node_directory = self._build_node_directory(org_id)
                if node_directory:
                    kwargs["node_directory"] = node_directory
                # RC-conv: only the real LLM-orchestration path opts into the
                # tighter convergence budgets + wall-clock soft landing. The
                # passthrough single-shot path keeps the factory defaults
                # (byte-for-byte unchanged). Config read is defensive: any
                # failure leaves the factory defaults in place.
                self._apply_convergence_budget(kwargs)

        return factory(**kwargs)

    @staticmethod
    def _apply_convergence_budget(kwargs: dict[str, Any]) -> None:
        """Inject the LLM-path convergence budgets + soft ceiling into kwargs.

        RC-conv: aligns ``max_turns`` / ``max_replans`` / ``max_stalls`` so the
        graceful terminals are reachable inside the wall clock, and derives the
        self-imposed ``wall_clock_soft_budget_s`` from
        ``supervisor_hard_ceiling_s * orgs_supervisor_soft_ceiling_ratio`` so
        the supervisor degrades gracefully (with a deliverable) *before* the
        external hard ceiling force-cancels it into a bare ``status=error``.
        """
        try:
            from openakita.config import settings as _settings

            kwargs["max_turns"] = int(getattr(_settings, "orgs_supervisor_max_turns", 12) or 12)
            kwargs["max_replans"] = int(getattr(_settings, "orgs_supervisor_max_replans", 2) or 2)
            kwargs["max_stalls"] = int(getattr(_settings, "orgs_supervisor_max_stalls", 3) or 3)
            ceiling = int(getattr(_settings, "supervisor_hard_ceiling_s", 0) or 0)
            ratio = float(getattr(_settings, "orgs_supervisor_soft_ceiling_ratio", 0.8) or 0.0)
            if ceiling > 0 and ratio > 0:
                kwargs["wall_clock_soft_budget_s"] = float(ceiling) * ratio
        except Exception:  # noqa: BLE001 -- config must never break submit
            logger.debug(
                "[OrgCmd] convergence budget read failed; using factory defaults",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # RC-5 S3: org-gated LLM orchestration brain wiring
    # ------------------------------------------------------------------

    def _should_engage_llm_brain(self, org_id: str) -> bool:
        """Decide whether ``org_id`` runs the real LLM orchestration brain.

        Gray-launch rule (OR semantics, fail-safe to passthrough):

        * ``org_id`` is in ``settings.orgs_supervisor_llm_org_allowlist`` --
          the per-org explicit opt-in switch, OR
        * the global ``settings.orgs_supervisor_brain_mode == "llm"`` -- the
          full-rollout lever.

        Default (empty allowlist + global flag ``passthrough``) returns
        ``False`` so the default org is untouched. Any config read failure
        also returns ``False`` -- config must never break submit.
        """
        try:
            from openakita.config import settings

            if settings.orgs_supervisor_brain_mode == "llm":
                return True
            allow = settings.orgs_supervisor_llm_org_allowlist or []
            return org_id in set(allow)
        except Exception:  # noqa: BLE001 -- config must never break submit
            logger.debug(
                "[OrgCmd] supervisor brain-mode gate read failed (org=%s)",
                org_id,
                exc_info=True,
            )
            return False

    def _build_supervisor_llm_client(self, org_id: str) -> Any | None:
        """Construct the gateway-backed ``SupervisorLLMClient`` for ``org_id``.

        Returns ``None`` on any failure so the caller falls back to
        passthrough (the factory's ``_resolve_brain`` also guards this). A
        custom ``llm_client_provider`` can be injected for tests; otherwise we
        wrap the process-shared :func:`openakita.llm.client.get_default_client`
        in :class:`~openakita.runtime.llm_supervisor_client.GatewaySupervisorLLMClient`,
        locking the no-thinking endpoint from settings.
        """
        try:
            if self._llm_client_provider is not None:
                return self._llm_client_provider(org_id)
            from openakita.config import settings
            from openakita.llm.client import get_default_client
            from openakita.runtime.llm_supervisor_client import (
                GatewaySupervisorLLMClient,
            )

            return GatewaySupervisorLLMClient(
                get_default_client(),
                endpoint=settings.orgs_supervisor_llm_endpoint or None,
            )
        except Exception:  # noqa: BLE001 -- never crash submit; fall back passthrough
            logger.warning(
                "[OrgCmd] failed to build supervisor LLM client (org=%s); "
                "falling back to PassThrough",
                org_id,
                exc_info=True,
            )
            return None

    def _build_node_directory(self, org_id: str) -> list[Any] | None:
        """Build the real OrgV2 node directory for the brain (gap④).

        Reads the org's nodes from the injected lookup and maps each
        :class:`~openakita.orgs.org_models.OrgNode` to a
        :class:`~openakita.runtime.llm_supervisor_brain.NodeDescriptor`
        (``node_id`` / ``role`` / ``capabilities``) so the orchestration brain
        knows which concrete nodes it may route to instead of guessing.
        Returns ``None`` on any failure (the brain then degrades to the
        root-only team block).
        """
        try:
            from openakita.runtime.llm_supervisor_brain import NodeDescriptor

            org = self._lookup.get_org(org_id)
            nodes = list(getattr(org, "nodes", None) or [])
            edges = list(getattr(org, "edges", None) or [])
            # Project the org's edges onto per-node topology maps so the brain
            # routes ALONG the designed structure (★ "按组织架构连线流转"):
            #   hierarchy edge source->target  =>  source delegates_to target,
            #                                      target reports_to source.
            #   collaborate/consult            =>  symmetric collaborators.
            delegates: dict[str, list[str]] = {}
            reports: dict[str, list[str]] = {}
            collaborators: dict[str, list[str]] = {}
            for e in edges:
                src = getattr(e, "source", "") or ""
                tgt = getattr(e, "target", "") or ""
                if not src or not tgt or src == tgt:
                    continue
                et = getattr(e, "edge_type", None)
                et_val = getattr(et, "value", None) or str(et)
                if et_val in ("hierarchy", "escalate"):
                    delegates.setdefault(src, []).append(tgt)
                    reports.setdefault(tgt, []).append(src)
                else:  # collaborate / consult => peer link (bidirectional)
                    collaborators.setdefault(src, []).append(tgt)
                    collaborators.setdefault(tgt, []).append(src)
            directory: list[Any] = []
            for n in nodes:
                node_id = getattr(n, "id", "") or ""
                if not node_id:
                    continue
                role = getattr(n, "role_title", "") or ""
                goal = getattr(n, "role_goal", "") or ""
                dept = getattr(n, "department", "") or ""
                capabilities = goal or dept
                directory.append(
                    NodeDescriptor(
                        node_id=node_id,
                        role=role,
                        capabilities=capabilities,
                        is_root=(getattr(n, "level", None) == 0),
                        reports_to=tuple(dict.fromkeys(reports.get(node_id, []))),
                        delegates_to=tuple(dict.fromkeys(delegates.get(node_id, []))),
                        collaborates_with=tuple(
                            dict.fromkeys(collaborators.get(node_id, []))
                        ),
                    )
                )

            # ★ Multi-level routing: the central supervisor must only
            # connect the ROOT and the root's DIRECT reports ("supervisor
            # 只对接根节点及其直属下游"). Deeper levels are reached by each
            # coordinator dispatching DOWN its own edges (逐级下派) and
            # bubbling results UP (逐级汇报) — not by the supervisor
            # teleporting straight to a leaf. We therefore filter the brain
            # directory to {root} ∪ {root's direct hierarchy children}.
            # The kept descriptors retain their FULL topology (each L1
            # node's own ``delegates_to`` still lists its L2 reports) so the
            # supervisor can SEE the tree, but its selectable next_speaker
            # set excludes deep leaves. Fallback: if no clear single root is
            # identifiable, keep the full directory unchanged.
            roots = [d for d in directory if d.is_root]
            if len(roots) == 1:
                root = roots[0]
                allowed = {root.node_id, *root.delegates_to}
                scoped = [d for d in directory if d.node_id in allowed]
                if scoped:
                    directory = scoped
            return directory or None
        except Exception:  # noqa: BLE001 -- directory is best-effort
            logger.debug(
                "[OrgCmd] failed to build node directory (org=%s)",
                org_id,
                exc_info=True,
            )
            return None

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
        resume_checkpoint_id: str | None = None,
    ) -> None:
        """Build the Supervisor and spawn ``supervisor.run()`` as a Task.

        Sprint-9 supervisor takeover replacement for the legacy
        ``_run_minimal`` (which called ``runtime.send_command``
        directly). The new body:

        1. Build a :class:`Supervisor` via the injected factory; the
           factory wires the per-org StreamBus + checkpointer + a
           deliver callable that bridges to
           :meth:`AgentPipelineExecutor.activate_and_run` (so all
           Sprint-4 ``<dispatch>`` XML recursion + artefact
           persistence keep working).
        2. Register the supervisor in ``_active_supervisors`` BEFORE
           the first ``await`` so a cancel-during-submit race lands
           against the live supervisor's cancel token.
        3. Optionally call :meth:`Supervisor.resume_from_checkpoint`
           when ``continue_previous`` resolved a checkpoint id during
           submit; if the resume raises (missing / malformed
           checkpoint) we log + run from scratch.
        4. ``await supervisor.run()`` and translate its
           :class:`SupervisorOutcome` into the legacy command-state
           shape (status / phase / result / event_ref) so v1 callers
           and existing tests keep reading the same fields.
        """

        async def _run() -> None:
            effective_root = request.target_node_id or root_node_id
            supervisor = self._build_supervisor(
                org_id=request.org_id,
                command_id=command_id,
                root_node_id=effective_root,
                task=request.content,
            )
            self._active_supervisors[command_id] = supervisor
            try:
                if resume_checkpoint_id:
                    try:
                        await supervisor.resume_from_checkpoint(
                            resume_checkpoint_id
                        )
                    except (LookupError, ValueError) as exc:
                        logger.warning(
                            "[OrgCmd] supervisor resume failed for cid=%s cp=%s: %s; "
                            "falling back to fresh run",
                            command_id,
                            resume_checkpoint_id,
                            exc,
                        )
                outcome = await self._run_supervisor_with_hard_ceiling(
                    supervisor, command_id
                )
                self._reflect_supervisor_outcome(command_id, supervisor, outcome)
            except asyncio.CancelledError:
                # Hard fallback path: the supervisor's cooperative
                # cancel did not complete in time and our wrapping
                # task got force-cancelled. The supervisor itself
                # raises CancelledByToken on the cooperative path and
                # _terminate writes a cancelled checkpoint already, so
                # we only land here on the rare hard-cancel.
                #
                # v23 RC-4 fix: :meth:`Supervisor.run` now also catches
                # :class:`asyncio.CancelledError` when its
                # ``cancel_token`` was already fired (by
                # :meth:`_cooperative_cancel`'s explicit
                # ``task.cancel()``) and runs ``_terminate`` to write
                # the final ``cancelled`` checkpoint -- but the
                # surrounding ``asyncio.wait_for`` inside
                # :meth:`_run_supervisor_with_hard_ceiling` still
                # re-raises ``CancelledError`` once its own wait was
                # cancelled, so the outcome value never reaches the
                # ``_reflect_supervisor_outcome`` happy path. We
                # therefore read the checkpoint id + turn counters
                # off the supervisor instance here and mirror them
                # onto the command state so ``last_checkpoint_id``
                # surfaces on ``GET /command/{cid}`` and the command
                # is resumable via ``continue_previous``.
                cancel_cp = getattr(supervisor, "last_checkpoint_id", None)
                cancel_n_turns = int(
                    getattr(getattr(supervisor, "stall_detector", None), "n_turns", 0) or 0
                )
                cancel_n_replans = int(getattr(supervisor, "n_replans", 0) or 0)
                self._update_command_state(
                    command_id,
                    status="cancelled",
                    phase="cancelled",
                    error=None,
                    event_ref="agent_run_cancelled",
                    finished_at=time.time(),
                    cancelled_by_user=True,
                    supervisor_outcome="cancelled",
                    supervisor_n_turns=cancel_n_turns,
                    supervisor_n_replans=cancel_n_replans,
                    supervisor_last_checkpoint_id=cancel_cp,
                )
                raise
            except Exception as exc:  # noqa: BLE001 -- last-resort guardrail
                logger.exception(
                    "[OrgCmd] supervisor.run raised for cid=%s", command_id
                )
                self._update_command_state(
                    command_id,
                    status="error",
                    phase="error",
                    error=str(exc),
                    finished_at=time.time(),
                )
            finally:
                self._active_supervisors.pop(command_id, None)
                root_key = (request.org_id, root_node_id)
                if self._running_by_root.get(root_key) == command_id:
                    self._running_by_root.pop(root_key, None)
                self._inflight_tasks.pop(command_id, None)
                org_cids = self._inflight_by_org.get(request.org_id)
                if org_cids is not None:
                    org_cids.discard(command_id)
                    if not org_cids:
                        self._inflight_by_org.pop(request.org_id, None)

        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _run(), name=f"openakita-orgs-supervisor-{command_id}"
        )
        # Register the task + by-org index synchronously, before the
        # first ``await``, so cancel-while-still-pending races land
        # against a live task slot.
        self._inflight_tasks[command_id] = task
        self._inflight_by_org.setdefault(request.org_id, set()).add(command_id)

    async def _run_supervisor_with_hard_ceiling(
        self, supervisor: Supervisor, command_id: str
    ) -> Any:
        """Run ``supervisor.run()`` under ``settings.supervisor_hard_ceiling_s``.

        v22 P1 (audit v10 ``cmd_1779887674678_00000035_f092f4`` 14m49s
        slot leak): when the supervisor itself is wedged inside a
        provider call that has no cooperative cancel point, the
        cooperative :class:`CancellationToken` never sees a check, so
        the ``_schedule_run.run`` ``finally`` block that releases
        ``_running_by_root`` never executes. We wrap the awaitable in
        :func:`asyncio.wait_for` so the outer loop fires after
        ``supervisor_hard_ceiling_s`` wall-clock seconds:

        1. fire ``supervisor.cancel_token.cancel("hard_ceiling")`` so
           the cooperative path still gets one chance to write a
           ``cancelled`` final checkpoint;
        2. ``asyncio.sleep(0.5)`` -- short grace window so the
           supervisor's ``_terminate`` can flush;
        3. re-raise :class:`asyncio.TimeoutError` so the surrounding
           generic ``except Exception`` branch records a FAILED
           outcome and the ``finally`` block runs (releasing the
           slot).

        Returns the supervisor outcome on the happy path. Setting
        ``settings.supervisor_hard_ceiling_s <= 0`` disables the
        wrapper and falls back to the Sprint-9 ``await
        supervisor.run()`` behaviour byte-for-byte (so anyone who
        explicitly opts out via env keeps the old semantics).
        """
        ceiling = self._hard_ceiling_seconds()
        if ceiling <= 0:
            return await supervisor.run()
        try:
            return await asyncio.wait_for(supervisor.run(), timeout=ceiling)
        except TimeoutError as exc:
            logger.warning(
                "[OrgCmd] supervisor hard ceiling exceeded for cid=%s "
                "(ceiling=%ds); forcing cancel",
                command_id,
                ceiling,
            )
            try:
                supervisor.cancel_token.cancel("hard_ceiling")
            except Exception:  # noqa: BLE001 -- defensive, we are already aborting
                logger.debug(
                    "[OrgCmd] cancel_token.cancel raised under hard ceiling",
                    exc_info=True,
                )
            # Brief grace window so the supervisor's cooperative
            # ``_terminate`` can write its final checkpoint before
            # the outer finally clears bookkeeping. 0.5s is enough
            # for an in-process checkpoint write; LLM-stuck supervisors
            # will not observe it anyway and the slot is still released.
            with suppress(Exception):
                await asyncio.sleep(0.5)
            # Stamp the outcome cache so observability has a real
            # ``cancelled_by`` / ``reason`` instead of a bare
            # ``status=error`` from the generic except branch.
            prior = self._command_outcomes.get(command_id) or {}
            prior.update(
                {
                    "event": "agent_run_cancelled",
                    "cancelled_by": "hard_ceiling",
                    "reason": "supervisor_hard_ceiling_exceeded",
                    "ts": time.time(),
                }
            )
            self._command_outcomes[command_id] = prior
            # Best-effort: fabricate a SupervisorOutcome so
            # ``_reflect_supervisor_outcome`` writes a FAILED state
            # consistent with cooperative-cancel paths instead of the
            # generic ``except Exception`` branch's plain "error".
            try:
                from openakita.runtime.supervisor import FinalOutcome, SupervisorOutcome

                # RC-conv: even on the hard-ceiling fallback, salvage whatever
                # concrete output the run produced so the command can degrade
                # to a "completed-with-partial-result" instead of a bare error.
                try:
                    ceiling_deliverable = supervisor.best_effort_deliverable()
                except Exception:  # noqa: BLE001 -- never block the cleanup path
                    ceiling_deliverable = ""
                synthetic = SupervisorOutcome(
                    outcome=FinalOutcome.FAILED,
                    final_message=(
                        f"supervisor hard ceiling exceeded\n\n{ceiling_deliverable}"
                        if ceiling_deliverable
                        else "supervisor hard ceiling exceeded"
                    ),
                    final_checkpoint_id=getattr(supervisor, "last_checkpoint_id", None),
                    n_turns=int(getattr(getattr(supervisor, "stall_detector", None), "n_turns", 0) or 0),
                    n_replans=int(getattr(supervisor, "n_replans", 0) or 0),
                    reason="hard_ceiling",
                    deliverable=ceiling_deliverable,
                )
                self._reflect_supervisor_outcome(command_id, supervisor, synthetic)
            except Exception:  # noqa: BLE001 -- never block the cleanup path
                logger.debug(
                    "[OrgCmd] hard-ceiling outcome reflection failed",
                    exc_info=True,
                )
            raise exc

    @staticmethod
    def _hard_ceiling_seconds() -> int:
        """Read ``settings.supervisor_hard_ceiling_s`` defensively.

        Lazy import keeps the ``openakita.orgs`` <-> ``openakita.config``
        cycle loose (settings reads .env at process startup). Falls
        back to ``0`` (= disabled) when the attribute is missing so a
        fork that prunes the field cannot crash the runtime.
        """
        try:
            from openakita.config import settings as _settings

            return int(getattr(_settings, "supervisor_hard_ceiling_s", 0) or 0)
        except Exception:  # noqa: BLE001 -- never block submit()
            return 0

    # ------------------------------------------------------------------
    # v22 P1: background reconcile loop for ``_running_by_root``
    # ------------------------------------------------------------------

    async def start_reconcile_loop(self) -> None:
        """Spawn the background ``_reconcile_loop`` task (idempotent).

        Called from ``api/server.py`` lifespan startup. When
        ``settings.orgs_reconcile_interval_s <= 0`` the loop is
        disabled and this is a no-op. Safe to call twice -- the
        second call returns without spawning a duplicate task.
        """
        if self._reconcile_task is not None and not self._reconcile_task.done():
            return
        interval = self._reconcile_interval_seconds()
        if interval <= 0:
            logger.debug(
                "[OrgCmd] reconcile loop disabled (orgs_reconcile_interval_s=%d)",
                interval,
            )
            return
        loop = asyncio.get_running_loop()
        self._reconcile_stop_event = asyncio.Event()
        self._reconcile_task = loop.create_task(
            self._reconcile_loop(interval),
            name="openakita-orgs-reconcile-loop",
        )
        logger.info(
            "[OrgCmd] reconcile loop started (interval=%ds)", interval
        )

    async def stop_reconcile_loop(self, *, timeout: float = 2.0) -> None:
        """Signal + await the background reconcile task (idempotent)."""
        task = self._reconcile_task
        stop_event = self._reconcile_stop_event
        self._reconcile_task = None
        self._reconcile_stop_event = None
        if task is None or task.done():
            return
        if stop_event is not None:
            stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
            with suppress(BaseException):
                await task
        except Exception:  # noqa: BLE001 -- best-effort shutdown
            logger.debug(
                "[OrgCmd] reconcile loop stop raised", exc_info=True
            )

    @staticmethod
    def _reconcile_interval_seconds() -> int:
        """Read ``settings.orgs_reconcile_interval_s`` defensively."""
        try:
            from openakita.config import settings as _settings

            return int(getattr(_settings, "orgs_reconcile_interval_s", 10) or 0)
        except Exception:  # noqa: BLE001 -- never block startup
            return 10

    @staticmethod
    def _cancel_drain_budget_s() -> int:
        """Read ``settings.orgs_cancel_drain_budget_s`` defensively.

        v22 RCA RC-6: the graceful drain window used by
        :meth:`_cooperative_cancel`. Lazy import keeps the
        ``openakita.orgs`` <-> ``openakita.config`` cycle loose and a
        config-load failure cannot block cancel; falls back to the
        Sprint-9 historical 5s on any error.
        """
        try:
            from openakita.config import settings as _settings

            return int(getattr(_settings, "orgs_cancel_drain_budget_s", 8) or 0)
        except Exception:  # noqa: BLE001 -- never block cancel
            return 5

    async def _reconcile_loop(self, interval: int) -> None:
        """Background coroutine: sleep, then ``_reconcile_tick``, repeat.

        Listens on ``_reconcile_stop_event`` so ``stop_reconcile_loop``
        can wake us promptly. A tick that raises is logged + the loop
        keeps running -- a transient failure on one org should not
        wedge the reconciler for the whole process.
        """
        stop_event = self._reconcile_stop_event
        while True:
            try:
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    except TimeoutError:
                        pass
                    if stop_event.is_set():
                        return
                else:
                    await asyncio.sleep(interval)
                try:
                    self._reconcile_tick()
                except Exception:  # noqa: BLE001 -- never wedge the loop
                    logger.exception("[OrgCmd] _reconcile_tick raised")
            except asyncio.CancelledError:
                return

    def _reconcile_tick(self) -> None:
        """Scan ``_running_by_root`` and pop stale entries.

        Pop conditions (audit v10 §19 / cmd_..._f092f4 trace):

        * the command id is missing from ``_commands`` entirely;
        * the command id is in ``_commands`` but its ``status`` is
          terminal (``done`` / ``error`` / ``cancelled``);
        * the command id has no live :class:`Supervisor` in
          ``_active_supervisors`` (the supervisor exited but the
          finally release path was skipped, e.g. KeyError before the
          pop reached us).

        Reconcile NEVER cancels live tasks. The Sprint-9 watchdog used
        to do that and conflated "stale bookkeeping" with "stuck
        supervisor"; we kept that responsibility in the hard ceiling
        wrapper instead so the two layers do not race each other.
        """
        # Materialise the keys upfront so we can mutate ``_running_by_root``
        # while iterating without a RuntimeError.
        stale_keys: list[tuple[str, str]] = []
        for key, cid in list(self._running_by_root.items()):
            cmd = self._commands.get(cid)
            has_supervisor = cid in self._active_supervisors
            if cmd is None:
                # Command bookkeeping is gone (TTL purge / crash) but
                # the slot lingers. Safe to drop.
                stale_keys.append(key)
                continue
            status = cmd.get("status")
            if status in ("done", "error", "cancelled"):
                # Terminal command still pinning the slot -> definitely
                # a leak (real running cmds keep status=running).
                stale_keys.append(key)
                continue
            if status == "running" and not has_supervisor:
                # The supervisor entry is gone but the command thinks
                # it is still running. This is the classic
                # ``cmd_..._f092f4`` shape: the finally block was
                # skipped before reaching ``_running_by_root.pop``.
                stale_keys.append(key)
                continue
        for key in stale_keys:
            popped_cid = self._running_by_root.pop(key, None)
            if popped_cid is not None:
                logger.warning(
                    "[OrgCmd] reconcile dropped stale _running_by_root "
                    "entry %s -> %s",
                    key,
                    popped_cid,
                )

    def _reflect_supervisor_outcome(
        self,
        command_id: str,
        supervisor: Supervisor,
        outcome: Any,
    ) -> None:
        """Translate a :class:`SupervisorOutcome` into command-state fields."""

        # Lazy import keeps the runtime <-> orgs cycle loose.
        from openakita.runtime.supervisor import FinalOutcome

        outcome_value = getattr(getattr(outcome, "outcome", None), "value", None)
        n_turns = int(getattr(outcome, "n_turns", 0) or 0)
        n_replans = int(getattr(outcome, "n_replans", 0) or 0)
        final_cp = getattr(outcome, "final_checkpoint_id", None)
        final_msg = getattr(outcome, "final_message", "") or ""
        deliverable = str(getattr(outcome, "deliverable", "") or "")

        # RC-conv (优雅降级): OUT_OF_TURNS / REPLAN_BUDGET_EXHAUSTED are budget
        # exits, not crashes. When the run actually produced a usable
        # deliverable we surface a "completed-with-partial-result" instead of a
        # bare ``status=error`` with no output -- the worst UX. Only when there
        # is genuinely nothing to show do we fall back to the error state.
        graceful_partial = bool(deliverable.strip())
        if outcome_value == FinalOutcome.DONE.value:
            status, phase, error = "done", "done", None
        elif outcome_value == FinalOutcome.CANCELLED.value:
            status, phase, error = "cancelled", "cancelled", None
        elif outcome_value == FinalOutcome.OUT_OF_TURNS.value:
            if graceful_partial:
                status, phase, error = "done", "partial", None
            else:
                status, phase, error = "error", "out_of_turns", final_msg or outcome_value
        elif outcome_value == FinalOutcome.REPLAN_BUDGET_EXHAUSTED.value:
            if graceful_partial:
                status, phase, error = "done", "partial", None
            else:
                status, phase, error = "error", "error", final_msg or outcome_value
        elif outcome_value == FinalOutcome.FAILED.value:
            # hard-ceiling / hard failure: still surface a partial deliverable
            # if the run managed to produce one before being force-cancelled.
            if graceful_partial:
                status, phase, error = "done", "partial", None
            else:
                status, phase, error = "error", "error", final_msg or outcome_value
        else:
            status, phase, error = "done", "done", None

        event_ref = f"supervisor_{outcome_value}" if outcome_value else None
        self._update_command_state(
            command_id,
            status=status,
            phase=phase,
            result={
                "final_message": final_msg,
                "deliverable": deliverable,
                "partial": (phase == "partial"),
                "n_turns": n_turns,
                "n_replans": n_replans,
                "final_checkpoint_id": final_cp,
                "outcome": outcome_value,
            },
            error=error,
            event_ref=event_ref,
            finished_at=time.time(),
            supervisor_outcome=outcome_value,
            supervisor_n_turns=n_turns,
            supervisor_n_replans=n_replans,
            supervisor_last_checkpoint_id=final_cp,
        )
        # Mirror into ``_command_outcomes`` so :meth:`get_status` can
        # surface ``event_ref`` (the v1 outcome-cache lookup is the
        # single source of truth for that field).
        existing = self._command_outcomes.get(command_id) or {}
        existing.update(
            {
                "event": event_ref,
                "reason": existing.get("reason"),
                "error": error,
                "ts": time.time(),
            }
        )
        self._command_outcomes[command_id] = existing

        # UI issue #8: flip the submit-time root task to delivered/rejected so
        # the project board shows completion (100%) once the command converges.
        oid_for_done: str | None = None
        try:
            fin = getattr(self._runtime, "finalize_command_project", None)
            if callable(fin):
                cmd_for_proj = self._commands.get(command_id) or {}
                oid_for_done = cmd_for_proj.get("org_id")
                if oid_for_done:
                    fin(oid_for_done, command_id, ok=(status == "done"))
        except Exception:  # noqa: BLE001
            pass

        # Item 2: route the terminal state through the event bus so a
        # ``command_done`` event is persisted (events.jsonl) + streamed (SSE) +
        # WS-broadcast, instead of only being discoverable by polling. Best
        # effort + idempotent (the runtime dedupes per command_id). We schedule
        # it because ``_reflect_supervisor_outcome`` is sync but always runs
        # inside the supervisor task's event loop.
        try:
            emit_done = getattr(self._runtime, "emit_command_done", None)
            if callable(emit_done) and oid_for_done:
                cmd_final = self._commands.get(command_id) or {}
                done_result = cmd_final.get("result")
                done_error = cmd_final.get("error")
                done_status = cmd_final.get("status") or status
                import asyncio as _asyncio

                try:
                    loop = _asyncio.get_running_loop()
                    loop.create_task(
                        emit_done(
                            oid_for_done,
                            command_id,
                            status=done_status,
                            result=done_result,
                            error=done_error,
                        )
                    )
                except RuntimeError:
                    # No running loop (sync test context): skip live emit; the
                    # polling fallback still surfaces the terminal state.
                    pass
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Continue-previous resume helper
    # ------------------------------------------------------------------

    def _lookup_resume_checkpoint(
        self, org_id: str, root_node_id: str
    ) -> tuple[str | None, str | None]:
        """Find the last terminated command for ``root_node_id`` + its checkpoint.

        Returns ``(checkpoint_id, previous_command_id)``. Both fall to
        ``None`` when no terminated command exists, in which case the
        caller falls back to :meth:`_build_continue_content` legacy
        content-concatenation. The actual checkpoint *file* existence
        is verified inside the background task via
        :meth:`Supervisor.resume_from_checkpoint`; here we only pick
        out the metadata that the bookkeeping dict carries so the
        fast path stays sync.
        """

        previous = self._find_recent_previous_command(org_id, root_node_id)
        if previous is None:
            return None, None
        prev_cid = str(previous.get("command_id") or "")
        # The most reliable signal is the ``supervisor_last_checkpoint_id``
        # the Sprint-9 outcome reflection writes onto the command dict;
        # falls back to ``result.final_checkpoint_id`` for the v20
        # transition window when the old shape might still be on disk.
        cp_id = previous.get("supervisor_last_checkpoint_id") or None
        if not cp_id:
            result = previous.get("result")
            if isinstance(result, dict):
                cp_id = result.get("final_checkpoint_id") or None
        return (str(cp_id) if cp_id else None), (prev_cid or None)

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
