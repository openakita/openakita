"""Plugin sandbox — timeout wrappers, exception capture, fallback strategies."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_CONSECUTIVE_ERRORS = 10
ERROR_WINDOW = 300  # 5 minutes


class PluginErrorTracker:
    """Track per-plugin errors and decide when to auto-disable."""

    def __init__(self) -> None:
        self._errors: dict[str, list[dict]] = defaultdict(list)
        self._disabled: set[str] = set()
        self._on_auto_disable: Callable[[str], None] | None = None

    def set_auto_disable_callback(
        self, callback: Callable[[str], None]
    ) -> None:
        """Set a callback invoked when a plugin is auto-disabled (for tool cleanup)."""
        self._on_auto_disable = callback

    def record_error(
        self, plugin_id: str, context: str, error: str
    ) -> bool:
        """Record an error. Returns True if plugin should be auto-disabled."""
        entry = {"time": time.time(), "context": context, "error": error}
        self._errors[plugin_id].append(entry)

        cutoff = time.time() - ERROR_WINDOW
        recent = [e for e in self._errors[plugin_id] if e["time"] > cutoff]
        self._errors[plugin_id] = recent

        if len(recent) >= MAX_CONSECUTIVE_ERRORS:
            self._disabled.add(plugin_id)
            if self._on_auto_disable:
                try:
                    self._on_auto_disable(plugin_id)
                except Exception as e:
                    logger.warning(
                        "Auto-disable callback failed for plugin '%s': %s",
                        plugin_id, e,
                    )
            return True
        return False

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self._disabled

    def reset(self, plugin_id: str) -> None:
        self._errors.pop(plugin_id, None)
        self._disabled.discard(plugin_id)

    def get_errors(self, plugin_id: str) -> list[dict]:
        return list(self._errors.get(plugin_id, []))


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
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        logger.warning(
            "Plugin '%s' %s timed out (%.1fs), skipped",
            plugin_id, context, timeout,
        )
        if error_tracker:
            error_tracker.record_error(plugin_id, context, "timeout")
        return default
    except Exception as e:
        logger.error(
            "Plugin '%s' %s raised %s: %s",
            plugin_id, context, type(e).__name__, e,
        )
        if error_tracker:
            error_tracker.record_error(plugin_id, context, str(e))
        return default


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
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(
            "Plugin '%s' %s raised %s: %s",
            plugin_id, context, type(e).__name__, e,
        )
        if error_tracker:
            error_tracker.record_error(plugin_id, context, str(e))
        return default


def sandbox_async(
    timeout: float = 5.0,
    default: Any = None,
    context: str = "",
):
    """Decorator that wraps an async method with timeout + exception isolation."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs), timeout=timeout
                )
            except TimeoutError:
                logger.warning(
                    "%s timed out (%.1fs), returning default",
                    context or func.__qualname__, timeout,
                )
                return default
            except Exception as e:
                logger.error(
                    "%s raised %s: %s",
                    context or func.__qualname__, type(e).__name__, e,
                )
                return default

        return wrapper

    return decorator
