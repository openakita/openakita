"""``_runtime_plugin_assets.py`` -- v2 OrgRuntime plugin assets (P9.6h).

Fourth-heaviest sibling: lifts the plugin-asset recording +
file-output registration + legacy tool_handler bridging
machinery out of v1 ``OrgRuntime`` (~15 methods, ~1 064 LOC
dominated by ``_record_plugin_asset_output`` 349 LOC,
``_register_org_tool_handler`` 161 LOC).

This commit (P9.6h1a) ships the helpers + dataclass +
:class:`ToolHandlerBridge`. The
:class:`PluginAssetRecorder` body rides P9.6h1b
(next commit -- pure file-append, no surface churn);
:class:`FileOutputRegistry` + react-trace + task-delivery
synth ride P9.6h2.

ADR-0012 (no-shim): zero ``openakita.orgs`` imports.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from time import time
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

_PLUGIN_TOOL_PREFIXES: tuple[str, ...] = (
    "plugin_",
    "plg_",
    "openakita.plugin.",
    "mcp.",
)
_PLUGIN_TOOL_SUFFIXES: tuple[str, ...] = (".plugin", "_plugin")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_asset_filename(raw: str, *, max_len: int = 96) -> str:
    """v1 ``_safe_asset_filename`` parity -- replace unsafe chars."""

    if not raw:
        return "asset"
    cleaned = _SAFE_FILENAME_RE.sub("_", raw.strip()).strip("._")
    if not cleaned:
        return "asset"
    if len(cleaned) > max_len:
        head, sep, ext = cleaned.rpartition(".")
        if sep and len(ext) <= 8:
            cleaned = f"{head[: max_len - len(ext) - 1]}.{ext}"
        else:
            cleaned = cleaned[:max_len]
    return cleaned


def ext_for_url(url: str) -> str:
    """v1 ``_ext_for_url`` parity -- extract trailing extension, lower-case."""

    if not url:
        return ""
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001
        return ""
    if "." not in path:
        return ""
    ext = path.rsplit(".", 1)[-1].lower()
    if not ext or len(ext) > 8 or not ext.isalnum():
        return ""
    return ext


def is_plugin_tool(tool_name: str) -> bool:
    """v1 ``_is_plugin_tool`` parity (39 LOC -> ~6 LOC)."""

    if not tool_name:
        return False
    name = tool_name.lower()
    if any(name.startswith(p) for p in _PLUGIN_TOOL_PREFIXES):
        return True
    return any(name.endswith(s) for s in _PLUGIN_TOOL_SUFFIXES)


def plugin_id_for_tool(tool_name: str) -> str | None:
    """v1 ``_plugin_id_for_tool`` parity (24 LOC -> ~9 LOC)."""

    if not is_plugin_tool(tool_name):
        return None
    name = tool_name
    for prefix in _PLUGIN_TOOL_PREFIXES:
        if name.lower().startswith(prefix):
            rest = name[len(prefix) :]
            return rest.split("_", 1)[0] or None
    return None


@dataclass
class PluginAsset:
    """v1 plugin-asset dict shape (parity)."""

    org_id: str
    plugin_id: str
    tool_name: str
    path: str
    size_bytes: int = 0
    digest: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    recorded_at: float = field(default_factory=time)


_ToolHandler = Callable[[str, str, Mapping[str, Any]], Awaitable[Any]]


class ToolHandlerBridge:
    """Adapts the v1 ``handle_org_tool`` callable to a v2 seam.

    v1 ``_register_org_tool_handler`` (161 LOC) registers
    one global handler that fan-outs by tool name; v2
    exposes a clean register / dispatch API. The runtime
    composition root re-registers the legacy handler via
    :meth:`register` (or :meth:`register_fallback`) so
    existing plugin tools keep working.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, _ToolHandler] = {}
        self._fallback: _ToolHandler | None = None

    def register(self, tool_name: str, handler: _ToolHandler) -> None:
        if not tool_name:
            raise ValueError("tool_name must be non-empty")
        self._handlers[tool_name] = handler

    def register_fallback(self, handler: _ToolHandler | None) -> None:
        self._fallback = handler

    async def dispatch(
        self,
        *,
        org_id: str,
        tool_name: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        """v1 ``handle_org_tool`` parity (5 LOC + 161 LOC glue)."""

        handler = self._handlers.get(tool_name) or self._fallback
        if handler is None:
            return {"status": "error", "reason": "no_handler", "tool_name": tool_name}
        try:
            return await handler(org_id, tool_name, payload or {})
        except Exception as exc:  # noqa: BLE001 (v1 parity)
            _LOGGER.exception("tool handler raised (org=%s tool=%s)", org_id, tool_name)
            return {
                "status": "error",
                "reason": "handler_raised",
                "tool_name": tool_name,
                "error": str(exc),
            }


__all__ = [
    "PluginAsset",
    "ToolHandlerBridge",
    "ext_for_url",
    "is_plugin_tool",
    "plugin_id_for_tool",
    "safe_asset_filename",
]
