"""Hook registry — 10 lifecycle hooks with per-callback isolation."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .sandbox import PluginErrorTracker

logger = logging.getLogger(__name__)

HOOK_NAMES = frozenset({
    "on_init",
    "on_shutdown",
    "on_message_received",
    "on_message_sending",
    "on_retrieve",
    "on_tool_result",
    "on_session_start",
    "on_session_end",
    "on_prompt_build",
    "on_schedule",
})

DEFAULT_HOOK_TIMEOUT = 5.0
_SKIP = object()  # sentinel for skipped/failed callbacks


def _wrap_callback(fn: Callable, plugin_id: str) -> Callable:
    """Wrap a callback so we can attach metadata even for bound methods."""
    async def _wrapper(**kwargs):
        result = fn(**kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    _wrapper.__plugin_id__ = plugin_id  # type: ignore[attr-defined]
    _wrapper.__hook_timeout__ = DEFAULT_HOOK_TIMEOUT  # type: ignore[attr-defined]
    return _wrapper


class HookRegistry:
    """Registry and dispatcher for plugin hooks.

    Each callback is isolated: timeout or exception in one callback
    does not affect other callbacks in the same hook chain.
    """

    def __init__(self, error_tracker: PluginErrorTracker | None = None) -> None:
        self._hooks: dict[str, list[Callable]] = defaultdict(list)
        self._error_tracker = error_tracker or PluginErrorTracker()

    def register(
        self,
        hook_name: str,
        callback: Callable,
        *,
        plugin_id: str = "",
    ) -> None:
        if hook_name not in HOOK_NAMES:
            raise ValueError(
                f"Unknown hook '{hook_name}', must be one of {sorted(HOOK_NAMES)}"
            )
        try:
            callback.__plugin_id__ = plugin_id  # type: ignore[attr-defined]
            callback.__hook_timeout__ = DEFAULT_HOOK_TIMEOUT  # type: ignore[attr-defined]
        except AttributeError:
            # Python 3.13+ does not allow setting attributes on bound methods;
            # wrap in a thin lambda to carry metadata.
            wrapper = _wrap_callback(callback, plugin_id)
            self._hooks[hook_name].append(wrapper)
            logger.debug(
                "Hook '%s' registered (wrapped) callback from plugin '%s'",
                hook_name, plugin_id,
            )
            return
        self._hooks[hook_name].append(callback)
        logger.debug(
            "Hook '%s' registered callback from plugin '%s'",
            hook_name, plugin_id,
        )

    def set_timeout(self, hook_name: str, plugin_id: str, timeout: float) -> None:
        for cb in self._hooks.get(hook_name, []):
            if getattr(cb, "__plugin_id__", "") == plugin_id:
                cb.__hook_timeout__ = timeout  # type: ignore[attr-defined]

    def unregister_plugin(self, plugin_id: str) -> int:
        """Remove all hooks registered by a plugin. Returns count removed."""
        removed = 0
        for hook_name in list(self._hooks):
            before = len(self._hooks[hook_name])
            self._hooks[hook_name] = [
                cb
                for cb in self._hooks[hook_name]
                if getattr(cb, "__plugin_id__", "") != plugin_id
            ]
            removed += before - len(self._hooks[hook_name])
        return removed

    async def dispatch(self, hook_name: str, **kwargs) -> list[Any]:
        """Dispatch a hook to all registered callbacks in parallel.

        Each callback is independently wrapped with timeout and exception
        isolation — a failing callback never blocks others.
        Snapshot the callback list to avoid concurrent-modification issues.
        """
        callbacks = list(self._hooks.get(hook_name, []))
        if not callbacks:
            return []

        async def _run_one(callback: Callable) -> Any:
            plugin_id = getattr(callback, "__plugin_id__", "unknown")
            timeout = getattr(callback, "__hook_timeout__", DEFAULT_HOOK_TIMEOUT)

            if self._error_tracker.is_disabled(plugin_id):
                return _SKIP

            try:
                if asyncio.iscoroutinefunction(callback):
                    return await asyncio.wait_for(
                        callback(**kwargs), timeout=timeout
                    )
                else:
                    return await asyncio.wait_for(
                        asyncio.to_thread(callback, **kwargs),
                        timeout=timeout,
                    )
            except TimeoutError:
                logger.warning(
                    "Hook '%s' callback from plugin '%s' timed out (%.1fs), skipped",
                    hook_name, plugin_id, timeout,
                )
                should_disable = self._error_tracker.record_error(
                    plugin_id, f"hook:{hook_name}", "timeout"
                )
                if should_disable:
                    logger.error(
                        "Plugin '%s' auto-disabled due to repeated errors",
                        plugin_id,
                    )
                return _SKIP
            except Exception as e:
                logger.error(
                    "Hook '%s' callback from plugin '%s' raised %s: %s",
                    hook_name, plugin_id, type(e).__name__, e,
                )
                should_disable = self._error_tracker.record_error(
                    plugin_id, f"hook:{hook_name}", str(e)
                )
                if should_disable:
                    logger.error(
                        "Plugin '%s' auto-disabled due to repeated errors",
                        plugin_id,
                    )
                return _SKIP

        raw = await asyncio.gather(*(_run_one(cb) for cb in callbacks))
        return [r for r in raw if r is not _SKIP]

    def get_hooks(self, hook_name: str) -> list[Callable]:
        return list(self._hooks.get(hook_name, []))

    def clear(self) -> None:
        self._hooks.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {name: len(cbs) for name, cbs in self._hooks.items() if cbs}
