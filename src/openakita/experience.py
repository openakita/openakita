"""Sanitized tool and skill execution experience records."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"),
]


def _redact(text: str, *, limit: int = 1200) -> str:
    value = str(text or "")
    for pat in _SECRET_PATTERNS:
        value = pat.sub(lambda m: f"{m.group(1)}{m.group(2) if len(m.groups()) > 1 else ''}[REDACTED]", value)
    value = value.replace("\x00", "")
    if len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


@dataclass
class ToolExperienceTracker:
    path: Path

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        tool_name: str,
        agent_profile_id: str = "default",
        skill_name: str = "",
        env_scope: str = "",
        success: bool,
        duration_ms: float | None = None,
        error_type: str = "",
        exit_code: int | None = None,
        output: str = "",
        input_summary: Any = None,
        deps_hash: str = "",
    ) -> None:
        entry = {
            "ts": int(time.time()),
            "agent_profile_id": agent_profile_id or "default",
            "tool_name": tool_name,
            "skill_name": skill_name,
            "env_scope": env_scope,
            "deps_hash": deps_hash,
            "success": bool(success),
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
            "error_type": error_type,
            "exit_code": exit_code,
            "input_summary": _redact(json.dumps(input_summary, ensure_ascii=False, default=str), limit=600)
            if input_summary is not None
            else "",
            "output_summary": _redact(output, limit=1200),
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


_tracker: ToolExperienceTracker | None = None


def get_tool_experience_tracker() -> ToolExperienceTracker:
    global _tracker
    if _tracker is None:
        from .config import settings

        _tracker = ToolExperienceTracker(settings.project_root / "data" / "tool_experience.jsonl")
    return _tracker
