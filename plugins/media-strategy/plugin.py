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

from fastapi import APIRouter, Body, HTTPException, Query
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


class CreatePackageBody(_StrictBase):
    label_zh: str
    label_en: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    enabled: bool = True
    prefer_id: str = ""
    clone_from: str = ""


class UpdatePackageBody(_StrictBase):
    label_zh: str | None = None
    label_en: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    enabled: bool | None = None


class BulkPackageSourcesBody(_StrictBase):
    enabled: bool = True


class AddFeedBody(_StrictBase):
    name: str
    url: str
    package_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    authority: float | None = None


class UpdateSourceBody(_StrictBase):
    label_zh: str | None = None
    label_en: str | None = None
    url: str | None = None
    package_ids: list[str] | None = None
    authority: float | None = None
    enabled: bool | None = None


class ToggleSourceBody(_StrictBase):
    enabled: bool = True


class CreateTaskBody(_StrictBase):
    mode: Literal[
        "ingest",
        "hot_radar",
        "daily_brief",
        "verify_pack",
        "replicate_plan",
        "ai_topic_analysis",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class AiTopicAnalysisBody(_StrictBase):
    package_id: str = ""
    since_hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=10, ge=1, le=20)
    min_coverage: int = Field(default=1, ge=1, le=20)
    evidence_limit: int = Field(default=5, ge=1, le=8)


class TopTopicsBody(_StrictBase):
    package_id: str = ""
    since_hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=5, ge=1, le=20)
    min_coverage: int = Field(default=1, ge=1, le=20)
    compact: bool = True


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
        try:
            p = Path(value).expanduser()
        except Exception as exc:  # noqa: BLE001
            return None, f"路径解析失败：{exc}"
        if not p.is_absolute():
            return None, "请填写绝对路径，例如 D:\\media-strategy 或 /home/me/media-strategy"
        if not p.exists() and not p.parent.exists():
            return None, f"父目录不存在：{p.parent}"
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, f"无法创建目录：{exc}"
        try:
            probe = p / ".media_strategy_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            return None, f"目录不可写：{exc}"
        return p.resolve(), ""

    def _default_data_dir(self) -> Path:
        host = self._api.get_data_dir() if self._api is not None else None
        return Path(host) / "media_strategy" if host else Path.cwd() / ".media-strategy"

    def _resolve_data_dir(self) -> Path:
        cfg = self._load_settings()
        custom = str(cfg.get("custom_data_dir") or "").strip()
        if custom:
            path, err = self._validate_custom_data_dir(custom)
            if path is not None:
                return path
            if self._api is not None:
                self._api.log(f"{PLUGIN_ID}: ignoring invalid custom_data_dir {custom!r}: {err}", "warning")
        return self._default_data_dir()

    def _storage_dirs(self) -> dict[str, Path]:
        base = self._data_dir or self._resolve_data_dir()
        return {
            "data_dir": base,
            "outputs": base / "outputs",
            "database": base,
        }

    def _enriched_settings(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self._load_settings()
        requested = str(cfg.get("custom_data_dir") or "").strip()
        active = self._data_dir or self._resolve_data_dir()
        cfg["data_dir_active"] = str(active)
        cfg["data_dir_default"] = str(self._default_data_dir())
        cfg["data_dir_status"] = ""
        cfg["data_dir_pending_reload"] = False
        if requested:
            resolved, err = self._validate_custom_data_dir(requested)
            if resolved is None:
                cfg["data_dir_status"] = err
            else:
                cfg["custom_data_dir"] = str(resolved)
                cfg["data_dir_pending_reload"] = str(resolved) != str(active)
        else:
            cfg["data_dir_pending_reload"] = str(self._default_data_dir()) != str(active)
        return {
            "settings": settings or {},
            "host_config": cfg,
            "config": {**(settings or {}), **cfg},
        }

    def _build_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            from media_inline.dep_bootstrap import get_dep_state

            sources = await self._tm.list_sources()
            enabled_count = sum(1 for source in sources if source.get("enabled"))
            failed_count = sum(1 for source in sources if source.get("last_status") == "failed")
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
                "sources_total": len(sources),
                "sources_enabled": enabled_count,
                "sources_failed": failed_count,
                "deps": get_dep_state(),
                "timestamp": time.time(),
            }

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            settings = await self._tm.get_settings()
            return self._enriched_settings(settings)

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
            enriched = self._enriched_settings(settings)
            return {
                "ok": True,
                **enriched,
                "reload_required": bool(enriched["host_config"].get("data_dir_pending_reload")),
            }

        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            await self._ensure_ready()
            stats: dict[str, dict[str, Any]] = {}
            truncated_any = False
            max_files = 50000
            for key, folder in self._storage_dirs().items():
                total_bytes = 0
                file_count = 0
                truncated = False
                if folder.is_dir():
                    try:
                        for path in folder.rglob("*"):
                            try:
                                if path.is_file():
                                    total_bytes += path.stat().st_size
                                    file_count += 1
                                    if file_count >= max_files:
                                        truncated = True
                                        break
                            except OSError:
                                continue
                    except OSError:
                        pass
                truncated_any = truncated_any or truncated
                stats[key] = {
                    "path": str(folder),
                    "size_bytes": total_bytes,
                    "size_mb": round(total_bytes / 1048576, 1),
                    "file_count": file_count,
                    "truncated": truncated,
                }
            return {"ok": True, "stats": stats, "truncated": truncated_any}

        @router.post("/storage/open-folder")
        async def open_folder(body: dict[str, Any]) -> dict[str, Any]:
            raw_path = str(body.get("path") or "").strip()
            key = str(body.get("key") or "").strip()
            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                dirs = self._storage_dirs()
                if key not in dirs:
                    raise HTTPException(status_code=400, detail=f"Unknown key: {key}")
                target = dirs[key]
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Cannot create folder: {exc}") from exc
            import subprocess

            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except (OSError, FileNotFoundError) as exc:
                raise HTTPException(status_code=500, detail=f"Cannot open folder: {exc}") from exc
            return {"ok": True, "path": str(target)}

        @router.get("/storage/list-dir")
        async def list_dir(path: str = "") -> dict[str, Any]:
            raw = (path or "").strip()
            if not raw:
                anchors: list[dict[str, Any]] = []
                home = Path.home()
                anchors.append({"name": "Home", "path": str(home), "is_dir": True, "kind": "home"})
                for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Movies"):
                    p = home / sub
                    if p.is_dir():
                        anchors.append({"name": sub, "path": str(p), "is_dir": True, "kind": "shortcut"})
                if sys.platform == "win32":
                    import string

                    for letter in string.ascii_uppercase:
                        drive = Path(f"{letter}:/")
                        try:
                            if drive.exists():
                                anchors.append({
                                    "name": f"{letter}:",
                                    "path": str(drive),
                                    "is_dir": True,
                                    "kind": "drive",
                                })
                        except OSError:
                            continue
                else:
                    anchors.append({"name": "/", "path": "/", "is_dir": True, "kind": "drive"})
                return {"ok": True, "path": "", "parent": None, "items": anchors, "is_anchor": True}

            try:
                target = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            items: list[dict[str, Any]] = []
            try:
                for entry in target.iterdir():
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            items.append({"name": entry.name, "path": str(entry), "is_dir": True})
                    except (PermissionError, OSError):
                        continue
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            items.sort(key=lambda item: str(item["name"]).lower())
            parent_path = str(target.parent) if target.parent != target else None
            return {
                "ok": True,
                "path": str(target),
                "parent": parent_path,
                "items": items,
                "is_anchor": False,
            }

        @router.post("/storage/mkdir")
        async def make_dir(body: dict[str, Any]) -> dict[str, Any]:
            parent = str(body.get("parent") or "").strip()
            name = str(body.get("name") or "").strip()
            if not parent or not name:
                raise HTTPException(status_code=400, detail="Missing parent or name")
            if "/" in name or "\\" in name or name in (".", ".."):
                raise HTTPException(status_code=400, detail="Invalid folder name")
            try:
                parent_path = Path(parent).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not parent_path.is_dir():
                raise HTTPException(status_code=400, detail="Parent is not a directory")
            new_path = parent_path / name
            try:
                new_path.mkdir(parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail="Folder already exists") from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"ok": True, "path": str(new_path)}

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

        @router.post("/packages")
        async def create_package(body: CreatePackageBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            label = body.label_zh.strip()
            if not label:
                raise HTTPException(status_code=422, detail="label_zh is required")
            try:
                if body.clone_from:
                    package = await self._tm.clone_builtin_package(
                        body.clone_from, label_zh=label, prefer_id=body.prefer_id
                    )
                else:
                    package = await self._tm.add_custom_package(
                        label_zh=label,
                        label_en=body.label_en,
                        description=body.description,
                        keywords=body.keywords,
                        enabled=body.enabled,
                        prefer_id=body.prefer_id,
                    )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown source package: {body.clone_from}") from exc
            return {"ok": True, "package": package, "packages": await self._tm.list_packages()}

        @router.patch("/packages/{package_id}")
        async def update_package(package_id: str, body: UpdatePackageBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                package = await self._tm.update_package(
                    package_id,
                    label_zh=body.label_zh,
                    label_en=body.label_en,
                    description=body.description,
                    keywords=body.keywords,
                    enabled=body.enabled,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown package: {package_id}") from exc
            return {"ok": True, "package": package, "packages": await self._tm.list_packages()}

        @router.delete("/packages/{package_id}")
        async def delete_package(package_id: str) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                await self._tm.delete_custom_package(package_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown package: {package_id}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            return {"ok": True, "packages": await self._tm.list_packages()}

        @router.post("/packages/{package_id}/bulk-toggle-sources")
        async def bulk_toggle_sources(package_id: str, body: BulkPackageSourcesBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            stats = await self._tm.bulk_set_sources_enabled_for_package(package_id, body.enabled)
            return {"ok": True, "stats": stats, "sources": await self._tm.list_sources()}

        @router.get("/sources")
        async def sources() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            return {"sources": await self._tm.list_sources()}

        @router.post("/sources/sync")
        async def sync_sources() -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            stats = await self._tm.sync_builtin_sources()
            return {"ok": True, "stats": stats, "sources": await self._tm.list_sources()}

        @router.post("/sources/{source_id}/enabled")
        async def toggle_source(source_id: str, body: ToggleSourceBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                source = await self._tm.set_source_enabled(source_id, body.enabled)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown source: {source_id}") from exc
            return {"ok": True, "source": source}

        @router.patch("/sources/{source_id}")
        async def update_source(source_id: str, body: UpdateSourceBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            normalized_url: str | None = None
            if body.url is not None:
                try:
                    normalized_url = validate_feed_url(body.url)
                except Exception as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            try:
                source = await self._tm.update_source(
                    source_id,
                    label_zh=body.label_zh,
                    label_en=body.label_en,
                    url=normalized_url,
                    package_ids=body.package_ids,
                    authority=body.authority,
                    enabled=body.enabled,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown source: {source_id}") from exc
            return {"ok": True, "source": source}

        @router.delete("/sources/{source_id}")
        async def delete_source(source_id: str) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._tm is not None
            try:
                await self._tm.delete_custom_source(source_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"unknown source: {source_id}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            return {"ok": True, "sources": await self._tm.list_sources()}

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
            if body.authority is not None:
                source = await self._tm.update_source(
                    source["id"], authority=body.authority
                )
            return {"ok": True, "source": source}

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            await self._ensure_ready()
            return await self._create_and_run_task(body.mode, body.params)

        @router.post("/ingest")
        async def ingest_now(params: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
            await self._ensure_ready()
            return await self._create_and_run_task("ingest", params or {})

        @router.get("/radar")
        async def radar(
            package_id: str = "",
            q: str = "",
            since_hours: int = Query(default=24, ge=1, le=168),
            limit: int = Query(default=30, ge=1, le=100),
            cluster: bool = False,
            compact: bool = False,
            min_coverage: int = Query(default=1, ge=1, le=20),
        ) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._pipeline is not None
            if q.strip():
                return await self._pipeline.search_news(
                    {"q": q, "package_id": package_id, "limit": limit}
                )
            return await self._pipeline.hot_radar(
                {
                    "package_id": package_id,
                    "since_hours": since_hours,
                    "limit": limit,
                    "cluster": cluster,
                    "compact": compact,
                    "min_coverage": min_coverage,
                }
            )

        @router.post("/top-topics")
        async def top_topics(body: TopTopicsBody) -> dict[str, Any]:
            await self._ensure_ready()
            assert self._pipeline is not None
            return await self._pipeline.top_topics(body.model_dump())

        @router.post("/ai/analyze-top")
        async def ai_analyze_top(body: AiTopicAnalysisBody) -> dict[str, Any]:
            await self._ensure_ready()
            return await self._create_and_start_background_task(
                "ai_topic_analysis",
                body.model_dump(),
            )

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
        assert self._tm is not None
        task = await self._tm.create_task(mode, params)
        task_id = task["id"]
        return await self._run_existing_task(task_id, mode, params)

    async def _create_and_start_background_task(
        self, mode: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        await self._ensure_ready()
        assert self._tm is not None
        task = await self._tm.create_task(mode, params)
        task_id = task["id"]
        await self._tm.update_task(
            task_id,
            status="running",
            started_at=utcnow_iso(),
            progress=0.03,
            pipeline_step="任务已创建，等待执行",
        )
        if self._api is None:
            raise HTTPException(status_code=503, detail="plugin api is not ready")
        self._api.spawn_task(
            self._run_existing_task(task_id, mode, params),
            name=f"plugin:{PLUGIN_ID}:task:{task_id}",
        )
        return {"ok": True, "background": True, "task": await self._tm.get_task(task_id)}

    async def _run_existing_task(self, task_id: str, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_ready()
        assert self._tm is not None and self._pipeline is not None
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
            elif mode == "ai_topic_analysis":
                result = await self._pipeline.ai_topic_analysis(task_id, params)
            else:
                raise ValueError(f"unsupported mode: {mode}")
            await self._tm.update_task(
                task_id,
                status="done",
                progress=1.0,
                pipeline_step="已完成",
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
            _tool(
                "media_strategy_top_topics",
                "选题推荐：按多源覆盖+权威加权聚合输出 Top 5-10 高权重热点，仅返回标题与原文链接以节省 Token。",
                {
                    "package_id": "string",
                    "since_hours": "integer",
                    "limit": "integer",
                    "min_coverage": "integer",
                    "compact": "boolean",
                },
            ),
            _tool("media_strategy_search_news", "按关键词、分类检索新闻。", {"q": "string", "package_id": "string", "limit": "integer"}),
            _tool(
                "media_strategy_ai_analyze_topics",
                "对规则筛选后的 Top N 热点簇调用主程序大模型生成选题分析报告，避免逐条新闻烧模型。",
                {
                    "package_id": "string",
                    "since_hours": "integer",
                    "limit": "integer",
                    "min_coverage": "integer",
                    "evidence_limit": "integer",
                },
            ),
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
        if name == "media_strategy_top_topics":
            payload = dict(args)
            if "limit" not in payload:
                payload["limit"] = 5
            if "compact" not in payload:
                payload["compact"] = True
            return {"ok": True, **(await self._pipeline.top_topics(payload))}
        if name == "media_strategy_search_news":
            return {"ok": True, **(await self._pipeline.search_news(args))}
        if name == "media_strategy_ai_analyze_topics":
            return await self._create_and_run_task("ai_topic_analysis", args)
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
