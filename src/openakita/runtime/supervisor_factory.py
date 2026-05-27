"""Shared :class:`~openakita.runtime.supervisor.Supervisor` factory.

Single composition root for *both* HTTP and IM dispatch paths. The
v2 IM canary (``runtime.channel_routing.dispatch_inbound_message_to_v2``)
and the v2 HTTP command surface (``orgs.command_service.OrgCommandService.submit``)
historically wired their own supervisor instances with subtly
different defaults -- different checkpointer (Memory vs Sqlite),
different brain (Degenerate vs ad-hoc), different StreamBus
(fresh per-call vs registry-shared). The Sprint-9 HTTP takeover
collapses them onto this factory so the two surfaces are
byte-for-byte equivalent at the supervisor-construction boundary.

What this module is NOT:

* It is not the LLM brain. The brain is a parameter; we provide a
  :class:`PassThroughSupervisorBrain` default which is enough for
  Sprint-9 (single-shot delegation that lets Sprint-4 ``<dispatch>``
  XML recursion inside the agent do the multi-turn work). A real
  multi-turn LLM-driven brain is the P-RC-4 follow-up.
* It is not the executor. The executor lives in
  ``orgs._runtime_agent_pipeline_executor`` and is exposed to the
  supervisor through the ``deliver`` callable built here.

Per-org checkpointer cache (audit §9 item 2):
  The factory keeps a process-local ``dict[org_id, SqliteCheckpointer]``
  keyed by org so a long-running process amortises the SQLite open
  cost across commands while still keeping each org's checkpoint
  store isolated on disk (``data/orgs/<id>/runtime/checkpoints.db``).
  ``aclose_all`` is exposed for the FastAPI ``shutdown`` hook.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from openakita.agent.supervisor_brain import PassThroughSupervisorBrain
from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import BaseCheckpointer, MemoryCheckpointer
from openakita.runtime.stream import StreamBus
from openakita.runtime.stream_registry import get_or_create_org_stream_bus
from openakita.runtime.supervisor import (
    DelegationResult,
    DeliverCallable,
    Supervisor,
    SupervisorBrain,
)

__all__ = [
    "DEFAULT_CHECKPOINT_DIR",
    "aclose_all_checkpointers",
    "build_supervisor_for_command",
    "get_or_create_checkpointer",
    "reset_checkpointer_cache",
]

logger = logging.getLogger(__name__)

#: Where per-org sqlite checkpoint files live. Lazily created on first
#: write so a clean dev checkout has no on-disk side effects until a
#: command actually runs.
DEFAULT_CHECKPOINT_DIR = Path("data/orgs")


_CHECKPOINTER_LOCK = threading.Lock()
_ORG_CHECKPOINTERS: dict[str, BaseCheckpointer] = {}


def get_or_create_checkpointer(
    org_id: str,
    *,
    base_dir: Path | None = None,
) -> BaseCheckpointer:
    """Return the long-lived :class:`SqliteCheckpointer` for ``org_id``.

    First call mints the on-disk file under
    ``<base_dir>/<org_id>/runtime/checkpoints.db``; subsequent calls
    return the cached handle. ``base_dir`` defaults to the
    :data:`DEFAULT_CHECKPOINT_DIR` constant; tests inject a tmp_path
    to keep them sandboxed.

    Thread-safe via a module-level lock; the underlying SQLite
    backend itself uses ``check_same_thread=False`` plus its own
    RLock so multi-loop access is safe.
    """
    if not org_id:
        raise ValueError("org_id must be a non-empty string")
    base = base_dir or DEFAULT_CHECKPOINT_DIR
    with _CHECKPOINTER_LOCK:
        existing = _ORG_CHECKPOINTERS.get(org_id)
        if existing is not None:
            return existing
        # Local import keeps the cycle between runtime.* leaves loose.
        from openakita.runtime.backends.sqlite import SqliteCheckpointer

        target_dir = base / org_id / "runtime"
        target_dir.mkdir(parents=True, exist_ok=True)
        cp = SqliteCheckpointer(target_dir / "checkpoints.db")
        _ORG_CHECKPOINTERS[org_id] = cp
        logger.debug("SupervisorFactory: minted checkpointer for org=%s", org_id)
        return cp


async def aclose_all_checkpointers() -> None:
    """Close every cached checkpointer; safe to call multiple times.

    Used by the FastAPI ``shutdown`` lifespan to release SQLite file
    handles cleanly. Errors are logged + swallowed: shutdown must
    never raise.
    """
    with _CHECKPOINTER_LOCK:
        items = list(_ORG_CHECKPOINTERS.items())
        _ORG_CHECKPOINTERS.clear()
    for org_id, cp in items:
        try:
            await cp.aclose()
        except Exception:  # noqa: BLE001 -- shutdown best-effort
            logger.debug("SupervisorFactory.aclose failed for org=%s", org_id, exc_info=True)


def reset_checkpointer_cache() -> None:
    """Drop the cache without closing (test teardown only)."""
    with _CHECKPOINTER_LOCK:
        _ORG_CHECKPOINTERS.clear()


def _make_executor_deliver(
    *,
    org_id: str,
    command_id: str,
    executor: Any,
) -> DeliverCallable:
    """Build a :class:`DeliverCallable` that routes to the v2 executor.

    The supervisor calls ``deliver(next_speaker, instruction, progress)``;
    this adapter forwards to
    :meth:`AgentPipelineExecutor.activate_and_run` so the existing
    Sprint-3 ContextVar setup + Sprint-4 ``<dispatch>`` XML recursion
    + artefact persistence + ``cancel_source_provider`` machinery
    keeps working unchanged. The executor remains the single owner
    of all per-node lifecycle; the supervisor only owns inter-node
    orchestration.
    """

    async def _deliver(speaker: str, instruction: str, progress: Any) -> DelegationResult:
        node_id = speaker or ""
        # ``speaker`` may be a role / address (Sprint-9 PassThroughBrain
        # always sets it to the root node_id directly, so the simple
        # lookup is fine). Future brains that emit role-style
        # ``next_speaker`` will need an address resolver here.
        try:
            result = await executor.activate_and_run(
                org_id=org_id,
                node_id=node_id,
                content=instruction,
                command_id=command_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- never crash the supervisor loop
            logger.warning(
                "SupervisorFactory: executor.activate_and_run raised "
                "(org=%s node=%s cid=%s): %s",
                org_id, node_id, command_id, exc,
            )
            return DelegationResult(
                success=False,
                speaker=node_id,
                message=f"executor error: {exc}",
                metadata={"error": str(exc), "command_id": command_id},
            )
        status = str(result.get("status") or "")
        output = str(result.get("output") or "")
        reason = result.get("reason")
        ok = status == "ok"
        message = output or (str(reason) if reason else status)
        return DelegationResult(
            success=ok,
            speaker=node_id,
            message=message,
            metadata={
                "status": status,
                "reason": reason,
                "command_id": command_id,
            },
        )

    return _deliver


def build_supervisor_for_command(
    *,
    org_id: str,
    command_id: str,
    root_node_id: str,
    task: str,
    executor: Any,
    cancel_token: CancellationToken | None = None,
    brain: SupervisorBrain | None = None,
    stream: StreamBus | None = None,
    checkpointer: BaseCheckpointer | None = None,
    deliver: DeliverCallable | None = None,
    max_stalls: int = 3,
    max_turns: int = 30,
    max_replans: int = 5,
    progress_ledger_max_retries: int = 10,
) -> Supervisor:
    """Build a fully-wired :class:`Supervisor` for one user command.

    Single composition root for HTTP and IM. Each injectable component
    has a sensible default; callers (tests, IM legacy canary, HTTP
    submit) override only what they need:

    * ``executor``: required. The v2 agent executor that owns node
      activation. Production wiring uses the singleton on
      ``app.state.org_agent_executor``.
    * ``deliver``: optional. When None we build
      :func:`_make_executor_deliver` over the supplied executor. IM
      canary that wants a different transport (e.g. messenger.deliver
      addressing through node registries) can pass its own.
    * ``brain``: defaults to :class:`PassThroughSupervisorBrain`
      keyed on ``root_node_id`` -- single-shot delegation, then DONE.
      A real LLM-driven multi-turn brain is the P-RC-4 follow-up.
    * ``stream``: defaults to the org-scoped registry bus so SSE
      consumers (``GET /api/v2/orgs/{id}/events/stream``) see live
      events.
    * ``checkpointer``: defaults to the per-org cached
      :class:`SqliteCheckpointer`. Tests pass
      :class:`MemoryCheckpointer` for isolation.
    * ``cancel_token``: optional. We create a fresh one when None so
      :meth:`OrgCommandService.cancel` always has something to fire.

    Returns the supervisor; the caller is responsible for awaiting
    :meth:`Supervisor.run` (typically in a background task) and for
    registering the cancel token in the per-org lookup map so the
    cancel HTTP endpoint can reach it.
    """
    if not org_id:
        raise ValueError("org_id required")
    if not command_id:
        raise ValueError("command_id required")
    if not root_node_id:
        raise ValueError("root_node_id required")
    if executor is None and deliver is None:
        raise ValueError("either `executor` or `deliver` must be supplied")

    resolved_stream = stream or get_or_create_org_stream_bus(org_id)
    resolved_checkpointer = checkpointer or get_or_create_checkpointer(org_id)
    resolved_token = cancel_token or CancellationToken()
    resolved_brain = brain or PassThroughSupervisorBrain(root_node_id=root_node_id)
    resolved_deliver = deliver or _make_executor_deliver(
        org_id=org_id, command_id=command_id, executor=executor
    )

    return Supervisor(
        command_id=command_id,
        org_id=org_id,
        root_node_id=root_node_id,
        task=task,
        brain=resolved_brain,
        deliver=resolved_deliver,
        stream=resolved_stream,
        checkpointer=resolved_checkpointer,
        cancel_token=resolved_token,
        max_stalls=max_stalls,
        max_turns=max_turns,
        max_replans=max_replans,
        progress_ledger_max_retries=progress_ledger_max_retries,
    )


# Re-export for callers that want a fresh in-memory backend without
# pulling the checkpoint module directly.
DefaultMemoryCheckpointer: type[BaseCheckpointer] = MemoryCheckpointer
DeliverFactoryCallable = Callable[..., Awaitable[DelegationResult]]
