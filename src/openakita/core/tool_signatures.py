"""Shared helpers for identifying tool invocations.

The supervisor must distinguish "same tool, same arguments" from normal batch
work such as writing several different files. Keep that rule in one place so
reasoning paths and diagnostics cannot drift apart again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

_TARGET_FIELDS = (
    "path",
    "file_path",
    "filepath",
    "target_path",
    "directory",
    "url",
    "query",
    "command",
)


def _stable_param_string(tool_args: Any) -> str:
    try:
        return json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(tool_args)


def tool_invocation_key(tool_name: str, tool_args: Any, *, hash_len: int = 8) -> str:
    """Return the canonical ``name(hash)`` key for one concrete invocation."""
    param_hash = hashlib.md5(_stable_param_string(tool_args).encode()).hexdigest()[:hash_len]
    return f"{tool_name}({param_hash})"


def canonical_tool_name(tool_name: str, tool_args: Any) -> str:
    """Normalize tool names only when two names need different loop semantics."""
    name = str(tool_name or "")
    if name == "read_file" and isinstance(tool_args, dict):
        path = str(tool_args.get("path", "") or tool_args.get("file_path", ""))
        normalized_path = path.replace("\\", "/").lower()
        if "/terminals/" in normalized_path and normalized_path.endswith(".txt"):
            return "read_file_terminal"
    return name


class ToolSignatureBuilder:
    """Stateful signature builder for one reasoning loop.

    Some browser page-read tools have tiny argument payloads, so their signature
    also includes the last navigated URL. The caller owns one builder per task.
    """

    def __init__(self, browser_page_read_tools: Iterable[str] = ()) -> None:
        self._browser_page_read_tools = frozenset(browser_page_read_tools)
        self._last_browser_url = ""

    def signature_for_call(self, tool_call: dict[str, Any]) -> str:
        name = str(tool_call.get("name", ""))
        tool_args = tool_call.get("input", tool_call.get("arguments", {}))

        if name == "browser_navigate" and isinstance(tool_args, dict):
            self._last_browser_url = str(tool_args.get("url", "") or "")

        param_str = _stable_param_string(tool_args)
        canonical_name = canonical_tool_name(name, tool_args)
        if (
            canonical_name in self._browser_page_read_tools
            and len(param_str) <= 20
            and self._last_browser_url
        ):
            param_str = f"{param_str}|url={self._last_browser_url}"

        param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
        return f"{canonical_name}({param_hash})"


def tool_call_target_summary(tool_call: dict[str, Any], *, max_chars: int = 120) -> str:
    """Return a human-readable target summary for diagnostics."""
    name = str(tool_call.get("name", "?") or "?")
    tool_args = tool_call.get("input", tool_call.get("arguments", {}))
    if not isinstance(tool_args, dict):
        return name

    for field in _TARGET_FIELDS:
        value = tool_args.get(field)
        if value:
            text = str(value).replace("\n", " ")
            if len(text) > max_chars:
                text = text[: max_chars - 3] + "..."
            return f"{name}:{field}={text}"

    return name
