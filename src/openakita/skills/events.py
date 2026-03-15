"""Cross-layer skill change notification.

Decouples the tools layer from the API layer using the Observer pattern.

- The API layer registers its cache-invalidation / WS-broadcast callbacks
  via ``register_on_change`` at import time.
- The tools layer (or any other layer) calls ``notify_skills_changed``
  after mutating the skill set; all registered callbacks fire in order.

Both layers depend on this module (``skills/events``), not on each other,
keeping the dependency DAG acyclic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_on_change_callbacks: list[Callable[[str], None]] = []


def register_on_change(callback: Callable[[str], None]) -> None:
    """Register a callback invoked when the skill set changes.

    Args:
        callback: Receives an *action* string such as
                  ``"load"``, ``"reload"``, ``"install"``, ``"enable"``.
    """
    if callback not in _on_change_callbacks:
        _on_change_callbacks.append(callback)


def notify_skills_changed(action: str = "reload") -> None:
    """Fire all registered callbacks to signal a skill-set mutation."""
    for cb in _on_change_callbacks:
        try:
            cb(action)
        except Exception:
            logger.debug("skills change callback failed", exc_info=True)
