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
from enum import Enum

logger = logging.getLogger(__name__)


class SkillEvent(str, Enum):
    """Skill lifecycle event types."""
    LOAD = "load"
    RELOAD = "reload"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    ENABLE = "enable"
    DISABLE = "disable"
    FAILED_LOAD = "failed_load"
    HOT_RELOAD = "hot_reload"
    STORE_INSTALL = "store_install"
    PLUGIN_LOAD = "plugin_load"
    CONTENT_UPDATE = "content_update"


_on_change_callbacks: list[Callable[[str], None]] = []


def register_on_change(callback: Callable[[str], None]) -> None:
    """Register a callback invoked when the skill set changes.

    Args:
        callback: Receives an *action* string (a ``SkillEvent`` value) such as
                  ``"load"``, ``"reload"``, ``"install"``, ``"enable"``.
    """
    if callback not in _on_change_callbacks:
        _on_change_callbacks.append(callback)


def unregister_on_change(callback: Callable[[str], None]) -> bool:
    """Remove a previously registered on-change callback.

    Returns:
        True if the callback was found and removed, False otherwise.
    """
    try:
        _on_change_callbacks.remove(callback)
        return True
    except ValueError:
        return False


def notify_skills_changed(action: str | SkillEvent = SkillEvent.RELOAD) -> int:
    """Fire all registered callbacks to signal a skill-set mutation.

    Returns:
        Number of callbacks that failed (0 = all succeeded).
    """
    action_str = action.value if isinstance(action, SkillEvent) else action
    failures = 0
    for cb in _on_change_callbacks:
        try:
            cb(action_str)
        except Exception:
            failures += 1
            logger.warning("skills change callback failed for action=%s", action_str, exc_info=True)
    return failures
