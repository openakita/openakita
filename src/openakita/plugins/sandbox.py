"""Plugin sandbox — timeout wrappers, exception capture, fallback strategies."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_CONSECUTIVE_ERRORS = 10
ERROR_WINDOW = 300  # 5 minutes

# Per-kind error weights. Timeouts are the strongest hang signal so they
# count triple; permission_denied is not a plugin bug and is ignored.
ERROR_WEIGHTS: dict[str, int] = {
    "timeout": 3,
    "exception": 1,
    "permission_denied": 0,
}


class PluginErrorTracker:
    """Track per-plugin errors and decide when to auto-disable."""

    def __init__(self) -> None:
        self._errors: dict[str, list[dict]] = defaultdict(list)
        self._last_success: dict[str, float] = {}
        self._disabled: set[str] = set()
        self._on_auto_disable: Callable[[str], None] | None = None

    def set_auto_disable_callback(self, callback: Callable[[str], None]) -> None:
        """Set a callback invoked when a plugin is auto-disabled (for tool cleanup)."""
        self._on_auto_disable = callback

    def record_success(self, plugin_id: str) -> None:
        """Stamp the last successful execution time for a plugin."""
        if not plugin_id:
            return
        self._last_success[plugin_id] = time.time()

    def record_error(
        self,
        plugin_id: str,
        context: str,
        error: str,
        *,
        kind: str = "exception",
    ) -> bool:
        """Record an error. Returns True if plugin should be auto-disabled.

        Errors are weighted by ``kind`` (see :data:`ERROR_WEIGHTS`):
        timeouts count triple, generic exceptions count once, and
        ``permission_denied`` is treated as non-fault and ignored.
        Auto-disable triggers when the **weighted** sum of recent errors
        within :data:`ERROR_WINDOW` reaches :data:`MAX_CONSECUTIVE_ERRORS`.
        """
        weight = ERROR_WEIGHTS.get(kind, 1)
        if weight <= 0:
            return False

        entry = {
            "time": time.time(),
            "context": context,
            "error": error,
            "kind": kind,
            "weight": weight,
        }
        self._errors[plugin_id].append(entry)

        cutoff = time.time() - ERROR_WINDOW
        recent = [e for e in self._errors[plugin_id] if e["time"] > cutoff]
        self._errors[plugin_id] = recent

        weighted = sum(int(e.get("weight", 1)) for e in recent)
        if weighted >= MAX_CONSECUTIVE_ERRORS:
            self._disabled.add(plugin_id)
            if self._on_auto_disable:
                try:
                    self._on_auto_disable(plugin_id)
                except Exception as e:
                    logger.warning(
                        "Auto-disable callback failed for plugin '%s': %s",
                        plugin_id,
                        e,
                    )
            return True
        return False

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self._disabled

    def reset(self, plugin_id: str) -> None:
        self._errors.pop(plugin_id, None)
        self._last_success.pop(plugin_id, None)
        self._disabled.discard(plugin_id)

    def get_errors(self, plugin_id: str) -> list[dict]:
        return list(self._errors.get(plugin_id, []))

    def health_snapshot(self, plugin_id: str) -> dict[str, Any]:
        """Return a compact health summary suitable for the management API.

        Fields:
            weighted_errors: weighted error sum within the active window
            timeout_count:   raw count of timeout entries within the window
            exception_count: raw count of exception entries within the window
            last_success_at: epoch seconds of the last recorded success, or None
            is_disabled:     whether the plugin has been auto-disabled
        """
        cutoff = time.time() - ERROR_WINDOW
        recent = [
            e for e in self._errors.get(plugin_id, []) if e["time"] > cutoff
        ]
        return {
            "weighted_errors": sum(int(e.get("weight", 1)) for e in recent),
            "timeout_count": sum(1 for e in recent if e.get("kind") == "timeout"),
            "exception_count": sum(
                1 for e in recent if e.get("kind") == "exception"
            ),
            "last_success_at": self._last_success.get(plugin_id),
            "is_disabled": plugin_id in self._disabled,
        }


async def safe_call(
    coro,
    *,
    timeout: float = 5.0,
    default: Any = None,
    plugin_id: str = "",
    context: str = "",
    error_tracker: PluginErrorTracker | None = None,
) -> Any:
    """Execute an async callable with timeout and exception isolation.

    Returns ``default`` on timeout or exception, never raises.
    """
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "Plugin '%s' %s timed out (%.1fs), skipped",
            plugin_id,
            context,
            timeout,
        )
        if error_tracker:
            error_tracker.record_error(
                plugin_id, context, "timeout", kind="timeout"
            )
        return default
    except Exception as e:
        logger.error(
            "Plugin '%s' %s raised %s: %s",
            plugin_id,
            context,
            type(e).__name__,
            e,
        )
        if error_tracker:
            error_tracker.record_error(plugin_id, context, str(e))
        return default
    else:
        if error_tracker and plugin_id:
            error_tracker.record_success(plugin_id)
        return result


def safe_call_sync(
    func: Callable[..., T],
    *args,
    default: T | None = None,
    plugin_id: str = "",
    context: str = "",
    error_tracker: PluginErrorTracker | None = None,
    **kwargs,
) -> T | None:
    """Execute a sync callable with exception isolation."""
    try:
        result = func(*args, **kwargs)
    except Exception as e:
        logger.error(
            "Plugin '%s' %s raised %s: %s",
            plugin_id,
            context,
            type(e).__name__,
            e,
        )
        if error_tracker:
            error_tracker.record_error(plugin_id, context, str(e))
        return default
    else:
        if error_tracker and plugin_id:
            error_tracker.record_success(plugin_id)
        return result
