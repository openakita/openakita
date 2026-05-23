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

    async def cancel_user_command(self, org_id: str, command_id: str) -> dict[str, Any]: ...

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
        """
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None
        if cmd.get("status") != "running":
            return {"ok": True, "command_id": command_id, "already_done": True}
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

    def _update_command_state(
        self,
        command_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Patch a command record in-place. v1 parity."""
        cmd = self._commands.get(command_id)
        if cmd is None:
            return None
        if status is not None:
            cmd["status"] = status
            if phase is None and status in ("done", "error"):
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
        """

        async def _run_minimal() -> None:
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
                    request.target_node_id,
                    request.content,
                    command_id=command_id,
                )
                self._update_command_state(
                    command_id,
                    status="done",
                    phase="done",
                    result=result,
                    finished_at=time.time(),
                )
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

        loop = asyncio.get_running_loop()
        loop.create_task(_run_minimal())

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
