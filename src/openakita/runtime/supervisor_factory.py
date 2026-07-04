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


def _resolve_speaker_to_node_id(
    speaker: str,
    *,
    node_directory: Any,
    root_node_id: str,
) -> str:
    """RC-5 S4 gap②: map a (possibly role-style) ``speaker`` to a node_id.

    Production-grade promotion of the deliver-layer address resolution the
    Sprint-9 comment flagged ("Future brains that emit role-style
    next_speaker will need an address resolver here"). When a real
    ``node_directory`` (list of ``NodeDescriptor``) is supplied we run the
    brain's resolver (exact node_id -> exact role -> substring -> root
    fallback) so a model answer like ``"copywriter"`` lands on the concrete
    ``"node_writer"`` the executor expects.

    Byte-for-byte preservation of the passthrough path: when no directory is
    supplied (the default PassThrough submit path), the speaker is used
    verbatim exactly as before -- no resolution, no behaviour change.
    """
    raw = speaker or ""
    if not node_directory:
        return raw
    try:
        from openakita.runtime.llm_supervisor_brain import LLMSupervisorBrain

        return LLMSupervisorBrain.resolve_next_speaker(
            raw, node_directory, root_node_id
        )
    except Exception:  # noqa: BLE001 -- resolution is best-effort, never crash
        logger.debug(
            "SupervisorFactory: speaker resolution failed for %r; using raw",
            raw,
            exc_info=True,
        )
        return raw


def _make_executor_deliver(
    *,
    org_id: str,
    command_id: str,
    executor: Any,
    cancel_event: asyncio.Event | None = None,
    node_directory: Any = None,
    root_node_id: str = "",
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

    Sprint-13 H1 (RC-4 §6 H1 / ``_v27_biz/_drain_rca.md`` R-A): when
    :func:`build_supervisor_for_command` minted a ``cancel_event`` for
    the supervisor's :class:`CancellationToken` it is closure-captured
    here so every ``executor.activate_and_run`` call carries the same
    event without changing the public
    :data:`DeliverCallable` protocol shape -- supervisor unit tests
    that construct ``async def deliver(speaker, instruction, progress)``
    deliverers keep working unchanged. The event flows
    ``deliver -> executor -> agent.run -> brain.messages_create_async
    -> LLMClient.chat -> _race_with_cancel`` so a user cancel under
    the 10-concurrent storm shape from v25 C6 aborts the in-flight
    ``httpx`` request immediately instead of unwinding 13 await frames
    over the 8s drain budget.
    """

    async def _deliver(speaker: str, instruction: str, progress: Any) -> DelegationResult:
        # ``speaker`` may be a role / address. Sprint-9 PassThroughBrain
        # always sets it to the root node_id directly (no directory supplied
        # -> verbatim, unchanged). RC-5 S4 gap②: when an LLM brain runs with a
        # real node directory, resolve a role-style ``next_speaker`` to the
        # concrete node_id the executor expects.
        node_id = _resolve_speaker_to_node_id(
            speaker, node_directory=node_directory, root_node_id=root_node_id
        )
        try:
            result = await executor.activate_and_run(
                org_id=org_id,
                node_id=node_id,
                content=instruction,
                command_id=command_id,
                cancel_event=cancel_event,
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


def _resolve_brain(
    *,
    brain: SupervisorBrain | None,
    brain_mode: str | None,
    root_node_id: str,
    llm_client: Any,
    node_directory: Any,
) -> SupervisorBrain:
    """Pick the SupervisorBrain. Explicit ``brain`` wins; else flag-driven.

    RC-5 route-B gray-switch. Default ``"passthrough"`` preserves Sprint-9
    behaviour exactly. ``"llm"`` only engages the real LLM brain when a
    client is wired; otherwise it logs and safely falls back to PassThrough
    so flipping the config alone can never break the production submit path
    (which never passes ``llm_client``).
    """
    if brain is not None:
        return brain

    mode = brain_mode
    if mode is None:
        try:
            from openakita.config import settings

            mode = settings.orgs_supervisor_brain_mode
        except Exception:  # noqa: BLE001 -- config must never break submit
            logger.debug("SupervisorFactory: settings read failed", exc_info=True)
            mode = "passthrough"

    if mode == "llm":
        if llm_client is None:
            logger.warning(
                "SupervisorFactory: orgs_supervisor_brain_mode='llm' but no "
                "llm_client supplied; falling back to PassThroughSupervisorBrain "
                "(zero-impact safe default). Wire an llm_client to engage "
                "LLMSupervisorBrain (RC-5 route B)."
            )
        else:
            from openakita.runtime.llm_supervisor_brain import LLMSupervisorBrain

            logger.info(
                "SupervisorFactory: engaging LLMSupervisorBrain (RC-5 route B) "
                "for root_node=%s",
                root_node_id,
            )
            return LLMSupervisorBrain(
                root_node_id=root_node_id,
                client=llm_client,
                node_directory=node_directory,
            )

    return PassThroughSupervisorBrain(root_node_id=root_node_id)


def build_supervisor_for_command(
    *,
    org_id: str,
    command_id: str,
    root_node_id: str,
    task: str,
    executor: Any,
    cancel_token: CancellationToken | None = None,
    brain: SupervisorBrain | None = None,
    brain_mode: str | None = None,
    llm_client: Any = None,
    node_directory: Any = None,
    stream: StreamBus | None = None,
    checkpointer: BaseCheckpointer | None = None,
    deliver: DeliverCallable | None = None,
    max_stalls: int = 3,
    max_turns: int = 30,
    max_replans: int = 5,
    progress_ledger_max_retries: int = 10,
    wall_clock_soft_budget_s: float = 0.0,
    force_root_finalization: bool = False,
    wall_clock_hard_ceiling_s: float = 0.0,
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
    * ``brain``: when supplied, used verbatim (explicit injection always
      wins -- tests rely on this). When ``None`` the brain is selected by
      ``brain_mode`` (defaulting to ``settings.orgs_supervisor_brain_mode``,
      itself defaulting to ``"passthrough"``):

      - ``"passthrough"`` -> :class:`PassThroughSupervisorBrain` keyed on
        ``root_node_id`` (single-shot delegation, then DONE). This is the
        zero-production-impact default; the legacy ``submit`` path never
        passes ``brain_mode`` / ``llm_client`` so it always lands here.
      - ``"llm"`` -> RC-5 route-B :class:`LLMSupervisorBrain`, but **only
        when an ``llm_client`` is also supplied**. With the flag flipped but
        no client wired we log a warning and **safely fall back** to
        PassThrough rather than crash -- so flipping the config alone can
        never break production.

      ``llm_client`` / ``node_directory`` are forwarded to the
      :class:`LLMSupervisorBrain` constructor (RC-5 prototype). A real
      multi-turn LLM-driven brain is the P-RC-4 follow-up being prototyped
      under ``_rc5_biz/prototype/``.
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
    resolved_brain = _resolve_brain(
        brain=brain,
        brain_mode=brain_mode,
        root_node_id=root_node_id,
        llm_client=llm_client,
        node_directory=node_directory,
    )

    # v22 RCA RC-4 / Sprint-13 H1: bridge ``cancel_token`` ->
    # ``asyncio.Event`` at the composition root so the bridge is
    # established even before :meth:`Supervisor.run` is awaited. The
    # supervisor passes the event down through
    # :class:`SupervisorBrain` (used by ``extract_facts`` / ``draft_plan``
    # / ``emit_progress_ledger``) AND -- new in H1 -- the executor
    # ``deliver`` adapter closure-captures the same event so it
    # reaches ``Brain.messages_create_async`` ->
    # ``LLMClient._race_with_cancel`` and aborts the in-flight
    # ``httpx`` request the instant a user cancel fires
    # (audit ``_v27_biz/_drain_rca.md`` R-A). We mint the event before
    # building the deliver closure so the same instance flows down
    # both paths.
    cancel_event = asyncio.Event()
    resolved_token.add_callback(cancel_event.set)

    resolved_deliver = deliver or _make_executor_deliver(
        org_id=org_id,
        command_id=command_id,
        executor=executor,
        cancel_event=cancel_event,
        node_directory=node_directory,
        root_node_id=root_node_id,
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
        cancel_event=cancel_event,
        max_stalls=max_stalls,
        max_turns=max_turns,
        max_replans=max_replans,
        progress_ledger_max_retries=progress_ledger_max_retries,
        wall_clock_soft_budget_s=wall_clock_soft_budget_s,
        force_root_finalization=force_root_finalization,
        wall_clock_hard_ceiling_s=wall_clock_hard_ceiling_s,
    )


# Re-export for callers that want a fresh in-memory backend without
# pulling the checkpoint module directly.
DefaultMemoryCheckpointer: type[BaseCheckpointer] = MemoryCheckpointer
DeliverFactoryCallable = Callable[..., Awaitable[DelegationResult]]
