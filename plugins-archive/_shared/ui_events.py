"""UI event helpers — bridge the gap between host's namespaced events and
plugin code that wants bare event names.

Background: host's ``api.broadcast_ui_event(event_type, data)`` always
prepends ``plugin:<plugin_id>:`` to the event type before broadcasting.
Plugin authors however want to listen for ``"task_updated"``, not
``"plugin:my-plugin:task_updated"``.

Per audit3 (user decision 2026-04-18): we fix this in the SDK with full
backwards compatibility — old plugins keep working, new plugins can use
the helpers below for cleaner code.

This module is **pure backend-side**.  The frontend mirror lives in
``web/event-helpers.js`` and exposes ``window.OpenAkita.onEvent(...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


_PREFIX = "plugin:"


def strip_plugin_event_prefix(event_type: str) -> tuple[str | None, str]:
    """Split ``"plugin:<id>:<bare>"`` → ``("<id>", "<bare>")``.

    For events that do not have the prefix, returns ``(None, event_type)``
    unchanged so this is safe to call on any event string.
    """
    if not event_type or not event_type.startswith(_PREFIX):
        return None, event_type
    rest = event_type[len(_PREFIX):]
    sep = rest.find(":")
    if sep < 0:
        return None, event_type
    return rest[:sep], rest[sep + 1 :]


class UIEventEmitter:
    """Thin wrapper around ``api.broadcast_ui_event``.

    Provides a uniform place for plugins to:

    - call ``emit("task_updated", {...})`` — same as the raw API but with
      logging hooks
    - register **local** in-process listeners via :meth:`on` (useful for
      tests where there is no host gateway)
    """

    def __init__(self, api: Any) -> None:
        self._api = api
        self._local_handlers: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast to UI **and** call any local listeners."""
        try:
            broadcast = getattr(self._api, "broadcast_ui_event", None)
            if callable(broadcast):
                broadcast(event_type, dict(data))
        except Exception as e:  # noqa: BLE001 — broadcasting must never crash plugin
            logger.warning("broadcast_ui_event failed: %s", e)
        for h in list(self._local_handlers.get(event_type, ())):
            try:
                result = h(dict(data))
                if isinstance(result, Awaitable):  # type: ignore[arg-type]
                    import asyncio
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        pass
            except Exception as e:  # noqa: BLE001
                logger.warning("local UIEvent handler error: %s", e)

    def on(self, event_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        """Register an in-process listener (test-friendly)."""
        self._local_handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Callable[[dict[str, Any]], Any] | None = None) -> None:
        if event_type not in self._local_handlers:
            return
        if handler is None:
            del self._local_handlers[event_type]
            return
        self._local_handlers[event_type] = [
            h for h in self._local_handlers[event_type] if h is not handler
        ]
