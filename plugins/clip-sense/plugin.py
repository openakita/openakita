"""ClipSense Video Editor — AI-powered video editing plugin.

Backend entry point providing REST API endpoints for the frontend UI.
Supports 4 editing modes: highlight extraction, silence removal,
topic splitting, and talking-head polish. Uses DashScope Paraformer
for ASR, Qwen for content analysis, and local ffmpeg for execution.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from clip_asr_client import ClipAsrClient
from clip_ffmpeg_ops import FFmpegOps
from clip_models import (
    MODES,
    MODES_BY_ID,
    SILENCE_PRESETS_BY_ID,
    mode_to_dict,
)
from clip_pipeline import ClipPipelineContext, run_pipeline
from clip_sense_inline.storage_stats import collect_storage_stats
from clip_sense_inline.system_deps import SystemDepsManager
from clip_sense_inline.upload_preview import DEFAULT_PREVIEW_EXTENSIONS, build_preview_url
from clip_task_manager import TaskManager
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

_UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_INTERNAL_PARAM_PREFIX = "_"


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
    output_format: str | None = None
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
        await self._ensure_client_from_config()
        ffmpeg_path = await self._tm.get_config("ffmpeg_path") or ""
        self._ffmpeg = FFmpegOps(ffmpeg_path if ffmpeg_path else None)
        self._start_polling()

    async def _ensure_client_from_config(self) -> None:
        cfg = await self._tm.get_all_config()
        api_key, base_url = self._resolve_asr_endpoint(cfg)
        analysis_provider = cfg.get("analysis_provider") or "host"
        analysis_api_key = cfg.get("dashscope_analysis_api_key") or ""
        brain = self._get_host_brain()
        if api_key:
            # Rebuild on relay swap so the new base_url actually takes
            # effect (update_api_key alone keeps the previous transport
            # client). Cost is negligible — settings updates are rare.
            if self._client and not base_url:
                self._client.update_api_key(api_key)
            else:
                self._client = (
                    ClipAsrClient(api_key, base_url=base_url)
                    if base_url
                    else ClipAsrClient(api_key)
                )
            self._client.configure_analysis(
                provider=analysis_provider,
                brain=brain,
                api_key=analysis_api_key,
            )
        else:
            self._client = None

    def _resolve_asr_endpoint(self, cfg: dict) -> tuple[str, str]:
        """Resolve ASR api_key + base_url honouring an optional relay.

        Same shape as the other vendor plugins. Strict policy + missing
        relay raises HTTPException(400) so the Settings UI banner has
        actionable text.
        """
        api_key = (cfg.get("dashscope_api_key") or "").strip()
        relay_name = (cfg.get("dashscope_relay_endpoint") or "").strip()
        if not relay_name:
            return api_key, ""
        try:
            from openakita.relay import (
                SettingsRelayResolutionError,
                apply_relay_override,
            )

            merged = apply_relay_override(
                {
                    "api_key": api_key,
                    "base_url": "",
                    "relay_endpoint": relay_name,
                    "relay_fallback_policy": (
                        cfg.get("dashscope_relay_fallback_policy") or "official"
                    ),
                },
                required_capability="audio",
                plugin_name="clip-sense",
            )
        except (ImportError, ModuleNotFoundError) as exc:
            logger.info(
                "clip-sense: openakita.relay not importable (%s); "
                "keeping per-plugin DashScope endpoint",
                exc,
            )
            return api_key, ""
        except SettingsRelayResolutionError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=exc.user_message) from exc
        return (merged.get("api_key") or "").strip(), (merged.get("base_url") or "").strip()

    def _get_host_brain(self) -> Any:
        try:
            return self._api.get_brain()
        except Exception as exc:
            logger.debug("clip-sense host brain unavailable: %s", exc)
            return None

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
            task = await self._tm.get_task(tid)
            if not task:
                return f"Task {tid} not found"
            await self._tm.update_task(tid, status="cancelled")
            return f"Task {tid} marked as cancelled"
        return f"Unknown tool: {tool_name}"

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:
        @router.get("/uploads/{rel_path:path}")
        async def serve_upload(rel_path: str) -> Any:
            """Serve previews from the currently configured uploads directory."""
            from fastapi.responses import FileResponse

            if not rel_path or "\x00" in rel_path:
                raise HTTPException(404, "not found")
            base = Path(
                self._storage_defaults(await self._tm.get_all_config())["uploads_dir"]
            ).resolve()
            try:
                candidate = (base / rel_path).resolve()
                candidate.relative_to(base)
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(403, "forbidden") from exc
            if not candidate.is_file():
                raise HTTPException(404, "not found")
            ext = candidate.suffix.lower().lstrip(".")
            if ext not in DEFAULT_PREVIEW_EXTENSIONS:
                raise HTTPException(404, "not found")
            if candidate.stat().st_size > 50 * 1024 * 1024:
                raise HTTPException(413, "file too large")
            return FileResponse(
                candidate,
                headers={"Cache-Control": "public, max-age=300"},
            )

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
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            await self._tm.delete_task(task_id)
            # Clean up both task workspace and generated-output directory.
            config = await self._tm.get_all_config()
            defaults = self._storage_defaults(config)
            for task_dir in {
                Path(defaults["tasks_dir"]) / task_id,
                Path(defaults["output_dir"]) / task_id,
            }:
                if task_dir.exists() and task_dir.is_dir():
                    import shutil as _shutil
                    _shutil.rmtree(task_dir, ignore_errors=True)
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
        async def download_output(task_id: str, index: int | None = None) -> Any:
            """Download output. For topic_split with multiple files:
            - ?index=N  → download the Nth topic file (0-based)
            - ?index=-1 → download the zip of all topics
            - no index  → download the primary output_path (first topic)
            """
            from fastapi.responses import FileResponse
            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_path"):
                raise HTTPException(404, "Output not found")
            params = task.get("params") or {}
            if isinstance(params, str):
                import json as _json
                try:
                    params = _json.loads(params)
                except Exception:
                    params = {}
            topic_files = params.get("_topic_files") or []
            topics_zip = params.get("_topics_zip") or ""
            if index is not None:
                if index == -1 and topics_zip:
                    p = Path(topics_zip)
                elif 0 <= index < len(topic_files):
                    p = Path(topic_files[index])
                else:
                    raise HTTPException(404, "Topic index out of range")
            else:
                p = Path(task["output_path"])
            p = await self._resolve_task_file(task_id, p, kind="output")
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
            p = await self._resolve_task_file(
                task_id, Path(task["subtitle_path"]), kind="subtitle"
            )
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
            dirs = self._storage_defaults(await self._tm.get_all_config())
            uploads_dir = Path(dirs["uploads_dir"])
            uploads_dir.mkdir(parents=True, exist_ok=True)
            safe_name = self._safe_upload_name(file.filename or "video.mp4")
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
            raw = await self._tm.get_all_config()
            out = dict(raw)
            for k in ("output_dir", "uploads_dir", "tasks_dir"):
                v = out.get(k, "")
                if self._is_placeholder_or_hint_path(v):
                    out[k] = ""
            return out

        # 15. PUT /settings
        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict[str, str]:
            updates = dict(body.updates)
            if "analysis_provider" in updates:
                provider = (updates["analysis_provider"] or "host").strip().lower()
                updates["analysis_provider"] = provider if provider in ("host", "dashscope") else "host"
            for k in ("output_dir", "uploads_dir", "tasks_dir"):
                if k not in updates:
                    continue
                if self._is_placeholder_or_hint_path(updates[k]):
                    updates[k] = ""
            await self._tm.set_configs(updates)
            if any(
                k in updates
                for k in (
                    "dashscope_api_key",
                    "analysis_provider",
                    "dashscope_analysis_api_key",
                    "dashscope_relay_endpoint",
                    "dashscope_relay_fallback_policy",
                )
            ):
                await self._ensure_client_from_config()
            if "ffmpeg_path" in updates:
                fp = updates["ffmpeg_path"]
                self._ffmpeg = FFmpegOps(fp if fp else None)
            return {"status": "ok"}

        # 16. GET /storage/stats
        # Per-folder snapshot in the same shape seedance-video returns,
        # so the Settings page can render one stat card per managed
        # directory (output / uploads / tasks). Empty ``output_dir`` in
        # config uses ``<plugin_data_dir>/tasks`` — same parent as task
        # workspaces created by ``_create_task_internal``.
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

        # 16b. POST /storage/clear-cache — remove cached/output files
        @router.post("/storage/clear-cache")
        async def clear_cache(target: str = "uploads") -> dict[str, Any]:
            """Clear files in a managed directory.
            target: 'uploads' | 'output' | 'all'
            """
            config = await self._tm.get_all_config()
            defaults = self._storage_defaults(config)
            dirs_to_clear: list[Path] = []
            if target in ("uploads", "all"):
                dirs_to_clear.append(Path(defaults["uploads_dir"]))
            if target in ("output", "all"):
                dirs_to_clear.append(Path(defaults["output_dir"]))
            freed_bytes = 0
            removed_files = 0
            for dir_path in dirs_to_clear:
                if self._is_dangerous_clear_target(dir_path):
                    raise HTTPException(400, f"Refusing to clear unsafe directory: {dir_path}")
                if not dir_path.exists():
                    continue
                for f in dir_path.rglob("*"):
                    if f.is_file():
                        try:
                            freed_bytes += f.stat().st_size
                            f.unlink()
                            removed_files += 1
                        except OSError:
                            pass
                for d in sorted(dir_path.rglob("*"), reverse=True):
                    if d.is_dir():
                        try:
                            d.rmdir()
                        except OSError:
                            pass
            # When output dir is cleared, nullify file paths on all tasks
            # so the UI won't show broken video/download links.
            if target in ("output", "all"):
                all_tasks = await self._tm.list_tasks(limit=9999)
                for t in all_tasks.get("tasks", []):
                    if t.get("output_path") or t.get("subtitle_path"):
                        updates: dict[str, Any] = {}
                        if t.get("output_path"):
                            updates["output_path"] = ""
                        if t.get("subtitle_path"):
                            updates["subtitle_path"] = ""
                        await self._tm.update_task(t["id"], **updates)

            return {
                "ok": True,
                "freed_bytes": freed_bytes,
                "freed_mb": round(freed_bytes / 1048576, 1),
                "removed_files": removed_files,
            }

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

    @staticmethod
    def _is_placeholder_or_hint_path(value: str) -> bool:
        """True if the stored config is empty or looks like UI placeholder / hint text.

        Users sometimes blur-save the placeholder line (e.g. ``默认: <插件数据目录>/tasks/``)
        into SQLite; ``Path`` would then point at a non-existent name and storage stats
        stay at zero even though real files live under the plugin data directory.
        """
        s = (value or "").strip()
        if not s:
            return True
        low = s.lower()
        if s.startswith("<") or "<" in s:
            return True
        if "插件数据" in s:
            return True
        if s.startswith("默认:") or low.startswith("default:"):
            return True
        if "plugin data dir" in low or "<plugin" in low:
            return True
        return False

    @staticmethod
    def _normalize_config_path(path_str: str) -> str:
        """Expand ``~`` / user profile and resolve to an absolute path.

        SQLite stores folder overrides like ``~/clip-sense-output``; ``Path``
        does not treat that as existing unless expanded, so storage walks
        would otherwise return zero bytes/files on Windows/macOS.
        """
        s = (path_str or "").strip()
        if not s:
            return ""
        try:
            return str(Path(s).expanduser().resolve(strict=False))
        except (OSError, RuntimeError):
            return str(Path(s).expanduser())

    def _storage_defaults(self, config: dict[str, str]) -> dict[str, str]:
        """Return the resolved (user override OR default) path for every
        managed storage slot. Keep keys in sync with the Settings folder
        section + ``/storage/open-folder``.

        UI placeholders like ``<插件数据目录>/uploads/`` must not be passed
        to ``Path`` literally — they are treated as “use plugin default”.
        """
        raw_out = (config.get("output_dir") or "").strip()
        if self._is_placeholder_or_hint_path(raw_out):
            out_dir = str(self._data_dir / "tasks")
        else:
            out_dir = raw_out

        raw_up = (config.get("uploads_dir") or "").strip()
        if self._is_placeholder_or_hint_path(raw_up):
            uploads = str(self._data_dir / "uploads")
        else:
            uploads = raw_up

        raw_td = (config.get("tasks_dir") or "").strip()
        if self._is_placeholder_or_hint_path(raw_td):
            tasks = str(self._data_dir / "tasks")
        else:
            tasks = raw_td

        return {
            "output_dir": self._normalize_config_path(out_dir),
            "uploads_dir": self._normalize_config_path(uploads),
            "tasks_dir": self._normalize_config_path(tasks),
        }

    async def _task_dirs(self, task_id: str) -> tuple[Path, Path]:
        defaults = self._storage_defaults(await self._tm.get_all_config())
        return Path(defaults["tasks_dir"]) / task_id, Path(defaults["output_dir"]) / task_id

    async def _allowed_task_roots(self, task_id: str) -> list[Path]:
        task_dir, output_dir = await self._task_dirs(task_id)
        roots = {task_dir.resolve(strict=False), output_dir.resolve(strict=False)}
        return list(roots)

    async def _resolve_task_file(self, task_id: str, path: Path, *, kind: str) -> Path:
        try:
            candidate = path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(400, f"Invalid {kind} path") from exc
        for root in await self._allowed_task_roots(task_id):
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        raise HTTPException(403, f"{kind} path is outside this task workspace")

    @staticmethod
    def _safe_upload_name(filename: str) -> str:
        base = Path(filename or "video.mp4").name.strip().strip(".")
        if not base:
            base = "video.mp4"
        base = _UPLOAD_NAME_RE.sub("_", base)
        return f"{uuid.uuid4().hex[:8]}_{base}"

    @staticmethod
    def _is_dangerous_clear_target(path: Path) -> bool:
        try:
            target = path.expanduser().resolve(strict=False)
            home = Path.home().resolve(strict=False)
        except (OSError, RuntimeError):
            return True
        return target == target.parent or target == home

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
            and not str(k).startswith(_INTERNAL_PARAM_PREFIX)
        }
        # Inject default_output_format from config when not explicitly set
        if not params.get("output_format"):
            fmt = (await self._tm.get_config("default_output_format")) or "mp4"
            params["output_format"] = fmt
        if "burn_subtitle" not in params:
            default_subtitle = (await self._tm.get_config("default_subtitle")) or "false"
            params["burn_subtitle"] = default_subtitle.strip().lower() == "true"

        task = await self._tm.create_task(
            mode=mode_id,
            source_video_path=source_path,
            params=params,
        )

        source_url = args.get("source_url", "")
        if not source_url and Path(source_path).exists():
            rel = Path(source_path).name
            source_url = build_preview_url("clip-sense", rel)

        task_dir, output_dir = await self._task_dirs(task["id"])
        ctx = ClipPipelineContext(
            task_id=task["id"],
            mode=mode_id,
            params=params,
            task_dir=task_dir,
            output_dir=output_dir,
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
            try:
                await self._tm.update_task(
                    ctx.task_id,
                    status="failed",
                    error_kind="unknown",
                    error_message=f"Unexpected pipeline error: {exc}",
                )
            except Exception as update_exc:
                logger.warning("clip-sense failed to mark task failed: %s", update_exc)
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
