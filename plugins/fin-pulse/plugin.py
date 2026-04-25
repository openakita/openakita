# ruff: noqa: N999
"""fin-pulse (财经脉动) — finance news radar plugin entry.

Three canonical modes — ``daily_brief`` / ``hot_radar`` / ``ask_news`` —
are surfaced over a FastAPI router and a small set of agent tools
registered with the host Brain. Data sources (Phase 2), AI filter
(Phase 3), daily-brief rendering and host-gateway dispatch (Phase 4),
and agent-tools dispatch (Phase 5) are layered on this skeleton without
breaking the initial minimal-loadable contract:

* ``on_load`` registers the router + tool definitions, spawns an async
  bootstrap task for the SQLite schema, and logs a single status line so
  the host plugin-status panel ticks green immediately.
* ``on_unload`` cancels the bootstrap task and closes the task manager
  connection.

The skeleton is deliberately kept import-safe: modules that arrive in
later Phases are imported lazily inside ``on_load`` so the earliest
Phase-1a commit stays loadable even before Phase 1b lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import HTMLResponse

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "fin-pulse"
PLUGIN_VERSION = "1.0.0"


# V1.0 canonical mode identifiers; mirrored in finpulse_models.MODES from
# Phase 1b onwards. The skeleton keeps an inline fallback so /modes
# responds even before the models module lands.
_FALLBACK_MODES: dict[str, dict[str, Any]] = {
    "daily_brief": {
        "display_zh": "早午晚报",
        "display_en": "Daily Brief",
        "sessions": ("morning", "noon", "evening"),
    },
    "hot_radar": {
        "display_zh": "热点雷达",
        "display_en": "Hot Radar",
    },
    "ask_news": {
        "display_zh": "Agent 问询",
        "display_en": "Ask News",
    },
}


class Plugin(PluginBase):
    """fin-pulse plugin entry — see module docstring for lifecycle shape."""

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_load(self, api: PluginAPI) -> None:
        # Step 1 — cache handle + per-plugin data dir.
        self._api = api
        host_data_dir = api.get_data_dir()
        if host_data_dir is None:
            api.log(
                "data.own permission missing — fin-pulse will run in "
                "degraded read-only mode; task manager will not open.",
                "error",
            )
            self._data_dir: Path | None = None
        else:
            self._data_dir = Path(host_data_dir) / "fin_pulse"
            self._data_dir.mkdir(parents=True, exist_ok=True)

        # Step 2 — task manager + pipeline (Phase 1b / 2a). Imported lazily
        # so a missing module in a half-applied branch still yields a
        # degraded-but-loadable plugin.
        self._tm: Any | None = None
        self._pipeline: Any | None = None
        self._dispatch: Any | None = None
        self._init_task: asyncio.Task | None = None
        if self._data_dir is not None:
            try:
                from finpulse_task_manager import FinpulseTaskManager  # type: ignore

                self._tm = FinpulseTaskManager(self._data_dir / "finpulse.sqlite")
            except ImportError:
                api.log(
                    "finpulse_task_manager not yet available — skeleton "
                    "skipping DB bootstrap until Phase 1b lands.",
                    "debug",
                )
                self._tm = None
            if self._tm is not None:
                try:
                    from finpulse_pipeline import FinpulsePipeline  # type: ignore

                    self._pipeline = FinpulsePipeline(self._tm, api)
                except ImportError:
                    api.log(
                        "finpulse_pipeline not yet available — ingest "
                        "routes will return 503 until Phase 2a lands.",
                        "debug",
                    )
                    self._pipeline = None
        # Dispatch service (Phase 4b) — pure over-the-gateway wrapper,
        # works even when the task manager is None so /dispatch/send can
        # still probe an IM adapter from the plugin UI for smoke tests.
        try:
            from finpulse_dispatch import DispatchService  # type: ignore

            self._dispatch = DispatchService(api)
        except ImportError:
            api.log(
                "finpulse_dispatch not yet available — hot_radar push "
                "routes will return 503 until Phase 4b lands.",
                "debug",
            )
            self._dispatch = None

        # Phase 4c — on_schedule hook binding. The match filter keeps us
        # from being woken up by other plugins' schedules; once bound,
        # the host Scheduler will call us every time a cron/once/interval
        # trigger fires for a ``fin-pulse:`` task. We register only when
        # the pipeline is ready so the hook never touches a None.
        self._hook_registered = False
        if self._pipeline is not None:
            try:
                api.register_hook(
                    "on_schedule",
                    self._on_schedule,
                    match=_is_finpulse_schedule,
                )
                self._hook_registered = True
            except Exception as exc:  # noqa: BLE001 — defensive boundary
                api.log(
                    f"register_hook(on_schedule) failed — scheduled digests "
                    f"will not fire automatically: {exc}",
                    "warning",
                )

        # Step 3 — FastAPI router (21 routes eventually; the skeleton
        # registers the read-only /health, /modes and /config endpoints
        # so the loader contract is satisfied immediately).
        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        # Step 4 — register agent tools (7 tools — see plugin.json
        # provides.tools). The handler routes into the query service
        # from Phase 5; Phase 1a stubs it with a ``not_implemented``
        # envelope so the host never sees a hard exception.
        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        # Step 5 — async bootstrap (SQLite schema seeding). Silent no-op
        # when Phase 1b has not landed yet.
        if self._tm is not None:
            self._init_task = api.spawn_task(
                self._async_init(), name=f"{PLUGIN_ID}:init"
            )

        # Step 6 — log so the host status panel ticks green immediately.
        api.log(
            f"fin-pulse plugin loaded (v{PLUGIN_VERSION}, "
            f"{len(self._tool_definitions())} tools)"
        )

    async def _async_init(self) -> None:
        try:
            if self._tm is not None:
                await self._tm.init()
        except Exception as exc:  # noqa: BLE001 — top-level bootstrap
            logger.error("fin-pulse task manager init failed: %s", exc)
            raise

    async def on_unload(self) -> None:
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("fin-pulse init task drain error: %s", exc)
        if self._tm is not None:
            try:
                await self._tm.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("fin-pulse task manager close error: %s", exc)

    # ── Schedule hook (Phase 4c) ────────────────────────────────────

    async def _on_schedule(self, **kwargs: Any) -> dict[str, Any]:
        """Called by the host Scheduler every time a ``fin-pulse:`` task
        fires. ``task.prompt`` carries a JSON-encoded mode payload of
        the shape::

            [fin-pulse] {"mode":"daily_brief","session":"morning",
                         "channel":"feishu","chat_id":"oc_xxx"}

        For daily-brief mode we render through :meth:`run_daily_brief`
        then push the HTML blob via :class:`DispatchService`. Hot-radar
        mode skips the render step and calls :meth:`run_hot_radar`
        directly with the stored rule text.

        We never raise — the scheduler suppresses the task on the
        third consecutive failure so silent-downgrade with a log entry
        is the safest default.
        """
        task = kwargs.get("task")
        execution = kwargs.get("execution")
        if task is None or self._pipeline is None:
            return {"ok": False, "reason": "pipeline_unavailable"}
        try:
            payload = _parse_schedule_prompt(getattr(task, "prompt", "") or "")
        except ValueError as exc:
            logger.warning(
                "fin-pulse: schedule %s prompt parse failed: %s", task.id, exc
            )
            return {"ok": False, "reason": "prompt_parse_failed", "error": str(exc)}
        mode = payload.get("mode")
        channel = str(payload.get("channel") or "").strip()
        chat_id = str(payload.get("chat_id") or "").strip()
        if not channel or not chat_id:
            logger.warning(
                "fin-pulse: schedule %s missing channel/chat_id payload", task.id
            )
            return {"ok": False, "reason": "missing_target"}

        try:
            if mode == "daily_brief":
                return await self._run_scheduled_digest(payload, channel, chat_id)
            if mode == "hot_radar":
                return await self._run_scheduled_radar(payload, channel, chat_id)
        except Exception as exc:  # noqa: BLE001 — fatal hook boundary
            logger.exception(
                "fin-pulse: schedule %s (%s) failed: %s",
                getattr(task, "id", "?"),
                mode,
                exc,
            )
            return {"ok": False, "reason": "run_failed", "error": str(exc)}

        logger.info(
            "fin-pulse: schedule %s ignored — unknown mode %r (execution=%s)",
            getattr(task, "id", "?"),
            mode,
            getattr(execution, "id", None),
        )
        return {"ok": False, "reason": "unknown_mode", "mode": mode}

    async def _run_scheduled_digest(
        self, payload: dict[str, Any], channel: str, chat_id: str
    ) -> dict[str, Any]:
        session = str(payload.get("session") or "morning")
        if session not in {"morning", "noon", "evening"}:
            return {"ok": False, "reason": "invalid_session", "session": session}
        since_hours = int(payload.get("since_hours", 12) or 12)
        top_k = int(payload.get("top_k", 20) or 20)
        lang = str(payload.get("lang") or "zh")
        internal_task = await self._tm.create_task(
            mode="daily_brief",
            params={
                "session": session,
                "since_hours": since_hours,
                "top_k": top_k,
                "lang": lang,
                "scheduled": True,
                "channel": channel,
                "chat_id": chat_id,
            },
            status="running",
        )
        result = await self._pipeline.run_daily_brief(
            session=session,
            since_hours=max(1, min(since_hours, 72)),
            top_k=max(1, min(top_k, 60)),
            lang=lang,
            task_id=internal_task["id"],
        )
        dispatched: dict[str, Any] | None = None
        if self._dispatch is not None:
            md = result.get("markdown") or ""
            dispatched_res = await self._dispatch.send(
                channel=channel,
                chat_id=chat_id,
                content=md,
                cooldown_key=f"daily:{session}:{_today_utc_ymd()}",
                cooldown_s=60 * 60 * 6,  # 6h dedupe per session/day
                dedupe_by_content=False,
            )
            dispatched = dispatched_res.as_dict()
        return {"ok": True, "digest": result, "dispatched": dispatched}

    async def _run_scheduled_radar(
        self, payload: dict[str, Any], channel: str, chat_id: str
    ) -> dict[str, Any]:
        rules_text = payload.get("rules_text") or payload.get("rules") or ""
        if not isinstance(rules_text, str) or not rules_text.strip():
            return {"ok": False, "reason": "missing_rules"}
        if self._dispatch is None:
            return {"ok": False, "reason": "dispatch_unavailable"}
        since_hours = int(payload.get("since_hours", 24) or 24)
        limit = int(payload.get("limit", 100) or 100)
        min_score = payload.get("min_score")
        cooldown_s = float(payload.get("cooldown_s", 600) or 600)
        title = payload.get("title")
        internal_task = await self._tm.create_task(
            mode="hot_radar",
            params={
                "rules_text": rules_text,
                "targets": [{"channel": channel, "chat_id": chat_id}],
                "since_hours": since_hours,
                "limit": limit,
                "min_score": min_score,
                "cooldown_s": cooldown_s,
                "scheduled": True,
            },
            status="running",
        )
        result = await self._pipeline.run_hot_radar(
            self._dispatch,
            rules_text=rules_text,
            targets=[{"channel": channel, "chat_id": chat_id}],
            since_hours=max(1, min(since_hours, 168)),
            limit=max(1, min(limit, 500)),
            min_score=float(min_score) if min_score is not None else None,
            title=title if isinstance(title, str) else None,
            cooldown_s=cooldown_s,
            task_id=internal_task["id"],
        )
        return {"ok": True, "radar": result}

    # ── Agent tools (Phase 5 fills in the body) ─────────────────────

    def _tool_definitions(self) -> list[dict]:
        """Seven tools exposed to the host Brain — keep in lockstep with
        ``plugin.json`` ``provides.tools``. Phase 5 wires the handler
        into ``finpulse_services.query.*``; Phase 1a returns a stub
        envelope so the host never sees a hard exception.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_create",
                    "description": "Create a fin-pulse task (ingest / daily_brief / hot_radar).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": ["ingest", "daily_brief", "hot_radar"],
                            },
                            "params": {"type": "object"},
                        },
                        "required": ["mode"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_status",
                    "description": "Inspect a fin-pulse task by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_list",
                    "description": "List recent fin-pulse tasks.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string"},
                            "status": {"type": "string"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_cancel",
                    "description": "Cancel a running fin-pulse task.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_settings_get",
                    "description": "Read fin-pulse configuration (webhook / api_key redacted).",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_settings_set",
                    "description": "Write fin-pulse configuration values.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "updates": {
                                "type": "object",
                                "description": "Flat string map of config keys to values.",
                            }
                        },
                        "required": ["updates"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fin_pulse_search_news",
                    "description": "Search finance news by keyword, source, or date range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {
                                "type": "string",
                                "description": "Keyword; supports + must and ! exclude syntax.",
                            },
                            "source_id": {
                                "type": "string",
                                "description": "Restrict to one source.",
                            },
                            "days": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 90,
                                "default": 1,
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                            "min_score": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 10,
                            },
                        },
                    },
                },
            },
        ]

    async def _handle_tool(self, name: str, args: dict, **_: Any) -> Any:
        """Route an agent tool invocation into ``finpulse_services.query``.

        The host Brain hands us ``(name, arguments)`` and expects a
        string back; our service layer returns rich dicts so we JSON
        encode them at the boundary. Every failure is caught so a bad
        LLM payload never crashes the plugin dispatcher — the envelope
        always carries ``ok`` and an ``error`` kind instead.
        """

        if not isinstance(args, dict):
            args = {}
        try:
            from finpulse_services.query import (  # type: ignore
                build_tool_dispatch,
                serialize_tool_result,
            )
        except ImportError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error": "services_unavailable",
                    "detail": str(exc),
                    "tool": name,
                }
            )
        dispatch_table = build_tool_dispatch(
            tm=self._tm, pipeline=self._pipeline, dispatch=self._dispatch
        )
        handler = dispatch_table.get(name)
        if handler is None:
            return serialize_tool_result(
                {"ok": False, "error": "unknown_tool", "tool": name}
            )
        try:
            payload = await handler(args)
        except Exception as exc:  # noqa: BLE001 — envelope every failure
            try:
                from finpulse_errors import map_exception  # type: ignore

                kind, msg, hints = map_exception(exc)
            except Exception:  # noqa: BLE001
                kind, msg, hints = "unknown", str(exc), []
            payload = {
                "ok": False,
                "error": kind,
                "message": msg,
                "hints": hints,
                "tool": name,
            }
        return serialize_tool_result(payload)

    # ── FastAPI routes ──────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        """Register the Phase-1 read-only surface so the host health page
        can confirm the plugin is alive even before later Phases land.
        """

        @router.get("/health")
        async def health() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin_id": PLUGIN_ID,
                "version": PLUGIN_VERSION,
                "phase": "skeleton",
                "db_ready": self._tm is not None
                and getattr(self._tm, "_db", None) is not None,
                "data_dir": str(self._data_dir) if self._data_dir else None,
                "timestamp": time.time(),
            }

        @router.get("/modes")
        async def modes() -> dict[str, Any]:
            try:
                from finpulse_models import MODES  # type: ignore

                return {"modes": MODES}
            except ImportError:
                return {"modes": _FALLBACK_MODES}

        @router.get("/sources")
        async def list_sources() -> dict[str, Any]:
            """Expose ``SOURCE_DEFS`` to the UI so the Today-tab source
            dropdown always matches the real backend ids (avoids drift
            between the frontend ``KNOWN_SOURCES`` and the canonical
            ``finpulse_models.SOURCE_DEFS`` list).
            """
            try:
                from finpulse_models import iter_sources_for_ui  # type: ignore
            except ImportError:
                return {"ok": True, "items": []}
            return {"ok": True, "items": iter_sources_for_ui()}

        @router.post("/sources/probe-rss")
        async def probe_rss(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
            """Fetch a single RSS/Atom URL and return parse result."""
            url = (payload.get("url") or "").strip()
            if not url:
                raise HTTPException(status_code=400, detail="url_required")
            try:
                from finpulse_fetchers.rss import parse_feed  # type: ignore
                import httpx
            except ImportError as exc:
                raise HTTPException(
                    status_code=500, detail="rss_module_unavailable"
                ) from exc
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(15, connect=8),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; FinPulse/1.0)"
                    })
                    resp.raise_for_status()
                    items = parse_feed("_probe", resp.text)
                    return {
                        "ok": True,
                        "count": len(items),
                        "sample": [
                            {"title": it.title or "", "url": it.url or ""}
                            for it in items[:3]
                        ],
                    }
            except httpx.HTTPStatusError as exc:
                return {
                    "ok": False,
                    "error": f"http_{exc.response.status_code}",
                    "detail": str(exc),
                }
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": "parse_failed", "detail": str(exc)}

        @router.get("/config")
        async def get_config() -> dict[str, Any]:
            if self._tm is None:
                return {"ok": False, "error": "task_manager_unavailable", "config": {}}
            try:
                cfg = await self._tm.get_all_config()
                return {"ok": True, "config": _redact_secrets(cfg)}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @router.put("/config")
        async def put_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            updates = payload.get("updates")
            if not isinstance(updates, dict):
                raise HTTPException(status_code=400, detail="updates must be an object")
            flat: dict[str, str] = {}
            for k, v in updates.items():
                if not isinstance(k, str):
                    continue
                flat[k] = v if isinstance(v, str) else str(v)
            await self._tm.set_configs(flat)
            return {"ok": True, "applied": sorted(flat.keys())}

        @router.get("/tasks")
        async def list_tasks(
            mode: str | None = Query(None),
            status: str | None = Query(None),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_tasks(
                mode=mode, status=status, offset=offset, limit=limit
            )
            return {"ok": True, "items": items, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "task": row}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            await self._tm.update_task_safe(task_id, status="canceled")
            return {"ok": True, "task_id": task_id, "status": "canceled"}

        @router.post("/ingest")
        async def ingest_all(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            sources = payload.get("sources") if isinstance(payload, dict) else None
            since_hours = payload.get("since_hours") if isinstance(payload, dict) else 24
            task = await self._tm.create_task(
                mode="ingest",
                params={"sources": sources, "since_hours": since_hours},
                status="running",
            )
            try:
                summary = await self._pipeline.ingest(
                    sources=sources,
                    since_hours=int(since_hours) if since_hours is not None else 24,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "summary": summary}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception  # lazy — may be absent

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.post("/ingest/source/{source_id}")
        async def ingest_source(source_id: str) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            task = await self._tm.create_task(
                mode="ingest",
                params={"sources": [source_id], "since_hours": 24},
                status="running",
            )
            try:
                summary = await self._pipeline.ingest(
                    sources=[source_id], since_hours=24, task_id=task["id"]
                )
                return {"ok": True, "task_id": task["id"], "summary": summary}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.get("/articles")
        async def list_articles(
            q: str | None = Query(None),
            source_id: str | None = Query(None),
            since: str | None = Query(None),
            until: str | None = Query(None),
            min_score: float | None = Query(None),
            sort: str = Query("time"),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=1000),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_articles(
                source_id=source_id,
                since=since,
                until=until,
                q=q,
                min_score=min_score,
                sort=sort,
                offset=offset,
                limit=limit,
            )
            return {"ok": True, "items": items, "total": total}

        @router.get("/articles/{article_id}")
        async def get_article(article_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_article(article_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "article": row}

        @router.post("/translate")
        async def translate_articles(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            """Translate article title/summary via the host Brain.

            This mirrors TrendRadar's translation idea but keeps it scoped to
            on-demand card translation instead of adding another background
            pipeline.
            """

            raw_items = payload.get("items") if isinstance(payload, dict) else None
            if raw_items is None and isinstance(payload, dict):
                raw_items = [payload]
            if not isinstance(raw_items, list) or not raw_items:
                return {"ok": False, "error": "items is required"}
            target_language = str(payload.get("target_language") or "中文")
            items: list[dict[str, str]] = []
            for raw in raw_items[:8]:
                if not isinstance(raw, dict):
                    continue
                item_id = str(raw.get("id") or "")
                title = str(raw.get("title") or "")[:800]
                summary = str(raw.get("summary") or "")[:1600]
                if item_id and (title or summary):
                    items.append({"id": item_id, "title": title, "summary": summary})
            if not items:
                return {"ok": False, "error": "no translatable items"}
            try:
                brain = self._api.get_brain() if self._api is not None else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("translate brain access failed: %s", exc)
                brain = None
            if brain is None:
                return {"ok": False, "error": "brain.access not granted"}

            user_payload = json.dumps(
                {"target_language": target_language, "items": items},
                ensure_ascii=False,
            )
            system_prompt = (
                "You translate financial news for a product UI. "
                "Return strict JSON only: {\"items\":[{\"id\":\"...\","
                "\"title_translated\":\"...\",\"summary_translated\":\"...\"}]}. "
                "Keep tickers, company names, numbers, dates, and URLs unchanged. "
                "Do not add commentary."
            )
            try:
                if hasattr(brain, "think_lightweight"):
                    response = await brain.think_lightweight(
                        prompt=user_payload,
                        system=system_prompt,
                        max_tokens=1800,
                    )
                elif hasattr(brain, "think"):
                    response = await brain.think(
                        prompt=user_payload,
                        system=system_prompt,
                        max_tokens=1800,
                    )
                elif hasattr(brain, "chat"):
                    response = await brain.chat(
                        messages=[{"role": "user", "content": user_payload}],
                        system=system_prompt,
                        temperature=0.1,
                        max_tokens=1800,
                    )
                else:
                    return {
                        "ok": False,
                        "error": "brain has no think_lightweight/think/chat method",
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("translate brain call failed: %s", exc)
                return {"ok": False, "error": f"brain error: {exc}"}
            raw_text = response if isinstance(response, str) else getattr(response, "content", None)
            if raw_text is None and isinstance(response, dict):
                raw_text = response.get("content")
            text = str(raw_text or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            try:
                parsed = json.loads(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("translate JSON parse failed: %s", exc)
                return {"ok": False, "error": "translation_json_parse_failed"}
            out_items = parsed.get("items") if isinstance(parsed, dict) else None
            if not isinstance(out_items, list):
                return {"ok": False, "error": "translation_items_missing"}
            cleaned: list[dict[str, str]] = []
            for raw in out_items:
                if not isinstance(raw, dict):
                    continue
                cleaned.append(
                    {
                        "id": str(raw.get("id") or ""),
                        "title_translated": str(raw.get("title_translated") or ""),
                        "summary_translated": str(raw.get("summary_translated") or ""),
                    }
                )
            return {"ok": True, "items": [row for row in cleaned if row["id"]]}

        @router.post("/digest/run")
        async def run_digest(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            session = payload.get("session") if isinstance(payload, dict) else None
            if session not in {"morning", "noon", "evening"}:
                raise HTTPException(
                    status_code=400,
                    detail="session must be one of morning|noon|evening",
                )
            since_hours = payload.get("since_hours", 12)
            top_k = payload.get("top_k", 20)
            lang = payload.get("lang", "zh") or "zh"
            try:
                since_hours_int = max(1, min(int(since_hours), 72))
                top_k_int = max(1, min(int(top_k), 60))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail=f"invalid numeric arg: {exc}"
                ) from exc
            task = await self._tm.create_task(
                mode="daily_brief",
                params={
                    "session": session,
                    "since_hours": since_hours_int,
                    "top_k": top_k_int,
                    "lang": lang,
                },
                status="running",
            )
            try:
                result = await self._pipeline.run_daily_brief(
                    session=session,
                    since_hours=since_hours_int,
                    top_k=top_k_int,
                    lang=lang,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "digest": result}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.get("/digests")
        async def list_digests(
            session: str | None = Query(None),
            offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200),
        ) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            items, total = await self._tm.list_digests(
                session=session, offset=offset, limit=limit
            )
            for item in items:
                item.pop("html_blob", None)
                item.pop("markdown_blob", None)
            return {"ok": True, "items": items, "total": total}

        @router.get("/digests/{digest_id}")
        async def get_digest(digest_id: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_digest(digest_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            return {"ok": True, "digest": row}

        @router.get("/digests/{digest_id}/html", response_class=HTMLResponse)
        async def get_digest_html(digest_id: str) -> HTMLResponse:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            row = await self._tm.get_digest(digest_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not_found")
            html_blob = row.get("html_blob") or ""
            return HTMLResponse(content=html_blob, media_type="text/html")

        # ── Hot radar ───────────────────────────────────────────────

        @router.post("/radar/evaluate")
        async def radar_evaluate(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            rules_text = payload.get("rules_text") if isinstance(payload, dict) else None
            if not isinstance(rules_text, str):
                raise HTTPException(status_code=400, detail="rules_text must be a string")
            since_hours = int(payload.get("since_hours", 24) or 24)
            limit = int(payload.get("limit", 100) or 100)
            min_score = payload.get("min_score")
            min_score_f = float(min_score) if min_score is not None else None
            return await self._pipeline.evaluate_radar(
                rules_text=rules_text,
                since_hours=max(1, min(since_hours, 168)),
                limit=max(1, min(limit, 500)),
                min_score=min_score_f,
            )

        @router.post("/radar/ai-suggest")
        async def radar_ai_suggest(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            """Turn a plain-language description into a rules_text.

            The host Brain is best-effort: when ``brain.access`` is not
            granted or the LLM errors out we fall back to a deterministic
            keyword splitter so the UI always gets a draft. The
            ``source`` field tells the caller which path produced it.
            """

            description = payload.get("description") if isinstance(payload, dict) else None
            if not isinstance(description, str) or not description.strip():
                raise HTTPException(status_code=400, detail="description is required")
            existing = payload.get("existing") if isinstance(payload, dict) else ""
            lang = str(payload.get("lang") or "zh") or "zh"
            try:
                from finpulse_ai.rules_suggest import suggest_rules_text  # type: ignore
            except ImportError as exc:
                raise HTTPException(status_code=500, detail=f"ai_module_unavailable: {exc}") from exc
            brain: Any = None
            try:
                brain = self._api.get_brain() if self._api is not None else None
            except Exception:  # noqa: BLE001 — brain.access may be absent
                brain = None
            return await suggest_rules_text(
                brain,
                description=description,
                existing=existing if isinstance(existing, str) else "",
                lang=lang,
            )

        @router.get("/radar/library")
        async def radar_library_list() -> dict[str, Any]:
            """List saved rule presets (config key ``radar_rules_library``)."""

            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            from finpulse_services.radar_library import list_presets

            items = await list_presets(self._tm)
            return {"ok": True, "items": items}

        @router.post("/radar/library")
        async def radar_library_save(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            """Save or upsert a rule preset under a user-chosen name.

            Duplicates on ``name`` overwrite in place.
            """

            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            from finpulse_services.radar_library import save_preset

            name_raw = payload.get("name") if isinstance(payload, dict) else None
            rules_raw = payload.get("rules_text") if isinstance(payload, dict) else None
            if not isinstance(name_raw, str):
                raise HTTPException(status_code=400, detail="name is required")
            if not isinstance(rules_raw, str):
                raise HTTPException(status_code=400, detail="rules_text is required")
            try:
                entry = await save_preset(
                    self._tm, name=name_raw, rules_text=rules_raw
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "item": entry}

        @router.delete("/radar/library/{name}")
        async def radar_library_delete(name: str) -> dict[str, Any]:
            if self._tm is None:
                raise HTTPException(status_code=503, detail="task_manager_unavailable")
            from finpulse_services.radar_library import delete_preset

            removed = await delete_preset(self._tm, name)
            if not removed:
                return {"ok": False, "error": "not_found", "name": name}
            return {"ok": True, "name": name}

        @router.post("/hot_radar/run")
        async def hot_radar_run(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._tm is None or self._pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline_unavailable")
            if self._dispatch is None:
                raise HTTPException(status_code=503, detail="dispatch_unavailable")
            rules_text = payload.get("rules_text") if isinstance(payload, dict) else None
            if not isinstance(rules_text, str) or not rules_text.strip():
                raise HTTPException(status_code=400, detail="rules_text must be non-empty")
            targets = payload.get("targets") or []
            if not isinstance(targets, list) or not targets:
                raise HTTPException(status_code=400, detail="targets must be a non-empty list")
            clean_targets: list[dict[str, str]] = []
            for t in targets:
                if not isinstance(t, dict):
                    continue
                ch = str(t.get("channel") or "").strip()
                ci = str(t.get("chat_id") or "").strip()
                if ch and ci:
                    clean_targets.append({"channel": ch, "chat_id": ci})
            if not clean_targets:
                raise HTTPException(status_code=400, detail="no usable targets (channel/chat_id)")
            since_hours = max(1, min(int(payload.get("since_hours", 24) or 24), 168))
            limit = max(1, min(int(payload.get("limit", 100) or 100), 500))
            min_score = payload.get("min_score")
            min_score_f = float(min_score) if min_score is not None else None
            cooldown_s = float(payload.get("cooldown_s", 600) or 600)
            title = payload.get("title")
            task = await self._tm.create_task(
                mode="hot_radar",
                params={
                    "targets": clean_targets,
                    "since_hours": since_hours,
                    "limit": limit,
                    "min_score": min_score_f,
                    "cooldown_s": cooldown_s,
                    "title": title,
                },
                status="running",
            )
            try:
                result = await self._pipeline.run_hot_radar(
                    self._dispatch,
                    rules_text=rules_text,
                    targets=clean_targets,
                    since_hours=since_hours,
                    limit=limit,
                    min_score=min_score_f,
                    title=title if isinstance(title, str) else None,
                    cooldown_s=cooldown_s,
                    task_id=task["id"],
                )
                return {"ok": True, "task_id": task["id"], "result": result}
            except Exception as exc:  # noqa: BLE001
                from finpulse_errors import map_exception

                kind, msg, hints = map_exception(exc)
                await self._tm.update_task_safe(
                    task["id"],
                    status="failed",
                    error_kind=kind,
                    error_message=msg,
                    error_hints=hints,
                )
                raise HTTPException(status_code=500, detail=msg) from exc

        @router.post("/dispatch/send")
        async def dispatch_send(
            payload: dict[str, Any] = Body(default={}),
        ) -> dict[str, Any]:
            if self._dispatch is None:
                raise HTTPException(status_code=503, detail="dispatch_unavailable")
            channel = str(payload.get("channel") or "").strip()
            chat_id = str(payload.get("chat_id") or "").strip()
            content = payload.get("content")
            if not channel or not chat_id:
                raise HTTPException(status_code=400, detail="channel and chat_id are required")
            if not isinstance(content, str):
                raise HTTPException(status_code=400, detail="content must be a string")
            cooldown_key = payload.get("cooldown_key")
            cooldown_s = float(payload.get("cooldown_s", 0) or 0)
            dedupe = bool(payload.get("dedupe_by_content"))
            header = str(payload.get("header") or "")
            result = await self._dispatch.send(
                channel=channel,
                chat_id=chat_id,
                content=content,
                cooldown_key=cooldown_key if isinstance(cooldown_key, str) else None,
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe,
                header=header,
            )
            return {"ok": result.ok, "dispatch": result.as_dict()}

        # ── Schedules ───────────────────────────────────────────────

        @router.get("/schedules")
        async def list_schedules() -> dict[str, Any]:
            scheduler = _get_active_scheduler()
            if scheduler is None:
                return {"ok": True, "items": [], "scheduler_ready": False}
            tasks = scheduler.list_tasks()
            items = [
                _serialize_schedule(t)
                for t in tasks
                if _task_name_is_finpulse(getattr(t, "name", "") or "")
            ]
            return {"ok": True, "items": items, "scheduler_ready": True}

        @router.post("/schedules")
        async def create_schedule(
            payload: dict[str, Any] = Body(...),
        ) -> dict[str, Any]:
            scheduler = _get_active_scheduler()
            if scheduler is None:
                raise HTTPException(status_code=503, detail="scheduler_unavailable")
            mode = str(payload.get("mode") or "daily_brief").strip() or "daily_brief"
            if mode not in {"daily_brief", "hot_radar"}:
                raise HTTPException(
                    status_code=400,
                    detail="mode must be daily_brief or hot_radar",
                )
            cron = payload.get("cron") or payload.get("cron_expression")
            if not isinstance(cron, str) or not cron.strip():
                raise HTTPException(status_code=400, detail="cron expression required")
            channel = str(payload.get("channel") or "").strip()
            chat_id = str(payload.get("chat_id") or "").strip()
            if not channel or not chat_id:
                raise HTTPException(
                    status_code=400, detail="channel and chat_id are required"
                )
            body: dict[str, Any] = {
                "mode": mode,
                "channel": channel,
                "chat_id": chat_id,
            }
            name_suffix: str
            description: str
            if mode == "daily_brief":
                session = str(payload.get("session") or "morning")
                if session not in {"morning", "noon", "evening"}:
                    raise HTTPException(
                        status_code=400,
                        detail="session must be morning|noon|evening",
                    )
                body["session"] = session
                body["since_hours"] = int(payload.get("since_hours", 12) or 12)
                body["top_k"] = int(payload.get("top_k", 20) or 20)
                body["lang"] = str(payload.get("lang") or "zh")
                name_suffix = session
                description = f"fin-pulse {session} brief → {channel}/{chat_id}"
            else:
                rules_text = payload.get("rules_text") or ""
                if not isinstance(rules_text, str) or not rules_text.strip():
                    raise HTTPException(status_code=400, detail="rules_text required for hot_radar")
                body["rules_text"] = rules_text
                body["since_hours"] = int(payload.get("since_hours", 24) or 24)
                body["limit"] = int(payload.get("limit", 100) or 100)
                body["cooldown_s"] = float(payload.get("cooldown_s", 600) or 600)
                title = payload.get("title")
                if isinstance(title, str):
                    body["title"] = title
                radar_key = _radar_key(rules_text)
                # NOTE: keep name `:`-free — host _validate_task_name rejects
                # ":" in task names (Windows/log-file safety), so the radar
                # suffix must use "-" rather than the old "radar:<hash>".
                name_suffix = f"radar-{radar_key}"
                description = f"fin-pulse radar {radar_key} → {channel}/{chat_id}"

            # Optional custom name override from the in-page dialog — must
            # still start with one of our approved prefixes so ownership
            # checks (delete/toggle/on_schedule hook) stay watertight.
            override = str(payload.get("name") or "").strip()
            if override:
                if not _task_name_is_finpulse(override):
                    raise HTTPException(
                        status_code=400,
                        detail="custom name must start with 'fin-pulse '",
                    )
                task_name = override
            else:
                task_name = f"fin-pulse {name_suffix}"

            enabled = bool(payload.get("enabled", True))

            try:
                from openakita.scheduler.task import ScheduledTask  # type: ignore
            except ImportError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"scheduler module unavailable: {exc}",
                ) from exc

            task = ScheduledTask.create_cron(
                name=task_name,
                description=description,
                cron_expression=cron.strip(),
                prompt="[fin-pulse] " + json.dumps(body, ensure_ascii=False),
                channel_id=channel,
                chat_id=chat_id,
                silent=True,  # fin-pulse handles its own notification
                metadata={"plugin_id": PLUGIN_ID, "mode": mode},
            )
            task.enabled = enabled
            try:
                task_id = await scheduler.add_task(task)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "id": task_id, "schedule": _serialize_schedule(task)}

        @router.delete("/schedules/{schedule_id}")
        async def delete_schedule(schedule_id: str) -> dict[str, Any]:
            scheduler = _get_active_scheduler()
            if scheduler is None:
                raise HTTPException(status_code=503, detail="scheduler_unavailable")
            existing = scheduler.get_task(schedule_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="not_found")
            if not _task_name_is_finpulse(getattr(existing, "name", "") or ""):
                raise HTTPException(
                    status_code=403,
                    detail="refusing to delete schedule not owned by fin-pulse",
                )
            outcome = await scheduler.remove_task(schedule_id)
            if outcome != "ok":
                raise HTTPException(status_code=400, detail=outcome)
            return {"ok": True, "id": schedule_id, "deleted": True}

        @router.post("/schedules/{schedule_id}/toggle")
        async def toggle_schedule(schedule_id: str) -> dict[str, Any]:
            """Enable/disable a fin-pulse schedule in place so the in-page
            list can show run/pause controls without redirecting users to
            the host SchedulerView panel. Ownership is checked against
            the ``fin-pulse `` / ``fin-pulse:`` name prefix so we never
            touch schedules that belong to other plugins.
            """
            scheduler = _get_active_scheduler()
            if scheduler is None:
                raise HTTPException(status_code=503, detail="scheduler_unavailable")
            existing = scheduler.get_task(schedule_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="not_found")
            if not _task_name_is_finpulse(getattr(existing, "name", "") or ""):
                raise HTTPException(
                    status_code=403,
                    detail="refusing to toggle schedule not owned by fin-pulse",
                )
            if getattr(existing, "enabled", True):
                await scheduler.disable_task(schedule_id)
            else:
                await scheduler.enable_task(schedule_id)
            updated = scheduler.get_task(schedule_id)
            return {
                "ok": True,
                "id": schedule_id,
                "schedule": _serialize_schedule(updated) if updated else None,
            }

        @router.post("/schedules/{schedule_id}/trigger")
        async def trigger_schedule(schedule_id: str) -> dict[str, Any]:
            """Fire a fin-pulse schedule immediately — proxies to the host
            scheduler's ``trigger_task`` (non-blocking, backgrounded).
            """
            scheduler = _get_active_scheduler()
            if scheduler is None:
                raise HTTPException(status_code=503, detail="scheduler_unavailable")
            existing = scheduler.get_task(schedule_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="not_found")
            if not _task_name_is_finpulse(getattr(existing, "name", "") or ""):
                raise HTTPException(
                    status_code=403,
                    detail="refusing to trigger schedule not owned by fin-pulse",
                )
            trigger = getattr(scheduler, "trigger_task", None)
            if callable(trigger):
                try:
                    # Some implementations are async; some return a coroutine.
                    result = trigger(schedule_id)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
            else:
                raise HTTPException(
                    status_code=501,
                    detail="host scheduler does not expose trigger_task",
                )
            return {"ok": True, "id": schedule_id, "triggered": True}

        @router.get("/scheduler/channels")
        async def scheduler_channels() -> dict[str, Any]:
            """Forward to the host ``/api/scheduler/channels`` route so
            the fin-pulse UI can show the same IM channel dropdown as
            the main SchedulerView — with chat_name, chat_type, alias
            and bot display names enriched. We call the host function
            in-process (no extra HTTP round-trip).
            """
            host = getattr(self._api, "_host", None) or {}
            api_app = host.get("api_app") if isinstance(host, dict) else None
            if api_app is None:
                return {"ok": True, "channels": []}
            try:
                from openakita.api.routes.scheduler import (  # type: ignore
                    list_channels as _host_list_channels,
                )
            except Exception:  # noqa: BLE001
                return {"ok": True, "channels": []}
            from types import SimpleNamespace

            request_stub = SimpleNamespace(app=api_app)
            try:
                payload = await _host_list_channels(request_stub)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "channels": [], "detail": str(exc)}
            channels = (payload or {}).get("channels") or []
            return {"ok": True, "channels": channels}

        @router.get("/available-channels")
        async def available_channels() -> dict[str, Any]:
            """Expose the list of adapter names the host gateway
            currently carries so the Settings UI can render a channel
            picker without hard-coding the 7-strong roster. Falls back
            to a probe list when the gateway hides ``_adapters``.
            """
            host = getattr(self._api, "_host", None) or {}
            gateway = host.get("gateway") if isinstance(host, dict) else None
            if gateway is None:
                return {"ok": True, "channels": []}
            names: list[str] = []
            adapters = getattr(gateway, "_adapters", None)
            if isinstance(adapters, dict):
                names = [str(k) for k in adapters.keys()]
            else:
                probe = [
                    "feishu",
                    "wework",
                    "wework_ws",
                    "dingtalk",
                    "telegram",
                    "onebot",
                    "qqbot",
                    "wechat",
                    "email",
                ]
                get = getattr(gateway, "get_adapter", None)
                if callable(get):
                    for name in probe:
                        try:
                            if get(name) is not None:
                                names.append(name)
                        except Exception:  # noqa: BLE001 — probe only
                            continue
            return {"ok": True, "channels": names}


# ── Utilities ────────────────────────────────────────────────────────


_SECRET_KEYS = (
    "api_key",
    "token",
    "webhook",
    "secret",
    "password",
)


def _redact_secrets(cfg: dict[str, str]) -> dict[str, str]:
    """Mask any config value whose key contains a secret-looking suffix."""
    redacted: dict[str, str] = {}
    for k, v in cfg.items():
        if any(s in k.lower() for s in _SECRET_KEYS) and v:
            redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted


# ── Schedule plumbing ────────────────────────────────────────────────

_SCHEDULE_PROMPT_PREFIX = "[fin-pulse] "


#: Prefixes recognised as fin-pulse-owned tasks on the host scheduler.
#: The space-delimited form is the new canonical one (the host UI shows
#: names more cleanly without ``:``); the colon form is kept for
#: backwards compatibility with existing installs that already have
#: ``fin-pulse:morning`` rows persisted.
_FINPULSE_NAME_PREFIXES: Final[tuple[str, ...]] = ("fin-pulse ", "fin-pulse:")


def _task_name_is_finpulse(name: str) -> bool:
    if not name:
        return False
    for prefix in _FINPULSE_NAME_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def _is_finpulse_schedule(**kwargs: Any) -> bool:
    """Match predicate for the ``on_schedule`` hook — only fire when
    the task is ours. Checks both the ``name`` prefix (authoritative)
    and the ``prompt`` prefix (used by natural-language creation paths
    that might not set the name).
    """
    task = kwargs.get("task")
    if task is None:
        return False
    name = getattr(task, "name", "") or ""
    if _task_name_is_finpulse(name):
        return True
    prompt = getattr(task, "prompt", "") or ""
    return prompt.startswith(_SCHEDULE_PROMPT_PREFIX)


def _parse_schedule_prompt(prompt: str) -> dict[str, Any]:
    """Strip the ``[fin-pulse] `` prefix and ``json.loads`` the rest.

    Accepts an already-stripped JSON body too so tooling that emits
    ``{"mode":...}`` directly still works.
    """
    text = (prompt or "").strip()
    if text.startswith(_SCHEDULE_PROMPT_PREFIX):
        text = text[len(_SCHEDULE_PROMPT_PREFIX):]
    if not text:
        raise ValueError("empty prompt")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("prompt must decode to an object")
    return data


def _get_active_scheduler() -> Any:
    """Fetch the host's active :class:`TaskScheduler` singleton. Returns
    ``None`` when the host hasn't brought the scheduler up yet (common
    in headless test harnesses).
    """
    try:
        from openakita.scheduler import get_active_scheduler  # type: ignore
    except ImportError:
        return None
    try:
        return get_active_scheduler()
    except Exception:  # noqa: BLE001 — host boot-order defensive
        return None


def _serialize_schedule(task: Any) -> dict[str, Any]:
    """Shape a :class:`ScheduledTask` into the JSON the UI expects.

    We deliberately only expose the fields fin-pulse cares about so
    rogue scheduler extensions can't leak fields (e.g. agent_profile_id)
    into the plugin API contract.
    """
    prompt = getattr(task, "prompt", "") or ""
    try:
        meta = _parse_schedule_prompt(prompt)
    except ValueError:
        meta = {}
    next_run = getattr(task, "next_run", None)
    trigger_config = getattr(task, "trigger_config", {}) or {}
    cron = ""
    if isinstance(trigger_config, dict):
        cron = str(trigger_config.get("cron") or "")
    return {
        "id": getattr(task, "id", ""),
        "name": getattr(task, "name", ""),
        "description": getattr(task, "description", ""),
        "cron": cron,
        "enabled": bool(getattr(task, "enabled", True)),
        "status": str(getattr(task, "status", "")),
        "next_run": next_run.isoformat() if hasattr(next_run, "isoformat") else None,
        "run_count": int(getattr(task, "run_count", 0)),
        "fail_count": int(getattr(task, "fail_count", 0)),
        "channel": getattr(task, "channel_id", None),
        "chat_id": getattr(task, "chat_id", None),
        "mode": meta.get("mode"),
        "session": meta.get("session"),
    }


def _radar_key(rules_text: str) -> str:
    """Short 8-char hash of a rule body — used to mint stable schedule
    names so Settings UI shows ``fin-pulse:radar:a1b2c3d4`` rather than
    the raw rule blob.
    """
    digest = hashlib.sha256((rules_text or "").encode("utf-8")).hexdigest()
    return digest[:8]


def _today_utc_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


__all__ = ["Plugin", "PLUGIN_ID", "PLUGIN_VERSION"]
