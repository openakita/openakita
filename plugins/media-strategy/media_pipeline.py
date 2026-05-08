# ruff: noqa: N999
"""Task pipeline for RSS ingest, radar, brief, verification and planning."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from media_ai.analyzer import (
    build_brief,
    build_replicate_plan,
    build_verify_pack,
    markdown_to_html,
    score_article,
)
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

    async def ingest(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = await self.tm.get_settings()
        packages = await self.tm.list_packages()
        enabled_packages = {pid for pid, meta in packages.items() if meta.get("enabled")}
        package_filter = set(params.get("package_ids") or [])
        active_packages = package_filter if package_filter else enabled_packages
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

        stats = {"sources": len(sources), "fetched": 0, "inserted": 0, "failed": 0, "errors": []}
        for source in sources:
            started = utcnow_iso()
            try:
                source_payload = {**source, "id": source["id"]}
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
                message = f"network: {exc}"
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
        items = await self.tm.recent_articles(
            since_hours=since_hours,
            package_id=package_id,
            limit=limit,
        )
        return {
            "items": items,
            "stats": {
                "total": len(items),
                "since_hours": since_hours,
                "package_id": package_id,
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
        radar = await self.hot_radar(params)
        items = radar["items"]
        session = str(params.get("session") or "morning")
        title = f"融媒智策{_session_label(session)}"
        settings = await self.tm.get_settings()
        md, source = await build_brief(
            self._brain(),
            items,
            title=title,
            session=session,
            temperature=float(settings.get("llm_temperature") or 0.2),
        )
        report = await self._save_report(task_id, "daily_brief", title, md, {"source": source, **radar["stats"]})
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
        html = markdown_to_html(markdown)
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
