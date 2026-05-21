"""Module-level singleton for tracking degraded storage subsystems.

Why a module-level singleton instead of ``app.state``?

- ``token_tracking._writer_loop`` runs in a daemon thread that has **no**
  FastAPI ``Request`` context, so it cannot reach ``app.state``.
- ``asset_bus.init()`` runs in lifespan startup, sometimes *before*
  ``app.state.*`` attributes are populated.
- ``feedback_store`` is invoked from HTTP handlers and already has access
  to ``app.state``, but routing all three of them through one place is
  simpler than three separate code paths.

The registry is intentionally tiny: ``register`` is idempotent (first
caller wins), ``unregister`` is no-op on missing keys, and ``snapshot``
returns a defensive copy. ``threading.RLock`` is used because callers
might recursively register inside a handler that itself was called from
``register`` (rare, but possible during shutdown sequencing).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

__all__ = ["DegradedRegistry", "registry"]


class DegradedRegistry:
    """Thread-safe set of degraded subsystems.

    Each entry is keyed by ``subsystem`` (e.g. ``"token_tracking"``,
    ``"feedback"``, ``"asset_bus"``, ``"memory"``). The first registration
    wins; subsequent calls for the same subsystem are silently ignored,
    so logging the reason during initial failure is what counts.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}

    def register(
        self,
        subsystem: str,
        reason: str,
        *,
        repair: str | None = None,
        details: str | None = None,
    ) -> None:
        """Mark a subsystem as degraded. First caller wins."""
        if not subsystem:
            return
        with self._lock:
            if subsystem in self._items:
                return
            entry: dict = {
                "subsystem": subsystem,
                "reason": reason or "unknown",
                "since": datetime.now(UTC).isoformat(timespec="seconds"),
                "repair_action": repair or "manual_quarantine",
            }
            if details:
                entry["details"] = details
            self._items[subsystem] = entry

    def unregister(self, subsystem: str) -> None:
        """Clear a previously-registered degraded entry (no-op if absent)."""
        with self._lock:
            self._items.pop(subsystem, None)

    def is_degraded(self, subsystem: str) -> bool:
        with self._lock:
            return subsystem in self._items

    def snapshot(self) -> list[dict]:
        """Return a defensive copy of the current degraded entries."""
        with self._lock:
            return [dict(entry) for entry in self._items.values()]

    def clear(self) -> None:
        """Clear all entries (test-only helper)."""
        with self._lock:
            self._items.clear()


registry = DegradedRegistry()
