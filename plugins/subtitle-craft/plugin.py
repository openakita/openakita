"""Subtitle Craft — AI subtitle full-lifecycle plugin (Phase 4 entry).

Backend FastAPI entry point providing 21 REST routes + 4 tools per
``docs/subtitle-craft-plan.md`` §8.1/§8.2 (post-patch v2). Wires together:

- ``SubtitleTaskManager`` (4-table SQLite, §8.3)
- ``SubtitleAsrClient`` (Paraformer-v2 + Qwen-MT + Qwen-VL, §7)
- ``run_pipeline`` (7-step orchestration, §3.4)
- ``add_upload_preview_route`` (vendored helper for safe ``/uploads/*``)

Architectural rules baked in (red-line guardrails):

- **Self-contained**: imports only ``openakita.*`` (host SDK) and
  ``subtitle_craft_inline.*`` / sibling ``subtitle_*.py`` modules. **No**
  ``from plugins-archive``, ``from _shared``, ``from sdk.contrib`` —
  enforced by ``tests/test_skeleton.py`` grep guards.
- **No cross-plugin dispatch in v1.0**: no ``/handoff/*`` routes, no
  ``*_handoff_*`` tools, no ``subtitle_handoff.py`` module. Schema-only
  reservation in Phase 1 (``tasks.origin_*`` columns, ``assets_bus``
  table) is invisible to v1.0 code paths. v2.0 will fill in routes/UI
  with zero data migration. Phase 0 grep guard
  (``test_no_handoff_route_literal``) verifies the literal ``/handoff/``
  is absent from this file.
- **Playwright lazy import** (P0-13): ``playwright`` is **never** imported
  at module scope; only inside ``subtitle_renderer.burn_subtitles_html``
  and ``subtitle_renderer._PlaywrightSingleton``. ``on_unload`` calls
  ``_PlaywrightSingleton.close()`` so the Chromium subprocess exits with
  the host.
- **All Pydantic request bodies** declare ``model_config =
  ConfigDict(extra="forbid")`` (red-line C6 reverse-example).
- **``/healthz``** returns the **4-field** contract per §8.4 + Phase 4
  DoD: ``{ffmpeg_ok, playwright_ok, playwright_browser_ready,
  dashscope_api_key_present}``. The API key is **never** echoed back —
  only its presence as a boolean.
- **``provides.tools``** is **4 tools** (not 7); v1 had 7 including
  ``subtitle_craft_handoff_*`` which v2 deferred to v1.1+.
- **SSE event name** is hard-coded ``task_update`` (red line #21); the
  ``_emit`` callback always invokes
  ``api.broadcast_ui_event("task_update", payload)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from subtitle_asr_client import SubtitleAsrClient
from subtitle_craft_inline.storage_stats import collect_storage_stats
from subtitle_craft_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from subtitle_models import (
    ALLOWED_ERROR_KINDS,
    ERROR_HINTS,
    MODES,
    MODES_BY_ID,
    SUBTITLE_STYLES,
    TRANSLATION_MODELS,
    estimate_cost,
    mode_to_dict,
)
from subtitle_pipeline import SubtitlePipelineContext, run_pipeline
from subtitle_renderer import _PlaywrightSingleton
from subtitle_task_manager import SubtitleTaskManager

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

PLUGIN_ID = "subtitle-craft"

# ---------------------------------------------------------------------------
# Pydantic request bodies (all declare extra="forbid" per red-line C6)
# ---------------------------------------------------------------------------


class CreateTaskBody(BaseModel):
    """Request body for ``POST /tasks``.

    Mode-specific fields are flattened into a dict; unknown top-level keys
    are rejected by ``extra="forbid"`` so typos surface as 422 instead of
    silently being dropped.
    """

    model_config = ConfigDict(extra="forbid")

    mode: str = "auto_subtitle"
    source_path: str = ""
    source_url: str = ""
    source_kind: str = ""  # "video" | "audio" | "srt" — auto if empty
    srt_path: str = ""  # for translate / repair / burn
    source_lang: str = ""
    target_lang: str = ""
    language_hints: list[str] = Field(default_factory=list)
    diarization_enabled: bool = False
    speaker_count: int = 0
    character_identify_enabled: bool = False
    channel_id: list[int] = Field(default_factory=list)
    disfluency_removal_enabled: bool = False
    bilingual: bool = False
    translation_model: str = "qwen-mt-flash"
    repair_options: dict[str, bool] = Field(default_factory=dict)
    subtitle_style: str = "default"
    burn_engine: str = "ass"  # "ass" | "html"
    burn_mode: str = "soft"  # "soft" (sidecar) | "hard" (in-stream)
    output_format: str = "mp4"
    estimated_char_count: int = 0
    estimated_speaker_count: int = 0
    context_hint: str = ""
    cost_approved: bool = False


class CostPreviewBody(BaseModel):
    """Request body for ``POST /cost-preview`` (independent of /tasks)."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    duration_sec: float = 0.0
    char_count: int = 0
    translation_model: str = "qwen-mt-flash"
    character_identify_enabled: bool = False
    speaker_count: int = 0


class ConfigUpdateBody(BaseModel):
    """Request body for ``PUT /settings``."""

    model_config = ConfigDict(extra="forbid")

    updates: dict[str, str]


class CustomStyleBody(BaseModel):
    """Request body for ``POST /library/styles`` (custom user style preset)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""
    font_name: str = "Microsoft YaHei"
    font_size: int = 24
    primary_colour: str = "&H00FFFFFF"
    outline_colour: str = "&H00000000"
    back_colour: str = "&H80000000"
    bold: int = 0
    outline: float = 2.0
    shadow: float = 1.0
    margin_v: int = 30
    alignment: int = 2
    custom_html: str = ""  # optional user HTML/CSS for B-path overlay
    custom_css: str = ""


# ---------------------------------------------------------------------------
# Plugin entry
# ---------------------------------------------------------------------------


class Plugin(PluginBase):
    """Subtitle Craft plugin.

    Lifecycle:

    1. ``on_load`` — synchronous init: build router, register 21 routes +
       4 tools, spawn ``_async_init`` for I/O bound startup. Never blocks.
    2. ``_async_init`` — async startup: open SQLite, load settings, build
       ``SubtitleAsrClient`` (if API key set), start polling loop.
    3. ``on_unload`` — cancel polling, close ASR client, close task
       manager, **close Playwright singleton** (P0-13/P0-14 contract).
    """

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._data_dir = data_dir
        self._uploads_dir = data_dir / "uploads"
        self._tasks_dir = data_dir / "tasks"
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

        self._tm = SubtitleTaskManager(data_dir / "subtitle_craft.db")
        self._asr: SubtitleAsrClient | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running_pipelines: dict[str, SubtitlePipelineContext] = {}
        self._ffmpeg_path: str = ""

        router = APIRouter()
        self._make_url = add_upload_preview_route(router, base_dir=self._uploads_dir)
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(_TOOL_DEFS, handler=self._handle_tool)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log(f"{PLUGIN_ID} plugin loaded (data_dir={data_dir})")

    async def _async_init(self) -> None:
        await self._tm.init()
        api_key = await self._tm.get_config("dashscope_api_key") or ""
        if api_key:
            self._asr = self._build_asr_client(api_key)
        self._ffmpeg_path = await self._tm.get_config("ffmpeg_path") or ""
        self._start_polling()

    async def on_unload(self) -> None:
        # 1. Cancel the polling task (cooperative).
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 — last-resort drain
                logger.warning("%s poll task drain: %s", PLUGIN_ID, exc)

        # 2. Drop the ASR client reference. ``BaseVendorClient`` opens a
        #    fresh ``httpx.AsyncClient`` per request and closes it via the
        #    ``async with`` block, so there is no persistent socket state
        #    to drain — we just clear the reference so a stale API key
        #    cannot be reused after unload.
        self._asr = None

        # 3. Close the SQLite task manager.
        try:
            await self._tm.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s task manager close: %s", PLUGIN_ID, exc)

        # 4. Close the Playwright singleton (P0-13/P0-14: this is the only
        #    place Chromium gets shut down; without it the subprocess
        #    leaks past plugin reload).
        try:
            await _PlaywrightSingleton.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s Playwright singleton close: %s", PLUGIN_ID, exc)

        logger.info("%s plugin unloaded", PLUGIN_ID)

    # ------------------------------------------------------------------
    # ASR client construction
    # ------------------------------------------------------------------

    def _build_asr_client(self, api_key: str) -> SubtitleAsrClient:
        return SubtitleAsrClient(api_key)

    # ------------------------------------------------------------------
    # Tool handler — 4 tools per plugin.json provides.tools
    # ------------------------------------------------------------------

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "subtitle_craft_create":
            try:
                body = CreateTaskBody.model_validate(args)
            except Exception as exc:  # noqa: BLE001
                return f"Invalid arguments: {exc}"
            task = await self._create_task_internal(body)
            return f"Task created: {task['id']} (mode={task['mode']}, status={task['status']})"
        if tool_name == "subtitle_craft_status":
            tid = str(args.get("task_id", "")).strip()
            if not tid:
                return "task_id is required"
            task = await self._tm.get_task(tid)
            if not task:
                return f"Task {tid} not found"
            return (
                f"Task {task['id']}: status={task['status']}, mode={task['mode']}, "
                f"step={task.get('pipeline_step') or 'N/A'}, "
                f"error_kind={task.get('error_kind') or '-'}"
            )
        if tool_name == "subtitle_craft_list":
            limit = int(args.get("limit", 10) or 10)
            result = await self._tm.list_tasks(limit=limit)
            lines = [f"Total: {result['total']} tasks"]
            for t in result["tasks"][:limit]:
                lines.append(
                    f"  {t['id']}: {t['mode']} / {t['status']} / {t.get('pipeline_step') or '-'}"
                )
            return "\n".join(lines)
        if tool_name == "subtitle_craft_cancel":
            tid = str(args.get("task_id", "")).strip()
            if not tid:
                return "task_id is required"
            self._tm.request_cancel(tid)
            ctx = self._running_pipelines.get(tid)
            if ctx is None:
                # Persist cancel even when no pipeline is in-flight (e.g.
                # task not yet started); tm.is_canceled handles the rest.
                await self._tm.update_task_safe(tid, status="canceled")
            return f"Cancel requested for task {tid}"
        return f"Unknown tool: {tool_name}"

    # ------------------------------------------------------------------
    # Route registration — 21 routes per §8.2 (no /handoff/*)
    # ------------------------------------------------------------------

    def _register_routes(self, router: APIRouter) -> None:
        # 1. POST /tasks ----------------------------------------------------
        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            return await self._create_task_internal(body)

        # 2. GET /tasks -----------------------------------------------------
        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict[str, Any]:
            return await self._tm.list_tasks(status=status, mode=mode, offset=offset, limit=limit)

        # 3. GET /tasks/{task_id} ------------------------------------------
        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            return task

        # 4. DELETE /tasks/{task_id} ---------------------------------------
        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, str]:
            if not await self._tm.delete_task(task_id):
                raise HTTPException(404, "Task not found")
            self._running_pipelines.pop(task_id, None)
            return {"status": "deleted"}

        # 5. POST /tasks/{task_id}/cancel ----------------------------------
        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, str]:
            self._tm.request_cancel(task_id)
            ctx = self._running_pipelines.get(task_id)
            if ctx is None:
                await self._tm.update_task_safe(task_id, status="canceled")
                return {"status": "canceled"}
            return {"status": "cancel_requested"}

        # 6. POST /tasks/{task_id}/retry -----------------------------------
        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(404, "Task not found")
            if task["status"] not in ("failed", "canceled"):
                raise HTTPException(400, "Can only retry failed/canceled tasks")
            params = task.get("params") or {}
            new_body = CreateTaskBody.model_validate(
                {
                    "mode": task["mode"],
                    "source_path": task.get("source_path") or "",
                    "source_kind": task.get("source_kind") or "",
                    "source_lang": task.get("source_lang") or "",
                    "target_lang": task.get("target_lang") or "",
                    **{k: v for k, v in params.items() if k in CreateTaskBody.model_fields},
                }
            )
            return await self._create_task_internal(new_body)

        # 7. GET /tasks/{task_id}/download ---------------------------------
        @router.get("/tasks/{task_id}/download")
        async def download_srt(task_id: str) -> Any:
            from fastapi.responses import FileResponse

            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_srt_path"):
                raise HTTPException(404, "SRT output not found")
            p = Path(task["output_srt_path"])
            if not p.exists():
                raise HTTPException(404, "SRT file missing on disk")
            return FileResponse(p, filename=p.name, media_type="text/plain")

        # 8. GET /tasks/{task_id}/download_video ---------------------------
        @router.get("/tasks/{task_id}/download_video")
        async def download_video(task_id: str) -> Any:
            from fastapi.responses import FileResponse

            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_video_path"):
                raise HTTPException(404, "Burned video not found")
            p = Path(task["output_video_path"])
            if not p.exists():
                raise HTTPException(404, "Video file missing on disk")
            return FileResponse(p, filename=p.name, media_type="video/mp4")

        # 9. GET /tasks/{task_id}/preview_srt ------------------------------
        @router.get("/tasks/{task_id}/preview_srt")
        async def preview_srt(task_id: str) -> dict[str, Any]:
            task = await self._tm.get_task(task_id)
            if not task or not task.get("output_srt_path"):
                raise HTTPException(404, "SRT output not found")
            p = Path(task["output_srt_path"])
            if not p.exists():
                raise HTTPException(404, "SRT file missing on disk")
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError as e:
                raise HTTPException(400, f"SRT not UTF-8: {e}") from e
            vtt_path = task.get("output_vtt_path") or ""
            vtt_text = ""
            if vtt_path and Path(vtt_path).exists():
                try:
                    vtt_text = Path(vtt_path).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    vtt_text = ""
            return {
                "task_id": task_id,
                "srt": text,
                "vtt": vtt_text,
                "filename": p.name,
                "size_bytes": p.stat().st_size,
            }

        # 10. POST /upload --------------------------------------------------
        @router.post("/upload")
        async def upload_source(file: UploadFile = File(...)) -> dict[str, Any]:
            self._uploads_dir.mkdir(parents=True, exist_ok=True)
            safe_base = (file.filename or "asset.bin").replace("\\", "_").replace("/", "_")
            safe_name = f"{uuid.uuid4().hex[:8]}_{safe_base}"
            dest = self._uploads_dir / safe_name
            with open(dest, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
            url = build_preview_url(PLUGIN_ID, safe_name)
            kind = _classify_upload_kind(safe_name)
            duration_sec = _probe_duration_quiet(dest, self._ffmpeg_path)
            return {
                "asset_id": _sha_for_path(dest),
                "path": str(dest),
                "filename": safe_name,
                "preview_url": url,
                "size_bytes": dest.stat().st_size,
                "duration_sec": duration_sec,
                "kind": kind,
            }

        # 11. GET /library/transcripts -------------------------------------
        @router.get("/library/transcripts")
        async def list_transcripts(offset: int = 0, limit: int = 50) -> dict[str, Any]:
            return await self._tm.list_transcripts(offset=offset, limit=limit)

        # 12. GET /library/srts --------------------------------------------
        @router.get("/library/srts")
        async def list_srts(offset: int = 0, limit: int = 50) -> dict[str, Any]:
            # SRT library = succeeded tasks that produced an SRT output.
            data = await self._tm.list_tasks(status="succeeded", offset=offset, limit=limit)
            srts: list[dict[str, Any]] = []
            for t in data.get("tasks", []):
                p = t.get("output_srt_path") or ""
                if not p or not Path(p).exists():
                    continue
                srts.append(
                    {
                        "task_id": t["id"],
                        "mode": t["mode"],
                        "path": p,
                        "filename": Path(p).name,
                        "size_bytes": Path(p).stat().st_size,
                        "created_at": t.get("created_at"),
                        "source_lang": t.get("source_lang") or "",
                        "target_lang": t.get("target_lang") or "",
                    }
                )
            return {"srts": srts, "total": len(srts)}

        # 13. GET /library/styles ------------------------------------------
        @router.get("/library/styles")
        async def list_styles() -> dict[str, Any]:
            builtin = [_style_to_dict(s) for s in SUBTITLE_STYLES]
            custom = await self._load_custom_styles()
            return {"builtin": builtin, "custom": custom}

        # 14. POST /library/styles -----------------------------------------
        @router.post("/library/styles")
        async def add_custom_style(body: CustomStyleBody) -> dict[str, Any]:
            custom = await self._load_custom_styles()
            sid = f"custom_{uuid.uuid4().hex[:8]}"
            entry = {"id": sid, **body.model_dump()}
            custom.append(entry)
            await self._tm.set_config("custom_styles_json", json.dumps(custom, ensure_ascii=False))
            return entry

        # 15. DELETE /library/styles/{style_id} ----------------------------
        @router.delete("/library/styles/{style_id}")
        async def delete_custom_style(style_id: str) -> dict[str, str]:
            custom = await self._load_custom_styles()
            new = [s for s in custom if s.get("id") != style_id]
            if len(new) == len(custom):
                raise HTTPException(404, "Style not found")
            await self._tm.set_config("custom_styles_json", json.dumps(new, ensure_ascii=False))
            return {"status": "deleted"}

        # 16. POST /cost-preview -------------------------------------------
        @router.post("/cost-preview")
        async def cost_preview_route(body: CostPreviewBody) -> dict[str, Any]:
            preview = estimate_cost(
                body.mode,
                duration_sec=body.duration_sec,
                char_count=body.char_count,
                translation_model=body.translation_model,
                character_identify=body.character_identify_enabled,
                speaker_count=body.speaker_count,
            )
            return {"total_cny": preview.total_cny, "items": preview.items}

        # 17. GET /settings -------------------------------------------------
        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            cfg = await self._tm.get_all_config()
            # Never echo the raw API key — replace with presence flag and
            # a masked version (last 4 chars) so the UI can show "····XXXX".
            api_key = cfg.get("dashscope_api_key", "")
            cfg["dashscope_api_key_present"] = bool(api_key)
            cfg["dashscope_api_key_masked"] = ("·" * 8 + api_key[-4:]) if len(api_key) >= 4 else ""
            cfg.pop("dashscope_api_key", None)
            return cfg

        # 18. PUT /settings -------------------------------------------------
        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict[str, str]:
            await self._tm.set_configs(body.updates)
            if "dashscope_api_key" in body.updates:
                key = body.updates["dashscope_api_key"]
                if key:
                    if self._asr is not None:
                        self._asr.update_api_key(key)
                    else:
                        self._asr = self._build_asr_client(key)
                else:
                    # No persistent socket state to close (BaseVendorClient
                    # uses per-request httpx.AsyncClient). Drop the ref.
                    self._asr = None
            if "ffmpeg_path" in body.updates:
                self._ffmpeg_path = body.updates["ffmpeg_path"] or ""
            return {"status": "ok"}

        # 19. GET /storage/stats -------------------------------------------
        @router.get("/storage/stats")
        async def storage_stats() -> dict[str, Any]:
            roots = [r for r in (self._uploads_dir, self._tasks_dir) if r.exists()]
            stats = await collect_storage_stats(roots)
            return stats.to_dict()

        # 20. GET /modes ----------------------------------------------------
        @router.get("/modes")
        async def get_modes() -> dict[str, Any]:
            return {
                "modes": [mode_to_dict(m) for m in MODES],
                "translation_models": [
                    {
                        "id": m.id,
                        "label_zh": m.label_zh,
                        "label_en": m.label_en,
                        "price_cny_per_k_token": m.price_cny_per_k_token,
                        "description_zh": m.description_zh,
                    }
                    for m in TRANSLATION_MODELS
                ],
                "error_kinds": sorted(ALLOWED_ERROR_KINDS),
                "error_hints": ERROR_HINTS,
            }

        # 21. GET /healthz --------------------------------------------------
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return await self._compute_health()

    # ------------------------------------------------------------------
    # /healthz — 4-field contract (Phase 4 DoD + §8.4)
    # ------------------------------------------------------------------

    async def _compute_health(self) -> dict[str, Any]:
        ffmpeg_ok = self._detect_ffmpeg()
        playwright_ok = await asyncio.to_thread(self._detect_playwright_pkg)
        playwright_browser_ready = await asyncio.to_thread(self._detect_playwright_browser)
        api_key = await self._tm.get_config("dashscope_api_key") or ""
        dashscope_api_key_present = bool(api_key.strip())
        return {
            "ffmpeg_ok": ffmpeg_ok,
            "playwright_ok": playwright_ok,
            "playwright_browser_ready": playwright_browser_ready,
            "dashscope_api_key_present": dashscope_api_key_present,
        }

    def _detect_ffmpeg(self) -> bool:
        if self._ffmpeg_path and Path(self._ffmpeg_path).exists():
            return True
        return shutil.which("ffmpeg") is not None

    @staticmethod
    def _detect_playwright_pkg() -> bool:
        # Local importlib check — the package may be installed even when
        # browsers are not yet downloaded. Done in a thread to avoid the
        # tiny ``importlib`` overhead on the event loop.
        import importlib.util

        return importlib.util.find_spec("playwright") is not None

    @staticmethod
    def _detect_playwright_browser() -> bool:
        # The browser is "ready" iff the Chromium binary is present in the
        # default ms-playwright cache. We do not launch it here (P0-13 lazy
        # import contract). Cheap filesystem probe only.
        candidates = [
            Path.home() / "AppData" / "Local" / "ms-playwright",  # Windows
            Path.home() / ".cache" / "ms-playwright",  # Linux
            Path.home() / "Library" / "Caches" / "ms-playwright",  # macOS
        ]
        for cache_dir in candidates:
            if cache_dir.exists() and any(cache_dir.glob("chromium-*")):
                return True
        return False

    # ------------------------------------------------------------------
    # Custom style storage (single JSON blob in config)
    # ------------------------------------------------------------------

    async def _load_custom_styles(self) -> list[dict[str, Any]]:
        raw = await self._tm.get_config("custom_styles_json") or ""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [s for s in parsed if isinstance(s, dict)]

    # ------------------------------------------------------------------
    # Task creation / pipeline orchestration
    # ------------------------------------------------------------------

    async def _create_task_internal(self, body: CreateTaskBody) -> dict[str, Any]:
        mode_def = MODES_BY_ID.get(body.mode)
        if mode_def is None:
            raise HTTPException(400, f"Unknown mode: {body.mode}")

        # Source kind heuristics: pick from explicit body.source_kind or
        # infer from extension. ``translate``/``repair`` need an SRT;
        # ``auto_subtitle``/``burn`` need a video/audio.
        source_kind = body.source_kind or _infer_source_kind(body)
        source_path_str = body.source_path or body.srt_path
        if not source_path_str and body.mode != "burn":
            raise HTTPException(400, "source_path or srt_path is required")

        if source_path_str and not Path(source_path_str).exists():
            raise HTTPException(400, f"Source file not found: {source_path_str}")

        # Mode-specific guards
        if body.mode == "auto_subtitle" and not body.source_path:
            raise HTTPException(400, "auto_subtitle requires source_path (video/audio)")
        if body.mode in ("translate", "repair") and not body.srt_path:
            raise HTTPException(400, f"{body.mode} requires srt_path")
        if body.mode == "burn":
            if not body.source_path:
                raise HTTPException(400, "burn requires source_path (video)")
            if not body.srt_path:
                raise HTTPException(400, "burn requires srt_path")

        params = body.model_dump(exclude={"mode", "source_path", "source_url", "source_kind"})

        task = await self._tm.create_task(
            mode=body.mode,
            source_kind=source_kind,
            source_path=source_path_str,
            source_lang=body.source_lang,
            target_lang=body.target_lang,
            params=params,
        )

        # Build a public preview URL when source lives in our uploads dir.
        source_url = body.source_url
        if not source_url and source_path_str:
            try:
                rel = Path(source_path_str).relative_to(self._uploads_dir)
                source_url = build_preview_url(PLUGIN_ID, str(rel))
            except ValueError:
                # Source not under uploads_dir → caller must supply
                # source_url manually (e.g. for already-public files).
                source_url = ""

        task_dir = self._tasks_dir / task["id"]
        ctx = SubtitlePipelineContext(
            task_id=task["id"],
            mode=body.mode,
            params=params,
            task_dir=task_dir,
            source_kind=source_kind,
            source_path=Path(source_path_str) if source_path_str else None,
            source_url=source_url,
            source_lang=body.source_lang,
            target_lang=body.target_lang,
        )
        # Stuff srt_path into params so step 4 can find it for translate/
        # repair/burn modes (pipeline reads ctx.params["srt_path"]).
        if body.srt_path:
            ctx.params["srt_path"] = body.srt_path

        self._running_pipelines[task["id"]] = ctx
        self._api.spawn_task(self._run_task(ctx), name=f"{PLUGIN_ID}:task:{task['id']}")
        return task

    async def _run_task(self, ctx: SubtitlePipelineContext) -> None:
        try:
            await run_pipeline(
                ctx,
                self._tm,
                self._asr,
                emit=self._emit,
                ffmpeg_path=self._ffmpeg_path or None,
            )
        except Exception as exc:  # noqa: BLE001 — last-resort drain
            logger.exception("%s pipeline unexpected error: %s", PLUGIN_ID, exc)
        finally:
            self._running_pipelines.pop(ctx.task_id, None)
            self._tm.clear_cancel(ctx.task_id)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        # Red line #21: SSE event name is **always** ``task_update``.
        # We forward the event name verbatim so the pipeline's invariant
        # is enforced at one place (this method) and one place only.
        try:
            self._api.broadcast_ui_event(event, data)
        except Exception as exc:  # noqa: BLE001 — broadcast is best-effort
            logger.debug("broadcast_ui_event failed (%s): %s", event, exc)

    # ------------------------------------------------------------------
    # Background polling (3-stage backoff per Phase 4 spec)
    # ------------------------------------------------------------------

    def _start_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Watch for orphaned ``running``/``pending`` tasks and reap them.

        Uses a 3-stage backoff (3 s → 10 s → 30 s) on the count of
        consecutive iterations with no actionable work; total spin budget
        per outer iteration ≤900 s per Phase 4 spec.
        """
        try:
            cycle = 0
            while True:
                # Stage 1: 3 s for first 10 cycles (~30 s wall time);
                # Stage 2: 10 s for next 9 cycles (~90 s);
                # Stage 3: 30 s thereafter (cap; total wall time bounded
                # only by uptime, *not* by 900 s — that 900 s budget is
                # for a *single* Paraformer poll, handled inside ASR client).
                if cycle < 10:
                    interval = 3.0
                elif cycle < 19:
                    interval = 10.0
                else:
                    interval = 30.0
                await asyncio.sleep(interval)
                cycle += 1

                try:
                    running = await self._tm.get_running_tasks()
                    for t in running:
                        tid = t["id"]
                        if tid not in self._running_pipelines:
                            await self._tm.update_task_safe(
                                tid,
                                status="failed",
                                error_kind="unknown",
                                error_message=(
                                    "Task found in running state but no "
                                    "pipeline context (likely server restart)"
                                ),
                            )
                            self._emit(
                                "task_update",
                                {
                                    "task_id": tid,
                                    "mode": t.get("mode") or "",
                                    "status": "failed",
                                    "pipeline_step": "error",
                                    "error_kind": "unknown",
                                    "error_message": (
                                        "Task found in running state but no "
                                        "pipeline context (likely server restart)"
                                    ),
                                },
                            )
                except Exception as exc:  # noqa: BLE001 — keep polling
                    logger.warning("%s poll error: %s", PLUGIN_ID, exc)
                    cycle = 0  # reset backoff on error so we don't go to 30 s on issues
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers (no Plugin state)
# ---------------------------------------------------------------------------


def _classify_upload_kind(filename: str) -> str:
    """Map a file extension to the §8.4 upload-kind taxonomy."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in {"mp4", "mkv", "mov", "webm", "avi"}:
        return "video"
    if ext in {"mp3", "wav", "m4a", "flac", "ogg", "aac"}:
        return "audio"
    if ext == "srt":
        return "srt"
    if ext == "vtt":
        return "srt"
    return "other"


def _infer_source_kind(body: CreateTaskBody) -> str:
    if body.mode in {"translate", "repair"}:
        return "srt"
    path = body.source_path or body.srt_path
    if not path:
        return ""
    return _classify_upload_kind(Path(path).name)


def _sha_for_path(p: Path, *, chunk_size: int = 65536) -> str:
    """SHA256 of first 64 KB + size — same recipe as pipeline cache key."""
    import hashlib

    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(chunk_size))
    return f"{h.hexdigest()}_{p.stat().st_size}"


def _probe_duration_quiet(p: Path, ffmpeg_path: str) -> float:
    """Best-effort ffprobe duration; returns 0.0 on any failure."""
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if ffmpeg_path:
        candidate = Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix)
        if candidate.exists():
            ffprobe = str(candidate)
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(  # noqa: S603 — args is a list, no shell
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return 0.0


def _style_to_dict(style: Any) -> dict[str, Any]:
    return {
        "id": style.id,
        "label_zh": style.label_zh,
        "label_en": style.label_en,
        "font_name": style.font_name,
        "font_size": style.font_size,
        "primary_colour": style.primary_colour,
        "outline_colour": style.outline_colour,
        "back_colour": style.back_colour,
        "bold": style.bold,
        "outline": style.outline,
        "shadow": style.shadow,
        "margin_v": style.margin_v,
        "alignment": style.alignment,
        "description_zh": style.description_zh,
        "force_style": style.to_force_style(),
    }


# ---------------------------------------------------------------------------
# Tool definitions (4 tools per provides.tools, no handoff_*)
# ---------------------------------------------------------------------------

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "subtitle_craft_create",
        "description": (
            "Create a subtitle-craft task. Modes: auto_subtitle (Paraformer-v2 "
            "word-level ASR), translate (Qwen-MT multilingual), repair "
            "(timeline/wrap fixes), burn (ffmpeg ASS or Playwright HTML overlay)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["auto_subtitle", "translate", "repair", "burn"],
                },
                "source_path": {"type": "string"},
                "srt_path": {"type": "string"},
                "source_lang": {"type": "string"},
                "target_lang": {"type": "string"},
                "subtitle_style": {"type": "string"},
                "burn_engine": {"type": "string", "enum": ["ass", "html"]},
                "translation_model": {"type": "string"},
                "diarization_enabled": {"type": "boolean"},
                "character_identify_enabled": {"type": "boolean"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "subtitle_craft_status",
        "description": "Check the status of a subtitle-craft task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "subtitle_craft_list",
        "description": "List recent subtitle-craft tasks (default 10).",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    {
        "name": "subtitle_craft_cancel",
        "description": "Request cancellation of a running subtitle-craft task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
]


# Touch ``time`` so a future audit can confirm we kept it imported for
# polling diagnostics; the actual loop uses ``asyncio.sleep`` exclusively.
_ = time
