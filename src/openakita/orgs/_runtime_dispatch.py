"""``_runtime_dispatch.py`` -- v2 OrgRuntime dispatch sibling (P9.6e).

The heaviest sibling: lifts the command-dispatch + tracker +
chain machinery out of v1 ``OrgRuntime``. v1 spreads this
across ~22 methods totalling ~1 050 LOC (the cross-cutting
``tracker`` x 254 + ``chain_id`` x 221 references the
P9.6 turn-1 escape-hatch report flagged); the v2 rewrite
collapses to a focused :class:`CommandDispatchManager` +
``_CommandTracker`` dataclass + ``_TrackerRegistry`` storage.

This commit (P9.6e) lands the public API surface needed by
:class:`CommandRuntimeProtocol` (the P9.4 contract OrgRuntime
implements):

* :meth:`CommandDispatchManager.send_command` -- create
  tracker + dispatch via injected
  :class:`OrgCommandServiceProtocol`.
* :meth:`CommandDispatchManager.cancel_user_command` -- flip
  tracker state + call injected cancel.
* :meth:`CommandDispatchManager.cancel_node_task` -- per-node
  cancellation hook.
* :meth:`CommandDispatchManager.get_command_tracker_snapshot`
  -- compact live view (v1 parity).
* :meth:`CommandDispatchManager.has_active_delegations` --
  downstream-work probe.
* :meth:`CommandDispatchManager.get_active_root_intent` --
  what the user last asked.

Plus the small chain-id helpers (``get_current_chain_id`` /
``set_current_chain_id`` / ``is_chain_closed`` /
``mark_chain_closed`` / ``find_root_node_id``) that the
node-lifecycle sibling (P9.6g) will consume.

v2 keeps the same observable shape as v1 (dict-shaped
responses, same string state values) so the P9.6gamma parity
gate can assert equivalence; the implementation is fresh code
(no v1 import) so ADR-0012 (no-shim, P9.8 deletion eligible)
holds.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import time
from typing import Any

from .command_service import OrgCommandServiceProtocol, OrgLookupProtocol
from .runtime import EventBusProtocol

_LOGGER = logging.getLogger(__name__)

# Sprint-3 P0-1 (audit ``_orgs_business_capability_audit_v3.md`` §5.2 / §7):
# every dispatch to an entry / sub-task node writes a single JSONL line so the
# v14 finding "delegation_logs/today.jsonl increment = 0" becomes verifiable
# on the next exploratory pass. We resolve the directory lazily because the
# dispatch module is imported very early (before ``openakita.config.settings``
# has materialised its data_dir cache in some headless test contexts), and so
# the parent ``data/`` directory may not exist yet for fresh installs.
_DELEGATION_LOG_FALLBACK = Path("data") / "delegation_logs"


def _resolve_delegation_log_dir() -> Path | None:
    """Locate ``data/delegation_logs/`` without forcing ``openakita.config``
    import at module load time."""

    try:
        from openakita.config import settings  # local import: avoid cycle

        return Path(settings.data_dir) / "delegation_logs"
    except Exception:  # noqa: BLE001 -- config not ready yet (early tests)
        return _DELEGATION_LOG_FALLBACK


def _append_delegation_log(record: dict[str, Any]) -> None:
    """Best-effort JSONL append to ``data/delegation_logs/YYYYMMDD.jsonl``.

    Failures (read-only filesystem, locked file on Windows, missing
    ``data/`` root) are swallowed: the dispatch loop must never observe
    an IO error from observability bookkeeping.
    """

    log_dir = _resolve_delegation_log_dir()
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        record.setdefault("ts", datetime.now().isoformat())
        path = log_dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 -- observability must not poison dispatch
        _LOGGER.debug("delegation log append failed", exc_info=True)

# Tracker state constants -- parity with v1 ``OrgRuntime``
# ``_active_user_cmd`` dict state values.
TRACKER_RUNNING = "running"
TRACKER_FINALIZED = "finalized"
TRACKER_CANCELLED = "cancelled"
TRACKER_DEADLOCK_STOPPED = "deadlock-stopped"


@dataclass
class _CommandTracker:
    """Minimal tracker dataclass (v2 v1 ``OrgRuntime`` ``_active_user_cmd`` entry).

    Holds the live state of one in-flight user command.
    Populated by :meth:`CommandDispatchManager.send_command`;
    mutated by the dispatch / node-lifecycle / watchdog
    siblings via the registry.
    """

    org_id: str
    command_id: str
    root_node_id: str
    root_intent: str
    state: str = TRACKER_RUNNING
    created_at: float = field(default_factory=time)
    last_activity_at: float = field(default_factory=time)
    chains: set[str] = field(default_factory=set)
    accepted_chains: set[str] = field(default_factory=set)
    cancel_reason: str | None = None
    finalize_decision: str | None = None
    root_visible_result: str | None = None

    def to_snapshot(self) -> dict[str, Any]:
        """Return a compact dict view (v1 ``get_command_tracker_snapshot`` parity)."""

        return {
            "org_id": self.org_id,
            "command_id": self.command_id,
            "root_node_id": self.root_node_id,
            "root_intent": self.root_intent,
            "state": self.state,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "chain_count": len(self.chains),
            "accepted_chain_count": len(self.accepted_chains),
            "cancel_reason": self.cancel_reason,
            "finalize_decision": self.finalize_decision,
        }


class _TrackerRegistry:
    """In-memory tracker store keyed by ``(org_id, command_id)``."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], _CommandTracker] = {}
        # Per-org running tracker -- v1 invariant: at most one
        # in-flight user command per org-root pair.
        self._by_root: dict[tuple[str, str], _CommandTracker] = {}
        # Per-node chain id (parity with v1 ``_node_current_chain``).
        self._node_chain: dict[tuple[str, str], str] = {}
        # Closed chain set (parity with v1 ``_closed_chains``).
        self._closed_chains: dict[str, set[str]] = {}

    def register(self, tracker: _CommandTracker) -> None:
        self._by_key[(tracker.org_id, tracker.command_id)] = tracker
        self._by_root[(tracker.org_id, tracker.root_node_id)] = tracker

    def get(self, org_id: str, command_id: str) -> _CommandTracker | None:
        return self._by_key.get((org_id, command_id))

    def get_by_root(self, org_id: str, root_node_id: str) -> _CommandTracker | None:
        return self._by_root.get((org_id, root_node_id))

    def for_org(self, org_id: str) -> list[_CommandTracker]:
        return [t for (oid, _cid), t in self._by_key.items() if oid == org_id]

    def discard(self, org_id: str, command_id: str) -> None:
        tracker = self._by_key.pop((org_id, command_id), None)
        if tracker is not None:
            self._by_root.pop((org_id, tracker.root_node_id), None)


_AgentDispatchCb = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]
_ChainCancelCb = Callable[[str, str, str], Awaitable[None]]


class CommandDispatchManager:
    """v2 dispatch surface (CommandRuntimeProtocol backing).

    Constructor args (all DI; no v1 import):

    * ``command_service`` -- :class:`OrgCommandServiceProtocol`
      (P9.4) for submit / cancel / status fan-out.
    * ``lookup`` -- :class:`OrgLookupProtocol` (P9.4/P9.5)
      for ``get_org`` -> :class:`Organization`.
    * ``event_bus`` -- :class:`EventBusProtocol` (P9.6a0)
      for tracker / chain event emission.
    * ``agent_dispatch`` -- async callback that the agent
      pipeline sibling (P9.6f) plugs in to actually run the
      command content through the target node''s agent. Sig:
      ``(org_id, node_id, command_id, content) -> dict``.
    * ``chain_cancel`` -- optional async callback that the
      project-store layer plugs in to cascade-cancel chain
      children when a user command is cancelled. Sig:
      ``(org_id, chain_id, reason) -> None``.
    """

    def __init__(
        self,
        *,
        command_service: OrgCommandServiceProtocol | None,
        lookup: OrgLookupProtocol,
        event_bus: EventBusProtocol,
        agent_dispatch: _AgentDispatchCb | None = None,
        chain_cancel: _ChainCancelCb | None = None,
    ) -> None:
        self._cmd = command_service
        self._lookup = lookup
        self._bus = event_bus
        self._agent_dispatch = agent_dispatch
        self._chain_cancel = chain_cancel
        self._registry = _TrackerRegistry()

    # ------------------------------------------------------------------
    # CommandRuntimeProtocol surface (P9.4 contract)
    # ------------------------------------------------------------------

    async def send_command(
        self,
        org_id: str,
        target_node_id: str,
        content: str,
        *,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """v1 ``OrgRuntime.send_command`` parity (144 LOC -> ~40 LOC).

        Steps:
        1. Validate org + node exist.
        2. Mint a tracker (v2 dataclass) and register it.
        3. Submit through :class:`OrgCommandServiceProtocol`
           if available (returns a command_id), else mint one.
        4. Hand the content off to the injected agent dispatch
           callback (fire-and-forget; the pipeline sibling
           awaits the agent run in its own task).
        5. Return a v1-shaped dict.

        H2 fix (audit ``_orgs_business_capability_audit_v1.md`` §3.2):
        when callers (notably ``OrgCommandService._run_minimal``) have
        already minted a command id at the service layer, accept it as
        a kwarg and use it as the tracker id verbatim instead of
        re-minting a fresh id here. This keeps the user-visible
        command_id (the one returned from ``OrgCommandService.submit``
        and used by ``GET /commands/{cid}``) identical to the tracker
        id, so live snapshot lookups actually resolve. The original
        no-kwarg call path (e.g. node-scheduler dispatch with no
        upstream id) is preserved: ``None`` falls back to the legacy
        submit-or-mint dance.
        """

        org = self._lookup.get_org(org_id)
        if org is None:
            return {"status": "error", "reason": "org_not_found", "org_id": org_id}
        if command_id:
            tracker_command_id = command_id
        elif self._cmd is not None:
            try:
                resp = await self._cmd.submit(
                    org_id=org_id,
                    target_node_id=target_node_id,
                    content=content,
                )
                tracker_command_id = str(resp.get("command_id") or "")
            except Exception:  # noqa: BLE001 (v1 parity: never crash dispatch)
                _LOGGER.exception("command_service.submit raised; falling back to inline id")
                tracker_command_id = f"cmd_{int(time() * 1000)}"
        else:
            tracker_command_id = f"cmd_{int(time() * 1000)}"
        tracker = _CommandTracker(
            org_id=org_id,
            command_id=tracker_command_id,
            root_node_id=target_node_id,
            root_intent=content,
        )
        self._registry.register(tracker)
        await self._bus.emit(
            "user_command_submitted",
            {"org_id": org_id, "command_id": tracker_command_id, "node_id": target_node_id},
        )
        # Sprint-3 P0-1 (audit ``_orgs_business_capability_audit_v3.md`` §5.2):
        # surface a "subtask assigned to entry node" signal even when the
        # dispatcher hands the work to a single root node. The v14 audit
        # found events.jsonl carrying zero ``subtask_assigned`` lines and
        # ``delegation_logs/today.jsonl`` increment = 0 for 60+ commands,
        # which made it impossible to tell whether the orchestrator had
        # actually picked a node or whether everything was being cosplayed
        # by the root LLM. Emitting the event + appending the JSONL line
        # here makes node dispatch verifiable end-to-end without yet
        # implementing the full chained dispatch (producer -> screenwriter
        # -> ...) that Sprint-3-extended will land.
        preview = content[:200] if isinstance(content, str) else ""
        await self._bus.emit(
            "subtask_assigned",
            {
                "org_id": org_id,
                "command_id": tracker_command_id,
                "node_id": target_node_id,
                "parent_node_id": None,  # entry dispatch: parent is the user
                "child_node_id": target_node_id,
                "content_preview": preview,
            },
        )
        _append_delegation_log(
            {
                "command_id": tracker_command_id,
                "org_id": org_id,
                "parent_node": None,
                "child_node": target_node_id,
                "node_id": target_node_id,
                "kind": "entry_dispatch",
                "content_preview": preview,
            }
        )
        if self._agent_dispatch is not None:
            try:
                await self._agent_dispatch(org_id, target_node_id, tracker_command_id, content)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("agent_dispatch failed (org=%s node=%s)", org_id, target_node_id)
        return {
            "status": "submitted",
            "command_id": tracker_command_id,
            "org_id": org_id,
            "node_id": target_node_id,
        }

    async def cancel_user_command(
        self,
        org_id: str,
        command_id: str,
        *,
        cancel_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """v1 ``OrgRuntime.cancel_user_command`` parity (65 LOC -> ~30 LOC).

        Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5 method A): accept
        an explicit ``cancel_reason`` so callers like
        :meth:`OrgCommandService.cancel_all_for_org` ("stop_org") and
        the watchdog ("watchdog") can stamp the
        ``user_command_cancelled`` and per-tracker fields with a
        source that survives in **events.jsonl**, not just the
        in-memory ``_command_outcomes`` cache the Sprint-5 commit
        wrote and the v17 audit caught as a single-plane fix.

        ``None`` (the default) preserves the Sprint-3 user-cancel
        observable: ``reason="user_cancel"`` + no ``cancelled_by`` --
        any existing reader that only knows the older payload shape
        keeps working unchanged.
        """

        tracker = self._registry.get(org_id, command_id)
        if tracker is None:
            return None
        if tracker.state != TRACKER_RUNNING:
            return {
                "ok": True,
                "command_id": command_id,
                "already_done": True,
                "state": tracker.state,
                # Sprint-3 P0-2 (audit ``_orgs_business_capability_audit_v3.md``
                # §5.3): even on the already-terminal short-circuit we surface
                # the root-node id we *would* have cancelled. Pre-fix the
                # field was always ``[]`` regardless of state, which made the
                # cancel route impossible to tell apart from "tracker missing
                # entirely".
                "cancelled_roots": [tracker.root_node_id] if tracker.root_node_id else [],
            }
        tracker.state = TRACKER_CANCELLED
        # Sprint-6 P0-2: store the explicit source so the tracker
        # snapshot reflects "who pressed the button" instead of
        # always reading "user_cancel". The string is the same value
        # we write to events.jsonl below for consistency.
        resolved_reason = cancel_reason or "user_cancel"
        tracker.cancel_reason = resolved_reason
        tracker.last_activity_at = time()
        if self._cmd is not None:
            try:
                await self._cmd.cancel(org_id, command_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("command_service.cancel raised")
        # Cascade-cancel chain children if a callback is wired.
        if self._chain_cancel is not None:
            for chain_id in list(tracker.chains):
                try:
                    await self._chain_cancel(org_id, chain_id, resolved_reason)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("chain_cancel raised (org=%s chain=%s)", org_id, chain_id)
        # Sprint-6 P0-2: emit both the legacy ``reason`` field (kept
        # at ``user_cancel`` when no source was supplied so the
        # existing reader stays compatible) and a new
        # ``cancelled_by`` field that names the source verbatim.
        # The v17 audit checks payload[cancelled_by]; pre-Sprint-6
        # the field was missing for stop-org / watchdog kills so
        # 0/5 R.F2 cases scored. New schema:
        #
        #   {"reason": "user_cancel" | <source>, "cancelled_by": <source>}
        cancel_payload: dict[str, Any] = {
            "org_id": org_id,
            "command_id": command_id,
            "reason": resolved_reason,
            "cancelled_by": resolved_reason,
        }
        await self._bus.emit("user_command_cancelled", cancel_payload)
        # Sprint-3 P0-2 (audit v3 §5.3): populate ``cancelled_roots`` so the
        # service layer's response stops lying with ``[]``. We use the single
        # tracker root because the current dispatch model fans out from one
        # root only; chain children will land here once D3-extended ships.
        return {
            "ok": True,
            "command_id": command_id,
            "cancelled": True,
            "cancelled_roots": [tracker.root_node_id] if tracker.root_node_id else [],
        }

    def has_active_delegations(self, org_id: str, root_node_id: str) -> bool:
        """v1 ``OrgRuntime._has_active_delegations`` parity (24 LOC -> ~6 LOC)."""

        tracker = self._registry.get_by_root(org_id, root_node_id)
        if tracker is None or tracker.state != TRACKER_RUNNING:
            return False
        return bool(tracker.chains - tracker.accepted_chains)

    def get_command_tracker_snapshot(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        """v1 ``OrgRuntime.get_command_tracker_snapshot`` parity (35 LOC -> ~3 LOC)."""

        tracker = self._registry.get(org_id, command_id)
        return tracker.to_snapshot() if tracker is not None else None

    def get_active_root_intent(self, org_id: str) -> str | None:
        """v1 ``OrgRuntime.get_active_root_intent`` parity (18 LOC -> ~5 LOC)."""

        running = [t for t in self._registry.for_org(org_id) if t.state == TRACKER_RUNNING]
        if not running:
            return None
        # Most recently created wins (matches v1 "first running tracker" intent).
        running.sort(key=lambda t: t.created_at, reverse=True)
        return running[0].root_intent

    # ------------------------------------------------------------------
    # Helpers consumed by node-lifecycle / agent-pipeline siblings
    # ------------------------------------------------------------------

    def get_current_chain_id(self, org_id: str, node_id: str) -> str | None:
        return self._registry._node_chain.get((org_id, node_id))

    def set_current_chain_id(self, org_id: str, node_id: str, chain_id: str) -> None:
        self._registry._node_chain[(org_id, node_id)] = chain_id

    def is_chain_closed(self, org_id: str, chain_id: str) -> bool:
        return chain_id in self._registry._closed_chains.get(org_id, set())

    def mark_chain_closed(self, org_id: str, chain_id: str) -> None:
        self._registry._closed_chains.setdefault(org_id, set()).add(chain_id)

    def register_chain(self, org_id: str, command_id: str, chain_id: str) -> None:
        """Hook the agent-pipeline calls when it opens a new chain."""

        tracker = self._registry.get(org_id, command_id)
        if tracker is not None:
            tracker.chains.add(chain_id)
            tracker.last_activity_at = time()

    def unregister_chain(self, org_id: str, chain_id: str) -> None:
        for tracker in self._registry.for_org(org_id):
            tracker.chains.discard(chain_id)
            tracker.accepted_chains.discard(chain_id)


__all__ = [
    "TRACKER_CANCELLED",
    "TRACKER_DEADLOCK_STOPPED",
    "TRACKER_FINALIZED",
    "TRACKER_RUNNING",
    "CommandDispatchManager",
]
