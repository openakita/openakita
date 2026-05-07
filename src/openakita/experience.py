"""Sanitized tool and skill execution experience records."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Failure summarization (P4.2): distill tool_experience.jsonl back into prompt
# ---------------------------------------------------------------------------
#
# Design constraints:
# - Read-side, never blocks the writer (writer holds tracker._lock).
# - O(N) over a sliding tail; default window 200 entries keeps it cheap.
# - mtime-keyed cache (TTL 60s) so repeated build_system_prompt calls within
#   the same task burst do not hit disk every time.
# - Returns sanitized & bounded structures; safe to embed in a prompt.
# - Never raises: corruption / missing file simply yields [].

_summary_cache: tuple[float, float, int, str, list[dict]] | None = None
# (cached_at, file_mtime, file_size_at_read, cache_key, result)


def _read_jsonl_tail(path: Path, *, window: int) -> list[dict]:
    """Read up to ``window`` last JSON lines from a file. Tolerant of bad rows."""
    if not path.exists():
        return []
    try:
        # File is line-delimited JSON; for typical sizes (<<10MB) reading all
        # lines is fine. We only ever need the last N entries.
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        logger.debug("[Experience] failed to read %s: %s", path, exc)
        return []
    if not lines:
        return []
    tail = lines[-window:]
    out: list[dict] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def summarize_recent_failures(
    *,
    window: int = 200,
    min_failures: int = 2,
    min_failure_rate: float = 0.5,
    max_results: int = 5,
    agent_profile_id: str | None = None,
    tracker: ToolExperienceTracker | None = None,
) -> list[dict]:
    """Aggregate the most recently failing tools.

    Args:
        window: Number of most-recent jsonl entries to scan.
        min_failures: A tool must have failed at least this many times within
            the window to be reported. Avoids one-off flukes.
        min_failure_rate: Only report tools whose failure rate (failures/total)
            is at least this. Prevents shaming widely-used reliable tools.
        max_results: Cap the number of returned entries (prompt budget guard).
        agent_profile_id: When provided, only consider records from that
            agent profile. ``None`` aggregates across all profiles.
        tracker: Optional injected tracker (testing). Defaults to the
            singleton.

    Returns:
        A list of ``{tool_name, total, failures, failure_rate, common_errors,
        last_error}`` dicts, sorted by ``(failures desc, failure_rate desc)``.
        Empty list when nothing qualifies or the file is missing / unreadable.
    """
    global _summary_cache

    tracker = tracker or get_tool_experience_tracker()
    path = tracker.path
    cache_key = f"{window}:{min_failures}:{min_failure_rate}:{max_results}:{agent_profile_id or '*'}"

    # mtime + size + cache_key + 60s TTL guard. We re-read on either content
    # change OR after 60s so a "cold" failure never lingers indefinitely.
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, 0

    now = time.time()
    if _summary_cache is not None:
        cached_at, cm, cs, ck, cached = _summary_cache
        if ck == cache_key and cm == mtime and cs == size and now - cached_at < 60:
            return cached

    rows = _read_jsonl_tail(path, window=window)
    if not rows:
        _summary_cache = (now, mtime, size, cache_key, [])
        return []

    totals: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    error_types: dict[str, Counter[str]] = defaultdict(Counter)
    last_error: dict[str, str] = {}

    for row in rows:
        tool = str(row.get("tool_name") or "").strip()
        if not tool:
            continue
        if agent_profile_id is not None:
            row_pid = str(row.get("agent_profile_id") or "default")
            if row_pid != agent_profile_id:
                continue
        totals[tool] += 1
        if not row.get("success", False):
            failures[tool] += 1
            err_type = str(row.get("error_type") or "").strip() or "unknown"
            error_types[tool][err_type] += 1
            # Keep the most recent failure summary (tail order = chronological)
            out_summary = str(row.get("output_summary") or "").strip()
            if out_summary:
                # Single line, capped, for prompt safety.
                last_error[tool] = out_summary.replace("\n", " ")[:160]

    summary: list[dict] = []
    for tool, fail_count in failures.items():
        total = totals[tool]
        rate = fail_count / total if total else 0.0
        if fail_count < min_failures or rate < min_failure_rate:
            continue
        common = [
            {"error_type": et, "count": cnt}
            for et, cnt in error_types[tool].most_common(2)
        ]
        summary.append(
            {
                "tool_name": tool,
                "total": total,
                "failures": fail_count,
                "failure_rate": round(rate, 2),
                "common_errors": common,
                "last_error": last_error.get(tool, ""),
            }
        )

    summary.sort(key=lambda r: (r["failures"], r["failure_rate"]), reverse=True)
    summary = summary[:max_results]

    _summary_cache = (now, mtime, size, cache_key, summary)
    return summary


def format_failure_hint_section(summary: list[dict]) -> str:
    """Render :func:`summarize_recent_failures` output for prompt injection.

    Returns an empty string when ``summary`` is empty so the caller can
    cheaply skip section assembly.
    """
    if not summary:
        return ""
    lines = [
        "## Recent Tool Reliability",
        "",
        "_Heads-up: these tools failed often in the last few attempts. "
        "Consider an alternative approach or different parameters before retrying._",
        "",
    ]
    for row in summary:
        tool = row.get("tool_name", "?")
        failures = row.get("failures", 0)
        total = row.get("total", 0)
        rate_pct = int(round(float(row.get("failure_rate", 0.0)) * 100))
        common_parts = []
        for ce in row.get("common_errors") or []:
            et = ce.get("error_type") or ""
            cnt = ce.get("count") or 0
            if et:
                common_parts.append(f"{et}×{cnt}")
        common_text = ", ".join(common_parts) if common_parts else "n/a"
        line = f"- `{tool}` — {failures}/{total} failed ({rate_pct}%); errors: {common_text}"
        last_err = (row.get("last_error") or "").strip()
        if last_err:
            line += f"; last: {last_err}"
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def reset_failure_summary_cache() -> None:
    """Test helper: drop the cached summary so the next call re-reads disk."""
    global _summary_cache
    _summary_cache = None
