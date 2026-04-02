"""
技能参数替换系统

支持 SKILL.md body 和脚本参数中的占位符替换。
占位符格式: {{placeholder_name}}

内置占位符:
- {{date}}        — 当前日期 (YYYY-MM-DD)
- {{datetime}}    — 当前日期时间 (ISO 8601)
- {{cwd}}         — 当前工作目录
- {{os}}          — 操作系统名称
- {{user}}        — 当前用户名
- {{project_root}} — 项目根目录
"""

from __future__ import annotations

import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _builtin_values(project_root: str | Path | None = None) -> dict[str, str]:
    """Return values for all built-in placeholders."""
    now = datetime.now()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.isoformat(timespec="seconds"),
        "cwd": os.getcwd(),
        "os": platform.system(),
        "user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
        "project_root": str(project_root) if project_root else os.getcwd(),
    }


def substitute(
    text: str,
    extra: dict[str, str] | None = None,
    *,
    project_root: str | Path | None = None,
) -> str:
    """Replace all {{placeholder}} occurrences in *text*.

    Built-in placeholders are always available. *extra* can override
    or extend them with skill-specific values.
    """
    values = _builtin_values(project_root)
    if extra:
        values.update(extra)

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return values.get(key, m.group(0))

    return _PLACEHOLDER_RE.sub(_replace, text)


def format_argument_schema(arguments: list[dict]) -> str:
    """Format an arguments schema into a human-readable block for get_skill_info."""
    if not arguments:
        return ""
    lines = ["**Parameters:**"]
    for arg in arguments:
        name = arg.get("name", "?")
        typ = arg.get("type", "string")
        desc = arg.get("description", "")
        required = arg.get("required", False)
        default = arg.get("default")
        marker = " (required)" if required else ""
        default_str = f", default: {default}" if default is not None else ""
        lines.append(f"- `{name}` ({typ}{marker}{default_str}): {desc}")
    return "\n".join(lines)


def resolve_skill_args(
    arguments_schema: list[dict],
    provided: dict[str, Any],
) -> dict[str, str]:
    """Resolve provided args against schema, applying defaults.

    Returns a flat dict of string values suitable for script invocation.
    """
    result: dict[str, str] = {}
    for arg_def in arguments_schema:
        name = arg_def.get("name", "")
        if not name:
            continue
        if name in provided:
            result[name] = str(provided[name])
        elif "default" in arg_def:
            result[name] = str(arg_def["default"])
    return result
