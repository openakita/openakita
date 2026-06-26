"""Shared validation + quarantine helpers for scheduled task names (Fix-15).

Before this module the name check existed only in the API route layer
(``api/routes/scheduler.py::_validate_task_name``).  Programmatic paths
(MCP tools, internal task seeding, restored legacy data) could still smuggle
in path-traversal-shaped names like ``../foo`` or ``a/b\\c``, leaving them
on disk forever.

Goals:

- Single source of truth for the naming policy.
- ``validate_task_name`` returns ``(ok, reason)`` so the route layer can
  surface a 422 without changing its public contract.
- ``quarantine_invalid_task_name`` rewrites bad names into a safe,
  unmistakable ``__quarantine__/<sanitised>_<md5>`` form and is idempotent
  (running it twice on already-quarantined names is a no-op).

Restraint:

- The validator only blocks the truly dangerous tokens.  No new restrictions
  on perfectly normal Unicode/Chinese names.
- Quarantine never deletes — operators can still inspect / rename via the UI
  if they choose.
"""

from __future__ import annotations

import hashlib
import re

# Tokens that should never appear in a stored task name.  Mirrors the
# previously route-only ``_TASK_NAME_FORBIDDEN`` tuple.
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "..",
    "/",
    "\\",
    "\x00",
    "<",
    ">",
    "|",
    ":",
    "*",
    "?",
    '"',
)

QUARANTINE_PREFIX = "__quarantine__/"
_QUARANTINE_RE = re.compile(r"^__quarantine__/")
_MAX_NAME_LEN = 200


def validate_task_name(name: str | None) -> tuple[bool, str]:
    """Return ``(ok, reason)``.  ``None`` is treated as 'unset' → ok."""
    if name is None:
        return True, ""
    if not isinstance(name, str):
        return False, "name 必须是字符串"
    n = name.strip()
    if not n:
        return False, "name 不能为空"
    if len(n) > _MAX_NAME_LEN:
        return False, f"name 长度不能超过 {_MAX_NAME_LEN}"
    # Quarantined names contain ``/`` by design — exempt them so a restart
    # doesn't keep "fixing" already-fixed entries.
    if _QUARANTINE_RE.match(n):
        return True, ""
    if any(token in n for token in FORBIDDEN_TOKENS):
        return False, "name 包含非法字符（路径穿越/控制字符/Windows 保留字符）"
    return True, ""


def is_quarantined(name: str | None) -> bool:
    if not isinstance(name, str):
        return False
    return bool(_QUARANTINE_RE.match(name))


def quarantine_invalid_task_name(name: str) -> str | None:
    """Return a safe quarantined name, or ``None`` when ``name`` is already
    safe (no-op signal for callers).
    """
    if name is None:
        return None
    if not isinstance(name, str):
        name = str(name)
    if is_quarantined(name):
        return None
    ok, _ = validate_task_name(name)
    if ok:
        return None

    digest = hashlib.md5(name.encode("utf-8", errors="replace")).hexdigest()[:10]
    sanitised = re.sub(r"[\s\\/<>|:*?\"\x00]+", "_", name).strip("_") or "unnamed"
    sanitised = sanitised[:80]
    return f"{QUARANTINE_PREFIX}{sanitised}_{digest}"
