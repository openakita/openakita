"""Per-project activity log for ppt-maker.

We keep two log surfaces:

1. **Verbose per-call dumps** (already produced by ``PptBrainAdapter._write_log``).
   These contain full prompts/responses and live next to each other under
   ``data/projects/<id>/logs/*.json``.
2. **Activity stream** (this module). A compact JSONL feed that tells the user
   what is happening *right now*: every Brain call, layout pick, fallback,
   asset fetch and pipeline step. The UI subscribes to this stream so the
   user sees the same kind of "Agent thinking" trail the OpenAkita main app
   exposes for normal chats.

Each event is a small dict::

    {
        "ts": 1761601234.567,                # epoch float
        "iso": "2026-04-28T01:08:30+08:00",   # ISO timestamp (local tz)
        "project_id": "ppt_2670d328",
        "stage": "outline",                  # short identifier
        "status": "success" | "start" | "fallback" | "error" | "info",
        "level": "info" | "warn" | "error",
        "message": "Brain 已生成 9 页大纲",      # human-readable summary
        "details": {...}                       # optional, JSON-serialisable
    }

Writes are append-only to ``data/projects/<id>/logs/activity.jsonl`` and never
raise on filesystem hiccups so the pipeline stays resilient.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ppt_maker_inline.file_utils import ensure_dir, safe_name

logger = logging.getLogger(__name__)

ACTIVITY_FILENAME = "activity.jsonl"


def _iso(ts: float) -> str:
    return (
        datetime.fromtimestamp(ts, tz=UTC)
        .astimezone()
        .isoformat(timespec="seconds")
    )


class PptActivityLogger:
    """Append + read structured activity events for one plugin install."""

    def __init__(self, *, data_root: str | Path) -> None:
        self._data_root = Path(data_root)
        # One asyncio lock per project keeps concurrent step writes ordered
        # without blocking other projects.
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Internals ──────────────────────────────────────────────────────

    def _project_dir(self, project_id: str) -> Path:
        return self._data_root / "projects" / safe_name(project_id) / "logs"

    def _activity_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / ACTIVITY_FILENAME

    def _lock_for(self, project_id: str) -> asyncio.Lock:
        lock = self._locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[project_id] = lock
        return lock

    # ── Write ──────────────────────────────────────────────────────────

    async def append(
        self,
        *,
        project_id: str,
        stage: str,
        status: str,
        message: str = "",
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one event. Returns the event dict (helpful for broadcasting)."""
        ts = time.time()
        event = {
            "ts": ts,
            "iso": _iso(ts),
            "project_id": project_id,
            "stage": stage,
            "status": status,
            "level": level,
            "message": message,
            "details": details or {},
        }
        path = self._activity_path(project_id)
        try:
            ensure_dir(path.parent)
            line = json.dumps(event, ensure_ascii=False, default=_json_default) + "\n"
            async with self._lock_for(project_id):
                # Append is atomic on POSIX for small writes; on Windows we
                # serialise via the per-project asyncio lock above.
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError:
            logger.exception("ppt-maker activity log write failed")
        return event

    # ── Read ───────────────────────────────────────────────────────────

    def read(
        self,
        project_id: str,
        *,
        since: float | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return events for the given project, oldest first."""
        path = self._activity_path(project_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if since is not None and float(event.get("ts", 0)) <= since:
                        continue
                    events.append(event)
        except OSError:
            logger.exception("ppt-maker activity log read failed")
            return []
        # Cap the most recent ``limit`` events; keep ascending order in output.
        if limit and limit > 0 and len(events) > limit:
            events = events[-limit:]
        return events

    def latest_ts(self, project_id: str) -> float | None:
        """Return the timestamp of the most recent event, or None if empty."""
        events = self.read(project_id, limit=1)
        return events[-1]["ts"] if events else None


def _json_default(value: Any) -> Any:
    """Best-effort serialiser for objects that ended up in ``details``."""
    if isinstance(value, (set, tuple)):
        return list(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")  # pydantic
        except Exception:
            return str(value)
    return str(value)


def summarise_event(event: dict[str, Any]) -> str:
    """One-line preview, used by tests / debugging."""
    return f"[{event.get('iso', '')}] {event.get('stage')} {event.get('status')} {event.get('message')}"


def collect_iso_window(events: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return the first/last iso timestamps in the iterable, or ``(None, None)``."""
    first: str | None = None
    last: str | None = None
    for event in events:
        iso = event.get("iso")
        if not iso:
            continue
        first = first or iso
        last = iso
    return first, last
