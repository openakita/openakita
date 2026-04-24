"""idea-research 选题研析室 — Plugin entry (Phase 4 wiring).

Wires up the §10 26 routes, §11 9 tools, the §13.1 SSE bus, the
background subscription scheduler, and on_load / on_unload lifecycle.

Architecture
------------
``Plugin.on_load`` performs the §14 Phase 4 8-step boot sequence:

1.  store ``api`` + ``data_dir``
2.  instantiate ``IdeaTaskManager`` (sqlite)
3.  instantiate ``MdrmAdapter`` (auto-detects 4 SDK口子)
4.  instantiate ``DashScopeClient`` (re-uses ``brain.access`` as fallback)
5.  instantiate ``CookiesVault`` + ``CollectorRegistry`` (engine A + B
    + Ranker injected with the MDRM adapter)
6.  build + register the FastAPI router (26 routes)
7.  register the 9 declarative tools, dispatched via ``_handle_tool``
8.  spawn the async init task that runs ``tm.init()`` + scheduler

``on_unload`` cancels every owned task, closes the playwright pool, the
sqlite connection pool and the DashScope HTTP client. All long-running
async work is kept on ``self._tasks`` so it can be cancelled in the
right order.

Pydantic body models all set ``extra='forbid'`` so the SDK returns 422
with a deterministic ``ignored: [...]`` field — verified by Phase 4
tests in ``tests/test_routes.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from idea_collectors import CollectorRegistry, Normalizer, Ranker
from idea_dashscope_client import DashScopeClient
from idea_engine_crawler import CookiesVault, PlaywrightDriver
from idea_models import (
    PLUGIN_ID,
    RANKER_WEIGHTS,
    estimate_cost,
    hint_for,
)
from idea_pipeline import (
    IdeaPipelineContext,
    run_breakdown_url,
    run_compare_accounts,
    run_radar_pull,
    run_script_remix,
)
from idea_research_inline.mdrm_adapter import MdrmAdapter
from idea_research_inline.upload_preview import add_upload_preview_routes
from idea_task_manager import IdeaTaskManager
from openakita_plugin_sdk import PluginBase
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover
    from openakita_plugin_sdk import PluginAPI


PLUGIN_VERSION = "1.0.0"
SCHEDULER_TICK_S = 60.0

_LOG = logging.getLogger(f"openakita.plugin.{PLUGIN_ID}")


# --------------------------------------------------------------------------- #
# Pydantic body models (§10 routes)                                            #
# --------------------------------------------------------------------------- #


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTaskBody(_StrictBase):
    mode: Literal["radar_pull", "breakdown_url", "compare_accounts", "script_remix"]
    input: dict[str, Any] = Field(default_factory=dict)
    persona: str | None = None
    handoff_target: str | None = None


class SubscriptionBody(_StrictBase):
    id: str | None = None
    name: str
    platforms: list[str]
    keywords: list[str] = Field(default_factory=list)
    time_window: str = "24h"
    refresh_interval_min: int = Field(default=60, ge=5, le=24 * 60)
    enabled: bool = True
    persona: str | None = None
    mdrm_weighting: bool = True


class SettingsUpdateBody(_StrictBase):
    updates: dict[str, Any] = Field(default_factory=dict)


class CookiesUploadBody(_StrictBase):
    cookies_dict: dict[str, Any] | None = None
    json_text: str | None = None
    risk_acknowledged: bool = False


class CleanupBody(_StrictBase):
    older_than_days: int = Field(default=30, ge=1, le=365)


class AccountsPreviewBody(_StrictBase):
    urls: list[str]


class MdrmClearBody(_StrictBase):
    confirm: bool = False


class MdrmReindexBody(_StrictBase):
    from_days_ago: int = Field(default=30, ge=1, le=365)


# --------------------------------------------------------------------------- #
# Plugin                                                                       #
# --------------------------------------------------------------------------- #


class Plugin(PluginBase):
    """idea-research plugin entry."""

    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._data_dir: Path | None = None
        self._tm: IdeaTaskManager | None = None
        self._mdrm: MdrmAdapter | None = None
        self._dashscope: DashScopeClient | None = None
        self._cookies_vault: CookiesVault | None = None
        self._playwright_driver: PlaywrightDriver | None = None
        self._collectors: CollectorRegistry | None = None
        self._http: httpx.AsyncClient | None = None
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._running_tasks: set[str] = set()
        self._scheduler_stop = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir() or Path.cwd() / "data" / PLUGIN_ID
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir

        self._tm = IdeaTaskManager(db_path=data_dir / "idea.sqlite")
        self._mdrm = MdrmAdapter(api, plugin_id=PLUGIN_ID)
        self._http = httpx.AsyncClient(timeout=60.0)
        self._dashscope = DashScopeClient(
            client=self._http,
            api_key=self._read_setting("dashscope_api_key") or os.environ.get("DASHSCOPE_API_KEY"),
        )
        self._cookies_vault = CookiesVault(db_path=data_dir / "idea.sqlite")
        self._playwright_driver = PlaywrightDriver(max_concurrent=2)
        self._collectors = CollectorRegistry(
            http_client=self._http,
            vault=self._cookies_vault,
            playwright_driver=self._playwright_driver,
            ranker=Ranker(
                weights=RANKER_WEIGHTS,
                mdrm_search=self._mdrm.search_similar_hooks,
            ),
            normalizer=Normalizer(),
            engine_b_enabled=False,
        )

        router = self._build_router()
        api.register_api_routes(router)

        api.register_tools(self._tool_definitions(), self._handle_tool)

        loop = self._get_loop()
        if loop is not None:
            self._tasks["init"] = loop.create_task(self._async_init(), name=f"{PLUGIN_ID}:init")

        api.log(f"[{PLUGIN_ID}] loaded v{PLUGIN_VERSION} (data_dir={data_dir}, tools=9, routes=26)")

    def on_unload(self) -> None:
        loop = self._get_loop()
        self._scheduler_stop.set()
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        if loop is not None and not loop.is_closed():
            loop.create_task(self._async_unload(), name=f"{PLUGIN_ID}:unload")
        if self._api is not None:
            self._api.log(f"[{PLUGIN_ID}] unloading")
        self._api = None

    async def _async_init(self) -> None:
        assert self._tm is not None and self._api is not None
        try:
            await self._tm.init()
            await self._tm.list_personas()  # seeds 12 personas on first run
            if not (self._dashscope and self._dashscope.api_key):
                self._api.log(
                    f"[{PLUGIN_ID}] WARN DashScope key not configured; "
                    "breakdown_url / script_remix will fall back to brain.access if granted."
                )
            loop = self._get_loop()
            if loop is not None:
                self._tasks["scheduler"] = loop.create_task(
                    self._scheduler(), name=f"{PLUGIN_ID}:scheduler"
                )
        except Exception:  # pragma: no cover — defensive
            _LOG.exception("[%s] async init failed", PLUGIN_ID)

    async def _async_unload(self) -> None:
        for task in list(self._tasks.values()):
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, timeout=5.0)
        if self._collectors is not None:
            with suppress(Exception):
                await self._collectors.aclose()
        if self._http is not None:
            await self._http.aclose()
        if self._tm is not None:
            await self._tm.close()

    # ------------------------------------------------------------------ #
    # Settings helpers                                                     #
    # ------------------------------------------------------------------ #

    def _read_setting(self, key: str, default: Any = None) -> Any:
        if self._api is None:
            return default
        try:
            cfg = self._api.get_config() or {}
        except Exception:
            cfg = {}
        return cfg.get(key, default)

    async def _read_settings_async(self) -> dict[str, Any]:
        if self._tm is None:
            return {}
        return await self._tm.get_all_settings()

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.get_event_loop()
            except RuntimeError:
                return None

    # ------------------------------------------------------------------ #
    # FastAPI router (26 routes)                                           #
    # ------------------------------------------------------------------ #

    def _build_router(self) -> APIRouter:
        r = APIRouter()
        plugin = self

        # 1 POST /tasks
        @r.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            return await plugin._create_and_spawn_task(
                body.mode,
                body.input,
                persona=body.persona,
                handoff_target=body.handoff_target,
            )

        # 2 POST /cost-preview
        @r.post("/cost-preview")
        async def cost_preview(body: CreateTaskBody) -> dict[str, Any]:
            return estimate_cost(body.mode, body.input)

        # 3 GET /tasks
        @r.get("/tasks")
        async def list_tasks(
            mode: str | None = Query(default=None),
            status_q: str | None = Query(default=None, alias="status"),
            limit: int = Query(default=50, ge=1, le=200),
            offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
            assert plugin._tm is not None
            return await plugin._tm.list_tasks(
                mode=mode, status=status_q, limit=limit, offset=offset
            )

        # 4 GET /tasks/{id}
        @r.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            row = await plugin._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            return row

        # 5 POST /tasks/{id}/cancel
        @r.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            task = plugin._tasks.get(f"task:{task_id}")
            if task is not None and not task.done():
                task.cancel()
            await plugin._tm.update_task_safe(
                task_id,
                {"status": "canceled", "finished_at": int(time.time())},
            )
            plugin._broadcast("idea.task.canceled", {"task_id": task_id})
            return {"ok": True}

        # 6 POST /tasks/{id}/retry
        @r.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            row = await plugin._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            inp = row.get("input_json") or {}
            if isinstance(inp, str):
                inp = json.loads(inp or "{}")
            new = await plugin._create_and_spawn_task(row["mode"], inp)
            return {"new_task_id": new["task_id"]}

        # 7 DELETE /tasks/{id}
        @r.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            await plugin._tm.delete_task(task_id)
            wd = plugin._task_workdir(task_id)
            if wd.exists():
                shutil.rmtree(wd, ignore_errors=True)
            return {"ok": True}

        # 8 GET /tasks/{id}/breakdown
        @r.get("/tasks/{task_id}/breakdown")
        async def get_breakdown(task_id: str) -> dict[str, Any]:
            wd = plugin._task_workdir(task_id)
            path = wd / "breakdown.json"
            if not path.exists():
                raise HTTPException(status_code=404, detail="breakdown not found")
            return json.loads(path.read_text(encoding="utf-8"))

        # 9 GET /recommendations
        @r.get("/recommendations")
        async def list_recommendations(
            limit: int = Query(default=20, ge=1, le=200),
            platforms: str | None = Query(default=None),
            sort: str = Query(default="score"),
            mdrm_weighting: bool = Query(default=True),  # noqa: ARG001 — UI hint only
            only_saved: bool = Query(default=False),
        ) -> dict[str, Any]:
            assert plugin._tm is not None
            plats = [p.strip() for p in platforms.split(",") if p.strip()] if platforms else None
            items = await plugin._tm.list_trend_items(
                platforms=plats, limit=limit, sort=sort, only_saved=only_saved
            )
            return {"items": items}

        # 10 POST /items/{id}/save
        @r.post("/items/{item_id}/save")
        async def save_item(item_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            await plugin._tm.mark_item_saved(item_id, saved=True)
            return {"ok": True}

        # 11 GET /subscriptions
        @r.get("/subscriptions")
        async def list_subs() -> dict[str, Any]:
            assert plugin._tm is not None
            return {"subs": await plugin._tm.list_subscriptions()}

        # 12 POST /subscriptions
        @r.post("/subscriptions")
        async def upsert_sub(body: SubscriptionBody) -> dict[str, Any]:
            assert plugin._tm is not None
            sub = body.model_dump()
            sub["id"] = sub.get("id") or str(uuid.uuid4())
            await plugin._tm.upsert_subscription(sub)
            return sub

        # 13 DELETE /subscriptions/{id}
        @r.delete("/subscriptions/{sub_id}")
        async def delete_sub(sub_id: str) -> dict[str, Any]:
            assert plugin._tm is not None
            await plugin._tm.delete_subscription(sub_id)
            return {"ok": True}

        # 14 GET /settings
        @r.get("/settings")
        async def get_settings_route() -> dict[str, Any]:
            assert plugin._tm is not None
            return await plugin._tm.get_all_settings()

        # 15 PUT /settings
        @r.put("/settings")
        async def update_settings(body: SettingsUpdateBody) -> dict[str, Any]:
            assert plugin._tm is not None and plugin._dashscope is not None
            for key, value in (body.updates or {}).items():
                await plugin._tm.set_setting(key, value)
                if key == "dashscope_api_key" and isinstance(value, str):
                    plugin._dashscope.api_key = value
            return await plugin._tm.get_all_settings()

        # 16 GET /sources
        @r.get("/sources")
        async def get_sources() -> dict[str, Any]:
            assert plugin._tm is not None and plugin._cookies_vault is not None
            settings = await plugin._tm.get_all_settings()
            engine_b = bool(settings.get("engine_b_enabled", False))
            return {
                "engine_a": {"enabled": True, "channels": ["bilibili", "youtube", "rsshub"]},
                "engine_b": {
                    "enabled": engine_b,
                    "platforms": ["douyin", "xhs", "ks", "bilibili", "weibo"],
                    "cookies_status": await plugin._cookies_vault.list_status(),
                    "encryption_ready": plugin._cookies_vault.encryption_ready,
                    "warnings": plugin._cookies_vault.warn_messages,
                },
            }

        # 17 POST /sources/cookies/{platform}
        @r.post("/sources/cookies/{platform}")
        async def upload_cookies(platform: str, body: CookiesUploadBody) -> dict[str, Any]:
            assert plugin._tm is not None and plugin._cookies_vault is not None
            if not body.risk_acknowledged:
                raise HTTPException(
                    status_code=422,
                    detail="必须勾选 risk_acknowledged 才能保存 cookies",
                )
            payload = body.cookies_dict
            if payload is None and body.json_text:
                try:
                    payload = json.loads(body.json_text)
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=422, detail=f"invalid json: {exc}") from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="cookies must be a dict")
            await plugin._cookies_vault.save(platform, payload)
            return {"ok": True}

        # 18 POST /sources/cookies/{platform}/test
        @r.post("/sources/cookies/{platform}/test")
        async def test_cookies(platform: str) -> dict[str, Any]:
            assert plugin._tm is not None and plugin._cookies_vault is not None
            entry = await plugin._cookies_vault.load(platform)
            ok = bool(entry and entry.cookies)
            message = "ok" if ok else "no cookies stored for this platform"
            await plugin._tm.update_cookies_test(platform, ok=ok)
            plugin._broadcast(
                "idea.cookies.test_done",
                {"platform": platform, "ok": ok, "message": message},
            )
            return {"ok": ok, "message": message}

        # 19 POST /accounts/preview
        @r.post("/accounts/preview")
        async def accounts_preview(body: AccountsPreviewBody) -> dict[str, Any]:
            return {
                "accounts": [{"url": u, "platform_guess": _platform_guess(u)} for u in body.urls]
            }

        # 20 POST /cleanup
        @r.post("/cleanup")
        async def cleanup(body: CleanupBody) -> dict[str, Any]:
            assert plugin._tm is not None and plugin._data_dir is not None
            cutoff = int(time.time()) - body.older_than_days * 86400
            response = await plugin._tm.list_tasks(limit=10_000)
            deleted = 0
            freed = 0.0
            for t in response.get("tasks", []):
                created = int(t.get("created_at") or 0)
                if created and created < cutoff:
                    wd = plugin._task_workdir(t["id"])
                    if wd.exists():
                        freed += _dir_size_mb(wd)
                        shutil.rmtree(wd, ignore_errors=True)
                    await plugin._tm.delete_task(t["id"])
                    deleted += 1
            return {"deleted": deleted, "freed_mb": round(freed, 2)}

        # 21 GET /healthz
        @r.get("/healthz")
        async def healthz() -> dict[str, Any]:
            assert plugin._tm is not None
            mdrm_stats = (
                await plugin._mdrm.stats()
                if plugin._mdrm is not None
                else {"caps": {}, "hook_count": 0}
            )
            return {
                "ok": True,
                "version": PLUGIN_VERSION,
                "db": str(plugin._tm.db_path),
                "dashscope_key": bool(plugin._dashscope and plugin._dashscope.api_key),
                "engine_b": bool((await plugin._tm.get_all_settings()).get("engine_b_enabled")),
                "mdrm": mdrm_stats,
            }

        # 22/23 upload + uploads
        # add_upload_preview_routes wires both POST /upload and GET /uploads/{path}.
        plugin._upload_dir()  # ensure directory exists before route registration
        add_upload_preview_routes(
            r,
            upload_dir=plugin._upload_dir(),
            api=plugin._api,
        )

        # 24 GET /mdrm/stats
        @r.get("/mdrm/stats")
        async def mdrm_stats() -> dict[str, Any]:
            if plugin._mdrm is None:
                return {
                    "caps": {},
                    "hook_count": 0,
                    "missing_perms": ["brain.access", "vector.access", "memory.write"],
                }
            return await plugin._mdrm.stats()

        # 25 POST /mdrm/clear
        @r.post("/mdrm/clear")
        async def mdrm_clear(body: MdrmClearBody) -> dict[str, Any]:
            if not body.confirm:
                raise HTTPException(status_code=422, detail="confirm must be true")
            if plugin._mdrm is None:
                return {
                    "cleared": {"vector": "skipped", "memory": "skipped", "hook_library": "skipped"}
                }
            cleared = await plugin._mdrm.clear_all()
            assert plugin._tm is not None
            await plugin._tm.clear_hook_library()
            cleared["hook_library"] = "ok"
            return {"cleared": cleared}

        # 26 POST /mdrm/reindex
        @r.post("/mdrm/reindex")
        async def mdrm_reindex(body: MdrmReindexBody) -> dict[str, Any]:
            if plugin._mdrm is None:
                return {"reindexed": 0, "skipped": 0, "failed": 0}
            return await plugin._mdrm.reindex_all_breakdowns()

        return r

    # ------------------------------------------------------------------ #
    # Tools (§11 9 tools)                                                  #
    # ------------------------------------------------------------------ #

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "idea_radar_pull",
                "description": "拉取多平台爆款列表，按互动+时效+关键词+MDRM 评分排序。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "platforms": {"type": "array", "items": {"type": "string"}},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "time_window": {"type": "string"},
                        "engine": {"type": "string", "enum": ["auto", "a", "b"]},
                        "limit": {"type": "integer"},
                    },
                    "required": ["platforms"],
                },
            },
            {
                "name": "idea_breakdown_url",
                "description": "拆解单条视频 URL：ASR + 关键帧 VLM + 结构化 + 评论摘要 + persona takeaways。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "persona": {"type": "string"},
                        "enable_comments": {"type": "boolean"},
                        "write_to_mdrm": {"type": "boolean"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "idea_compare_accounts",
                "description": "对标 N 个账号近期视频，输出共性、差异、空白与建议。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_urls": {"type": "array", "items": {"type": "string"}},
                        "window": {"type": "string"},
                        "max_videos_per_account": {"type": "integer"},
                    },
                    "required": ["account_urls"],
                },
            },
            {
                "name": "idea_script_remix",
                "description": "把选题改写成 N 版可执行脚本（可选 MDRM 历史 hook 注入）。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "trend_item_id": {"type": "string"},
                        "my_persona": {"type": "string"},
                        "my_brand_keywords": {"type": "array", "items": {"type": "string"}},
                        "target_duration_seconds": {"type": "integer"},
                        "num_variants": {"type": "integer"},
                        "target_platform": {"type": "string"},
                        "use_mdrm_hints": {"type": "boolean"},
                    },
                    "required": ["my_persona", "target_platform"],
                },
            },
            {
                "name": "idea_subscribe",
                "description": "创建/更新雷达订阅。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "platforms": {"type": "array", "items": {"type": "string"}},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "time_window": {"type": "string"},
                        "refresh_interval_min": {"type": "integer"},
                    },
                    "required": ["name", "platforms"],
                },
            },
            {
                "name": "idea_unsubscribe",
                "description": "删除订阅。",
                "input_schema": {
                    "type": "object",
                    "properties": {"subscription_id": {"type": "string"}},
                    "required": ["subscription_id"],
                },
            },
            {
                "name": "idea_list_subscriptions",
                "description": "列出所有订阅。",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "idea_export",
                "description": "导出选题或拆解结果（json/markdown/csv）。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "trend_item_id": {"type": "string"},
                        "format": {"type": "string", "enum": ["json", "markdown", "csv"]},
                    },
                },
            },
            {
                "name": "idea_cancel",
                "description": "取消运行中任务。",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
        ]

    async def _handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        assert self._tm is not None
        if name == "idea_radar_pull":
            return await self._create_and_spawn_task("radar_pull", arguments)
        if name == "idea_breakdown_url":
            return await self._create_and_spawn_task("breakdown_url", arguments)
        if name == "idea_compare_accounts":
            return await self._create_and_spawn_task("compare_accounts", arguments)
        if name == "idea_script_remix":
            return await self._create_and_spawn_task("script_remix", arguments)
        if name == "idea_subscribe":
            sub = {**arguments, "id": arguments.get("id") or str(uuid.uuid4())}
            await self._tm.upsert_subscription(sub)
            return sub
        if name == "idea_unsubscribe":
            await self._tm.delete_subscription(arguments["subscription_id"])
            return {"ok": True}
        if name == "idea_list_subscriptions":
            return {"subs": await self._tm.list_subscriptions()}
        if name == "idea_export":
            return await self._tool_export(arguments)
        if name == "idea_cancel":
            tid = arguments["task_id"]
            task = self._tasks.get(f"task:{tid}")
            if task is not None and not task.done():
                task.cancel()
            await self._tm.update_task_safe(
                tid, {"status": "canceled", "finished_at": int(time.time())}
            )
            return {"ok": True}
        raise ValueError(f"unknown tool: {name}")

    async def _tool_export(self, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._tm is not None
        fmt = arguments.get("format", "json")
        if arguments.get("task_id"):
            row = await self._tm.get_task(arguments["task_id"])
            if row is None:
                raise ValueError("task not found")
            return {"format": fmt, "payload": row}
        if arguments.get("trend_item_id"):
            items = await self._tm.list_trend_items(limit=10_000)
            for it in items:
                if it.get("id") == arguments["trend_item_id"]:
                    return {"format": fmt, "payload": it}
            raise ValueError("trend_item_id not found")
        raise ValueError("must supply task_id or trend_item_id")

    # ------------------------------------------------------------------ #
    # Task spawning + pipeline runner                                      #
    # ------------------------------------------------------------------ #

    async def _create_and_spawn_task(
        self,
        mode: str,
        inp: dict[str, Any],
        *,
        persona: str | None = None,
        handoff_target: str | None = None,
    ) -> dict[str, Any]:
        assert self._tm is not None
        task_id = await self._tm.insert_task(mode=mode, input_payload=inp)
        eta = self._eta_seconds_for(mode, inp)
        self._broadcast(
            "idea.task.created",
            {"task_id": task_id, "mode": mode, "input_summary": _summarize(inp)},
        )
        loop = self._get_loop()
        if loop is not None:
            t = loop.create_task(
                self._run_task(task_id, mode, inp, persona=persona, handoff_target=handoff_target),
                name=f"{PLUGIN_ID}:task:{task_id}",
            )
            self._tasks[f"task:{task_id}"] = t
            t.add_done_callback(lambda _t, k=f"task:{task_id}": self._tasks.pop(k, None))
        return {"task_id": task_id, "status": "pending", "eta_s": eta}

    async def _run_task(
        self,
        task_id: str,
        mode: str,
        inp: dict[str, Any],
        *,
        persona: str | None,
        handoff_target: str | None,
    ) -> None:
        assert (
            self._tm is not None
            and self._dashscope is not None
            and self._collectors is not None
            and self._mdrm is not None
        )
        ctx = IdeaPipelineContext(
            task_id=task_id,
            mode=mode,
            input=inp,
            work_dir=self._task_workdir(task_id),
            tm=self._tm,
            registry=self._collectors,
            dashscope=self._dashscope,
            mdrm=self._mdrm,
            persona_name=persona or inp.get("persona"),
            handoff_target=handoff_target,
        )
        try:
            if mode == "breakdown_url":
                out = await run_breakdown_url(ctx)
            elif mode == "radar_pull":
                out = await run_radar_pull(ctx)
            elif mode == "compare_accounts":
                out = await run_compare_accounts(ctx)
            elif mode == "script_remix":
                out = await run_script_remix(ctx)
            else:
                raise ValueError(f"unknown mode: {mode}")
        except asyncio.CancelledError:
            self._broadcast("idea.task.canceled", {"task_id": task_id})
            raise
        except Exception as exc:
            row = await self._tm.get_task(task_id)
            error_kind = (row or {}).get("error_kind") or "unknown"
            hint = hint_for(error_kind)
            self._broadcast(
                "idea.task.failed",
                {
                    "task_id": task_id,
                    "error_kind": error_kind,
                    "hint_zh": hint["zh"],
                    "hint_en": hint["en"],
                    "message": str(exc),
                },
            )
            return
        self._broadcast(
            "idea.task.done",
            {"task_id": task_id, "mode": mode, "output_summary": _summarize(out)},
        )

    def _eta_seconds_for(self, mode: str, inp: dict[str, Any]) -> int:
        if mode == "radar_pull":
            return 30
        if mode == "breakdown_url":
            duration = int(inp.get("duration_seconds_estimate", 90))
            return min(600, 60 + duration // 2)
        if mode == "compare_accounts":
            return 45 + 15 * len(inp.get("account_urls") or [])
        return 40

    # ------------------------------------------------------------------ #
    # Scheduler                                                            #
    # ------------------------------------------------------------------ #

    async def _scheduler(self) -> None:
        assert self._tm is not None
        while not self._scheduler_stop.is_set():
            try:
                subs = await self._tm.list_subscriptions()
            except Exception:
                _LOG.exception("[%s] scheduler list_subscriptions failed", PLUGIN_ID)
                subs = []
            now = int(time.time())
            for sub in subs:
                if not sub.get("enabled", True):
                    continue
                interval = int(sub.get("refresh_interval_min") or 60) * 60
                last_run = int(sub.get("last_run_at") or 0)
                if now - last_run < interval:
                    continue
                inp = {
                    "platforms": sub.get("platforms") or [],
                    "keywords": sub.get("keywords") or [],
                    "time_window": sub.get("time_window") or "24h",
                    "limit": int(sub.get("limit") or 20),
                    "mdrm_weighting": bool(sub.get("mdrm_weighting", True)),
                    "engine": sub.get("engine") or "auto",
                    "subscription_id": sub.get("id"),
                }
                await self._create_and_spawn_task("radar_pull", inp, persona=sub.get("persona"))
                with suppress(Exception):
                    await self._tm.upsert_subscription({**sub, "last_run_at": now})
            try:
                await asyncio.wait_for(self._scheduler_stop.wait(), timeout=SCHEDULER_TICK_S)
            except TimeoutError:
                continue

    # ------------------------------------------------------------------ #
    # Misc helpers                                                         #
    # ------------------------------------------------------------------ #

    def _task_workdir(self, task_id: str) -> Path:
        assert self._data_dir is not None
        return self._data_dir / "tasks" / task_id

    def _upload_dir(self) -> Path:
        assert self._data_dir is not None
        d = self._data_dir / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        if self._api is None:
            return
        with suppress(Exception):
            self._api.broadcast_ui_event(event_type, data)


# --------------------------------------------------------------------------- #
# Module helpers                                                               #
# --------------------------------------------------------------------------- #


def _summarize(payload: Any, *, max_chars: int = 240) -> str:
    try:
        s = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        s = str(payload)
    return s[:max_chars]


def _platform_guess(url: str) -> str:
    u = (url or "").lower()
    if "bilibili.com" in u:
        return "bilibili"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "douyin.com" in u:
        return "douyin"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xhs"
    if "kuaishou.com" in u:
        return "ks"
    if "weibo.com" in u or "weibo.cn" in u:
        return "weibo"
    return "other"


def _dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            with suppress(OSError):
                total += p.stat().st_size
    return total / (1024 * 1024)


__all__ = [
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "Plugin",
]
