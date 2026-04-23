"""ClipSense Video Editor — AI-powered video editing plugin.

Backend entry point providing REST API endpoints for the frontend UI.
Supports 4 editing modes: highlight extraction, silence removal,
topic splitting, and talking-head polish. Uses DashScope Paraformer
for ASR, Qwen for content analysis, and local ffmpeg for execution.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase
from clip_sense_inline.storage_stats import collect_storage_stats
from clip_sense_inline.system_deps import SystemDepsManager
from clip_sense_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)

from clip_asr_client import ClipAsrClient
from clip_ffmpeg_ops import FFmpegOps
from clip_models import (
    MODES,
    MODES_BY_ID,
    SILENCE_PRESETS,
    SILENCE_PRESETS_BY_ID,
    estimate_cost,
    get_error_hints,
    get_mode,
    mode_to_dict,
)
from clip_pipeline import ClipPipelineContext, run_pipeline
from clip_task_manager import TaskManager

logger = logging.getLogger(__name__)


# ── Request models ──

class CreateTaskBody(BaseModel):
    # ``extra="allow"`` keeps mode-specific UI flags (e.g. talking_polish's
    # remove_filler / remove_stutter / remove_repetition toggles) flowing
    # through to the per-mode pipeline params dict instead of being silently
    # dropped by Pydantic.
    model_config = ConfigDict(extra="allow")

    mode: str = "highlight_extract"
    source_video_path: str = ""
    source_url: str = ""
    flavor: str = ""
    target_count: int = 5
    target_duration: int = 30
    threshold_db: float = -40.0
    min_silence_sec: float = 0.5
    padding_sec: float = 0.1
    silence_preset: str = ""
    target_segment_duration: int = 180
    burn_subtitle: bool = False
    output_format: str = "mp4"
    # Talking-polish mode toggles (default: remove all detected categories).
    remove_filler: bool = True
    remove_stutter: bool = True
    remove_repetition: bool = True


class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]


# ── System dep installer (settings page) ──

class SystemInstallBody(BaseModel):
    """POST body for ``/system/{dep_id}/install``.

    ``method_index`` is the offset into the public method list returned by
    ``/system/components`` for this dep — defaults to 0 (the recommended
    method). The frontend usually just passes the index it computed from
    the snapshot it already has.
    """

    method_index: int = 0


class SystemUninstallBody(BaseModel):
    """POST body for ``/system/{dep_id}/uninstall``."""

    method_index: int = 0


class StorageOpenBody(BaseModel):
    """POST body for ``/storage/open-folder``.

    Either ``key`` (a known config slot like ``output_dir``) OR ``path``
    (a literal absolute path, after ``~`` expansion) must be provided.
    The backend mkdir -p's the target before launching the OS file
    manager so "Open" works even before the user customised the path.
    """

    model_config = ConfigDict(extra="ignore")

    key: str = ""
    path: str = ""


class StorageMkdirBody(BaseModel):
    """POST body for ``/storage/mkdir`` (folder picker "New folder")."""

    model_config = ConfigDict(extra="ignore")

    parent: str = ""
    name: str = ""


# ── Plugin entry ──

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._data_dir = data_dir
        self._tm = TaskManager(data_dir / "clip_sense.db")
        self._client: ClipAsrClient | None = None
        self._ffmpeg: FFmpegOps | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running_pipelines: dict[str, ClipPipelineContext] = {}
        # In-plugin system-dependency installer (FFmpeg via winget / brew /
        # apt / dnf). Mirrors seedance-video so the Settings UI gets the
        # same one-click install/uninstall flow + log panel — mandatory
        # because every clip-sense mode depends on a working ffmpeg.
        self._sysdeps = SystemDepsManager()

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "clip_sense_create",
                "description": "Create a video editing task (highlight extraction, silence removal, topic splitting, or talking-head polish)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["highlight_extract", "silence_clean", "topic_split", "talking_polish"]},
                        "source_video_path": {"type": "string", "description": "Path to the source video file"},
                        "flavor": {"type": "string", "description": "Highlight selection preference"},
                    },
                    "required": ["mode", "source_video_path"],
                },
            },
            {
                "name": "clip_sense_status",
                "description": "Check status of a clip-sense editing task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "clip_sense_list",
                "description": "List recent clip-sense editing tasks",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
            {
                "name": "clip_sense_cancel",
                "description": "Cancel a running clip-sense task",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
        ], handler=self._handle_tool)

        api.spawn_task(self._async_init(), name="clip-sense:init")
        api.log("ClipSense plugin loaded")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("dashscope_api_key")
        if api_key:
            self._client = ClipAsrClient(api_key)
        ffmpeg_path = await self._tm.get_config("ffmpeg_path") or ""
        self._ffmpeg = FFmpegOps(ffmpeg_path if ffmpeg_path else None)
        self._start_polling()

    async def on_unload(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("clip-sense poll task drain: %s", exc)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("clip-sense ASR client close: %s", exc)
        try:
            await self._sysdeps.aclose()
        except Exception as exc:
            logger.warning("clip-sense sysdeps close: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("clip-sense task manager close: %s", exc)

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "clip_sense_create":
            task = await self._create_task_internal(args)
            return f"Task created: {task['id']} (mode: {task['mode']}, status: {task['status']})"
        elif tool_name == "clip_sense_status":
            task = await self._tm.get_task(args.get("task_id", ""))
            if not task:
                return "Task not found"
            return (
                f"Task {task['id']}: status={task['status']}, mode={task['mode']}, "
                f"step={task.get('pipeline_step', 'N/A')}"
            )
        elif tool_name == "clip_sense_list":
            result = await self._tm.list_tasks(limit=args.get("limit", 10))
            lines = [f"Total: {result['total']} tasks"]
            for t in result["tasks"][:10]:
                lines.append(f"  {t['id']}: {t['mode']} / {t['status']}")
            return "\n".join(lines)
        elif tool_name == "clip_sense_cancel":
            tid = args.get("task_id", "")
            ctx = self._running_pipelines.get(tid)
            if ctx:
                ctx.cancelled = True
                return f"Cancel requested for task {tid}"
            return f"Task {tid} not found in running pipelines"
        return f"Unknown tool: {tool_name}"

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:
        uploads_dir = self._data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        add_upload_preview_route(router, base_dir=uploads_dir)

        # 1. POST /tasks — create task
        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            d = body.model_dump() if hasattr(body, "model_dump") else body.dict()
            task = await self._create_task_internal(d)
            return task

        # 2. GET /tasks — list tasks
        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict[str, Any]:
            return await self._tm.list_tasks(
                status=status, mode=mode, offset=offset, limit=limit
            )

        # 3. GET /tasks/{task_id} — get task
        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return task

        # 4. DELETE /tasks/{task_id}
        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, str]:
            if not await self._tm.delete_task(task_id):
                raise HTTPException(404, "Task not found")
            return {"status": "deleted"}

        # 5. POST /tasks/{task_id}/cancel
        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, str]:
            ctx = self._running_pipelines.get(task_id)
            if ctx:
                ctx.cancelled = True
                return {"status": "cancel_requested"}
            await self._tm.update_task(task_id, status="cancelled")
            return {"status": "cancelled"}

        # 6. POST /tasks/{task_id}/retry
        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] not in ("failed", "cancelled"):
                raise HTTPException(400, "Can only retry failed or cancelled tasks")
            new_params = task.get("params") or {}
            new_task = await self._create_task_internal({
                "mode": task["mode"],
                "source_video_path": task.get("source_video_path", ""),
                **new_params,
            })
            return new_task

        # 7. GET /tasks/{task_id}/download
        @router.get("/tasks/{task_id}/download")
        async def download_output(task_id: str) -> Any:
            from fastapi.responses import FileResponse
            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_path"):
                raise HTTPException(404, "Output not found")
            p = Path(task["output_path"])
            if not p.exists():
                raise HTTPException(404, "Output file missing")
            return FileResponse(p, filename=p.name)

        # 8. GET /tasks/{task_id}/subtitle
        @router.get("/tasks/{task_id}/subtitle")
        async def download_subtitle(task_id: str) -> Any:
            from fastapi.responses import FileResponse
            task = await self._tm.get_task(task_id)
            if not task or not task.get("subtitle_path"):
                raise HTTPException(404, "Subtitle not found")
            p = Path(task["subtitle_path"])
            if not p.exists():
                raise HTTPException(404, "Subtitle file missing")
            return FileResponse(p, filename=p.name, media_type="text/plain")

        # 9. GET /tasks/{task_id}/transcript
        @router.get("/tasks/{task_id}/transcript")
        async def get_transcript(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task or not task.get("transcript_id"):
                raise HTTPException(404, "Transcript not found")
            tr = await self._tm.get_transcript(task["transcript_id"])
            if not tr:
                raise HTTPException(404, "Transcript record not found")
            return tr

        # 10. POST /upload — honours Settings ▸ Folder ▸ "Upload cache
        # folder" override so the per-folder stat card and the "Open"
        # button always point at where the bytes actually land.
        @router.post("/upload")
        async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
            cfg_uploads = (await self._tm.get_config("uploads_dir") or "").strip()
            uploads_dir = (
                Path(cfg_uploads).expanduser() if cfg_uploads
                else self._data_dir / "uploads"
            )
            uploads_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename or 'video.mp4'}"
            dest = uploads_dir / safe_name
            with open(dest, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
            url = build_preview_url("clip-sense", safe_name)
            return {
                "path": str(dest),
                "filename": safe_name,
                "url": url,
                "size": dest.stat().st_size,
            }

        # 11. GET /library
        @router.get("/library")
        async def list_library(offset: int = 0, limit: int = 50) -> dict[str, Any]:
            return await self._tm.list_transcripts(offset=offset, limit=limit)

        # 12. DELETE /library/{tid}
        # NOTE: re-transcription on demand (POST /library/{tid}/transcribe)
        # was intentionally NOT shipped in v1 — re-running ASR on an existing
        # source is achieved transparently by creating a new task on the
        # same source path (the pipeline reuses cached transcripts via the
        # source_hash deduplication in TaskManager.create_transcript).
        @router.delete("/library/{tid}")
        async def delete_library(tid: str) -> dict[str, str]:
            if not await self._tm.delete_transcript(tid):
                raise HTTPException(404, "Not found")
            return {"status": "deleted"}

        # 14. GET /settings
        @router.get("/settings")
        async def get_settings() -> dict[str, str]:
            return await self._tm.get_all_config()

        # 15. PUT /settings
        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict[str, str]:
            await self._tm.set_configs(body.updates)
            if "dashscope_api_key" in body.updates:
                key = body.updates["dashscope_api_key"]
                if key:
                    if self._client:
                        self._client.update_api_key(key)
                    else:
                        self._client = ClipAsrClient(key)
                else:
                    self._client = None
            if "ffmpeg_path" in body.updates:
                fp = body.updates["ffmpeg_path"]
                self._ffmpeg = FFmpegOps(fp if fp else None)
            return {"status": "ok"}

        # 16. GET /storage/stats
        # Per-folder snapshot in the same shape seedance-video returns,
        # so the Settings page can render one stat card per managed
        # directory (output / uploads / tasks). ``output_dir`` falls back
        # to ~/clip-sense-output to mirror seedance's "default folder
        # opens even before the user customised anything" behaviour.
        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            config = await self._tm.get_all_config()
            stats: dict[str, dict[str, Any]] = {}
            truncated_any = False
            for key, default in self._storage_defaults(config).items():
                target = Path(default)
                report = await collect_storage_stats(
                    target, max_files=20000, sample_paths=0, skip_hidden=True,
                )
                truncated_any = truncated_any or report.truncated
                stats[key] = {
                    "path": str(target),
                    "size_bytes": report.total_bytes,
                    "size_mb": round(report.total_bytes / 1048576, 1),
                    "file_count": report.total_files,
                    "truncated": report.truncated,
                }
            return {"ok": True, "stats": stats, "truncated": truncated_any}

        # 17. POST /storage/open-folder — mkdir -p target then open in OS
        # file manager. Resolves either an explicit ``path`` or a known
        # config ``key`` (output_dir / uploads_dir / tasks_dir) so the
        # button works even before the user customised anything.
        @router.post("/storage/open-folder")
        async def open_folder(body: StorageOpenBody) -> dict[str, Any]:
            raw_path = (body.path or "").strip()
            key = (body.key or "").strip()
            if not raw_path and not key:
                raise HTTPException(400, "Missing path or key")

            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                config = await self._tm.get_all_config()
                defaults = self._storage_defaults(config)
                if key not in defaults:
                    raise HTTPException(400, f"Unknown key: {key}")
                target = Path(defaults[key])

            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    500, f"Cannot create folder: {exc}"
                ) from exc

            import subprocess
            import sys
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", str(target)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except (OSError, FileNotFoundError) as exc:
                raise HTTPException(
                    500, f"Cannot open folder: {exc}"
                ) from exc
            return {"ok": True, "path": str(target)}

        # 18. GET /storage/list-dir — folder picker navigation. Empty
        # ``path`` returns the anchor list (Home + common subfolders +
        # Windows drive letters / Unix root).
        @router.get("/storage/list-dir")
        async def list_dir(path: str = "") -> dict[str, Any]:
            import sys
            raw = (path or "").strip()
            if not raw:
                anchors: list[dict[str, Any]] = []
                home = Path.home()
                anchors.append({
                    "name": "Home", "path": str(home), "is_dir": True,
                    "kind": "home",
                })
                for sub in (
                    "Desktop", "Documents", "Downloads",
                    "Pictures", "Videos", "Movies",
                ):
                    p = home / sub
                    if p.is_dir():
                        anchors.append({
                            "name": sub, "path": str(p), "is_dir": True,
                            "kind": "shortcut",
                        })
                if sys.platform == "win32":
                    import string
                    for letter in string.ascii_uppercase:
                        drv = Path(f"{letter}:/")
                        try:
                            if drv.exists():
                                anchors.append({
                                    "name": f"{letter}:",
                                    "path": str(drv),
                                    "is_dir": True,
                                    "kind": "drive",
                                })
                        except OSError:
                            continue
                else:
                    anchors.append({
                        "name": "/", "path": "/", "is_dir": True,
                        "kind": "drive",
                    })
                return {
                    "ok": True, "path": "", "parent": None,
                    "items": anchors, "is_anchor": True,
                }

            try:
                target = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(400, str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(400, "Not a directory")

            items: list[dict[str, Any]] = []
            try:
                for entry in target.iterdir():
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            items.append({
                                "name": name, "path": str(entry),
                                "is_dir": True,
                            })
                    except (PermissionError, OSError):
                        continue
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(500, str(exc)) from exc

            items.sort(key=lambda it: it["name"].lower())
            parent_path = (
                str(target.parent) if target.parent != target else None
            )
            return {
                "ok": True, "path": str(target), "parent": parent_path,
                "items": items, "is_anchor": False,
            }

        # 19. POST /storage/mkdir — folder picker "New folder" action.
        @router.post("/storage/mkdir")
        async def make_dir(body: StorageMkdirBody) -> dict[str, Any]:
            parent = (body.parent or "").strip()
            name = (body.name or "").strip()
            if not parent or not name:
                raise HTTPException(400, "Missing parent or name")
            if "/" in name or "\\" in name or name in (".", ".."):
                raise HTTPException(400, "Invalid folder name")
            try:
                parent_path = Path(parent).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(400, str(exc)) from exc
            if not parent_path.is_dir():
                raise HTTPException(400, "Parent is not a directory")
            new_path = parent_path / name
            try:
                new_path.mkdir(parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise HTTPException(
                    409, "Folder already exists"
                ) from exc
            except OSError as exc:
                raise HTTPException(500, str(exc)) from exc
            return {"ok": True, "path": str(new_path)}

        # 20. GET /ffmpeg/status — legacy detection used by Create/Tasks
        # tabs to guard the "Run" button. Routes through SystemDepsManager
        # so this endpoint and the Settings panel agree.
        @router.get("/ffmpeg/status")
        async def ffmpeg_status() -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            try:
                snap = await loop.run_in_executor(
                    None, self._sysdeps.detect, "ffmpeg",
                )
                return {
                    "available": bool(snap.get("found")),
                    "version": snap.get("version", ""),
                    "path": snap.get("location", ""),
                }
            except Exception:
                if self._ffmpeg:
                    return await loop.run_in_executor(
                        None, self._ffmpeg.detect,
                    )
                return {"available": False, "version": "", "path": ""}

        # 21. GET /system/components — Settings page snapshot of every
        # managed system dep (currently just ffmpeg).
        @router.get("/system/components")
        async def system_components() -> dict[str, Any]:
            return {"ok": True, "items": self._sysdeps.list_components()}

        # 22. POST /system/{dep_id}/install
        @router.post("/system/{dep_id}/install")
        async def system_install(
            dep_id: str, body: SystemInstallBody,
        ) -> dict[str, Any]:
            try:
                result = await self._sysdeps.start_install(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(422, result)
            # After a successful winget/brew install the live FFmpegOps
            # client is still pinned to the old (missing) PATH. Refresh
            # it so subsequent pipeline runs pick up the new binary
            # without requiring a plugin reload.
            if result.get("ok"):
                try:
                    fp = await self._tm.get_config("ffmpeg_path") or ""
                    self._ffmpeg = FFmpegOps(fp if fp else None)
                except Exception as exc:
                    logger.debug("post-install ffmpeg refresh failed: %s", exc)
            return result

        # 23. POST /system/{dep_id}/uninstall
        @router.post("/system/{dep_id}/uninstall")
        async def system_uninstall(
            dep_id: str, body: SystemUninstallBody,
        ) -> dict[str, Any]:
            try:
                result = await self._sysdeps.start_uninstall(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(422, result)
            return result

        # 24. GET /system/{dep_id}/status — install/uninstall poll target.
        @router.get("/system/{dep_id}/status")
        async def system_status(dep_id: str) -> dict[str, Any]:
            try:
                return self._sysdeps.status(dep_id)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

        # 25. GET /permissions/check — drives the in-app "Grant" banner
        # so first-time users are not silently broken by an un-granted
        # manifest permission.
        @router.get("/permissions/check")
        async def permissions_check() -> dict[str, Any]:
            required = [
                ("brain.access",   "AI 内容分析（高光提取 / 段落拆条 需要主进程 LLM）"),
                ("routes.register", "插件 HTTP 接口（前端调用）"),
                ("data.own",       "本地任务/字幕缓存（SQLite + 输出文件）"),
                ("config.write",   "保存 API Key、FFmpeg 路径与默认参数"),
            ]
            checks = [
                {
                    "permission": p,
                    "feature": label,
                    "granted": bool(self._api.has_permission(p)),
                }
                for p, label in required
            ]
            missing = [c["permission"] for c in checks if not c["granted"]]
            return {
                "ok": True,
                "all_granted": not missing,
                "missing": missing,
                "checks": checks,
            }

        # 26. GET /modes
        @router.get("/modes")
        async def get_modes() -> list[dict[str, Any]]:
            return [mode_to_dict(m) for m in MODES]

    # ── Storage helpers ──

    def _storage_defaults(self, config: dict[str, str]) -> dict[str, str]:
        """Return the resolved (user override OR default) path for every
        managed storage slot. Keep keys in sync with the Settings folder
        section + ``/storage/open-folder``.
        """
        return {
            "output_dir": (
                (config.get("output_dir") or "").strip()
                or str(Path.home() / "clip-sense-output")
            ),
            "uploads_dir": (
                (config.get("uploads_dir") or "").strip()
                or str(self._data_dir / "uploads")
            ),
            "tasks_dir": (
                (config.get("tasks_dir") or "").strip()
                or str(self._data_dir / "tasks")
            ),
        }

    # ── Internal task creation ──

    async def _create_task_internal(self, args: dict[str, Any]) -> dict[str, Any]:
        mode_id = args.get("mode", "highlight_extract")
        mode_def = MODES_BY_ID.get(mode_id)
        if not mode_def:
            raise HTTPException(400, f"Unknown mode: {mode_id}")

        source_path = args.get("source_video_path", "")
        if not source_path:
            raise HTTPException(400, "source_video_path is required")

        preset_id = args.get("silence_preset", "")
        if preset_id and preset_id in SILENCE_PRESETS_BY_ID:
            preset = SILENCE_PRESETS_BY_ID[preset_id]
            args.setdefault("threshold_db", preset.threshold_db)
            args.setdefault("min_silence_sec", preset.min_silence_sec)
            args.setdefault("padding_sec", preset.padding_sec)

        params = {
            k: v for k, v in args.items()
            if k not in ("mode", "source_video_path", "source_url")
        }

        task = await self._tm.create_task(
            mode=mode_id,
            source_video_path=source_path,
            params=params,
        )

        source_url = args.get("source_url", "")
        if not source_url and Path(source_path).exists():
            rel = Path(source_path).name
            source_url = build_preview_url("clip-sense", rel)

        # Honour the user's Settings ▸ Folder ▸ "Output dir" override so
        # the final mp4 lands wherever they pointed it (seedance-style).
        # Falls back to ``<plugin_data_dir>/tasks`` when not customised
        # so existing behaviour is preserved.
        cfg_output = (await self._tm.get_config("output_dir") or "").strip()
        base_tasks_dir = (
            Path(cfg_output).expanduser() if cfg_output
            else self._data_dir / "tasks"
        )
        task_dir = base_tasks_dir / task["id"]
        ctx = ClipPipelineContext(
            task_id=task["id"],
            mode=mode_id,
            params=params,
            task_dir=task_dir,
            source_video_path=Path(source_path),
            source_url=source_url,
        )
        self._running_pipelines[task["id"]] = ctx
        self._api.spawn_task(
            self._run_task(ctx), name=f"clip-sense:task:{task['id']}"
        )

        return task

    async def _run_task(self, ctx: ClipPipelineContext) -> None:
        try:
            await run_pipeline(
                ctx, self._tm, self._client, self._ffmpeg, self._emit
            )
        except Exception as exc:
            logger.exception("clip-sense pipeline unexpected error: %s", exc)
        finally:
            self._running_pipelines.pop(ctx.task_id, None)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._api.broadcast_ui_event(event, data)
        except Exception:
            pass

    # ── Polling ──

    def _start_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Periodic check for stale running tasks."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    running = await self._tm.get_running_tasks()
                    for t in running:
                        tid = t["id"]
                        if tid not in self._running_pipelines:
                            await self._tm.update_task(
                                tid, status="failed",
                                error_kind="unknown",
                                error_message="Task found in running state but no pipeline context (likely server restart)",
                            )
                except Exception as exc:
                    logger.warning("clip-sense poll error: %s", exc)
        except asyncio.CancelledError:
            pass
