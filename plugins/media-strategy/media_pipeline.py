# ruff: noqa: N999
"""Task pipeline for RSS ingest, radar, brief, verification and planning."""

from __future__ import annotations

import asyncio
import html as html_lib
from pathlib import Path
from typing import Any

import httpx
from media_ai.analyzer import (
    build_brief,
    build_replicate_plan,
    build_topic_analysis,
    build_verify_pack,
    cluster_topics,
    markdown_to_html,
    score_article,
)
from media_fetchers.html import fetch_and_parse_html
from media_fetchers.rss import UnsafeFeedUrl, fetch_and_parse
from media_task_manager import MediaTaskManager, utcnow_iso


class MediaPipeline:
    def __init__(self, tm: MediaTaskManager, api: Any, *, output_dir: Path) -> None:
        self.tm = tm
        self.api = api
        self.output_dir = Path(output_dir)

    def _brain(self) -> Any:
        try:
            return self.api.get_brain()
        except Exception:
            return None

    async def _package_disabled(self, package_id: str) -> bool:
        if not package_id:
            return False
        packages = await self.tm.list_packages()
        return package_id in packages and not bool(packages[package_id].get("enabled"))

    async def ingest(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = await self.tm.get_settings()
        packages = await self.tm.list_packages()
        enabled_packages = {pid for pid, meta in packages.items() if meta.get("enabled")}
        package_filter = set(params.get("package_ids") or [])
        skipped_disabled = sorted(package_filter - enabled_packages)
        active_packages = (package_filter & enabled_packages) if package_filter else enabled_packages
        sources = await self.tm.list_sources(enabled_only=True)
        if active_packages:
            sources = [
                s for s in sources if set(s.get("package_ids") or []).intersection(active_packages)
            ]
        else:
            sources = []
        timeout = float(params.get("timeout_sec") or settings.get("fetch_timeout_sec") or 15)
        user_agent = str(settings.get("user_agent") or "OpenAkita-MediaStrategy/0.1")
        limit_sources = int(params.get("limit_sources") or 0)
        if limit_sources > 0:
            sources = sources[:limit_sources]

        stats = {
            "sources": len(sources),
            "fetched": 0,
            "inserted": 0,
            "failed": 0,
            "errors": [],
            "skipped_disabled_packages": skipped_disabled,
        }
        for source in sources:
            started = utcnow_iso()
            try:
                source_payload = {**source, "id": source["id"]}
                kind = str(source.get("kind") or "rss").lower()
                if kind == "html":
                    _, items = await fetch_and_parse_html(
                        source_payload,
                        timeout_sec=timeout,
                        user_agent=user_agent,
                    )
                else:
                    _, items = await fetch_and_parse(
                        source_payload,
                        timeout_sec=timeout,
                        user_agent=user_agent,
                    )
                inserted_count = 0
                for item in items:
                    payload = {
                        "source_id": source["id"],
                        "package_ids": source.get("package_ids") or [],
                        "url": item.url,
                        "title": item.title,
                        "summary": item.summary,
                        "author": item.author,
                        "tags": item.tags,
                        "published_at": item.published_at,
                        "fetched_at": utcnow_iso(),
                        "raw": item.raw,
                    }
                    payload.update(score_article(payload, source))
                    _, inserted = await self.tm.upsert_article(payload)
                    inserted_count += 1 if inserted else 0
                finished = utcnow_iso()
                await self.tm.update_source_status(source["id"], status="success", fetched_at=finished)
                await self.tm.insert_crawl_record(
                    source_id=source["id"],
                    status="success",
                    fetched_count=len(items),
                    inserted_count=inserted_count,
                    started_at=started,
                    finished_at=finished,
                )
                stats["fetched"] += len(items)
                stats["inserted"] += inserted_count
            except UnsafeFeedUrl as exc:
                finished = utcnow_iso()
                message = f"invalid_source: {exc}"
                await self.tm.update_source_status(source["id"], status="failed", error=message)
                await self.tm.insert_crawl_record(
                    source_id=source["id"],
                    status="failed",
                    fetched_count=0,
                    inserted_count=0,
                    error_message=message,
                    started_at=started,
                    finished_at=finished,
                )
                stats["failed"] += 1
                stats["errors"].append({"source_id": source["id"], "error": message})
                await asyncio.sleep(0)
            except Exception as exc:  # noqa: BLE001
                finished = utcnow_iso()
                message = _fetch_error_message(exc)
                await self.tm.update_source_status(source["id"], status="failed", error=message)
                await self.tm.insert_crawl_record(
                    source_id=source["id"],
                    status="failed",
                    fetched_count=0,
                    inserted_count=0,
                    error_message=message,
                    started_at=started,
                    finished_at=finished,
                )
                stats["failed"] += 1
                stats["errors"].append({"source_id": source["id"], "error": message})
                await asyncio.sleep(0)
        return stats

    async def hot_radar(self, params: dict[str, Any]) -> dict[str, Any]:
        since_hours = int(params.get("since_hours") or 24)
        package_id = str(params.get("package_id") or params.get("category") or "")
        limit = int(params.get("limit") or 30)
        cluster = bool(params.get("cluster"))
        compact = bool(params.get("compact"))
        if await self._package_disabled(package_id):
            return {
                "items": [],
                "stats": {
                    "total": 0,
                    "since_hours": since_hours,
                    "package_id": package_id,
                    "package_disabled": True,
                    "compact": compact,
                    "cluster": cluster,
                },
            }
        if cluster:
            return await self.top_topics(
                {
                    "since_hours": since_hours,
                    "package_id": package_id,
                    "limit": limit,
                    "min_coverage": int(params.get("min_coverage") or 1),
                    "compact": compact,
                }
            )
        items = await self.tm.recent_articles(
            since_hours=since_hours,
            package_id=package_id,
            limit=limit,
        )
        if compact:
            items = [_compact_article(it) for it in items]
        return {
            "items": items,
            "stats": {
                "total": len(items),
                "since_hours": since_hours,
                "package_id": package_id,
                "compact": compact,
                "cluster": False,
            },
        }

    async def top_topics(self, params: dict[str, Any]) -> dict[str, Any]:
        """图2「选题推荐逻辑」：跨源聚合 + 权威加权，默认输出 Top 5。

        - ``limit`` 默认 5，可由用户自定义（上限 20）。
        - ``min_coverage`` 控制至少要被几家源同时报道才入选，默认 1
          （等于退化为单源排序），常用值 2 表示「至少两家媒体同时报道」。
        - ``compact=True``（默认）：仅返回标题、链接、来源列表与权重分，
          减少 Token 消耗；用户点击链接自行阅读原文。
        """

        since_hours = int(params.get("since_hours") or 24)
        package_id = str(params.get("package_id") or params.get("category") or "")
        raw_limit = int(params.get("limit") or 5)
        limit = max(1, min(raw_limit, 20))
        min_coverage = max(1, int(params.get("min_coverage") or 1))
        compact = params.get("compact")
        compact = True if compact is None else bool(compact)
        if await self._package_disabled(package_id):
            return {
                "items": [],
                "stats": {
                    "total_candidates": 0,
                    "total_clusters": 0,
                    "filtered": 0,
                    "selected": 0,
                    "since_hours": since_hours,
                    "package_id": package_id,
                    "package_disabled": True,
                    "min_coverage": min_coverage,
                    "limit": limit,
                    "compact": compact,
                    "cluster": True,
                },
            }

        # Pull a wider candidate window so cross-source clustering has enough
        # material; bound it to keep DB scans cheap.
        fetch_limit = max(120, limit * 12)
        fetch_limit = min(fetch_limit, 500)
        items = await self.tm.recent_articles(
            since_hours=since_hours,
            package_id=package_id,
            limit=fetch_limit,
        )
        clusters = cluster_topics(items)
        filtered = [c for c in clusters if int(c.get("sources_count") or 1) >= min_coverage]
        selected = filtered[:limit]
        out_items: list[dict[str, Any]]
        if compact:
            out_items = [
                {
                    "title": c["title"],
                    "url": c["url"],
                    "sources": c["source_ids"],
                    "sources_count": c["sources_count"],
                    "weighted_score": c["weighted_score"],
                    "hot_score_max": c["hot_score_max"],
                    "risk_level": c["risk_level"],
                    "published_at": c.get("published_at"),
                    "article_ids": c["article_ids"][:5],
                }
                for c in selected
            ]
        else:
            out_items = selected
        return {
            "items": out_items,
            "stats": {
                "total_candidates": len(items),
                "total_clusters": len(clusters),
                "filtered": len(filtered),
                "selected": len(selected),
                "since_hours": since_hours,
                "package_id": package_id,
                "min_coverage": min_coverage,
                "limit": limit,
                "compact": compact,
                "cluster": True,
            },
        }

    async def search_news(self, params: dict[str, Any]) -> dict[str, Any]:
        items = await self.tm.search_articles(
            q=str(params.get("q") or ""),
            package_id=str(params.get("package_id") or ""),
            limit=int(params.get("limit") or 30),
        )
        return {"items": items, "stats": {"total": len(items)}}

    async def daily_brief(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.tm.update_task(
            task_id,
            progress=0.16,
            pipeline_step="素材筛选中：正在按套餐和时间窗口读取热点",
        )
        radar = await self.hot_radar(params)
        items = radar["items"]
        session = str(params.get("session") or "morning")
        title = f"融媒智策{_session_label(session)}"
        settings = await self.tm.get_settings()
        await self.tm.update_task(
            task_id,
            progress=0.42,
            pipeline_step="大模型分析中：正在生成摘要、风向和采编建议",
        )
        md, source = await build_brief(
            self._brain(),
            items,
            title=title,
            session=session,
            temperature=float(settings.get("llm_temperature") or 0.2),
        )
        await self.tm.update_task(
            task_id,
            progress=0.78,
            pipeline_step="HTML 报表渲染中：正在生成美化预览",
        )
        report = await self._save_report(
            task_id,
            "daily_brief",
            title,
            md,
            {"source": source, "session": session, **radar["stats"]},
        )
        return {"report": report, "items": items, "source": source}

    async def verify_pack(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        items = await self._select_items(params)
        topic = str(params.get("topic") or "")
        settings = await self.tm.get_settings()
        md, source = await build_verify_pack(
            self._brain(),
            items,
            topic=topic,
            temperature=float(settings.get("llm_temperature") or 0.2),
        )
        title = f"{topic or '热点'}信源复核"
        report = await self._save_report(task_id, "verify_pack", title, md, {"source": source})
        return {"report": report, "items": items, "source": source}

    async def ai_topic_analysis(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.tm.update_task(
            task_id,
            progress=0.12,
            pipeline_step="规则聚类中：正在筛选高价值热点",
        )
        limit = max(1, min(int(params.get("limit") or 10), 20))
        evidence_limit = max(1, min(int(params.get("evidence_limit") or 5), 8))
        radar = await self.top_topics(
            {
                "since_hours": int(params.get("since_hours") or 24),
                "package_id": str(params.get("package_id") or ""),
                "limit": limit,
                "min_coverage": int(params.get("min_coverage") or 1),
                "compact": False,
            }
        )
        clusters = list(radar.get("items") or [])

        await self.tm.update_task(
            task_id,
            progress=0.28,
            pipeline_step=f"证据整理中：已选出 {len(clusters)} 个热点簇",
        )
        topics: list[dict[str, Any]] = []
        for cluster in clusters:
            evidence = await self.tm.get_articles_by_ids(
                [str(x) for x in (cluster.get("article_ids") or [])[:evidence_limit]]
            )
            topics.append(
                {
                    "title": cluster.get("title"),
                    "url": cluster.get("url"),
                    "source_ids": cluster.get("source_ids") or [],
                    "sources_count": cluster.get("sources_count"),
                    "weighted_score": cluster.get("weighted_score"),
                    "risk_level": cluster.get("risk_level"),
                    "published_at": cluster.get("published_at"),
                    "article_ids": cluster.get("article_ids") or [],
                    "evidence": [
                        {
                            "id": item.get("id"),
                            "title": item.get("title"),
                            "source_id": item.get("source_id"),
                            "url": item.get("url"),
                            "published_at": item.get("published_at"),
                            "summary": item.get("summary") or item.get("ai_summary"),
                            "risk_level": item.get("risk_level"),
                        }
                        for item in evidence
                    ],
                }
            )

        await self.tm.update_task(
            task_id,
            progress=0.42,
            pipeline_step="大模型分析中：通常需要 30-90 秒，请不要关闭页面",
        )
        settings = await self.tm.get_settings()
        md, source = await build_topic_analysis(
            self._brain(),
            topics,
            temperature=float(settings.get("llm_temperature") or 0.2),
        )
        await self.tm.update_task(
            task_id,
            progress=0.88,
            pipeline_step="报告保存中",
        )
        report = await self._save_report(
            task_id,
            "ai_topic_analysis",
            "AI 选题分析报告",
            md,
            {"source": source, **(radar.get("stats") or {})},
        )
        return {"report": report, "topics": topics, "source": source, "stats": radar.get("stats") or {}}

    async def replicate_plan(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        items = await self._select_items(params)
        topic = str(params.get("topic") or "")
        target_format = str(params.get("target_format") or "short_video")
        tone = str(params.get("tone") or "稳健客观")
        settings = await self.tm.get_settings()
        md, source = await build_replicate_plan(
            self._brain(),
            items,
            topic=topic,
            target_format=target_format,
            tone=tone,
            temperature=float(settings.get("llm_temperature") or 0.2),
        )
        title = f"{topic or '热点'}策研采编计划"
        report = await self._save_report(task_id, "replicate_plan", title, md, {"source": source})
        return {"report": report, "items": items, "source": source}

    async def _select_items(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        article_ids = [str(x) for x in (params.get("article_ids") or []) if str(x).strip()]
        if article_ids:
            return await self.tm.get_articles_by_ids(article_ids)
        q = str(params.get("topic") or params.get("q") or "")
        result = await self.tm.search_articles(q=q, limit=int(params.get("limit") or 8))
        return result

    async def _save_report(
        self,
        task_id: str,
        kind: str,
        title: str,
        markdown: str,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        html = _styled_report_html(title=title, kind=kind, markdown=markdown, meta=meta)
        path = self._write_report_file(kind, title, markdown)
        return await self.tm.save_report(
            task_id=task_id,
            kind=kind,
            title=title,
            markdown=markdown,
            html=html,
            meta=meta,
            path=str(path),
        )

    def _write_report_file(self, kind: str, title: str, markdown: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:64] or kind
        day = utcnow_iso()[:10]
        folder = self.output_dir / day / kind
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe}.md"
        path.write_text(markdown, encoding="utf-8")
        return path


def _session_label(session: str) -> str:
    return {"morning": "早报", "noon": "午报", "evening": "晚报"}.get(session, "简报")


def _styled_report_html(
    *, title: str, kind: str, markdown: str, meta: dict[str, Any] | None = None
) -> str:
    meta = meta or {}
    body = markdown_to_html(markdown)
    source = html_lib.escape(str(meta.get("source") or ""))
    session = html_lib.escape(str(meta.get("session") or ""))
    label = {
        "daily_brief": "融媒简报",
        "verify_pack": "信源复核",
        "ai_topic_analysis": "AI 选题分析",
        "replicate_plan": "策研采编",
    }.get(kind, kind)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_lib.escape(title)}</title>
  <style>
    :root {{ --primary:#0F766E; --soft:#ECFDF5; --text:#0f172a; --muted:#64748b; --border:#dbe4ef; }}
    body {{ margin:0; background:#f8fafc; color:var(--text); font:14px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }}
    .hero {{ padding:34px 38px; background:linear-gradient(135deg,#0F766E,#14B8A6 58%,#F59E0B); color:#fff; }}
    .hero h1 {{ margin:0 0 12px; font-size:30px; letter-spacing:.02em; }}
    .hero p {{ margin:0; opacity:.92; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:18px; }}
    .chip {{ padding:5px 12px; border-radius:999px; background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.28); font-size:12px; }}
    main {{ max-width:980px; margin:0 auto; padding:26px 28px 40px; background:#fff; min-height:70vh; box-shadow:0 18px 60px rgba(15,23,42,.08); }}
    h1 {{ font-size:28px; line-height:1.3; margin:0 0 18px; padding-bottom:12px; border-bottom:1px solid var(--border); }}
    h2 {{ margin:28px 0 12px; padding-left:12px; border-left:4px solid var(--primary); font-size:19px; }}
    h3 {{ margin:20px 0 10px; color:var(--primary); font-size:16px; }}
    p {{ margin:10px 0; }}
    ul,ol {{ padding-left:24px; }}
    li {{ margin:7px 0; }}
    strong {{ color:#0b5f59; }}
    a {{ color:var(--primary); text-decoration:none; border-bottom:1px dotted rgba(15,118,110,.5); }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; margin:16px 0; border:1px solid var(--border); border-radius:12px; overflow:hidden; }}
    th,td {{ padding:10px 12px; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; }}
    th {{ background:var(--soft); color:#0b5f59; }}
    tr:last-child td {{ border-bottom:0; }}
    blockquote {{ margin:16px 0; padding:12px 14px; border-left:4px solid var(--primary); background:var(--soft); border-radius:10px; color:#334155; }}
    code {{ padding:2px 6px; border-radius:6px; background:#f1f5f9; border:1px solid var(--border); }}
    .footer {{ margin-top:34px; color:var(--muted); font-size:12px; border-top:1px dashed var(--border); padding-top:14px; }}
  </style>
</head>
<body>
  <section class="hero">
    <h1>{html_lib.escape(title)}</h1>
    <p>AI 辅助生成的融媒简报，保留来源线索，建议编辑复核后发布。</p>
    <div class="chips"><span class="chip">{html_lib.escape(label)}</span><span class="chip">{session or "综合"}</span><span class="chip">{source or "规则/大模型"}</span></div>
  </section>
  <main>{body}<div class="footer">由 OpenAkita 融媒智策生成 · 请以原文链接和人工复核为准</div></main>
</body>
</html>"""


def _fetch_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        url = str(exc.request.url)
        if status == 404:
            return f"invalid_source: 源地址返回 404，可能已下线或改版：{url}"
        if 400 <= status < 500:
            return f"invalid_source: 源地址返回 {status}，请检查是否改版或限制访问：{url}"
        return f"network: 源站返回 {status}：{url}"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return f"timeout: 源站响应超时：{exc}"
    return f"network: {exc}"


_COMPACT_KEYS: tuple[str, ...] = (
    "id",
    "source_id",
    "package_ids",
    "title",
    "url",
    "hot_score",
    "risk_level",
    "published_at",
    "fetched_at",
)


def _compact_article(article: dict[str, Any]) -> dict[str, Any]:
    return {key: article.get(key) for key in _COMPACT_KEYS if key in article}
