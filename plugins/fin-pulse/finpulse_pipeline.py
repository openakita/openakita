# ruff: noqa: N999
"""Ingest + analysis pipeline for fin-pulse.

The pipeline is deliberately thin: each stage is an ``async def`` that
reads rows from :class:`FinpulseTaskManager` and writes the next stage
back. Phase 2 lands :func:`ingest` (collect → normalize → dedupe);
Phase 3 layers AI scoring on top; Phase 4 renders digests and hands the
payload to :mod:`finpulse_dispatch`.

All stages are side-effect free on the event loop — long-running
fetches move off the hot path via :func:`asyncio.gather` with a
concurrency cap read from ``config['fetch_concurrency']``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from finpulse_errors import map_exception
from finpulse_fetchers import SOURCE_REGISTRY, get_fetcher
from finpulse_fetchers.base import FetchReport, NormalizedItem
from finpulse_models import SOURCE_DEFS

if TYPE_CHECKING:
    from finpulse_task_manager import FinpulseTaskManager

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _resolve_enabled_sources(
    tm: FinpulseTaskManager, *, include: list[str] | None = None
) -> list[str]:
    """Read enabled-source flags from config, intersected with ``include``.

    ``include == None`` → every registered source whose
    ``config['source.{id}.enabled']`` is ``"true"`` is returned.
    Passing ``include`` restricts the run to the named subset (still
    requiring the enabled flag).
    """
    cfg = await tm.get_all_config()
    sources: list[str] = []
    universe = include if include else list(SOURCE_REGISTRY.keys())
    for source_id in universe:
        if source_id not in SOURCE_REGISTRY:
            continue
        if cfg.get(f"source.{source_id}.enabled", "false") != "true":
            continue
        sources.append(source_id)
    return sources


async def _fetch_one(
    source_id: str,
    *,
    cfg: dict[str, str],
    timeout_sec: float,
    since: datetime | None,
) -> FetchReport:
    """Run a single fetcher and wrap its outcome in :class:`FetchReport`.

    Exceptions never escape — they are classified via
    :func:`finpulse_errors.map_exception` and surface as ``error_kind``
    on the report so the pipeline can write ``config['source.{id}.last_error']``.
    """
    t0 = time.perf_counter()
    fetcher = get_fetcher(source_id, config=cfg)
    if fetcher is None:
        return FetchReport(
            source_id=source_id,
            error=f"fetcher not available: {source_id}",
            error_kind="dependency",
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
    fetcher._timeout_sec = float(timeout_sec)  # type: ignore[attr-defined]
    try:
        if fetcher.supports_since:
            items = await fetcher.fetch(since=since)
        else:
            items = await fetcher.fetch()
        return FetchReport(
            source_id=source_id,
            items=list(items or []),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
    except Exception as exc:  # noqa: BLE001 — intentional pipeline boundary
        kind, msg, _hints = map_exception(exc)
        logger.warning("fetcher %s failed: %s (%s)", source_id, msg, kind)
        return FetchReport(
            source_id=source_id,
            error=msg,
            error_kind=kind,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )


async def _persist_items(
    tm: FinpulseTaskManager, items: list[NormalizedItem]
) -> tuple[int, int]:
    """Insert-or-update every item; return ``(inserted, updated)`` counts."""
    inserted = 0
    updated = 0
    now = _utcnow_iso()
    for item in items:
        if not item.title or not item.url:
            continue
        try:
            _aid, is_new = await tm.upsert_article(
                source_id=item.source_id,
                url=item.url,
                url_hash=item.url_hash(),
                title=item.title,
                fetched_at=now,
                summary=item.summary,
                content=item.content,
                published_at=item.published_at,
                raw=item.extra,
            )
        except Exception as exc:  # noqa: BLE001 — defensive per-row isolation
            logger.warning("upsert article failed for %s: %s", item.url, exc)
            continue
        if is_new:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


async def ingest(
    tm: FinpulseTaskManager,
    *,
    sources: list[str] | None = None,
    since_hours: int | None = 24,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Fan-out to every enabled source, dedupe into ``articles``, update
    ``last_ok`` / ``last_error`` config keys, and return a per-source
    summary suitable for the ``tasks.result_json`` payload.
    """
    cfg = await tm.get_all_config()
    enabled = await _resolve_enabled_sources(tm, include=sources)
    if not enabled:
        return {"ok": False, "reason": "no_sources_enabled", "by_source": {}}

    since: datetime | None = None
    if since_hours:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        since = datetime.fromtimestamp(
            now.timestamp() - int(since_hours) * 3600, tz=timezone.utc
        )

    timeout_sec = float(cfg.get("fetch_timeout_sec", "15") or "15")
    try:
        concurrency = int(cfg.get("fetch_concurrency", "4") or "4")
    except ValueError:
        concurrency = 4
    concurrency = max(1, min(concurrency, 16))

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(source_id: str) -> FetchReport:
        async with sem:
            return await _fetch_one(
                source_id, cfg=cfg, timeout_sec=timeout_sec, since=since
            )

    reports = await asyncio.gather(*[_guarded(sid) for sid in enabled])

    summary: dict[str, Any] = {
        "ok": True,
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None,
        "by_source": {},
        "totals": {"fetched": 0, "inserted": 0, "updated": 0, "failed_sources": 0},
    }
    updates: dict[str, str] = {}

    for report in reports:
        entry: dict[str, Any] = {
            "fetched": len(report.items),
            "duration_ms": round(report.duration_ms, 2),
        }
        if report.error:
            entry["error_kind"] = report.error_kind
            entry["error"] = report.error
            updates[f"source.{report.source_id}.last_error"] = (
                f"{_utcnow_iso()}: {report.error_kind}: {report.error}"
            )
            summary["totals"]["failed_sources"] += 1
        else:
            # Only persist items + clear last_error if the fetch succeeded.
            inserted, updated = await _persist_items(tm, report.items)
            entry["inserted"] = inserted
            entry["updated"] = updated
            summary["totals"]["inserted"] += inserted
            summary["totals"]["updated"] += updated
            updates[f"source.{report.source_id}.last_ok"] = _utcnow_iso()
            updates[f"source.{report.source_id}.last_error"] = ""
        summary["totals"]["fetched"] += entry["fetched"]
        summary["by_source"][report.source_id] = entry

    if updates:
        await tm.set_configs(updates)

    if task_id is not None:
        await tm.update_task_safe(
            task_id,
            status="succeeded",
            progress=1.0,
            result=summary,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return summary


class FinpulsePipeline:
    """Thin wrapper that bundles the pipeline entry points for
    ``plugin.py`` to call. Keeps the plugin module free of direct
    function-import clutter.
    """

    def __init__(self, tm: FinpulseTaskManager, api: Any) -> None:
        self._tm = tm
        self._api = api

    async def ingest(
        self,
        *,
        sources: list[str] | None = None,
        since_hours: int | None = 24,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return await ingest(
            self._tm, sources=sources, since_hours=since_hours, task_id=task_id
        )


__all__ = [
    "FinpulsePipeline",
    "FetchReport",
    "ingest",
]
