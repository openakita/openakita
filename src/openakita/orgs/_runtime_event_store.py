"""``_runtime_event_store.py`` -- v2 per-org event log (smoke-5-sse fix).

In-memory event log backing ``GET /api/v2/orgs/{id}/{events,activity,
audit-log}`` (B45-B48 on ``orgs_v2_runtime_state.py``). Pre-fix the
mint runtime never wired a store onto ``OrgRuntime`` so every events
route 404'd for mint-created orgs (``tmp_p10/_step2_report.md`` RT13
/ RT34). JSONL persistence under ``<org_dir>/logs/events.jsonl`` is
best-effort -- IO errors log + swallow (parity with v1).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _coerce_epoch(value: Any) -> float:
    """Best-effort epoch-seconds coercion (audit cutoff math)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


class OrgEventStore:
    """Thread-safe in-memory + JSONL-backed event log for one org."""

    def __init__(self, org_id: str, jsonl_path: Path | None = None) -> None:
        self._org_id = org_id
        self._jsonl = jsonl_path
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if jsonl_path is not None and jsonl_path.is_file():
            try:
                with jsonl_path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            self._events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError as exc:  # noqa: BLE001
                _LOGGER.warning("OrgEventStore replay failed for %s: %s", org_id, exc)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append ``event``; stamps ``org_id`` + ``at`` + ``ts`` if absent.

        The dual ``at`` / ``ts`` stamp closes the v17-v20 audit
        observability hole. Pre-fix only ``at`` was stamped, and every
        exploratory script (``_v*_biz/_lib.py``) read ``e.get("ts")``
        and reported ``last_event_ts = null`` for the entire run. We
        keep ``at`` so older readers continue to work and add ``ts``
        as a numeric-epoch mirror with the same value, so any new
        reader can use the canonical field without dual-key fallback.
        """
        record = dict(event)
        record.setdefault("org_id", self._org_id)
        now = time.time()
        record.setdefault("at", now)
        record.setdefault("ts", record.get("at", now))
        with self._lock:
            self._events.append(record)
        if self._jsonl is not None:
            try:
                self._jsonl.parent.mkdir(parents=True, exist_ok=True)
                with self._jsonl.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:  # noqa: BLE001
                _LOGGER.warning("OrgEventStore persist failed for %s: %s", self._org_id, exc)
        return record

    def query(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        chain_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Filter + tail by ``limit`` (most recent suffix)."""
        with self._lock:
            items = list(self._events)
        if event_type:
            items = [
                e for e in items if e.get("event_type") == event_type or e.get("type") == event_type
            ]
        if actor:
            items = [e for e in items if e.get("actor") == actor]
        if chain_id:
            items = [e for e in items if e.get("chain_id") == chain_id]
        if task_id:
            items = [e for e in items if e.get("task_id") == task_id]
        if since:
            items = [e for e in items if str(e.get("at", "")) >= str(since)]
        if until:
            items = [e for e in items if str(e.get("at", "")) <= str(until)]
        capped = max(0, int(limit))
        return items[-capped:] if capped else []

    def get_audit_log(self, *, days: int = 7) -> list[dict[str, Any]]:
        """Subset of events newer than ``days`` ago."""
        cutoff = time.time() - max(0, int(days)) * 86400.0
        with self._lock:
            return [dict(e) for e in self._events if _coerce_epoch(e.get("at")) >= cutoff]


__all__ = ["OrgEventStore"]
