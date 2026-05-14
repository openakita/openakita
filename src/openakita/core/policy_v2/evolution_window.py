"""C15 §17.1 — Evolution self-fix audit window (R4-9).

Motivation
==========

``evolution.self_check._attempt_fix`` spawns a fresh agent and lets it
run mostly-arbitrary tool calls under a strict prompt. Two problems:

1. Pre-C14, that agent inherited the *parent's* PolicyContext —
   which for most channels means ``is_unattended=False``, so any
   CONFIRM-class tool would hang waiting for a UI response that
   never came (C14 follow-up gap).
2. The fix agent's tool calls were not differentiable from the
   parent agent's tool calls in the audit log, so operators had no
   way to retrospectively answer "what did Evolution try to change
   last night?".

This module solves (2): it carries a stable ``fix_id`` through the
PolicyContext for the duration of the fix attempt, and exposes a
hook the engine uses to append every decision made during the
window to ``data/audit/evolution_decisions.jsonl``.

(1) is solved by the caller wiring
``classify_entry("evolution", force_unattended=True)`` →
``build_policy_context(...)`` → ``set_current_context`` around the
``agent.chat`` call — see ``evolution.self_check._attempt_fix``.

What this module does NOT do
============================

We deliberately do **not** relax ``safety_immune`` during the
window. The plan (§17.1) mentions a "time-window exception" but
implementing it without a careful threat model is risky:

- Evolution writing to ``identity/runtime/**`` could rewrite the
  agent's own personality permissions.
- Evolution writing to ``data/audit/**`` could rewrite history.

Phase C v1 keeps the existing safety_immune DENY behavior for those
paths. The audit trail this module produces is the foundation for a
future commit that, with operator opt-in, can add **explicit, narrow**
exemption rules (e.g. "allow rewriting ``identity/runtime/cache/``
during an active evolution window only").

State model
===========

Two pieces of state are tracked:

- A process-wide ``contextvars.ContextVar`` of the *active* fix id,
  set by ``self_check`` via :func:`set_active_fix_id` /
  :func:`reset_active_fix_id` (the same wrapper pattern as
  :func:`policy_v2.set_current_context`). This lets nested helpers
  inside the fix agent read the current id without explicit
  threading.

- A short-lived in-memory window dict (``_WINDOWS``) keyed by
  fix_id with the open/close timestamps and reason. Used purely for
  audit serialisation (deadline checks, duration measurement); it
  is **not** consulted by the policy decision path.

The window is bounded by an absolute deadline (default 600s) so a
crashed ``_attempt_fix`` cannot leave a stale window open and lure
later decisions into evolution-marked audit records.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Window dataclass
# ---------------------------------------------------------------------------


# Default 10 minutes. Matches the typical self_check fix timeout (5min)
# with a 2x slack for cleanup work. Operators / tests can override per
# call to :func:`open_window`.
DEFAULT_WINDOW_TTL_SECONDS: float = 600.0


@dataclass(frozen=True)
class EvolutionWindow:
    """An open audit window for one Evolution self-fix attempt."""

    fix_id: str
    reason: str
    started_at: float
    deadline_at: float
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.time() >= self.deadline_at

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - time.time())


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------


_active_fix_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openakita_evolution_active_fix_id", default=None
)

_WINDOWS_LOCK = threading.RLock()
_WINDOWS: dict[str, EvolutionWindow] = {}


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------


def open_window(
    *,
    reason: str,
    ttl_seconds: float = DEFAULT_WINDOW_TTL_SECONDS,
    extra: dict[str, Any] | None = None,
    fix_id: str | None = None,
) -> EvolutionWindow:
    """Open a new evolution audit window.

    Args:
        reason: Short label (``"self-fix"`` / ``"manual-evolution"`` /
            ``"scheduled-check"``) — logged for forensics.
        ttl_seconds: Maximum lifetime. After this many seconds the
            window is considered ``expired`` even if
            :func:`close_window` was never called (defends against
            stale state after crashes).
        extra: Optional free-form metadata (e.g. ``{"error_id": ...,
            "module": ...}``) attached to every audit record.
        fix_id: Pre-allocated identifier; defaults to a new UUID
            hex shortened to 16 chars.

    Returns:
        The :class:`EvolutionWindow` describing the open state.
    """
    if not fix_id:
        fix_id = uuid.uuid4().hex[:16]
    win = EvolutionWindow(
        fix_id=fix_id,
        reason=reason,
        started_at=time.time(),
        deadline_at=time.time() + max(0.001, float(ttl_seconds)),
        extra=dict(extra or {}),
    )
    with _WINDOWS_LOCK:
        _WINDOWS[fix_id] = win
    logger.info(
        "[C15 evolution_window] opened fix_id=%s reason=%s ttl=%.0fs",
        fix_id,
        reason,
        ttl_seconds,
    )
    return win


def close_window(fix_id: str) -> EvolutionWindow | None:
    """Remove a window from the active set, returning it for final
    audit recording. Returns ``None`` when ``fix_id`` is unknown
    (idempotent: closing twice is safe)."""
    with _WINDOWS_LOCK:
        win = _WINDOWS.pop(fix_id, None)
    if win is None:
        logger.debug(
            "[C15 evolution_window] close called for unknown fix_id=%s",
            fix_id,
        )
        return None
    logger.info(
        "[C15 evolution_window] closed fix_id=%s duration=%.2fs",
        fix_id,
        time.time() - win.started_at,
    )
    return win


def get_window(fix_id: str) -> EvolutionWindow | None:
    """Look up a still-open (and not-expired) window. Returns ``None``
    for missing or expired windows. Expired windows are also evicted
    from the tracker so callers don't leak references."""
    with _WINDOWS_LOCK:
        win = _WINDOWS.get(fix_id)
        if win is None:
            return None
        if win.expired:
            _WINDOWS.pop(fix_id, None)
            logger.warning(
                "[C15 evolution_window] fix_id=%s expired (deadline %.0fs "
                "ago) — evicting; audit records from after expiry will "
                "be tagged but the window state is gone",
                fix_id,
                time.time() - win.deadline_at,
            )
            return None
    return win


def active_windows() -> dict[str, EvolutionWindow]:
    """Snapshot of all open + non-expired windows. Used by debug
    endpoints (setup-center 'Active Evolution Windows' panel)."""
    with _WINDOWS_LOCK:
        out = {}
        expired = []
        for fid, win in _WINDOWS.items():
            if win.expired:
                expired.append(fid)
                continue
            out[fid] = win
        for fid in expired:
            _WINDOWS.pop(fid, None)
    return out


def reset_windows() -> None:
    """Test helper — wipe all in-memory state."""
    with _WINDOWS_LOCK:
        _WINDOWS.clear()


# ---------------------------------------------------------------------------
# ContextVar wrapper (mirrors policy_v2.context get/set/reset pattern)
# ---------------------------------------------------------------------------


def get_active_fix_id() -> str | None:
    """Current contextvar value (None outside an active window)."""
    return _active_fix_id_var.get()


def set_active_fix_id(fix_id: str | None) -> contextvars.Token:
    """Install ``fix_id`` as the active id for this task / thread.

    Returns the token from :pymeth:`ContextVar.set` — caller MUST
    pass it back to :func:`reset_active_fix_id` in a ``finally`` block
    to avoid leaking the marker into the parent context.
    """
    return _active_fix_id_var.set(fix_id)


def reset_active_fix_id(token: contextvars.Token) -> None:
    """Reset the contextvar using ``token`` from :func:`set_active_fix_id`."""
    try:
        _active_fix_id_var.reset(token)
    except (ValueError, LookupError) as exc:
        logger.warning(
            "[C15 evolution_window] reset_active_fix_id ignored "
            "(token from different context?): %s",
            exc,
        )


# ---------------------------------------------------------------------------
# Audit append
# ---------------------------------------------------------------------------


def record_decision(
    *,
    fix_id: str,
    audit_path: Path,
    decision_record: dict[str, Any],
) -> None:
    """Append one policy decision to ``evolution_decisions.jsonl``.

    Args:
        fix_id: The currently-active fix identifier (from
            :func:`get_active_fix_id` or
            :pyattr:`PolicyContext.evolution_fix_id`).
        audit_path: Destination JSONL path. Caller chooses (engine
            passes ``workspace / "data" / "audit" /
            "evolution_decisions.jsonl"``).
        decision_record: Caller-shaped dict. We add ``fix_id``,
            ``window_reason``, and ``ts``; everything else is
            passed through verbatim. Sanitize sensitive fields
            before passing.

    Failures are logged at WARNING but never raised — same as
    :mod:`system_tasks` audit append: losing an audit line is
    preferable to crashing the decision path.
    """
    record = dict(decision_record)
    record.setdefault("ts", time.time())
    record["fix_id"] = fix_id
    win = get_window(fix_id)
    if win is not None:
        record.setdefault("window_reason", win.reason)
        if win.extra:
            record.setdefault("window_extra", dict(win.extra))
    try:
        from .audit_chain import get_writer

        get_writer(audit_path).append(record)
    except OSError as exc:
        logger.warning(
            "[C15 evolution_window] failed to append evolution audit "
            "%s for fix_id=%s: %s",
            audit_path,
            fix_id,
            exc,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort audit append
        logger.warning(
            "[C16 evolution_window] chain append failed for %s fix_id=%s: %s; "
            "falling back to raw append.",
            audit_path,
            fix_id,
            exc,
        )
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as fallback_exc:
            logger.warning(
                "[C15 evolution_window] fallback raw append also failed for "
                "%s fix_id=%s: %s",
                audit_path,
                fix_id,
                fallback_exc,
            )


def default_audit_path(workspace: Path) -> Path:
    """Canonical location for the evolution decisions jsonl."""
    return workspace / "data" / "audit" / "evolution_decisions.jsonl"


# ---------------------------------------------------------------------------
# Convenience snapshot for setup-center / tests
# ---------------------------------------------------------------------------


def snapshot_window(win: EvolutionWindow) -> dict[str, Any]:
    """Return a JSON-safe dict for one window — used by the
    introspection helpers and tests."""
    return {
        **asdict(win),
        "expired": win.expired,
        "remaining_seconds": win.remaining_seconds(),
    }


__all__ = [
    "DEFAULT_WINDOW_TTL_SECONDS",
    "EvolutionWindow",
    "active_windows",
    "close_window",
    "default_audit_path",
    "get_active_fix_id",
    "get_window",
    "open_window",
    "record_decision",
    "reset_active_fix_id",
    "reset_windows",
    "set_active_fix_id",
    "snapshot_window",
]
