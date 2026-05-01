# ruff: noqa: N999
"""Footage Gate — final-cut quality gate plugin.

This is the plugin entry point for ``footage-gate`` (中文：成片质量门).
It exposes 4 post-production modes — ``source_review`` /
``silence_cut`` / ``auto_color`` / ``cut_qc`` — over a FastAPI router
plus 5 AI tools wired through ``PluginAPI.register_tools``.

The seven-step ``on_load`` ritual mirrors ``seedance-video`` so operators
who already know that plugin can recognise the lifecycle at a glance:

1. Cache the :class:`PluginAPI` handle and prepare the per-plugin data
   directory (``api.get_data_dir() / "footage_gate"``).
2. Construct the SQLite-backed task manager (DB seeding is async — kicked
   off in the ``_async_init`` task).
3. Construct the in-plugin :class:`SystemDepsManager` so the FFmpeg
   installer panel and the dependency probe agree on state.
4. Build the FastAPI router and register the 16 routes (see
   :meth:`Plugin._register_routes`).
5. Register the 5 AI tools with a single dispatch handler.
6. Spawn the async DB-init task on the host event loop via
   ``api.spawn_task`` so the route layer does not have to await it.
7. Emit a "loaded" log line so the host's plugin-status panel ticks
   green even before the first request lands.

The ``on_unload`` teardown is the canonical 3-piece cleanup:
TaskManager.close → SystemDepsManager.aclose → cancel any inflight
``_async_init`` task.

Routes are deliberately kept light — they Pydantic-validate the body,
push work into the pipeline executor, broadcast a ``task_update`` UI
event when the task transitions, and return a JSON envelope with
``ok: bool`` so the React UI can branch with one check.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent

# ``footage_gate_pipeline`` imports modules with top-level ``import numpy``.
# Bootstrap NumPy before those local imports so a freshly-added plugin can
# load in the PyInstaller desktop build even when the host lacks NumPy.
try:
    from footage_gate_inline.dep_bootstrap import DepInstallFailed, ensure_importable

    ensure_importable(
        "numpy",
        "numpy>=1.24.0",
        plugin_dir=PLUGIN_DIR,
        friendly_name="NumPy",
    )
except DepInstallFailed:
    raise
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(f"footage-gate dependency bootstrap failed: {exc}") from exc

from fastapi import APIRouter, File, HTTPException, UploadFile
from footage_gate_inline.storage_stats import collect_storage_stats
from footage_gate_inline.system_deps import SystemDepsManager
from footage_gate_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from footage_gate_models import ERROR_HINTS, MODE_IDS, MODES
from footage_gate_pipeline import PipelineContext, run_pipeline
from footage_gate_task_manager import DEFAULT_CONFIG, FootageGateTaskManager
from pydantic import BaseModel, Field, field_validator

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


PLUGIN_ID = "footage-gate"


# ── Pydantic request models ──────────────────────────────────────────────


class CreateTaskBody(BaseModel):
    """Body for ``POST /tasks``.

    The route accepts either ``input_path`` (an absolute path that the
    plugin can already read — typically returned by ``POST /upload``) or
    ``upload_rel`` (the rel path relative to the plugin's
    ``data/uploads`` dir). Exactly one is required; the route resolves
    them to a single absolute path before dispatch.
    """

    mode: str = Field(..., description="One of MODE_IDS")
    input_path: str = ""
    upload_rel: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in MODE_IDS:
            raise ValueError(f"unknown mode '{v}'; expected one of {list(MODE_IDS)}")
        return v


class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]


class SystemInstallBody(BaseModel):
    method_index: int = 0


class SystemUninstallBody(BaseModel):
    method_index: int = 0


class StorageCleanupBody(BaseModel):
    dir_type: str = "cache"


class OpenFolderBody(BaseModel):
    path: str = ""
    key: str = ""


# ── Plugin entry point ───────────────────────────────────────────────────


class Plugin(PluginBase):
    """Footage Gate plugin — 4-mode post-production quality gate.

    Lifecycle is intentionally aligned with ``seedance-video.Plugin`` so
    operators only have to learn one shape across the post-production
    plugin family.
    """

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_load(self, api: PluginAPI) -> None:
        # Step 1 — cache handle + per-plugin data dir.
        self._api = api
        host_data_dir = api.get_data_dir() or Path.cwd() / "_footage_gate_data"
        self._data_dir: Path = Path(host_data_dir) / "footage_gate"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._uploads_dir: Path = self._data_dir / "uploads"
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_dir: Path = self._data_dir / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

        # Pre-warm optional Python wheels that feature paths use later.
        # NumPy is already ensured at module import; Pillow is optional but
        # gives image probing / QC-grid rendering their full fidelity.
        try:
            from footage_gate_inline.dep_bootstrap import preinstall_async

            preinstall_async(
                [
                    ("numpy", "numpy>=1.24.0"),
                    ("PIL", "Pillow>=10.0.0"),
                ],
                plugin_dir=PLUGIN_DIR,
            )
        except Exception as exc:  # noqa: BLE001
            api.log(
                f"footage-gate: dependency preinstall skipped ({exc!r}); "
                "feature paths will retry on first use",
                level="warning",
            )

        # Step 2 — task manager (init runs in async bootstrap).
        self._tm = FootageGateTaskManager(self._data_dir / "footage_gate.sqlite")
        self._init_task: asyncio.Task | None = None

        # Step 3 — system dependency manager (FFmpeg installer panel).
        # Mirrors seedance — the in-plugin replacement for the retired
        # SDK 0.6.x DependencyGate. See
        # ``footage_gate_inline/system_deps.py`` module docstring.
        self._sysdeps = SystemDepsManager()

        # Step 4 — FastAPI router with the 16 routes (the upload-preview
        # GET route and the dispatcher routes both live here).
        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        # Step 5 — register the 5 AI tools.
        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        # Step 6 — async bootstrap (DB schema + default config seeding).
        self._init_task = api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")

        # Step 7 — log so the host status panel ticks green immediately.
        api.log("Footage Gate plugin loaded (4 modes, 16 routes, 5 tools)")

    async def _async_init(self) -> None:
        try:
            await self._tm.init()
        except Exception as exc:
            logger.error("footage-gate task manager init failed: %s", exc)
            raise

    async def on_unload(self) -> None:
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("footage-gate init task drain error: %s", exc)
        try:
            await self._sysdeps.aclose()
        except Exception as exc:
            logger.warning("footage-gate system deps close error: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("footage-gate task manager close error: %s", exc)

    # ── AI tools ─────────────────────────────────────────────────────

    def _tool_definitions(self) -> list[dict]:
        """The 5 tools surfaced via ``register_tools``.

        Schemas are intentionally minimal — they expose just enough
        for an LLM agent to drive the plugin from the brain channel
        without learning the REST routes. Keep in sync with
        ``plugin.json`` ``provides.tools``.
        """
        return [
            {
                "name": "footage_gate_create",
                "description": (
                    "Create a footage-gate task in one of 4 modes "
                    "(source_review / silence_cut / auto_color / cut_qc)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": list(MODE_IDS)},
                        "input_path": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["mode", "input_path"],
                },
            },
            {
                "name": "footage_gate_status",
                "description": "Fetch the current status / output paths for a footage-gate task.",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "footage_gate_list",
                "description": "List recent footage-gate tasks (optionally filtered by mode / status).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "footage_gate_cancel",
                "description": "Mark a pending / running footage-gate task as cancelled.",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "footage_gate_settings_get",
                "description": "Fetch the persisted footage-gate settings (config table snapshot).",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        if tool_name == "footage_gate_create":
            mode = args.get("mode", "")
            if mode not in MODE_IDS:
                return f"unknown mode: {mode!r}"
            input_path = args.get("input_path", "")
            if not input_path or not Path(input_path).is_file():
                return f"input_path is required and must exist: {input_path!r}"
            params = args.get("params") or {}
            task = await self._create_and_dispatch(
                mode=mode,
                input_path=input_path,
                params=params,
            )
            return f"Task created: {task['id']} (mode={mode}, status={task['status']})"

        if tool_name == "footage_gate_status":
            task_id = args.get("task_id", "")
            task = await self._tm.get_task(task_id)
            if not task:
                return f"task {task_id!r} not found"
            return (
                f"Task {task['id']}: mode={task['mode']}, status={task['status']},"
                f" output_path={task.get('output_path') or 'N/A'},"
                f" qc_attempts={task.get('qc_attempts', 0)},"
                f" error_kind={task.get('error_kind') or '—'}"
            )

        if tool_name == "footage_gate_list":
            limit = int(args.get("limit", 20) or 20)
            tasks, total = await self._tm.list_tasks(
                mode=args.get("mode") or None,
                status=args.get("status") or None,
                limit=limit,
            )
            lines = [f"Total: {total} tasks (showing {len(tasks)})"]
            for t in tasks:
                lines.append(
                    f"  {t['id']}: mode={t['mode']} status={t['status']}"
                    f" hdr={t.get('is_hdr_source')} attempts={t.get('qc_attempts', 0)}"
                )
            return "\n".join(lines)

        if tool_name == "footage_gate_cancel":
            task_id = args.get("task_id", "")
            task = await self._tm.get_task(task_id)
            if not task:
                return f"task {task_id!r} not found"
            if task["status"] in ("done", "failed", "cancelled"):
                return f"task {task_id} already terminal ({task['status']})"
            await self._tm.update_task_safe(task_id, status="cancelled")
            return f"task {task_id} cancelled"

        if tool_name == "footage_gate_settings_get":
            cfg = await self._tm.get_all_config()
            return json.dumps(cfg, ensure_ascii=False)

        return f"Unknown tool: {tool_name}"

    # ── Task creation + execution ────────────────────────────────────

    async def _create_and_dispatch(
        self,
        *,
        mode: str,
        input_path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Common path used by both the REST route and the AI tool.

        Creates the task row in ``pending`` and immediately spawns the
        executor coroutine so the caller can return without waiting for
        the pipeline to finish (it can take minutes for cut_qc).
        """
        task = await self._tm.create_task(
            mode=mode,
            input_path=input_path,
            params=params,
        )
        self._api.spawn_task(
            self._run_task_async(task["id"]),
            name=f"{PLUGIN_ID}:run:{task['id']}",
        )
        return task

    async def _run_task_async(self, task_id: str) -> None:
        """Drive a single task through the pipeline executor.

        The pipeline itself is synchronous (so it can be unit-tested
        without an event loop), so we run it on the default thread
        pool via ``loop.run_in_executor``. Status transitions are
        broadcast to the UI on every transition so the Tasks tab
        renders the spinner and the eventual badge correctly.
        """
        task = await self._tm.get_task(task_id)
        if not task:
            return
        if task["status"] != "pending":
            # Defensive — somebody else already started this task. Bail.
            return

        await self._tm.update_task_safe(task_id, status="running")
        self._broadcast_update(task_id, "running")

        cfg = await self._tm.get_all_config()
        ffmpeg_path = self._sysdeps.detect("ffmpeg").get("location") or None
        ffprobe_path = self._derive_ffprobe_path(ffmpeg_path)

        ctx = PipelineContext(
            task_id=task_id,
            mode=task["mode"],
            input_path=Path(task["input_path"]),
            work_dir=self._tasks_dir / task_id,
            params=dict(task.get("params") or {}),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )

        try:
            ctx.params.setdefault(
                "ffmpeg_timeout_sec",
                float(cfg.get("ffmpeg_timeout_sec") or 600),
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: run_pipeline(
                    ctx,
                    emit=lambda evt, payload: self._emit_progress(task_id, evt, payload),
                ),
            )
        except Exception as exc:
            logger.exception("footage-gate task %s crashed in executor", task_id)
            await self._tm.update_task_safe(
                task_id,
                status="failed",
                error_kind="unknown",
                error_message=f"{type(exc).__name__}: {exc}",
                error_hints=ERROR_HINTS["unknown"]["zh"],
                completed_at=time.time(),
            )
            self._broadcast_update(task_id, "failed")
            return

        if ctx.error_kind:
            await self._tm.update_task_safe(
                task_id,
                status="failed",
                **{k: v for k, v in ctx.to_task_update().items() if v is not None},
            )
            self._broadcast_update(task_id, "failed")
            return

        await self._tm.update_task_safe(
            task_id,
            status="done",
            **{k: v for k, v in ctx.to_task_update().items() if v is not None},
        )
        self._broadcast_update(task_id, "done")

    @staticmethod
    def _derive_ffprobe_path(ffmpeg_path: str | None) -> str | None:
        """If we have an absolute ffmpeg path, ffprobe usually lives next to it."""
        if not ffmpeg_path:
            return None
        try:
            candidate = Path(ffmpeg_path).with_name(
                "ffprobe.exe" if ffmpeg_path.lower().endswith(".exe") else "ffprobe"
            )
            return str(candidate) if candidate.is_file() else None
        except (OSError, ValueError):
            return None

    def _emit_progress(self, task_id: str, event: str, payload: dict[str, Any]) -> None:
        try:
            self._api.broadcast_ui_event(
                "task_progress",
                {"task_id": task_id, "event": event, **payload},
            )
        except Exception as exc:
            logger.debug("broadcast_ui_event(progress) failed for %s: %s", task_id, exc)

    def _broadcast_update(self, task_id: str, status: str) -> None:
        try:
            self._api.broadcast_ui_event(
                "task_update",
                {"task_id": task_id, "status": status},
            )
        except Exception as exc:
            logger.warning("broadcast_ui_event(update) failed for %s: %s", task_id, exc)

    # ── Routes ───────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        """Wire the 16 REST routes onto ``router``.

        Route inventory (matches the v1.0 plan §6.2):

        Tasks (6) — POST /tasks · GET /tasks · GET /tasks/{id}
                   · DELETE /tasks/{id} · POST /tasks/{id}/cancel
                   · POST /tasks/{id}/retry
        Files (2) — POST /upload · GET /uploads/{rel_path:path}
        System  (4) — GET /system/components · GET /system/ffmpeg/status
                     · POST /system/ffmpeg/install · POST /system/ffmpeg/uninstall
        Settings(2) — GET /settings · PUT /settings
        Storage (3) — GET /storage/stats · POST /storage/cleanup
                     · POST /storage/open-folder
        """
        # ── File preview (issue-#479 hardened uploads) ──
        # Sprint 7 / C1 — register a safe GET /uploads/{rel_path:path}
        # so the UI can preview uploaded media via
        # <img src="/api/plugins/footage-gate/uploads/<file>"> after upload.
        add_upload_preview_route(router, base_dir=self._uploads_dir)

        # ── Tasks ──

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict:
            input_path = self._resolve_input_path(body)
            task = await self._create_and_dispatch(
                mode=body.mode,
                input_path=input_path,
                params=dict(body.params or {}),
            )
            return {"ok": True, "task": task}

        @router.get("/tasks")
        async def list_tasks(
            mode: str | None = None,
            status: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict:
            tasks, total = await self._tm.list_tasks(
                mode=mode,
                status=status,
                offset=offset,
                limit=limit,
            )
            return {"ok": True, "tasks": tasks, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="task not found")
            return {"ok": True, "task": task}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if task:
                # Best-effort — purge per-task work directory too.
                work_dir = self._tasks_dir / task_id
                if work_dir.is_dir():
                    shutil.rmtree(work_dir, ignore_errors=True)
            await self._tm.delete_task(task_id)
            return {"ok": True}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="task not found")
            if task["status"] in ("done", "failed", "cancelled"):
                return {"ok": True, "task": task, "note": "already_terminal"}
            await self._tm.update_task_safe(task_id, status="cancelled")
            self._broadcast_update(task_id, "cancelled")
            updated = await self._tm.get_task(task_id)
            return {"ok": True, "task": updated}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="task not found")
            new_task = await self._create_and_dispatch(
                mode=task["mode"],
                input_path=task["input_path"],
                params=dict(task.get("params") or {}),
            )
            return {"ok": True, "task": new_task}

        # ── Upload ──

        @router.post("/upload")
        async def upload_file(file: UploadFile = File(...)) -> dict:
            content = await file.read()
            ext = Path(file.filename or "file").suffix.lower()
            if ext in (".mp4", ".mov", ".webm", ".mkv"):
                subdir = "videos"
            elif ext in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
                subdir = "audios"
            elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
                subdir = "images"
            else:
                subdir = "other"

            dest_dir = self._uploads_dir / subdir
            dest_dir.mkdir(parents=True, exist_ok=True)

            import uuid as _uuid

            safe_name = file.filename or "file"
            filename = f"{_uuid.uuid4().hex[:8]}_{safe_name}"
            filepath = dest_dir / filename
            filepath.write_bytes(content)
            rel_path = f"{subdir}/{filename}"

            preview_b64 = None
            if subdir == "images" and len(content) < 10_000_000:
                preview_b64 = (
                    f"data:{file.content_type};base64,{base64.b64encode(content).decode('ascii')}"
                )
            return {
                "ok": True,
                "rel_path": rel_path,
                "input_path": str(filepath),
                "size_bytes": len(content),
                "url": build_preview_url(PLUGIN_ID, rel_path),
                "base64": preview_b64,
            }

        # ── System dependency (FFmpeg installer) ──

        @router.get("/system/components")
        async def system_components() -> dict:
            return {"ok": True, "items": self._sysdeps.list_components()}

        @router.get("/system/ffmpeg/status")
        async def ffmpeg_status() -> dict:
            snap = self._sysdeps.detect("ffmpeg")
            return {"ok": True, "status": snap}

        @router.post("/system/ffmpeg/install")
        async def ffmpeg_install(body: SystemInstallBody) -> dict:
            try:
                result = await self._sysdeps.start_install(
                    "ffmpeg",
                    method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        @router.post("/system/ffmpeg/uninstall")
        async def ffmpeg_uninstall(body: SystemUninstallBody) -> dict:
            try:
                result = await self._sysdeps.start_uninstall(
                    "ffmpeg",
                    method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        # ── Settings (use /settings to avoid host /config collision) ──

        @router.get("/settings")
        async def get_settings() -> dict:
            cfg = await self._tm.get_all_config()
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return {
                "ok": True,
                "config": cfg,
                "modes": MODES,
                "available_keys": sorted(DEFAULT_CONFIG.keys()),
            }

        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict:
            # Accept anything (forward-compat) but warn on unknown keys
            # so a typo surfaces during integration testing.
            unknown = sorted(set(body.updates) - set(DEFAULT_CONFIG))
            await self._tm.set_configs({k: str(v) for k, v in body.updates.items()})
            cfg = await self._tm.get_all_config()
            return {"ok": True, "config": cfg, "unknown_keys": unknown}

        # ── Storage management ──

        @router.get("/storage/stats")
        async def storage_stats() -> dict:
            cfg = await self._tm.get_all_config()
            stats: dict[str, dict] = {}
            truncated_any = False
            for key, default in [
                ("output_dir", str(Path.home() / "footage-gate-output")),
                ("uploads", str(self._uploads_dir)),
                ("tasks", str(self._tasks_dir)),
            ]:
                d = Path(cfg.get(key) or default)
                report = await collect_storage_stats(
                    d,
                    max_files=20000,
                    sample_paths=0,
                    skip_hidden=True,
                )
                truncated_any = truncated_any or report.truncated
                stats[key] = {
                    "path": str(d),
                    "size_bytes": report.total_bytes,
                    "size_mb": round(report.total_bytes / 1048576, 1),
                    "file_count": report.total_files,
                    "truncated": report.truncated,
                }
            return {"ok": True, "stats": stats, "truncated": truncated_any}

        @router.post("/storage/cleanup")
        async def storage_cleanup(body: StorageCleanupBody) -> dict:
            dir_type = (body.dir_type or "").strip()
            if dir_type == "tasks":
                target = self._tasks_dir
            elif dir_type == "uploads":
                target = self._uploads_dir
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid dir_type: {dir_type!r}",
                )
            removed = 0
            if target.is_dir():
                for f in target.rglob("*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        removed += 1
            return {"ok": True, "removed": removed, "dir_type": dir_type}

        @router.post("/storage/open-folder")
        async def open_folder(body: OpenFolderBody) -> dict:
            raw_path = (body.path or "").strip()
            key = (body.key or "").strip()
            if not raw_path and not key:
                raise HTTPException(
                    status_code=400,
                    detail="missing 'path' or 'key'",
                )
            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                defaults = {
                    "output_dir": Path.home() / "footage-gate-output",
                    "uploads": self._uploads_dir,
                    "tasks": self._tasks_dir,
                }
                if key not in defaults:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unknown key: {key}",
                    )
                cfg = await self._tm.get_all_config()
                cfg_val = (cfg.get(key) or "").strip()
                target = Path(cfg_val).expanduser() if cfg_val else defaults[key]
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"cannot create folder: {exc}",
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
                    status_code=500,
                    detail=f"cannot open folder: {exc}",
                ) from exc
            return {"ok": True, "path": str(target)}

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve_input_path(self, body: CreateTaskBody) -> str:
        """Resolve the request body to a single absolute input path.

        Either ``input_path`` (absolute, must exist) or ``upload_rel``
        (rel to ``self._uploads_dir``, must resolve inside it) is
        accepted. Anything that escapes the uploads sandbox is rejected
        with HTTP 400 — the rel-path branch is the typical UI flow.
        """
        if body.input_path:
            p = Path(body.input_path).expanduser()
            if not p.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"input_path does not exist: {body.input_path}",
                )
            return str(p)
        if body.upload_rel:
            try:
                resolved = (self._uploads_dir / body.upload_rel).resolve()
                resolved.relative_to(self._uploads_dir.resolve())
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"upload_rel must resolve inside uploads dir: {exc}",
                ) from exc
            if not resolved.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"upload_rel does not exist: {body.upload_rel}",
                )
            return str(resolved)
        raise HTTPException(
            status_code=422,
            detail="either input_path or upload_rel is required",
        )
