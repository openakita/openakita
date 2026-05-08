# ruff: noqa: N999
"""融媒智策 — source-backed media radar and editorial planning plugin."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Literal

PLUGIN_DIR = Path(__file__).resolve().parent

try:
    from media_inline.dep_bootstrap import ensure_runtime_paths, preinstall_async

    ensure_runtime_paths(PLUGIN_DIR)
except Exception:
    pass

from fastapi import APIRouter, HTTPException, Query
from media_fetchers.rss import validate_feed_url
from media_models import BRAND, DISPLAY_NAME_ZH, PLUGIN_ID, PLUGIN_VERSION, SLOGAN, TOOL_NAMES
from media_pipeline import MediaPipeline
from media_task_manager import MediaTaskManager, utcnow_iso
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase


def _purge_module_cache() -> int:
    prefixes = ("media_models", "media_task_manager", "media_pipeline", "media_fetchers", "media_ai")
    removed = 0
    for name in list(sys.modules):
        if name == __name__:
            continue
        if name.startswith(prefixes):
            sys.modules.pop(name, None)
            removed += 1
    return removed


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsBody(_StrictBase):
    updates: dict[str, Any] = Field(default_factory=dict)


class SubscribePackageBody(_StrictBase):
    package_id: str
    enabled: bool = True


class AddFeedBody(_StrictBase):
    name: str
    url: str
    package_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class CreateTaskBody(_StrictBase):
    mode: Literal["ingest", "hot_radar", "daily_brief", "verify_pack", "replicate_plan"]
    params: dict[str, Any] = Field(default_factory=dict)


class Plugin(PluginBase):
    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._tm: MediaTaskManager | None = None
        self._pipeline: MediaPipeline | None = None
        self._init_task: asyncio.Task[Any] | None = None

    def on_load(self, api: PluginAPI) -> None:
        removed = _purge_module_cache()
        self._api = api
        if removed:
            api.log(f"{PLUGIN_ID}: cleared {removed} cached helper modules", "debug")

        self._data_dir = self._resolve_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tm = MediaTaskManager(self._data_dir / "media_strategy.sqlite")
        self._pipeline = MediaPipeline(self._tm, api, output_dir=self._data_dir / "outputs")

        try:
            preinstall_async(
                [("feedparser", "feedparser>=6.0.11"), ("bs4", "beautifulsoup4>=4.12.0")],
                plugin_dir=PLUGIN_DIR,
            )
        except Exception as exc:  # noqa: BLE001
            api.log(f"{PLUGIN_ID}: dependency preinstall skipped ({exc!r})", "warning")

        router = self._build_router()
        api.register_api_routes(router)
        api.register_tools(self._tool_definitions(), handler=self._handle_tool)
        self._init_task = api.spawn_task(self._init(), name=f"plugin:{PLUGIN_ID}:init")
        api.log(f"{DISPLAY_NAME_ZH} loaded (v{PLUGIN_VERSION}, {len(TOOL_NAMES)} tools)")

    async def on_unload(self) -> None:
        if self._tm is not None:
            await self._tm.close()

    async def _init(self) -> None:
        if self._tm is not None:
            await self._tm.init()

    async def _ensure_ready(self) -> None:
        if self._init_task is not None and not self._init_task.done():
            await asyncio.wait_for(asyncio.shield(self._init_task), timeout=10)
        if self._tm is None or not self._tm.ready:
            raise HTTPException(status_code=503, detail="media-strategy storage is not ready")

    def _load_settings(self) -> dict[str, Any]:
        if self._api is None:
            return {}
        try:
            return dict(self._api.get_config() or {})
        except Exception:
            return {}

    def _save_settings(self, updates: dict[str, Any]) -> None:
        if self._api is not None:
            self._api.set_config(updates)

    def _validate_custom_data_dir(self, raw: str) -> tuple[Path | None, str]:
        value = (raw or "").strip()
        if not value:
            return None, ""
        p = Path(value).expanduser()
        if not p.is_absolute():
            return None, "存储目录必须是绝对路径"
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".media_strategy_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            return None, f"目录不可写：{exc}"
        return p.resolve(), ""

    def _resolve_data_dir(self) -> Path:
        cfg = self._load_settings()
        custom = str(cfg.get("custom_data_dir") or "").strip()
        if custom:
            path, err = self._validate_custom_data_dir(custom)
            if path is not None:
                return path
            if self._api is not None:
                self._api.log(f"{PLUGIN_ID}: ignoring invalid custom_data_dir {custom!r}: {err}", "warning")
        host = self._api.get_data_dir() if self._api is not None else None
        return Path(host) / "media_strategy" if host else Path.cwd() / ".media-strategy"

    def _build_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            from media_inline.dep_bootstrap import get_dep_state

            return {
                "ok": True,
                "plugin_id": PLUGIN_ID,
                "version": PLUGIN_VERSION,
                "display_name": DISPLAY_NAME_ZH,
                "slogan": SLOGAN,
                "brand": BRAND,
                "data_dir": str(self._data_dir),
                "db_ready": self._tm.ready,
                "brain_available": self._api.get_brain() is not None if self._api else False,
                "deps": get_dep_state(),
                "timestamp": time.time(),
            }

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"settings": await self._tm.get_settings(), "host_config": self._load_settings()}

        @router.put("/settings")
        async def put_settings(body: SettingsBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            updates = dict(body.updates or {})
            if "custom_data_dir" in updates:
                path, err = self._validate_custom_data_dir(str(updates.get("custom_data_dir") or ""))
                if err:
                    raise HTTPException(status_code=422, detail=err)
                self._save_settings({"custom_data_dir": str(path) if path else ""})
            settings = await self._tm.set_settings(updates)
            return {"ok": True, "settings": settings, "reload_required": "custom_data_dir" in updates}

        @router.get("/packages")
        async def packages() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"packages": await self._tm.list_packages()}

        @router.post("/packages/subscribe")
        async def subscribe_package(body: SubscribePackageBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                packages = await self._tm.set_package_enabled(body.package_id, body.enabled)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown package: {body.package_id}") from exc
            return {"ok": True, "packages": packages}

        @router.get("/sources")
        async def sources() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"sources": await self._tm.list_sources()}

        @router.post("/feeds")
        async def add_feed(body: AddFeedBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                url = validate_feed_url(body.url)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            source = await self._tm.add_custom_source(
                name=body.name,
                url=url,
                package_ids=body.package_ids,
                enabled=body.enabled,
            )
            return {"ok": True, "source": source}

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            await self._ensure_ready()
            return await self._create_and_run_task(body.mode, body.params)

        @router.get("/tasks")
        async def list_tasks(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"tasks": await self._tm.list_tasks(limit=limit)}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            task = await self._tm.get_task(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            return {"task": task}

        @router.get("/articles")
        async def articles(
            q: str = "",
            package_id: str = "",
            limit: int = Query(default=30, ge=1, le=100),
        ) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._pipeline is not None
            return await self._pipeline.search_news({"q": q, "package_id": package_id, "limit": limit})

        @router.get("/reports")
        async def reports(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"reports": await self._tm.list_reports(limit=limit)}

        @router.get("/reports/{report_id}")
        async def report(report_id: str) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            row = await self._tm.get_report(report_id)
            if row is None:
                raise HTTPException(status_code=404, detail="report not found")
            return {"report": row}

        return router

    async def _create_and_run_task(self, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_ready()
        assert self._tm is not None and self._pipeline is not None
        task = await self._tm.create_task(mode, params)
        task_id = task["id"]
        await self._tm.update_task(task_id, status="running", started_at=utcnow_iso(), progress=0.05)
        try:
            if mode == "ingest":
                result = await self._pipeline.ingest(params)
            elif mode == "hot_radar":
                result = await self._pipeline.hot_radar(params)
            elif mode == "daily_brief":
                result = await self._pipeline.daily_brief(task_id, params)
            elif mode == "verify_pack":
                result = await self._pipeline.verify_pack(task_id, params)
            elif mode == "replicate_plan":
                result = await self._pipeline.replicate_plan(task_id, params)
            else:
                raise ValueError(f"unsupported mode: {mode}")
            await self._tm.update_task(
                task_id,
                status="done",
                progress=1.0,
                finished_at=utcnow_iso(),
                result=result,
            )
            return {"ok": True, "task": await self._tm.get_task(task_id), "result": result}
        except Exception as exc:  # noqa: BLE001
            kind = "unknown"
            message = str(exc)
            await self._tm.update_task(
                task_id,
                status="failed",
                progress=1.0,
                finished_at=utcnow_iso(),
                error_kind=kind,
                error_message=message,
            )
            return {"ok": False, "task": await self._tm.get_task(task_id), "error": kind, "hint": message}

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            _tool("media_strategy_subscribe_package", "订阅或取消融媒智策 RSS 套餐。", {"package_id": "string", "enabled": "boolean"}),
            _tool("media_strategy_add_feed", "添加自定义 RSS 源并做安全校验。", {"name": "string", "url": "string", "package_ids": "array"}),
            _tool("media_strategy_list_sources", "查看套餐、订阅源和健康状态。", {}),
            _tool("media_strategy_ingest", "手动拉取最新 RSS 新闻。", {"package_ids": "array", "limit_sources": "integer"}),
            _tool("media_strategy_hot_radar", "生成热点雷达榜。", {"package_id": "string", "since_hours": "integer", "limit": "integer"}),
            _tool("media_strategy_search_news", "按关键词、分类检索新闻。", {"q": "string", "package_id": "string", "limit": "integer"}),
            _tool("media_strategy_daily_brief", "生成融媒早报、午报、晚报或专题简报。", {"session": "string", "since_hours": "integer", "limit": "integer"}),
            _tool("media_strategy_verify_pack", "为热点生成信源复核清单。", {"article_ids": "array", "topic": "string"}),
            _tool("media_strategy_replicate_plan", "生成热点复刻、采访、拍摄和制作计划。", {"article_ids": "array", "topic": "string", "target_format": "string", "tone": "string"}),
        ]

    async def _handle_tool(self, name: str, arguments: dict[str, Any], **_: Any) -> Any:
        await self._ensure_ready()
        assert self._tm is not None and self._pipeline is not None
        args = dict(arguments or {})
        if name == "media_strategy_subscribe_package":
            packages = await self._tm.set_package_enabled(str(args.get("package_id")), bool(args.get("enabled", True)))
            return {"ok": True, "packages": packages}
        if name == "media_strategy_add_feed":
            url = validate_feed_url(str(args.get("url") or ""))
            source = await self._tm.add_custom_source(
                name=str(args.get("name") or "自定义 RSS"),
                url=url,
                package_ids=[str(x) for x in args.get("package_ids") or []],
                enabled=bool(args.get("enabled", True)),
            )
            return {"ok": True, "source": source}
        if name == "media_strategy_list_sources":
            return {"ok": True, "packages": await self._tm.list_packages(), "sources": await self._tm.list_sources()}
        if name == "media_strategy_ingest":
            return await self._create_and_run_task("ingest", args)
        if name == "media_strategy_hot_radar":
            return {"ok": True, **(await self._pipeline.hot_radar(args))}
        if name == "media_strategy_search_news":
            return {"ok": True, **(await self._pipeline.search_news(args))}
        if name == "media_strategy_daily_brief":
            return await self._create_and_run_task("daily_brief", args)
        if name == "media_strategy_verify_pack":
            return await self._create_and_run_task("verify_pack", args)
        if name == "media_strategy_replicate_plan":
            return await self._create_and_run_task("replicate_plan", args)
        return {"ok": False, "error": "unknown_tool", "hint": name}


def _tool(name: str, description: str, props: dict[str, str]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key, typ in props.items():
        schema: dict[str, Any] = {"type": typ}
        if typ == "array":
            schema["items"] = {"type": "string"}
        properties[key] = schema
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties},
    }
