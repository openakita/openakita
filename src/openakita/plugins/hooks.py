"""Hook registry — 15 lifecycle hooks with per-callback isolation."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .sandbox import PluginErrorTracker

logger = logging.getLogger(__name__)

HOOK_NAMES = frozenset(
    {
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
        "on_before_tool_use",
        "on_after_tool_use",
        "on_before_llm_call",
        "on_config_change",
        "on_error",
    }
)

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
    _wrapper.__hook_match__ = None  # type: ignore[attr-defined]
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
        match: Callable[..., bool] | None = None,
    ) -> None:
        """Register a hook callback.

        ``match``: optional predicate ``f(**kwargs) -> bool``. When provided,
        the dispatcher evaluates it with the same kwargs that would be passed
        to the callback and skips invocation when the predicate returns False.
        Predicates that raise are treated as no-match and recorded as a
        low-weight error so chronic offenders surface in the health snapshot.
        """
        if hook_name not in HOOK_NAMES:
            raise ValueError(f"Unknown hook '{hook_name}', must be one of {sorted(HOOK_NAMES)}")
        try:
            callback.__plugin_id__ = plugin_id  # type: ignore[attr-defined]
            callback.__hook_timeout__ = DEFAULT_HOOK_TIMEOUT  # type: ignore[attr-defined]
            callback.__hook_match__ = match  # type: ignore[attr-defined]
        except AttributeError:
            wrapper = _wrap_callback(callback, plugin_id)
            wrapper.__hook_match__ = match  # type: ignore[attr-defined]
            self._hooks[hook_name].append(wrapper)
            logger.debug(
                "Hook '%s' registered (wrapped) callback from plugin '%s'",
                hook_name,
                plugin_id,
            )
            return
        self._hooks[hook_name].append(callback)
        logger.debug(
            "Hook '%s' registered callback from plugin '%s'",
            hook_name,
            plugin_id,
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
                cb for cb in self._hooks[hook_name] if getattr(cb, "__plugin_id__", "") != plugin_id
            ]
            removed += before - len(self._hooks[hook_name])
        return removed

    async def dispatch(self, hook_name: str, **kwargs) -> list[Any]:
        """Dispatch a hook to all registered callbacks in parallel.

        Each callback is independently wrapped with timeout and exception
        isolation — a failing callback never blocks others.
        Catches BaseException (including CancelledError) to protect the host.
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

            match_fn = getattr(callback, "__hook_match__", None)
            if match_fn is not None:
                try:
                    if not match_fn(**kwargs):
                        return _SKIP
                except Exception as e:
                    logger.warning(
                        "Hook '%s' match predicate from plugin '%s' raised %s: %s, treating as no-match",
                        hook_name,
                        plugin_id,
                        type(e).__name__,
                        e,
                    )
                    self._error_tracker.record_error(
                        plugin_id, f"hook:{hook_name}:match", str(e)
                    )
                    return _SKIP

            try:
                if asyncio.iscoroutinefunction(callback):
                    result = await asyncio.wait_for(callback(**kwargs), timeout=timeout)
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(callback, **kwargs),
                        timeout=timeout,
                    )
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "Hook '%s' callback from plugin '%s' timed out (%.1fs), skipped",
                    hook_name,
                    plugin_id,
                    timeout,
                )
                self._error_tracker.record_error(
                    plugin_id, f"hook:{hook_name}", "timeout", kind="timeout"
                )
                return _SKIP
            except BaseException as e:
                logger.error(
                    "Hook '%s' callback from plugin '%s' raised %s: %s",
                    hook_name,
                    plugin_id,
                    type(e).__name__,
                    e,
                )
                self._error_tracker.record_error(plugin_id, f"hook:{hook_name}", str(e))
                return _SKIP
            else:
                if plugin_id and plugin_id != "unknown":
                    self._error_tracker.record_success(plugin_id)
                return result

        raw = await asyncio.gather(*(_run_one(cb) for cb in callbacks))
        return [r for r in raw if r is not _SKIP]

    def dispatch_sync(self, hook_name: str, **kwargs) -> list[Any]:
        """Synchronous dispatch — runs each callback serially in the current thread.

        Used by prompt builder, retrieval engine, and other sync contexts.
        For async callbacks, runs them in a separate thread with a fresh event
        loop to avoid deadlocking the caller's loop.
        """
        callbacks = list(self._hooks.get(hook_name, []))
        if not callbacks:
            return []

        results: list[Any] = []
        for callback in callbacks:
            plugin_id = getattr(callback, "__plugin_id__", "unknown")
            if self._error_tracker.is_disabled(plugin_id):
                continue
            match_fn = getattr(callback, "__hook_match__", None)
            if match_fn is not None:
                try:
                    if not match_fn(**kwargs):
                        continue
                except Exception as e:
                    logger.warning(
                        "Hook '%s' match predicate from plugin '%s' raised %s: %s, treating as no-match",
                        hook_name,
                        plugin_id,
                        type(e).__name__,
                        e,
                    )
                    self._error_tracker.record_error(
                        plugin_id, f"hook:{hook_name}:match", str(e)
                    )
                    continue
            timeout = getattr(callback, "__hook_timeout__", DEFAULT_HOOK_TIMEOUT)
            if asyncio.iscoroutinefunction(callback):
                import concurrent.futures

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, callback(**kwargs))
                        result = future.result(timeout=timeout)
                    if plugin_id and plugin_id != "unknown":
                        self._error_tracker.record_success(plugin_id)
                    if result is not None:
                        results.append(result)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "Hook '%s' async callback from plugin '%s' timed out in sync dispatch",
                        hook_name,
                        plugin_id,
                    )
                    self._error_tracker.record_error(
                        plugin_id, f"hook:{hook_name}", "timeout", kind="timeout"
                    )
                except BaseException as e:
                    logger.error(
                        "Hook '%s' sync-dispatch from plugin '%s' raised %s: %s",
                        hook_name,
                        plugin_id,
                        type(e).__name__,
                        e,
                    )
                    self._error_tracker.record_error(plugin_id, f"hook:{hook_name}", str(e))
            else:
                try:
                    result = callback(**kwargs)
                    if plugin_id and plugin_id != "unknown":
                        self._error_tracker.record_success(plugin_id)
                    if result is not None:
                        results.append(result)
                except BaseException as e:
                    logger.error(
                        "Hook '%s' sync callback from plugin '%s' raised %s: %s",
                        hook_name,
                        plugin_id,
                        type(e).__name__,
                        e,
                    )
                    self._error_tracker.record_error(plugin_id, f"hook:{hook_name}", str(e))
        return results

    def get_hooks(self, hook_name: str) -> list[Callable]:
        return list(self._hooks.get(hook_name, []))

    def clear(self) -> None:
        self._hooks.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {name: len(cbs) for name, cbs in self._hooks.items() if cbs}
