"""Presentation and waiter primitives for ``security_confirm`` events.

The implementation follows the RiskGate/PolicyV2 continuation contract
(presentation queue + confirm_event sidecar).

``UIConfirmBus`` owns only the UI-facing sidecar, presentation queue, and
async waiter wakeups for a confirmation id. Domain lifecycle remains outside:
PolicyV2 applies allowlist side effects through ``apply_resolution`` and
RiskGate applies record transitions and executable grants through
``RiskGateWorkflow``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from ..core.security_confirm_channel import require_security_confirm_decision

logger = logging.getLogger(__name__)


# Default TTL for pending UI confirms; ``configure_ttl`` lets the policy
# engine push the user-configured ``confirmation.confirm_ttl`` value in.
_DEFAULT_TTL_SECONDS: int = 300


# C17 Phase B.4: late-binding broadcast hook so multi-end clients can
# discover/cancel sibling confirms.
#
# ``hook(event_type, payload)`` is fired (best-effort) on:
#   - ``confirm_initiated`` after :meth:`UIConfirmBus.store_pending`
#   - ``confirm_revoked``  after :meth:`UIConfirmBus.resolve` / TTL expiry
#
# The hook is wired by ``api/server.py`` at startup via the same
# ``fire_event`` channel that PendingApprovalsStore uses. The bus must
# remain importable without a hook (CLI / tests) — set_broadcast_hook
# is optional and the bus tolerates a missing hook.
ConfirmEventHook = Callable[[str, dict[str, Any]], None]


class UIConfirmBus:
    """Single source of truth for SSE security confirm events."""

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, str] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        # C13 §15.5: dedup_followers tracks how many follower waiters are
        # parked on a leader's confirm_id (e.g., delegate_parallel siblings
        # racing on the same write_file). Used by cleanup() to defer real
        # removal until followers also resolve, preventing the race where
        # the leader's cleanup empties _decisions before followers read it.
        self._dedup_followers: dict[str, int] = {}
        # confirm_ids that the leader has already requested cleanup on, but
        # had to defer because followers were still waiting. When the last
        # follower deregisters, ``deregister_follower`` flushes these.
        self._pending_cleanup: set[str] = set()
        self._ttl_seconds = ttl_seconds
        # C17 Phase B.4：broadcast hook（optional）。
        self._broadcast_hook: ConfirmEventHook | None = None
        # Backend-owned presentation queue. ``_session_order`` contains all
        # pending confirm ids for a presentation session in FIFO order;
        # ``_session_active`` points at the id currently allowed to display as
        # a modal. Empty ``session_id`` values use a per-confirm presentation
        # scope so unrelated no-session callers do not block each other.
        self._session_order: dict[str, list[str]] = {}
        self._session_active: dict[str, str] = {}
        # Last resolve-side UI update, consumed by HTTP endpoints to return
        # ``next_confirm`` without changing apply_resolution's bool contract.
        self._last_resolution_ui: dict[str, dict[str, Any]] = {}

    # ----- C17 Phase B.4: broadcast hook plumbing --------------------------

    def set_broadcast_hook(self, hook: ConfirmEventHook | None) -> None:
        """Late-binding hook called on confirm_initiated / confirm_revoked.

        Wired by api/server.py to the WebSocket fire_event helper so other
        connected clients (web UI on a second device, IM gateway) see
        live confirm lifecycle and can render readonly badges / cancel
        their own pending UI cards. Idempotent.
        """
        self._broadcast_hook = hook

    def _broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        hook = self._broadcast_hook
        if hook is None:
            return
        try:
            hook(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[UIConfirmBus] broadcast hook raised for %s; payload keys=%s",
                event_type,
                list(payload.keys()),
                exc_info=True,
            )

    # ----- Backend-owned presentation queue --------------------------------

    @staticmethod
    def _presentation_session_id(confirm_id: str, session_id: str) -> str:
        """Return the queue scope for a confirm.

        Real conversation ids queue together. Empty session ids are isolated
        per confirm to preserve previous CLI/tests that did not pass a session.
        """
        return session_id or f"__confirm__:{confirm_id}"

    def _ensure_presentation_session(self, presentation_session_id: str) -> str | None:
        """Prune stale ids and return the current active confirm id."""
        if not presentation_session_id:
            return None
        order = [
            cid
            for cid in self._session_order.get(presentation_session_id, [])
            if cid in self._pending
            and self._pending[cid].get("presentation_session_id") == presentation_session_id
        ]
        if not order:
            self._session_order.pop(presentation_session_id, None)
            self._session_active.pop(presentation_session_id, None)
            return None

        active_id = self._session_active.get(presentation_session_id)
        if active_id not in order:
            active_id = order[0]
            self._session_active[presentation_session_id] = active_id
        self._session_order[presentation_session_id] = order
        return active_id

    def _presentation_for(self, confirm_id: str) -> dict[str, Any]:
        pending = self._pending.get(confirm_id)
        if pending is None:
            return {
                "presentation_state": "resolved",
                "ui_state": "resolved",
                "queue_position": None,
                "active_confirm_id": None,
                "queued_count": 0,
                "pending_count": 0,
            }

        presentation_session_id = str(pending.get("presentation_session_id") or "")
        active_id = self._ensure_presentation_session(presentation_session_id)
        order = self._session_order.get(presentation_session_id, [])
        queued_count = max(0, len(order) - (1 if active_id else 0))
        if active_id == confirm_id:
            state = "active"
            queue_position: int | None = 0
        else:
            state = "queued"
            queued_ids = [cid for cid in order if cid != active_id]
            queue_position = queued_ids.index(confirm_id) + 1 if confirm_id in queued_ids else None
        return {
            "presentation_state": state,
            "ui_state": state,
            "queue_position": queue_position,
            "active_confirm_id": active_id,
            "queued_count": queued_count,
            "pending_count": len(order),
        }

    def _presentation_status_for_session(self, presentation_session_id: str) -> dict[str, Any]:
        active_id = self._ensure_presentation_session(presentation_session_id)
        order = self._session_order.get(presentation_session_id, [])
        return {
            "active_confirm_id": active_id,
            "queued_count": max(0, len(order) - (1 if active_id else 0)),
            "pending_count": len(order),
        }

    def _confirm_event_with_presentation(self, confirm_id: str) -> dict[str, Any] | None:
        pending = self._pending.get(confirm_id)
        if pending is None:
            return None
        raw_event = pending.get("confirm_event")
        if not isinstance(raw_event, dict):
            return None
        event = dict(raw_event)
        event.setdefault("id", confirm_id)
        event.setdefault("confirm_id", confirm_id)
        event.update(self._presentation_for(confirm_id))
        return event

    def _remove_from_presentation(
        self,
        confirm_id: str,
        pending: dict[str, Any] | None,
    ) -> dict[str, Any]:
        presentation_session_id = ""
        session_id = ""
        tool_name = ""
        if pending is not None:
            presentation_session_id = str(pending.get("presentation_session_id") or "")
            session_id = str(pending.get("session_id") or "")
            tool_name = str(pending.get("tool_name") or "")
        if not presentation_session_id:
            for psid, order in self._session_order.items():
                if confirm_id in order:
                    presentation_session_id = psid
                    break
        if not presentation_session_id:
            return {
                "session_id": session_id,
                "tool_name": tool_name,
                "was_active": False,
                "promoted_id": None,
                "status": {
                    "active_confirm_id": None,
                    "queued_count": 0,
                    "pending_count": 0,
                },
            }

        old_active = self._session_active.get(presentation_session_id)
        order = [
            cid
            for cid in self._session_order.get(presentation_session_id, [])
            if cid != confirm_id and cid in self._pending
        ]
        was_active = old_active == confirm_id
        promoted_id: str | None = None
        if not order:
            self._session_order.pop(presentation_session_id, None)
            self._session_active.pop(presentation_session_id, None)
        elif was_active:
            promoted_id = order[0]
            self._session_order[presentation_session_id] = order
            self._session_active[presentation_session_id] = promoted_id
        else:
            self._session_order[presentation_session_id] = order
            if old_active in order:
                self._session_active[presentation_session_id] = old_active or order[0]
            else:
                self._session_active[presentation_session_id] = order[0]

        status = self._presentation_status_for_session(presentation_session_id)
        return {
            "session_id": session_id,
            "tool_name": tool_name,
            "was_active": was_active,
            "promoted_id": promoted_id,
            "status": status,
        }

    def consume_resolution_ui_update(self, confirm_id: str) -> dict[str, Any] | None:
        """Return and clear the UI update produced by the latest resolve."""
        if not confirm_id:
            return None
        return self._last_resolution_ui.pop(confirm_id, None)

    def _remember_resolution_ui_update(
        self,
        confirm_id: str,
        update: dict[str, Any],
    ) -> None:
        if not confirm_id:
            return
        self._last_resolution_ui[confirm_id] = update
        if len(self._last_resolution_ui) <= 512:
            return
        oldest_id = next(iter(self._last_resolution_ui), "")
        if oldest_id:
            self._last_resolution_ui.pop(oldest_id, None)

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
        dedup_key: str | None = None,
        confirm_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register the sidecar payload for a confirm_id about to be SSE'd.

        Must be called by the SSE producer **before** the event is yielded,
        so the resolver (gateway IM card click / web modal POST) can pop
        it back out.

        ``dedup_key`` (C13 §15.5) is an optional fingerprint used to
        coalesce identical confirms from ``delegate_parallel`` siblings:
        when two sub-agents race to ``store_pending`` for the same
        (tool_name, normalized_params, session), the second caller can
        instead join the first as a "follower" via ``find_dedup_leader``
        and skip its own SSE emission.
        """
        self._cleanup_expired()
        session_id = session_id or ""
        presentation_session_id = self._presentation_session_id(tool_id, session_id)
        previous = self._pending.get(tool_id)
        if previous is not None:
            self._remove_from_presentation(tool_id, previous)
        self._pending[tool_id] = {
            "tool_name": tool_name,
            "params": params,
            "created_at": time.time(),
            "session_id": session_id,
            "needs_sandbox": needs_sandbox,
            "dedup_key": dedup_key,
            "presentation_session_id": presentation_session_id,
        }
        if confirm_event is not None:
            self._pending[tool_id]["confirm_event"] = dict(confirm_event)

        order = self._session_order.setdefault(presentation_session_id, [])
        if tool_id not in order:
            order.append(tool_id)
        self._ensure_presentation_session(presentation_session_id)
        presentation = self._presentation_for(tool_id)

        # C17 Phase B.4：广播 confirm_initiated 给所有 WS 订阅者。借鉴
        # claude-code ``sendControlCancelRequest`` 的 "request lifecycle
        # broadcast"——多端 UI 看到同一个 confirm_id 时，第二端渲染
        # readonly "另一端正在确认"，避免双弹窗。Payload 故意不包含
        # ``params`` 全文（防隐私泄露到看板上）；前端只用 tool_name +
        # session_id 做幂等渲染。
        self._broadcast(
            "confirm_initiated",
            {
                "confirm_id": tool_id,
                "tool_name": tool_name,
                "session_id": session_id,
                "needs_sandbox": needs_sandbox,
                "created_at": self._pending[tool_id]["created_at"],
                **presentation,
            },
        )
        return presentation

    # C13 §15.5: dedup helpers ----------------------------------------------

    def find_dedup_leader(self, *, session_id: str, dedup_key: str) -> str | None:
        """Return existing pending confirm_id with matching dedup_key.

        Used by ``delegate_parallel`` siblings: if a leader sub-agent already
        emitted a CONFIRM SSE for the same (session, tool, normalized params),
        the follower sub-agent should attach to the leader's confirm_id
        instead of emitting a duplicate card.

        Returns None when no matching active leader exists; the caller then
        proceeds with the normal store_pending + emit path.
        """
        if not dedup_key:
            return None
        for cid, p in self._pending.items():
            if p.get("dedup_key") == dedup_key and p.get("session_id") == session_id:
                return cid
        return None

    def register_follower(self, leader_id: str) -> None:
        """Increment follower waiter count for ``leader_id``.

        Must be called before the follower awaits ``wait_for_resolution``;
        paired with ``deregister_follower`` after the wait returns. Together
        they make ``cleanup`` defer real removal until all followers have
        also read the decision (avoids the wake-then-cleanup-then-read race).
        """
        if not leader_id:
            return
        self._dedup_followers[leader_id] = self._dedup_followers.get(leader_id, 0) + 1

    def deregister_follower(self, leader_id: str) -> None:
        """Decrement follower count; flush deferred cleanup when count hits 0."""
        if not leader_id:
            return
        n = self._dedup_followers.get(leader_id, 0) - 1
        if n > 0:
            self._dedup_followers[leader_id] = n
            return
        self._dedup_followers.pop(leader_id, None)
        # If the leader already requested cleanup while followers were
        # still parked, the actual pop was deferred. Flush it now.
        if leader_id in self._pending_cleanup:
            self._pending_cleanup.discard(leader_id)
            self._events.pop(leader_id, None)
            self._decisions.pop(leader_id, None)

    def follower_count(self, leader_id: str) -> int:
        """Diagnostic accessor for tests / debug panel."""
        return self._dedup_followers.get(leader_id, 0)

    def list_pending(self) -> list[dict[str, Any]]:
        """Diagnostic accessor (SecurityView debug panel / tests)."""
        return [{"id": k, **v} for k, v in self._pending.items()]

    def active_confirms_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """C17 Phase B.4: list non-expired pending confirms for a session.

        Used by ``GET /api/sessions/{sid}/active_confirms`` so a second
        client (e.g. user opens the web UI on phone after the desktop
        already showed a card) can fetch the current pending confirms
        and render their readonly state without waiting for the next
        ``confirm_initiated`` event. Strips ``params`` payload — only
        ``tool_name`` + identity + sandbox bit leak out.
        """
        self._cleanup_expired()
        out: list[dict[str, Any]] = []
        for cid, p in self._pending.items():
            if p.get("session_id") != session_id:
                continue
            out.append(
                {
                    "confirm_id": cid,
                    "tool_name": p.get("tool_name"),
                    "needs_sandbox": p.get("needs_sandbox", False),
                    "created_at": p.get("created_at"),
                    **self._presentation_for(cid),
                }
            )
        return out

    def cleanup_session(self, session_id: str) -> None:
        """Drop all pending confirms tied to the given session.

        C13 §15.5 follow-up: also purge the associated ``_events``,
        ``_decisions``, ``_dedup_followers`` and ``_pending_cleanup``
        entries so a session teardown doesn't leak dedup waiter state.
        Otherwise a long-lived process that frequently spins up + tears
        down sessions would accumulate orphan follower counters in
        ``_dedup_followers`` (each ``register_follower`` without a paired
        ``deregister_follower`` after the session is gone).
        """
        to_remove = [k for k, v in self._pending.items() if v.get("session_id") == session_id]
        for k in to_remove:
            pending = self._pending.get(k)
            self._pending.pop(k, None)
            self._remove_from_presentation(k, pending)
            self._events.pop(k, None)
            self._decisions.pop(k, None)
            self._dedup_followers.pop(k, None)
            self._pending_cleanup.discard(k)

    def _cleanup_expired(self) -> None:
        """Garbage-collect pending confirms older than ``_ttl_seconds``."""
        now = time.time()
        expired = [
            k for k, v in self._pending.items() if now - v.get("created_at", 0) > self._ttl_seconds
        ]
        for k in expired:
            pending = self._pending.pop(k, None)
            presentation = self._remove_from_presentation(k, pending)
            status = presentation.get("status") or {}
            self._broadcast(
                "confirm_revoked",
                {
                    "confirm_id": k,
                    "decision": "timeout",
                    "tool_name": (pending or {}).get("tool_name"),
                    "session_id": (pending or {}).get("session_id"),
                    "active_confirm_id": status.get("active_confirm_id"),
                    "queued_count": status.get("queued_count", 0),
                    "pending_count": status.get("pending_count", 0),
                },
            )
            promoted_id = presentation.get("promoted_id")
            if isinstance(promoted_id, str) and promoted_id:
                promoted_event = self._confirm_event_with_presentation(promoted_id)
                if promoted_event is not None:
                    self._broadcast(
                        "security_confirm_promoted",
                        {
                            "confirm_id": promoted_id,
                            "session_id": promoted_event.get("conversation_id")
                            or (self._pending.get(promoted_id) or {}).get("session_id"),
                            "confirm": promoted_event,
                        },
                    )

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
        """Drop both the event and the resolved decision for an id.

        C13 §15.5: defer real removal when followers are still parked on
        this leader's event/decision. Without this, leader's caller
        immediately popping _decisions after wait_for_resolution returns
        would race with followers whose ``wait`` also woke but hasn't yet
        read _decisions — they'd see an empty dict and fall back to "deny".
        ``deregister_follower`` flushes the deferred cleanup when the last
        follower returns.
        """
        if not confirm_id:
            return
        if self._dedup_followers.get(confirm_id, 0) > 0:
            self._pending_cleanup.add(confirm_id)
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

        C17 Phase B.4：成功 pop / 第一次 wake 时广播 ``confirm_revoked``，
        让多端 UI 同步消失 / 隐藏对应卡片。重复调用是 no-op（pending 已
        None + decision 已设），不会广播两次。
        """
        pending = self._pending.pop(confirm_id, None)
        presentation = self._remove_from_presentation(confirm_id, pending)
        status = presentation.get("status") or {}
        promoted_id = presentation.get("promoted_id")
        promoted_event = (
            self._confirm_event_with_presentation(promoted_id)
            if isinstance(promoted_id, str) and promoted_id
            else None
        )
        self._remember_resolution_ui_update(
            confirm_id,
            {
                "active_confirm_id": status.get("active_confirm_id"),
                "queued_count": status.get("queued_count", 0),
                "pending_count": status.get("pending_count", 0),
                "next_confirm": promoted_event,
            },
        )

        decision = require_security_confirm_decision(decision)

        first_resolution = False
        # Ensure waiter wakes regardless of whether a pending sidecar
        # existed (gateway / API may resolve a confirm whose pending
        # was already GC'd, but a wait_for_resolution coroutine could
        # still be parked on the event).
        if confirm_id in self._events and confirm_id not in self._decisions:
            self._decisions[confirm_id] = decision
            ev = self._events.get(confirm_id)
            if ev is not None:
                ev.set()
            first_resolution = True

        if first_resolution or pending is not None:
            # 至少有一次状态推进（pending pop 或 waiter wake）。
            self._broadcast(
                "confirm_revoked",
                {
                    "confirm_id": confirm_id,
                    "decision": decision,
                    "tool_name": (pending or {}).get("tool_name"),
                    "session_id": (pending or {}).get("session_id"),
                    "active_confirm_id": status.get("active_confirm_id"),
                    "queued_count": status.get("queued_count", 0),
                    "pending_count": status.get("pending_count", 0),
                    "next_confirm_id": promoted_id,
                },
            )
            if promoted_event is not None:
                self._broadcast(
                    "security_confirm_promoted",
                    {
                        "confirm_id": promoted_id,
                        "session_id": promoted_event.get("conversation_id")
                        or (self._pending.get(promoted_id) or {}).get("session_id"),
                        "confirm": promoted_event,
                    },
                )

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

    # ----- C18 Phase B: batch resolve --------------------------------------

    def list_batch_candidates(
        self,
        session_id: str,
        *,
        within_seconds: float | None = None,
    ) -> list[str]:
        """Return confirm_ids in ``session_id`` eligible for batch resolution.

        When ``within_seconds`` is provided, restricts to confirms whose
        ``created_at`` is within ``within_seconds`` of *the youngest* pending
        confirm in the session — i.e. a sliding 5-second window anchored at
        the most recent emission. This matches the UX promise: "user clicks
        'Approve all' on a card that just appeared; we batch everything
        that arrived alongside it, not random old ones".

        Returns an empty list when the session has no pending confirms.
        """
        self._cleanup_expired()
        in_session = [
            (cid, p.get("created_at", 0.0))
            for cid, p in self._pending.items()
            if p.get("session_id") == session_id
        ]
        if not in_session:
            return []
        if within_seconds is None or within_seconds <= 0:
            return [cid for cid, _ in in_session]
        # Anchor on the most recent emission so the window covers
        # confirms that arrived ~together. This is what "5s aggregation"
        # means in UX terms — the user reacts to the latest popup.
        latest_ts = max(ts for _, ts in in_session)
        return [cid for cid, ts in in_session if (latest_ts - ts) <= within_seconds]

    def batch_resolve(
        self,
        session_id: str,
        decision: str,
        *,
        within_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve all pending confirms for a session with one decision.

        Returns the list of resolved sidecars (one per actually-resolved
        confirm_id). The caller is responsible for threading each entry
        through ``apply_resolution`` if allowlist side-effects are desired
        — keeping the bus side-effect-free preserves the C8b-3 invariant.

        Idempotent on each confirm_id: a previously-resolved id is a no-op.
        """
        ids = self.list_batch_candidates(session_id, within_seconds=within_seconds)
        results: list[dict[str, Any]] = []
        for cid in ids:
            resolved = self.resolve(cid, decision)
            if resolved is not None:
                results.append({"confirm_id": cid, **resolved})
        return results

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
