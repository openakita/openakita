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
import hashlib
import html
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from finpulse_errors import map_exception
from finpulse_fetchers import SOURCE_REGISTRY, get_fetcher
from finpulse_fetchers.base import FetchReport, NormalizedItem
from finpulse_frequency import FrequencyMatcher, compile_matcher
from finpulse_models import (
    SESSIONS,
    SOURCE_DEFS,
    get_max_age_hours,
    get_source_fetcher_id,
)
from finpulse_report import build_daily_brief

if TYPE_CHECKING:
    from finpulse_dispatch import DispatchService
    from finpulse_task_manager import FinpulseTaskManager

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _newsnow_rate_limit_remaining(cfg: dict[str, str]) -> float:
    """Return seconds the caller must wait before NewsNow can refresh.

    Returns ``0.0`` (or negative) when the floor has elapsed. The floor is
    only enforced for the public upstream aggregator — self-hosted
    instances are fair game.

    Uses ``newsnow.min_interval_s`` (default 300) and
    ``newsnow.last_fetch_ts`` (unix seconds). A non-positive floor
    disables the guard entirely so advanced users can opt out.
    """
    if (cfg.get("newsnow.mode") or "off") != "public":
        return 0.0
    try:
        floor = float(cfg.get("newsnow.min_interval_s") or "300")
    except ValueError:
        floor = 300.0
    if floor <= 0:
        return 0.0
    try:
        last = float(cfg.get("newsnow.last_fetch_ts") or "0")
    except ValueError:
        last = 0.0
    if last <= 0:
        return 0.0
    elapsed = time.time() - last
    return max(0.0, floor - elapsed)


async def _resolve_enabled_sources(
    tm: FinpulseTaskManager, *, include: list[str] | None = None
) -> list[str]:
    """Return source IDs for enabled direct/rss fetchers.

    NewsNow-backed sources (``kind=="newsnow"`` in SOURCE_DEFS) are NOT
    returned here — they are handled internally by :class:`NewsNowFetcher`.
    This function only returns sources that have an entry in
    :data:`SOURCE_REGISTRY` (direct + rss + the unified ``newsnow``
    aggregator entry).
    """
    cfg = await tm.get_all_config()
    sources: list[str] = []
    universe = include if include else list(SOURCE_REGISTRY.keys())
    for source_id in universe:
        if source_id == "newsnow":
            if _has_any_newsnow_source_enabled(cfg) and source_id not in sources:
                sources.append(source_id)
            continue
        fetcher_id = get_source_fetcher_id(source_id)
        if fetcher_id not in SOURCE_REGISTRY:
            continue
        enabled_val = cfg.get(f"source.{source_id}.enabled", "")
        if enabled_val == "":
            defn = SOURCE_DEFS.get(source_id, {})
            if not defn.get("default_enabled"):
                continue
        elif enabled_val.lower() != "true":
            continue
        if fetcher_id not in sources:
            sources.append(fetcher_id)
    return sources


def _has_any_newsnow_source_enabled(cfg: dict[str, str]) -> bool:
    """Check if at least one ``kind=newsnow`` source is enabled."""
    for sid, defn in SOURCE_DEFS.items():
        if defn.get("kind") != "newsnow":
            continue
        val = cfg.get(f"source.{sid}.enabled", "")
        if val == "":
            if defn.get("default_enabled"):
                return True
        elif val.lower() == "true":
            return True
    return False


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
        # Hybrid CN fetchers record which transport actually served the
        # rows via ``_last_via``. Stash it on the report so the Today
        # tab drawer can render a NewsNow / Direct badge per source.
        via_raw = getattr(fetcher, "_last_via", None)
        via = via_raw if isinstance(via_raw, str) and via_raw else "direct"
        via_reason_raw = getattr(fetcher, "_last_via_reason", None)
        via_reason = (
            via_reason_raw
            if isinstance(via_reason_raw, str) and via_reason_raw
            else None
        )
        return FetchReport(
            source_id=source_id,
            items=list(items or []),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            via=via,
            via_reason=via_reason,
            channel_reports=list(getattr(fetcher, "_channel_reports", []) or []),
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
        summary_empty: dict[str, Any] = {
            "ok": False,
            "reason": "no_sources_enabled",
            "by_source": {},
            "totals": {
                "fetched": 0,
                "inserted": 0,
                "updated": 0,
                "failed_sources": 0,
                "sources_total": 0,
                "sources_ok": 0,
            },
        }
        if task_id is not None:
            await tm.update_task_safe(
                task_id,
                status="skipped",
                progress=1.0,
                result=summary_empty,
                completed_at=time.time(),
                finished_at=_utcnow_iso(),
            )
        return summary_empty

    # If any NewsNow-backed source is enabled, ensure the aggregator
    # mode is active for this run. The NewsNowFetcher reads its channels
    # from SOURCE_DEFS at runtime, so we only need to guarantee the mode
    # and URL are set. This does NOT persist to config.
    explicit_sources = sources is not None
    explicit_newsnow_sources = [
        sid for sid in (sources or [])
        if sid != "newsnow" and SOURCE_DEFS.get(sid, {}).get("kind") == "newsnow"
    ]
    if explicit_newsnow_sources:
        cfg["_newsnow.only_sources"] = ",".join(explicit_newsnow_sources)
    requested_newsnow = "newsnow" in enabled
    if requested_newsnow or (not explicit_sources and _has_any_newsnow_source_enabled(cfg)):
        mode = (cfg.get("newsnow.mode") or "off").strip().lower()
        if mode == "off":
            cfg["newsnow.mode"] = "public"
        if not (cfg.get("newsnow.api_url") or "").strip():
            from finpulse_fetchers.newsnow_base import DEFAULT_NEWSNOW_URL

            cfg["newsnow.api_url"] = DEFAULT_NEWSNOW_URL
        if "newsnow" not in enabled:
            enabled.append("newsnow")

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

    # NewsNow rate-limit: quietly drop the newsnow source from this run
    # when the caller is still within the public-aggregator cooldown so
    # we never hammer the volunteer-run upstream node. Every other source
    # continues unaffected, and the returned summary surfaces the skip
    # reason so the Settings UI can render a countdown.
    newsnow_skip_remaining = 0.0
    if "newsnow" in enabled:
        newsnow_skip_remaining = _newsnow_rate_limit_remaining(cfg)
        if newsnow_skip_remaining > 0:
            enabled = [sid for sid in enabled if sid != "newsnow"]

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
        "totals": {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "failed_sources": 0,
            "sources_total": len(reports),
            "sources_ok": 0,
        },
    }
    updates: dict[str, str] = {}
    # Track whether any hybrid fetcher actually used NewsNow, so we can
    # refresh the shared ``newsnow.last_fetch_ts`` without needing the
    # standalone newsnow aggregator source to be enabled.
    newsnow_public_hit = False

    for report in reports:
        if report.source_id == "newsnow" and report.channel_reports and not report.error:
            summary["totals"]["sources_total"] += len(report.channel_reports) - 1
            for channel in report.channel_reports:
                sid = str(channel.get("source_id") or "")
                if not sid:
                    continue
                channel_items = [item for item in report.items if item.source_id == sid]
                channel_error = channel.get("error")
                entry = {
                    "fetched": len(channel_items),
                    "duration_ms": round(report.duration_ms, 2),
                    "via": "newsnow",
                }
                if channel_error:
                    entry["error_kind"] = str(channel_error)
                    entry["error"] = str(channel_error)
                    updates[f"source.{sid}.last_error"] = (
                        f"{_utcnow_iso()}: newsnow: {channel_error}"
                    )
                    summary["totals"]["failed_sources"] += 1
                else:
                    inserted, updated = await _persist_items(tm, channel_items)
                    entry["inserted"] = inserted
                    entry["updated"] = updated
                    summary["totals"]["inserted"] += inserted
                    summary["totals"]["updated"] += updated
                    summary["totals"]["sources_ok"] += 1
                    updates[f"source.{sid}.last_ok"] = _utcnow_iso()
                    updates[f"source.{sid}.last_error"] = ""
                    newsnow_public_hit = True
                summary["totals"]["fetched"] += entry["fetched"]
                summary["by_source"][sid] = entry
            continue

        # Hybrid CN fetchers stash their transport on the report so we
        # can surface a NewsNow / Direct / None badge in the UI drawer.
        via = report.via or "direct"
        entry: dict[str, Any] = {
            "fetched": len(report.items),
            "duration_ms": round(report.duration_ms, 2),
            "via": via,
        }
        if report.via_reason:
            entry["via_reason"] = report.via_reason
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
            summary["totals"]["sources_ok"] += 1
            updates[f"source.{report.source_id}.last_ok"] = _utcnow_iso()
            updates[f"source.{report.source_id}.last_error"] = ""
            if via == "newsnow":
                newsnow_public_hit = True
        summary["totals"]["fetched"] += entry["fetched"]
        summary["by_source"][report.source_id] = entry
        # Persist a fresh ``newsnow.last_fetch_ts`` after a successful
        # upstream call so the 5-minute floor is measured from the last
        # OK (not the last attempt). Self-hosted mode is exempt from the
        # throttle entirely.
        if (
            report.source_id == "newsnow"
            and not report.error
            and (cfg.get("newsnow.mode") or "off") == "public"
        ):
            updates["newsnow.last_fetch_ts"] = str(int(time.time()))

    # If any CN fetcher hit the public aggregator, bump the cooldown
    # clock too — the volunteer-run node sees all of those calls.
    if newsnow_public_hit and (cfg.get("newsnow.mode") or "off") == "public":
        updates["newsnow.last_fetch_ts"] = str(int(time.time()))

    if newsnow_skip_remaining > 0:
        summary["by_source"]["newsnow"] = {
            "fetched": 0,
            "duration_ms": 0.0,
            "error_kind": "rate_limited",
            "error": (
                "newsnow public aggregator cooldown in effect; "
                f"{int(newsnow_skip_remaining)}s remaining"
            ),
            "retry_after_s": int(newsnow_skip_remaining),
        }

    stale_cutoffs: dict[str, str] = {}
    now_ts = datetime.now(timezone.utc)
    for sid in SOURCE_DEFS:
        max_h = get_max_age_hours(sid)
        cutoff = datetime.fromtimestamp(
            now_ts.timestamp() - max_h * 3600, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_cutoffs[sid] = cutoff
    stale_count = await tm.mark_stale_articles(stale_cutoffs)
    if stale_count:
        summary["totals"]["marked_stale"] = stale_count

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


async def run_daily_brief(
    tm: FinpulseTaskManager,
    *,
    session: str,
    since_hours: int = 12,
    top_k: int = 20,
    lang: str = "zh",
    task_id: str | None = None,
    title: str | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a daily-brief digest and persist it to the ``digests`` table.

    This deliberately sits above :func:`ingest` — callers are expected
    to have already ingested fresh articles before triggering a digest,
    either via the ``pipeline_ingest`` task or the scheduled hook. The
    function only reads ``articles`` and writes the rendered blob into
    ``digests``; it does **not** dispatch notifications (that is Phase
    4b's job).

    ``session`` must be one of :data:`finpulse_models.SESSIONS`. The
    window defaults to 12h back — morning/noon/evening cadences each
    look back through the previous session's tail.
    """
    if session not in SESSIONS and not session.startswith("custom"):
        raise ValueError(f"invalid session {session!r}, expected one of {SESSIONS}")

    top_k = max(1, min(int(top_k), 60))
    since_hours = max(1, min(int(since_hours), 72))
    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(
        now.timestamp() - since_hours * 3600, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows, total = await tm.list_articles(
        since=since,
        sort="score",
        limit=500 if source_ids else max(top_k * 3, 60),
        offset=0,
    )
    if source_ids:
        allowed_sources = {str(s) for s in source_ids if str(s).strip()}
        rows = [row for row in rows if str(row.get("source_id") or "") in allowed_sources]
        total = len(rows)
    generated_at = _utcnow_iso()
    markdown, html_blob, stats = build_daily_brief(
        rows,
        session=session,
        top_k=top_k,
        lang=lang,
        generated_at=generated_at,
        title=title,
    )

    digest_id = await tm.create_digest(
        session=session,
        generated_at=generated_at,
        title=title,
        markdown_blob=markdown,
        html_blob=html_blob,
        stats=stats.as_dict(),
        task_id=task_id,
    )

    result: dict[str, Any] = {
        "ok": True,
        "digest_id": digest_id,
        "session": session,
        "generated_at": generated_at,
        "stats": stats.as_dict(),
        "window": {"since_hours": since_hours, "scanned_total": total},
        "source_ids": source_ids or [],
    }

    if task_id is not None:
        await tm.update_task_safe(
            task_id,
            status="succeeded",
            progress=1.0,
            result=result,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return result


async def evaluate_radar(
    tm: FinpulseTaskManager,
    *,
    rules_text: str,
    since_hours: int = 24,
    limit: int = 100,
    min_score: float | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Compile ``rules_text`` into a :class:`FrequencyMatcher` and run
    it over recent articles. Purely read-only — used by the Radar tab's
    preview and by :func:`run_hot_radar` below.

    Returns a dict with ``hits`` (articles that matched) and ``meta``
    (compiled rule counts / window) so the UI can render a rule-parse
    error banner without falling through to 500.
    """
    try:
        matcher = compile_matcher(rules_text or "")
    except Exception as exc:  # noqa: BLE001 — DSL error boundary
        logger.warning("radar rule compile failed: %s", exc)
        return {
            "ok": False,
            "error": "rule_compile_failed",
            "error_detail": str(exc),
            "hits": [],
            "meta": {"groups": 0, "filters": 0},
        }

    since_hours = max(1, min(int(since_hours), 168))
    limit = max(1, min(int(limit), 500))
    now = datetime.now(timezone.utc)
    since = datetime.fromtimestamp(
        now.timestamp() - since_hours * 3600, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows, _total = await tm.list_articles(
        since=since,
        min_score=min_score,
        sort="time",
        limit=500 if source_ids else limit,
        offset=0,
    )
    allowed_sources = {str(s) for s in (source_ids or []) if str(s).strip()}
    if allowed_sources:
        rows = [row for row in rows if str(row.get("source_id") or "") in allowed_sources]
        rows = rows[:limit]

    hits: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("title") or ""
        summary = row.get("summary") or ""
        src_def = SOURCE_DEFS.get(str(row.get("source_id") or ""), {})
        source_name = str(
            src_def.get("display_zh")
            or src_def.get("display_en")
            or row.get("source_id")
            or ""
        )
        source_id = str(row.get("source_id") or "")
        match_text = "\n".join([title, summary, source_name, source_id, _radar_source_terms(source_id)])
        if not matcher.match(match_text):
            continue
        terms = matcher.matched_terms(match_text)
        hits.append(
            {
                "id": row.get("id"),
                "source_id": row.get("source_id"),
                "content_type": src_def.get("content_type", "news"),
                "title": title,
                "summary": summary,
                "url": row.get("url"),
                "fetched_at": row.get("fetched_at"),
                "published_at": row.get("published_at"),
                "ai_score": row.get("ai_score"),
                "matched_terms": terms,
            }
        )

    return {
        "ok": True,
        "hits": hits,
        "meta": {
            "groups": len(matcher.rules.groups),
            "filters": len(matcher.rules.filter_words),
            "global_filters": len(matcher.rules.global_filters),
            "window_hours": since_hours,
            "scanned": len(rows),
            "matched": len(hits),
            "source_ids": list(allowed_sources),
        },
    }


def _radar_markdown(
    *, header: str | None, hits: list[dict[str, Any]], limit: int = 20
) -> str:
    """Render radar hits as the compact markdown the dispatcher will
    push over IM. Truncates above ``limit`` so a runaway rule doesn't
    silently spam a 200-line payload — the iframe in the UI still
    shows the full list.
    """
    lines: list[str] = []
    if header:
        lines.append(header.rstrip())
        lines.append("")
    trimmed = list(hits[:limit])
    for i, hit in enumerate(trimmed, start=1):
        title = (hit.get("title") or "").strip()
        url = (hit.get("url") or "").strip()
        src = (hit.get("source_id") or "").strip()
        score = hit.get("ai_score")
        terms = hit.get("matched_terms") or []
        score_suffix = (
            f" · score {float(score):.1f}"
            if isinstance(score, (int, float))
            else ""
        )
        term_suffix = f" · {' '.join(f'[{t}]' for t in terms[:4])}" if terms else ""
        if url:
            lines.append(f"{i}. [{title}]({url}) · {src}{score_suffix}{term_suffix}")
        else:
            lines.append(f"{i}. {title} · {src}{score_suffix}{term_suffix}")
    if len(hits) > limit:
        lines.append("")
        lines.append(f"… +{len(hits) - limit} more")
    return "\n".join(lines).rstrip() + "\n"


def _radar_html(*, header: str | None, hits: list[dict[str, Any]], limit: int = 80) -> str:
    title = html.escape(header or "fin-pulse 热点雷达")
    cards: list[str] = []
    for i, hit in enumerate(hits[:limit], start=1):
        h_title = html.escape(str(hit.get("title") or ""))
        url = html.escape(str(hit.get("url") or ""))
        src = html.escape(str(hit.get("source_id") or ""))
        terms = " ".join(
            f"<span>{html.escape(str(t))}</span>"
            for t in (hit.get("matched_terms") or [])[:6]
        )
        link = f'<a href="{url}">{h_title}</a>' if url else h_title
        cards.append(
            f"<article><b>{i}.</b> {link}"
            f"<p>{src} · {html.escape(str(hit.get('published_at') or hit.get('fetched_at') or ''))}</p>"
            f"<div>{terms}</div></article>"
        )
    body = "\n".join(cards) or "<p>暂无命中资讯</p>"
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#fff7f7;color:#221316}}
main{{max-width:880px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 8px;color:#b91c1c}}
.meta{{color:#7a4e59;margin-bottom:18px}}
article{{background:#fff;border:2px solid #f1c8cc;border-radius:14px;padding:14px 16px;margin:10px 0;box-shadow:0 6px 18px rgba(185,28,28,.06)}}
a{{color:#991b1b;text-decoration:none;font-weight:700}}
p{{color:#7a4e59;font-size:12px;margin:6px 0}}
span{{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:999px;background:#fee2e2;color:#991b1b;font-size:12px}}
</style></head><body><main>
<h1>{title}</h1><div class="meta">命中 {len(hits)} 条 · Generated by Fin Pulse</div>
{body}
</main></body></html>"""


def _radar_source_terms(source_id: str) -> str:
    """Extra source-level terms so broad rules like ``+科技`` can match
    tech/innovation feeds even when the individual headline omits the
    category word.
    """

    sid = source_id.lower()
    terms: list[str] = []
    if sid.startswith("36kr") or sid in {"ithome"}:
        terms.extend(["科技", "互联网", "创投", "数码", "IT"])
    if sid in {"zhihu-hot", "bilibili", "toutiao-hot", "weibo-hot", "baidu-hot"}:
        terms.extend(["热榜", "热点", "社交"])
    return " ".join(dict.fromkeys(terms))


async def run_hot_radar(
    tm: FinpulseTaskManager,
    dispatch: DispatchService,
    *,
    rules_text: str,
    targets: list[dict[str, str]],
    since_hours: int = 24,
    limit: int = 100,
    min_score: float | None = None,
    title: str | None = None,
    cooldown_s: float = 600.0,
    task_id: str | None = None,
    dedupe_by_content: bool = True,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate the rules and fan matching titles out to every target
    using :class:`DispatchService`. Cooldown keys include the radar key
    so the same ruleset can't re-fire within ``cooldown_s`` seconds.

    When no hits are found the call returns early without touching the
    dispatcher — this keeps quiet days truly quiet (important for
    chat rooms with multiple plugin tenants).
    """
    eval_result = await evaluate_radar(
        tm,
        rules_text=rules_text,
        since_hours=since_hours,
        limit=limit,
        min_score=min_score,
        source_ids=source_ids,
    )
    hits = eval_result.get("hits", [])

    dispatch_results: list[dict[str, Any]] = []
    if eval_result.get("ok") and hits:
        header = title or "📡 fin-pulse 热点雷达"
        md = _radar_markdown(header=header, hits=hits, limit=20)
        html_blob = _radar_html(header=header, hits=hits, limit=80)
        # Cooldown key derives from the header + hit set so identical
        # firings dedupe but a fresh batch of hits gets through. The
        # key is suffixed with ``channel:chat_id`` in the loop below so
        # fanning to multiple targets never self-cancels.
        key_basis = (header + "\n" + "|".join(str(h.get("id") or "") for h in hits)).encode(
            "utf-8"
        )
        base_key = "radar:" + hashlib.sha256(key_basis).hexdigest()[:8]
        for tgt in targets:
            channel = str(tgt.get("channel") or "").strip()
            chat_id = str(tgt.get("chat_id") or "").strip()
            if not channel or not chat_id:
                dispatch_results.append(
                    {
                        "ok": False,
                        "channel": channel,
                        "chat_id": chat_id,
                        "sent_chunks": 0,
                        "skipped": None,
                        "errors": ["missing_target"],
                    }
                )
                continue
            outcome = await dispatch.send(
                channel=channel,
                chat_id=chat_id,
                content=html_blob,
                cooldown_key=f"{base_key}:{channel}:{chat_id}",
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe_by_content,
                content_kind="html",
                file_name="fin-pulse-radar.pdf",
                fallback_text=md,
                header=header,
            )
            dispatch_results.append(outcome.as_dict())

    result: dict[str, Any] = {
        "ok": bool(eval_result.get("ok")),
        "hits": hits,
        "meta": eval_result.get("meta", {}),
        "dispatched": dispatch_results,
    }
    if not eval_result.get("ok"):
        result["error"] = eval_result.get("error")
        result["error_detail"] = eval_result.get("error_detail")

    if task_id is not None:
        status = "succeeded" if result["ok"] else "failed"
        await tm.update_task_safe(
            task_id,
            status=status,
            progress=1.0,
            result=result,
            completed_at=time.time(),
            finished_at=_utcnow_iso(),
        )
    return result


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

    async def run_daily_brief(
        self,
        *,
        session: str,
        since_hours: int = 12,
        top_k: int = 20,
        lang: str = "zh",
        task_id: str | None = None,
        title: str | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await run_daily_brief(
            self._tm,
            session=session,
            since_hours=since_hours,
            top_k=top_k,
            lang=lang,
            task_id=task_id,
            title=title,
            source_ids=source_ids,
        )

    async def evaluate_radar(
        self,
        *,
        rules_text: str,
        since_hours: int = 24,
        limit: int = 100,
        min_score: float | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await evaluate_radar(
            self._tm,
            rules_text=rules_text,
            since_hours=since_hours,
            limit=limit,
            min_score=min_score,
            source_ids=source_ids,
        )

    async def run_hot_radar(
        self,
        dispatch: DispatchService,
        *,
        rules_text: str,
        targets: list[dict[str, str]],
        since_hours: int = 24,
        limit: int = 100,
        min_score: float | None = None,
        title: str | None = None,
        cooldown_s: float = 600.0,
        task_id: str | None = None,
        dedupe_by_content: bool = True,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await run_hot_radar(
            self._tm,
            dispatch,
            rules_text=rules_text,
            targets=targets,
            since_hours=since_hours,
            limit=limit,
            min_score=min_score,
            title=title,
            cooldown_s=cooldown_s,
            task_id=task_id,
            dedupe_by_content=dedupe_by_content,
            source_ids=source_ids,
        )


__all__ = [
    "FinpulsePipeline",
    "FetchReport",
    "evaluate_radar",
    "ingest",
    "run_daily_brief",
    "run_hot_radar",
]
