"""happyhorse-video plugin entry point.

Wires together the 12 generation modes (HappyHorse 1.0 + Wan 2.6/2.7 +
5 digital-human + long video) into a single plugin that exposes:

- 14 categories of REST routes for the React SPA in ``ui/dist/``
  (catalog / settings / probe / upload / tasks / cost-preview /
  storyboard / long-video / storage / healthz / python-deps /
  voices / figures / SSE).
- 13 LLM tools registered through ``api.register_tools`` so an org
  agent (e.g. the new HAPPYHORSE_VIDEO_STUDIO template) can drive
  every mode by name and the OrgRuntime hook can ingest the produced
  ``video_url`` / ``last_frame_url`` / ``asset_ids`` automatically.
- Plugin lifecycle (``on_load`` / ``on_unload``) that boots the SQLite
  task manager, the DashScope client, and a lazy ``oss2`` /
  ``edge-tts`` / ``mutagen`` background install via dep_bootstrap.

Workbench protocol contract (kept stable across plugin versions —
``tests/test_happyhorse_workbench_protocol.py`` enforces it):

- Every ``hh_*`` tool returns JSON with the keys
  ``ok / task_id / status / mode / model_id / video_url / video_path /
  last_frame_url / last_frame_path / local_paths / asset_ids``.
  Failed tasks set ``ok=false`` + ``terminal=true`` + ``error_message``
  + ``error_kind``.
- Every ``hh_*`` tool that creates a task accepts ``from_asset_ids``
  (list[str]) — ``_expand_from_asset_ids(asset_ids, mode)`` resolves
  each upstream Asset Bus row into the right per-mode input field
  (first_frame / reference_urls / source_video_url / image_url).
- Successful tasks publish their video + last_frame through
  ``api.publish_asset(...)`` and stamp the resulting ids back into
  ``tasks.asset_ids_json`` so a downstream plugin (e.g. the next
  long-video segment, or a captioning workbench) can consume them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_ID = "happyhorse-video"

# Plugin loader injects PLUGIN_DIR onto sys.path so we can import the
# vendored helper modules by their bare ``happyhorse_*`` names.
from happyhorse_dashscope_client import (  # noqa: E402
    HappyhorseDashScopeClient,
    make_default_settings,
)
from happyhorse_inline.oss_uploader import (  # noqa: E402
    OssUploadError,
    OssUploader,
)
from happyhorse_inline.storage_stats import collect_storage_stats  # noqa: E402
from happyhorse_inline.system_deps import SystemDepsManager  # noqa: E402
from happyhorse_inline.upload_preview import (  # noqa: E402
    add_upload_preview_route,
    build_preview_url,
)
from happyhorse_inline.vendor_client import VendorError  # noqa: E402
from happyhorse_long_video import (  # noqa: E402
    ChainGenerator,
    concat_videos,
    decompose_storyboard,
    ffmpeg_available,
)
from happyhorse_model_registry import RegistryPayload, default_model  # noqa: E402
from happyhorse_models import (  # noqa: E402
    MODES_BY_ID,
    SYSTEM_VOICES,
    build_catalog,
    estimate_cost,
)
from happyhorse_pipeline import (  # noqa: E402
    HappyhorsePipelineContext,
    run_pipeline,
)
from happyhorse_prompt_optimizer import (  # noqa: E402
    ATMOSPHERE_KEYWORDS,
    CAMERA_KEYWORDS,
    MODE_FORMULAS,
    PROMPT_TEMPLATES,
    PromptOptimizeError,
    optimize_prompt,
)
from happyhorse_task_manager import HappyhorseTaskManager  # noqa: E402

from openakita.plugins.api import PluginAPI, PluginBase  # noqa: E402

logger = logging.getLogger(__name__)


# ─── Pydantic request bodies ──────────────────────────────────────────


class CreateTaskBody(BaseModel):
    mode: str
    prompt: str = ""
    model_id: str = ""
    duration: int | None = None
    resolution: str = "720P"
    aspect_ratio: str = "16:9"
    voice_id: str = ""
    tts_engine: str = ""
    text: str = ""
    audio_url: str = ""
    first_frame_url: str = ""
    last_frame_url: str = ""
    source_video_url: str = ""
    reference_urls: list[str] = Field(default_factory=list)
    image_url: str = ""
    image_urls: list[str] = Field(default_factory=list)
    animate_mode: str = "wan-std"
    cost_approved: bool = False
    client_request_id: str = ""
    from_asset_ids: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class CostPreviewBody(BaseModel):
    mode: str
    model_id: str = ""
    duration: int | None = None
    resolution: str = "720P"
    aspect_ratio: str = "16:9"
    text: str = ""
    audio_duration_sec: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SettingsUpdateBody(BaseModel):
    updates: dict[str, str]


class TestConnectionBody(BaseModel):
    api_key: str = ""


class StoryboardDecomposeBody(BaseModel):
    story: str
    total_duration: int = 60
    segment_duration: int = 10
    aspect_ratio: str = "16:9"
    style: str = "电影级画质"


class LongVideoCreateBody(BaseModel):
    segments: list[dict] = Field(default_factory=list)
    model_id: str = "happyhorse-1.0-i2v"
    aspect_ratio: str = "16:9"
    resolution: str = "720P"
    mode: str = "serial"
    transition: str = "none"
    fade_duration: float = 0.5
    first_frame_url: str = ""
    max_parallel: int = 3


class ConcatBody(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    transition: str = "none"
    fade_duration: float = 0.5
    output_name: str = ""


class PromptOptimizeBody(BaseModel):
    prompt: str
    mode: str = "t2v"
    model_id: str = ""
    duration: int = 5
    aspect_ratio: str = "16:9"
    resolution: str = "720P"
    asset_summary: str = "无"
    level: str = "professional"


class VoicePreviewBody(BaseModel):
    voice_id: str
    text: str = "你好，这是一段试听。"


class VoiceCloneBody(BaseModel):
    label: str
    sample_audio_url: str
    language: str = "zh-CN"
    gender: str = "unknown"


class FigureCreateBody(BaseModel):
    label: str
    image_path: str
    preview_url: str
    oss_url: str = ""
    oss_key: str = ""


class SystemInstallBody(BaseModel):
    method_index: int = 0


# ─── Plugin class ─────────────────────────────────────────────────────


class Plugin(PluginBase):
    """OpenAkita plugin entry — see module docstring for full design."""

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._data_dir: Path = api.get_data_dir()
        self._tm = HappyhorseTaskManager(self._data_dir / "happyhorse.db")
        self._client = HappyhorseDashScopeClient(self._read_settings)
        self._oss = OssUploader(
            read_settings=self._read_settings, plugin_dir=PLUGIN_DIR
        )
        self._sysdeps = SystemDepsManager()
        self._settings_cache: dict[str, Any] = {}
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}
        self._chain_tasks: dict[str, asyncio.Task[Any]] = {}
        self._pending_create: dict[str, asyncio.Future[Any]] = {}
        self._sse_subscribers: list[asyncio.Queue[dict[str, Any]]] = []

        # Lazy preinstall — non-fatal if it fails (install on first use).
        try:
            from happyhorse_inline.dep_bootstrap import preinstall_async

            preinstall_async(
                [
                    ("oss2", "oss2>=2.18.0"),
                    ("mutagen", "mutagen>=1.47.0"),
                ],
                plugin_dir=PLUGIN_DIR,
            )
        except Exception as exc:  # noqa: BLE001
            api.log(
                f"happyhorse-video: dep preinstall skipped ({exc!r})",
                level="warning",
            )

        router = APIRouter()
        add_upload_preview_route(router, base_dir=self._data_dir / "uploads")
        self._register_routes(router)
        api.register_api_routes(router)
        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log(
            "happyhorse-video loaded — 12 modes, 13 tools, single DashScope backend",
        )

    async def _async_init(self) -> None:
        await self._tm.init()
        await self._reload_settings_cache()
        try:
            stale = await self._tm.list_tasks(status="running", limit=200)
            for row in stale:
                await self._tm.update_task_safe(
                    row["id"],
                    status="failed",
                    error_kind="server",
                    error_message="plugin restarted while running",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("happyhorse-video: stale task drain error: %s", exc)

    async def on_unload(self) -> None:
        for tid, t in list(self._poll_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "happyhorse-video: pipeline %s drain error: %s",
                        tid,
                        exc,
                    )
        for gid, t in list(self._chain_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "happyhorse-video: chain %s drain error: %s",
                        gid,
                        exc,
                    )
        for fut in list(self._pending_create.values()):
            if not fut.done():
                fut.cancel()
        try:
            await self._sysdeps.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("happyhorse-video: sysdeps close error: %s", exc)
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("happyhorse-video: client close error: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("happyhorse-video: tm close error: %s", exc)

    # ── Settings I/O (sync read used by client / oss / pipeline) ──────

    def _read_settings(self) -> dict[str, Any]:
        merged = make_default_settings()
        for k, v in (self._settings_cache or {}).items():
            if v not in (None, ""):
                merged[k] = v
        return merged

    async def _reload_settings_cache(self) -> None:
        try:
            self._settings_cache = await self._tm.get_all_config()
        except Exception as exc:  # noqa: BLE001
            logger.warning("happyhorse-video: settings reload error: %s", exc)
            self._settings_cache = {}

    # ── Workbench protocol contract ────────────────────────────────────

    @staticmethod
    def _task_to_tool_payload(task: dict, *, brief: bool = False) -> dict:
        """Project a happyhorse-video task into the JSON shape required by
        :func:`OrgRuntime._record_plugin_asset_output` and the LLM-facing
        tool handlers. Stable across plugin versions —
        ``test_happyhorse_workbench_protocol`` enforces the schema.
        """
        terminal_failures = {"failed", "timeout", "cancelled"}
        status_str = str(task.get("status") or "")

        video_path = str(task.get("video_path") or "")
        last_frame_path = str(task.get("last_frame_path") or "")
        local_paths: list[str] = []
        if video_path:
            local_paths.append(video_path)
        if last_frame_path:
            local_paths.append(last_frame_path)

        asset_ids = task.get("asset_ids") or []
        if isinstance(asset_ids, str):
            try:
                asset_ids = json.loads(asset_ids) or []
            except Exception:  # noqa: BLE001
                asset_ids = []

        base: dict[str, Any] = {
            "ok": status_str not in terminal_failures,
            "task_id": task.get("id"),
            "status": status_str,
            "mode": task.get("mode"),
            "model_id": task.get("model_id") or "",
            "video_url": str(task.get("video_url") or ""),
            "video_path": video_path,
            "last_frame_url": str(task.get("last_frame_url") or ""),
            "last_frame_path": last_frame_path,
            "local_paths": local_paths,
            "asset_ids": list(asset_ids),
        }
        if status_str in terminal_failures:
            base["terminal"] = True
        if task.get("error_kind"):
            base["error_kind"] = task["error_kind"]
        if task.get("error_message"):
            base["error_message"] = task["error_message"]
        if (
            status_str == "succeeded"
            and base["video_url"]
            and not local_paths
            and not base["asset_ids"]
        ):
            base["download_warning"] = (
                "云端任务已成功，但本地素材下载/发布失败。下游 workbench 节点请直接"
                "使用 video_url 作为交付物，不要重新生成；后台会在网络恢复后自动补抓。"
            )
        if brief:
            base["prompt"] = (task.get("prompt") or "")[:200]
            base["created_at"] = task.get("created_at")
        return base

    async def _expand_from_asset_ids(
        self, asset_ids: list[str], mode: str
    ) -> dict[str, Any]:
        """Materialise upstream Asset Bus rows into per-mode input fields.

        Per-mode role assignment:

        - ``i2v``         → first_frame_url = asset_ids[0],
                            reference_urls  = asset_ids[1:]
        - ``i2v_end``     → first_frame_url = asset_ids[0],
                            last_frame_url  = asset_ids[1]
        - ``r2v``         → reference_urls  = asset_ids (all)
        - ``video_extend``/ ``video_edit`` → source_video_url = asset_ids[0]
                            (must be a video asset)
        - ``photo_speak`` / ``avatar_compose`` → image_url = asset_ids[0],
                            image_urls = asset_ids[1:]
        - ``video_relip`` → source_video_url = asset_ids[0]
                            (audio must come via ``audio_url`` directly)
        - ``video_reface`` → source_video_url = asset_ids[0],
                            image_url       = asset_ids[1]
        - ``pose_drive``  → image_url       = asset_ids[0],
                            source_video_url = asset_ids[1]

        Unknown / unreadable asset_ids are skipped silently; callers that
        require media validate the resulting dict and raise a 400.
        """
        if not asset_ids:
            return {}
        urls: list[str] = []
        kinds: list[str] = []
        for aid in asset_ids:
            try:
                asset = await self._api.consume_asset(aid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "happyhorse-video: consume_asset(%s) failed: %s", aid, exc
                )
                continue
            if not asset:
                continue
            url = (
                str(asset.get("preview_url") or "")
                or str(asset.get("public_url") or "")
                or str(asset.get("source_path") or "")
            )
            if not url:
                continue
            urls.append(url)
            kinds.append(str(asset.get("asset_kind") or ""))

        out: dict[str, Any] = {}
        if not urls:
            return out
        if mode == "i2v":
            out["first_frame_url"] = urls[0]
            if len(urls) > 1:
                out["reference_urls"] = urls[1:]
        elif mode == "i2v_end":
            out["first_frame_url"] = urls[0]
            if len(urls) > 1:
                out["last_frame_url"] = urls[1]
        elif mode == "r2v":
            out["reference_urls"] = list(urls)
        elif mode in ("video_extend", "video_edit", "video_relip"):
            out["source_video_url"] = urls[0]
            if len(urls) > 1 and mode != "video_relip":
                out["reference_urls"] = urls[1:]
        elif mode == "video_reface":
            out["source_video_url"] = urls[0]
            if len(urls) > 1:
                out["image_url"] = urls[1]
        elif mode == "pose_drive":
            out["image_url"] = urls[0]
            if len(urls) > 1:
                out["source_video_url"] = urls[1]
        elif mode in ("photo_speak", "avatar_compose"):
            out["image_url"] = urls[0]
            if len(urls) > 1:
                out["image_urls"] = urls[1:]
        else:  # t2v / long_video — references only
            out["reference_urls"] = list(urls)
        return out

    # ── SSE broadcast ──────────────────────────────────────────────────

    def _broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """Fan an event out to every SSE subscriber AND the host bus."""
        try:
            self._api.broadcast_ui_event(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: broadcast_ui_event %r failed: %s",
                event,
                exc,
            )
        msg = {"event": event, "data": payload}
        for q in list(self._sse_subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                logger.debug("happyhorse-video: SSE queue full, dropping event")

    # ── Internal task creation + pipeline launch ──────────────────────

    async def _create_task_internal(self, body: CreateTaskBody) -> dict[str, Any]:
        """Validate a CreateTaskBody, expand upstream asset_ids, persist a
        ``tasks`` row, and kick off the pipeline coroutine in the
        background. Returns the freshly inserted row.
        """
        if not self._client.has_api_key():
            raise HTTPException(
                status_code=400,
                detail=(
                    "尚未配置百炼 API Key — 请到「设置 → 阿里云百炼」填写"
                    "DashScope 密钥（北京区）。"
                ),
            )
        spec = MODES_BY_ID.get(body.mode)
        if spec is None:
            raise HTTPException(
                status_code=400, detail=f"不支持的模式 {body.mode!r}"
            )
        # Resolve default model when caller leaves model_id blank.
        if not body.model_id:
            entry = default_model(body.mode)
            if entry is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"模式 {body.mode} 没有可用模型，请检查注册表。",
                )
            body.model_id = entry.model_id

        # Idempotency guard against double-clicks / bridge retries.
        if body.client_request_id:
            existing = await self._tm.get_task_by_client_request_id(
                body.client_request_id
            )
            if existing:
                return existing
            in_flight = self._pending_create.get(body.client_request_id)
            if in_flight is not None:
                return await in_flight
            fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._pending_create[body.client_request_id] = fut
        else:
            fut = None

        try:
            params = body.model_dump()
            # Expand from_asset_ids before validation so per-mode required
            # asset checks see the materialised URLs.
            if body.from_asset_ids:
                expanded = await self._expand_from_asset_ids(
                    body.from_asset_ids, body.mode
                )
                for k, v in expanded.items():
                    if v and not params.get(k):
                        params[k] = v
                params["from_asset_ids"] = list(body.from_asset_ids)

            # Per-mode required-asset gates (Pixelle V1 — fail fast with
            # an actionable Chinese hint instead of a 5xx 12 minutes later).
            self._validate_required_assets(body.mode, params)

            task_id = await self._tm.create_task(
                mode=body.mode,
                model_id=body.model_id,
                prompt=body.prompt,
                params=params,
                client_request_id=body.client_request_id,
            )
            row = await self._tm.get_task(task_id)
            assert row is not None
            self._spawn_pipeline(task_id, body, params)
            if fut is not None and not fut.done():
                fut.set_result(row)
            return row
        except Exception as exc:
            if fut is not None and not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            if body.client_request_id:
                self._pending_create.pop(body.client_request_id, None)

    @staticmethod
    def _validate_required_assets(mode: str, params: dict[str, Any]) -> None:
        spec = MODES_BY_ID.get(mode)
        required = list(getattr(spec, "required_assets", []) or [])
        for key in required:
            if key == "first_frame_url" and not params.get("first_frame_url"):
                raise HTTPException(
                    status_code=400, detail="i2v 模式需要先上传或指定首帧图片"
                )
            if key == "last_frame_url" and not params.get("last_frame_url"):
                raise HTTPException(
                    status_code=400, detail="首尾帧模式需要同时提供首帧和尾帧"
                )
            if key == "source_video_url" and not params.get("source_video_url"):
                raise HTTPException(
                    status_code=400, detail="该模式需要先指定 source_video_url（公网 http(s)）"
                )
            if key == "reference_urls" and not params.get("reference_urls"):
                raise HTTPException(
                    status_code=400, detail="r2v 模式至少需要 1 张参考人物图"
                )
            if key == "image_url" and not params.get("image_url"):
                raise HTTPException(
                    status_code=400, detail="该模式需要 image_url（人脸 / 形象图）"
                )
            if key == "audio_url" and not params.get("audio_url") and not params.get("text"):
                raise HTTPException(
                    status_code=400,
                    detail="该模式需要 audio_url 或 text（用于 TTS 生成音频）",
                )

    def _spawn_pipeline(
        self,
        task_id: str,
        body: CreateTaskBody,
        params: dict[str, Any],
    ) -> None:
        """Schedule run_pipeline as a background task. Idempotent."""
        if task_id in self._poll_tasks and not self._poll_tasks[task_id].done():
            return

        async def emit(event: str, payload: dict[str, Any]) -> None:
            self._broadcast(event, payload)

        # Pipeline reads ``ctx.params['_publish_asset']`` to register
        # downloaded videos. Inject the bound method here.
        params = dict(params)
        params["_publish_asset"] = self._publish_local_asset

        ctx = HappyhorsePipelineContext(
            task_id=task_id,
            mode=body.mode,
            params=params,
            model_id=body.model_id,
        )
        ctx.cost_approved = bool(body.cost_approved)

        coro = run_pipeline(
            ctx,
            tm=self._tm,
            client=self._client,
            emit=emit,
            plugin_id=PLUGIN_ID,
            base_data_dir=self._data_dir,
        )
        task = self._api.spawn_task(coro, name=f"{PLUGIN_ID}:pipe:{task_id}")
        self._poll_tasks[task_id] = task

    async def _publish_local_asset(
        self,
        *,
        kind: str,
        local_path: Path | str,
        preview_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Publish a downloaded artifact to the Asset Bus and return its id.

        Called by ``happyhorse_pipeline._step_finalize`` once the video and
        last_frame have been downloaded. Errors are swallowed and a blank
        string is returned so a publish failure never blocks the task
        from succeeding (the LLM still gets ``video_url`` directly).
        """
        try:
            aid = await self._api.publish_asset(
                asset_kind=kind,
                source_path=str(local_path) if local_path else None,
                preview_url=preview_url or None,
                metadata=metadata or {},
                shared_with=["*"],
                ttl_seconds=86400,
            )
            return aid or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: publish_asset(%s) failed: %s", kind, exc
            )
            return ""

    # ── LLM tool definitions (13 tools) ───────────────────────────────

    def _tool_definitions(self) -> list[dict[str, Any]]:
        common_workbench_note = (
            "Returns JSON with {ok, task_id, status, mode, model_id, "
            "video_url, video_path, last_frame_url, last_frame_path, "
            "local_paths, asset_ids}. Set from_asset_ids to chain from an "
            "upstream image / video workbench (e.g. tongyi-image / "
            "another happyhorse-video task) and the input fields are "
            "filled automatically."
        )

        def _video_tool(
            name: str, mode: str, *, description: str
        ) -> dict[str, Any]:
            return {
                "name": name,
                "description": (
                    f"{description} {common_workbench_note}"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "model_id": {
                            "type": "string",
                            "description": (
                                f"Optional DashScope model id. Defaults to the "
                                f"per-mode default in /catalog (mode={mode})."
                            ),
                        },
                        "duration": {"type": "integer"},
                        "resolution": {
                            "type": "string",
                            "enum": ["720P", "1080P"],
                        },
                        "aspect_ratio": {"type": "string", "default": "16:9"},
                        "first_frame_url": {"type": "string"},
                        "last_frame_url": {"type": "string"},
                        "source_video_url": {"type": "string"},
                        "reference_urls": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "image_url": {"type": "string"},
                        "image_urls": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "voice_id": {"type": "string"},
                        "text": {"type": "string"},
                        "audio_url": {"type": "string"},
                        "from_asset_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Asset Bus IDs from an upstream workbench. "
                                "Per-mode role assignment: i2v → first_frame "
                                "(0) + reference_urls (1+); i2v_end → "
                                "first_frame (0) + last_frame (1); r2v → "
                                "reference_urls (all); video_extend / "
                                "video_edit → source_video_url (0); "
                                "photo_speak / avatar_compose → image_url "
                                "(0) + image_urls (1+)."
                            ),
                        },
                        "wait_for_completion": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "If true (default), the tool blocks until "
                                "the pipeline finishes. Set to false for "
                                "fire-and-forget UI-driven tasks."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
                "_mode": mode,  # internal — not part of MCP schema
            }

        return [
            _video_tool(
                "hh_t2v",
                "t2v",
                description=(
                    "Text-to-video via HappyHorse 1.0 (default) or Wan 2.6. "
                    "Native audio-sync when using a HappyHorse model."
                ),
            ),
            _video_tool(
                "hh_i2v",
                "i2v",
                description=(
                    "Image-to-video. Supply first_frame_url (or pull from "
                    "from_asset_ids[0]). Default model: happyhorse-1.0-i2v."
                ),
            ),
            _video_tool(
                "hh_r2v",
                "r2v",
                description=(
                    "Reference-to-video for multi-character interaction. "
                    "Supply reference_urls (or from_asset_ids). Default "
                    "model: happyhorse-1.0-r2v."
                ),
            ),
            _video_tool(
                "hh_video_edit",
                "video_edit",
                description=(
                    "Edit / restyle / inpaint an existing video via "
                    "happyhorse-1.0-video-edit. Requires source_video_url."
                ),
            ),
            _video_tool(
                "hh_photo_speak",
                "photo_speak",
                description=(
                    "Drive a portrait photo with a voice clip "
                    "(wan2.2-s2v). Supply image_url + (audio_url OR "
                    "text+voice_id)."
                ),
            ),
            _video_tool(
                "hh_video_relip",
                "video_relip",
                description=(
                    "Replace lip-sync of an existing video using a new "
                    "audio (videoretalk). Supply source_video_url + audio."
                ),
            ),
            _video_tool(
                "hh_video_reface",
                "video_reface",
                description=(
                    "Swap the face in a source video with a reference "
                    "portrait (wan2.2-animate-mix)."
                ),
            ),
            _video_tool(
                "hh_pose_drive",
                "pose_drive",
                description=(
                    "Animate a still image with the pose of a reference "
                    "video (wan2.2-animate-move)."
                ),
            ),
            _video_tool(
                "hh_avatar_compose",
                "avatar_compose",
                description=(
                    "Compose multiple reference images into a new avatar "
                    "and drive it with a voice (wan2.7-image → s2v)."
                ),
            ),
            {
                "name": "hh_status",
                "description": (
                    "Check the status of a happyhorse-video task. "
                    + common_workbench_note
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "hh_list",
                "description": (
                    "List recent happyhorse-video tasks. Returns JSON "
                    "{ok, total, tasks: [...]}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 10},
                        "mode": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            },
            {
                "name": "hh_cost_preview",
                "description": (
                    "Estimate the DashScope cost for a happyhorse-video "
                    "task without submitting it. Returns "
                    "{items, total_cny, formatted_total, exceeds_threshold}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "model_id": {"type": "string"},
                        "duration": {"type": "integer"},
                        "resolution": {"type": "string"},
                        "aspect_ratio": {"type": "string"},
                        "text": {"type": "string"},
                        "audio_duration_sec": {"type": "number"},
                    },
                    "required": ["mode"],
                },
            },
            {
                "name": "hh_long_video_create",
                "description": (
                    "Generate a long video from a list of storyboard "
                    "segments. Each segment is rendered as an i2v task; "
                    "consecutive segments chain via last_frame_url. "
                    "Returns the per-segment task ids and "
                    "chain_group_id; poll hh_status for each task to "
                    "obtain the final video_url + asset_ids."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "segments": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (
                                "List of {index, prompt, duration, "
                                "transition_to_next?} objects."
                            ),
                        },
                        "model_id": {
                            "type": "string",
                            "default": "happyhorse-1.0-i2v",
                        },
                        "aspect_ratio": {"type": "string", "default": "16:9"},
                        "resolution": {"type": "string", "default": "720P"},
                        "mode": {
                            "type": "string",
                            "enum": ["serial", "parallel", "cloud_extend"],
                            "default": "serial",
                        },
                        "first_frame_url": {"type": "string"},
                        "max_parallel": {"type": "integer", "default": 3},
                    },
                    "required": ["segments"],
                },
            },
        ]

    # ── LLM tool dispatch ──────────────────────────────────────────────

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "hh_status":
            return await self._tool_status(args)
        if tool_name == "hh_list":
            return await self._tool_list(args)
        if tool_name == "hh_cost_preview":
            return await self._tool_cost_preview(args)
        if tool_name == "hh_long_video_create":
            return await self._tool_long_video_create(args)

        # Video / digital-human tools — derive mode from tool name.
        mode_lookup = {
            "hh_t2v": "t2v",
            "hh_i2v": "i2v",
            "hh_r2v": "r2v",
            "hh_video_edit": "video_edit",
            "hh_photo_speak": "photo_speak",
            "hh_video_relip": "video_relip",
            "hh_video_reface": "video_reface",
            "hh_pose_drive": "pose_drive",
            "hh_avatar_compose": "avatar_compose",
        }
        mode = mode_lookup.get(tool_name)
        if mode is None:
            return json.dumps(
                {"ok": False, "error": f"Unknown tool: {tool_name}"},
                ensure_ascii=False,
            )
        return await self._tool_video(mode, args)

    async def _tool_video(self, mode: str, args: dict[str, Any]) -> str:
        try:
            body = CreateTaskBody(mode=mode, **{k: v for k, v in args.items() if k in CreateTaskBody.model_fields})
            task = await self._create_task_internal(body)
            if args.get("wait_for_completion", True):
                task = await self._wait_for_task(task["id"])
        except HTTPException as e:
            return json.dumps(
                {
                    "ok": False,
                    "terminal": e.status_code in (400, 401, 403, 413, 422),
                    "error": e.detail if isinstance(e.detail, str) else str(e.detail),
                    "status_code": e.status_code,
                },
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": str(e), "terminal": True},
                ensure_ascii=False,
            )
        return json.dumps(self._task_to_tool_payload(task), ensure_ascii=False)

    async def _tool_status(self, args: dict[str, Any]) -> str:
        task_id = str(args.get("task_id") or "")
        if not task_id:
            return json.dumps(
                {"ok": False, "error": "task_id is required"},
                ensure_ascii=False,
            )
        task = await self._tm.get_task(task_id)
        if task is None:
            return json.dumps(
                {"ok": False, "task_id": task_id, "error": "task not found"},
                ensure_ascii=False,
            )
        return json.dumps(self._task_to_tool_payload(task), ensure_ascii=False)

    async def _tool_list(self, args: dict[str, Any]) -> str:
        rows = await self._tm.list_tasks(
            status=args.get("status"),
            mode=args.get("mode"),
            limit=int(args.get("limit") or 10),
        )
        total = await self._tm.count_tasks()
        return json.dumps(
            {
                "ok": True,
                "total": total,
                "tasks": [
                    self._task_to_tool_payload(t, brief=True) for t in rows
                ],
            },
            ensure_ascii=False,
        )

    async def _tool_cost_preview(self, args: dict[str, Any]) -> str:
        mode = str(args.get("mode") or "")
        if mode not in MODES_BY_ID:
            return json.dumps(
                {"ok": False, "error": f"unknown mode: {mode}"},
                ensure_ascii=False,
            )
        params = {
            "model": args.get("model_id") or (
                default_model(mode).model_id if default_model(mode) else ""
            ),
            "duration": args.get("duration"),
            "resolution": args.get("resolution") or "720P",
            "aspect_ratio": args.get("aspect_ratio") or "16:9",
        }
        preview = estimate_cost(
            mode,
            params,
            audio_duration_sec=args.get("audio_duration_sec"),
            text_chars=len(str(args.get("text") or "")),
        )
        return json.dumps({"ok": True, **preview}, ensure_ascii=False)

    async def _tool_long_video_create(self, args: dict[str, Any]) -> str:
        try:
            body = LongVideoCreateBody(**args)
            chain_group_id = uuid.uuid4().hex
            chain = ChainGenerator(
                self._client, self._tm, chain_group_id=chain_group_id
            )
            task = self._api.spawn_task(
                chain.generate_chain(
                    segments=body.segments,
                    model_id=body.model_id,
                    ratio=body.aspect_ratio,
                    resolution=body.resolution,
                    mode=body.mode,
                    max_parallel=body.max_parallel,
                    first_frame_url=body.first_frame_url or None,
                ),
                name=f"{PLUGIN_ID}:chain:{chain_group_id}",
            )
            self._chain_tasks[chain_group_id] = task
            return json.dumps(
                {
                    "ok": True,
                    "chain_group_id": chain_group_id,
                    "segments_total": len(body.segments),
                    "message": (
                        "Long-video chain submitted. Poll hh_list with "
                        "chain_group_id to track per-segment progress."
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": str(e), "terminal": True},
                ensure_ascii=False,
            )

    async def _wait_for_task(
        self, task_id: str, *, timeout_s: int = 1800, interval: float = 5.0
    ) -> dict[str, Any]:
        deadline = time.time() + max(60, timeout_s)
        while time.time() < deadline:
            row = await self._tm.get_task(task_id)
            if row and row["status"] not in ("pending", "running"):
                return row
            await asyncio.sleep(interval)
        row = await self._tm.get_task(task_id)
        if row is None:
            return {"id": task_id, "status": "timeout"}
        out = dict(row)
        out["wait_hint"] = (
            f"同步等待已超过 {max(1, timeout_s // 60)} 分钟，任务仍在云端处理中。"
            "请使用 hh_status 查询，不要重新提交。"
        )
        return out

    # ── REST routes ────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:

        # Catalog --------------------------------------------------------
        @router.get("/catalog")
        async def get_catalog() -> dict:
            cat = build_catalog()
            return {
                "ok": True,
                "catalog": {
                    "modes": cat.modes,
                    "voices": cat.voices,
                    "resolutions": cat.resolutions,
                    "aspects": cat.aspects,
                    "animate_modes": cat.animate_modes,
                    "durations_video": cat.durations_video,
                    "cost_threshold": cat.cost_threshold,
                    "models": cat.models,
                    "default_models": cat.default_models,
                    "audio_limits": cat.audio_limits,
                },
                "has_api_key": self._client.has_api_key(),
                "oss_configured": self._oss.is_configured(),
                "ffmpeg_available": ffmpeg_available(),
            }

        # Settings -------------------------------------------------------
        @router.get("/settings")
        async def get_settings() -> dict:
            cfg = await self._tm.get_all_config()
            # Mask the api_key when echoing back so the UI surfaces "saved"
            # without exposing the full secret in DOM.
            redacted = dict(cfg)
            for sensitive in (
                "api_key",
                "ark_api_key",
                "oss_access_key_id",
                "oss_access_key_secret",
            ):
                if redacted.get(sensitive):
                    val = redacted[sensitive]
                    redacted[sensitive] = (
                        f"{val[:4]}***{val[-2:]}" if len(val) > 8 else "***"
                    )
                    redacted[f"{sensitive}_set"] = True
            return {"ok": True, "config": redacted}

        @router.put("/settings")
        async def put_settings(body: SettingsUpdateBody) -> dict:
            cleaned = {k: (v or "").strip() for k, v in body.updates.items()}
            await self._tm.set_configs(cleaned)
            await self._reload_settings_cache()
            self._client.update_api_key(self._settings_cache.get("api_key", ""))
            return {"ok": True}

        @router.post("/test-connection")
        async def test_connection(body: TestConnectionBody) -> dict:
            return await self._client.ping_api_key(body.api_key or None)

        # Upload ---------------------------------------------------------
        @router.post("/upload")
        async def upload(file: UploadFile = File(...)) -> dict:
            return await self._upload_handler(file)

        # Tasks ----------------------------------------------------------
        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict:
            row = await self._create_task_internal(body)
            return {"ok": True, "task": self._task_to_tool_payload(row)}

        @router.get("/tasks")
        async def list_tasks_route(
            status: str | None = None,
            mode: str | None = None,
            chain_group_id: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> dict:
            rows = await self._tm.list_tasks(
                status=status,
                mode=mode,
                chain_group_id=chain_group_id,
                limit=limit,
                offset=offset,
            )
            total = await self._tm.count_tasks(status=status)
            return {"ok": True, "total": total, "tasks": rows}

        @router.get("/tasks/{task_id}")
        async def get_task_route(task_id: str) -> dict:
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            return {"ok": True, "task": row}

        @router.delete("/tasks/{task_id}")
        async def delete_task_route(task_id: str) -> dict:
            ok = await self._tm.delete_task(task_id)
            return {"ok": ok}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task_route(task_id: str) -> dict:
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            params = row.get("params") or {}
            body = CreateTaskBody(
                mode=row["mode"],
                model_id=row.get("model_id") or "",
                prompt=row.get("prompt") or "",
                cost_approved=True,
                **{
                    k: v
                    for k, v in params.items()
                    if k in CreateTaskBody.model_fields
                    and k not in {"mode", "model_id", "prompt"}
                },
            )
            new_row = await self._create_task_internal(body)
            return {"ok": True, "task": new_row}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task_route(task_id: str) -> dict:
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            if row.get("dashscope_id"):
                await self._client.cancel_task(row["dashscope_id"])
            t = self._poll_tasks.get(task_id)
            if t is not None and not t.done():
                t.cancel()
            await self._tm.update_task_safe(task_id, status="cancelled")
            self._broadcast(
                "task_update", {"task_id": task_id, "status": "cancelled"}
            )
            return {"ok": True}

        # Cost preview ---------------------------------------------------
        @router.post("/cost-preview")
        async def cost_preview_route(body: CostPreviewBody) -> dict:
            params = body.model_dump()
            params["model"] = body.model_id or (
                default_model(body.mode).model_id
                if default_model(body.mode)
                else ""
            )
            preview = estimate_cost(
                body.mode,
                params,
                audio_duration_sec=body.audio_duration_sec,
                text_chars=len(body.text or ""),
            )
            return {"ok": True, **preview}

        # Storyboard / Long video ---------------------------------------
        @router.post("/storyboard/decompose")
        async def storyboard_decompose(body: StoryboardDecomposeBody) -> dict:
            if not self._api.has_permission("brain.access"):
                return {"ok": False, "error": "missing brain.access permission"}
            brain = self._api.get_brain()
            if not brain:
                return {"ok": False, "error": "brain unavailable"}
            result = await decompose_storyboard(
                brain=brain,
                story=body.story,
                total_duration=body.total_duration,
                segment_duration=body.segment_duration,
                ratio=body.aspect_ratio,
                style=body.style,
            )
            return {"ok": "error" not in result, **result}

        @router.post("/long-video/create")
        async def long_video_create(body: LongVideoCreateBody) -> dict:
            chain_group_id = uuid.uuid4().hex
            chain = ChainGenerator(
                self._client, self._tm, chain_group_id=chain_group_id
            )

            async def _run() -> None:
                try:
                    await chain.generate_chain(
                        segments=body.segments,
                        model_id=body.model_id,
                        ratio=body.aspect_ratio,
                        resolution=body.resolution,
                        mode=body.mode,
                        max_parallel=body.max_parallel,
                        first_frame_url=body.first_frame_url or None,
                    )
                finally:
                    self._chain_tasks.pop(chain_group_id, None)
                    self._broadcast(
                        "chain_update",
                        {"chain_group_id": chain_group_id, "status": "finished"},
                    )

            t = self._api.spawn_task(
                _run(), name=f"{PLUGIN_ID}:chain:{chain_group_id}"
            )
            self._chain_tasks[chain_group_id] = t
            return {
                "ok": True,
                "chain_group_id": chain_group_id,
                "segments_total": len(body.segments),
            }

        @router.get("/long-video/active-chains")
        async def long_video_active() -> dict:
            return {
                "ok": True,
                "chains": [
                    {"chain_group_id": gid, "running": not t.done()}
                    for gid, t in self._chain_tasks.items()
                ],
            }

        @router.post("/long-video/concat")
        async def long_video_concat(body: ConcatBody) -> dict:
            paths: list[str] = []
            for tid in body.task_ids:
                row = await self._tm.get_task(tid)
                if row and row.get("video_path"):
                    paths.append(row["video_path"])
            if len(paths) < 2:
                raise HTTPException(
                    status_code=400,
                    detail="至少需要 2 段已下载的视频片段才能拼接",
                )
            output_dir = self._data_dir / "outputs" / "concat"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_name = body.output_name or f"concat_{uuid.uuid4().hex[:8]}.mp4"
            output_path = output_dir / output_name
            ok = await concat_videos(
                paths,
                str(output_path),
                transition=body.transition,
                fade_duration=body.fade_duration,
            )
            if not ok:
                raise HTTPException(status_code=500, detail="ffmpeg concat failed")
            return {"ok": True, "output_path": str(output_path)}

        # Storage --------------------------------------------------------
        @router.get("/storage/stats")
        async def storage_stats_route() -> dict:
            stats: dict[str, dict] = {}
            for key, default in [
                ("output_dir", str(self._data_dir / "outputs")),
                ("cache_dir", str(self._data_dir / "cache")),
                ("uploads", str(self._data_dir / "uploads")),
                ("tasks", str(self._data_dir / "tasks")),
            ]:
                cfg = self._settings_cache or {}
                d = Path(cfg.get(key) or default)
                report = await collect_storage_stats(
                    d, max_files=20000, sample_paths=0, skip_hidden=True
                )
                stats[key] = {
                    "path": str(d),
                    "size_bytes": report.total_bytes,
                    "size_mb": round(report.total_bytes / 1048576, 1),
                    "file_count": report.total_files,
                    "truncated": report.truncated,
                }
            return {"ok": True, "stats": stats}

        @router.post("/storage/open-folder")
        async def open_folder(body: dict) -> dict:
            raw_path = (body.get("path") or "").strip()
            if not raw_path:
                raise HTTPException(status_code=400, detail="missing path")
            target = Path(raw_path).expanduser()
            target.mkdir(parents=True, exist_ok=True)
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
                    status_code=500, detail=f"cannot open: {exc}"
                ) from exc
            return {"ok": True, "path": str(target)}

        # Health + python-deps ------------------------------------------
        @router.get("/healthz")
        async def healthz() -> dict:
            return {
                "ok": True,
                "version": "1.0.0",
                "has_api_key": self._client.has_api_key(),
                "oss_configured": self._oss.is_configured(),
                "ffmpeg_available": ffmpeg_available(),
            }

        @router.get("/python-deps/status")
        async def deps_status() -> dict:
            try:
                from happyhorse_inline.dep_bootstrap import dep_status

                return {"ok": True, "deps": dep_status(plugin_dir=PLUGIN_DIR)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

        @router.post("/python-deps/install")
        async def deps_install(body: dict) -> dict:
            target = (body or {}).get("name") or ""
            if target not in {"oss2", "edge-tts", "mutagen", "dashscope"}:
                raise HTTPException(
                    status_code=400, detail=f"unsupported dep: {target}"
                )
            try:
                from happyhorse_inline.dep_bootstrap import ensure_importable

                ensure_importable(
                    target,
                    f"{target}>=0.0.0",
                    plugin_dir=PLUGIN_DIR,
                    friendly_name=target,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            return {"ok": True}

        # Voices ---------------------------------------------------------
        @router.get("/voices")
        async def list_voices_route() -> dict:
            cloned = await self._tm.list_voices()
            return {
                "ok": True,
                "system": [v.to_dict() for v in SYSTEM_VOICES],
                "cloned": cloned,
            }

        @router.post("/voices/preview")
        async def preview_voice(body: VoicePreviewBody) -> dict:
            try:
                preview_path = self._data_dir / "previews"
                preview_path.mkdir(parents=True, exist_ok=True)
                out = preview_path / f"{uuid.uuid4().hex[:8]}.mp3"
                # Engine selection — same logic as pipeline step-4.
                voice_id = body.voice_id
                engine = (
                    "edge"
                    if voice_id.startswith(("zh-CN", "zh-HK", "zh-TW"))
                    else "cosyvoice"
                )
                if engine == "edge":
                    from happyhorse_tts_edge import synth_voice as edge_synth

                    await edge_synth(
                        text=body.text or "你好，这是一段试听。",
                        voice=voice_id,
                        output_path=out,
                    )
                else:
                    audio_bytes = await self._client.synth_voice(
                        text=body.text or "你好，这是一段试听。",
                        voice=voice_id,
                    )
                    out.write_bytes(audio_bytes)
                return {"ok": True, "audio_path": str(out)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

        @router.post("/voices/clone")
        async def clone_voice(body: VoiceCloneBody) -> dict:
            try:
                cosyvoice_id = await self._client.clone_voice(
                    label=body.label,
                    sample_audio_url=body.sample_audio_url,
                )
                voice_id = await self._tm.create_custom_voice(
                    label=body.label,
                    source_audio_path=body.sample_audio_url,
                    dashscope_voice_id=cosyvoice_id,
                    sample_url=body.sample_audio_url,
                    language=body.language,
                    gender=body.gender,
                )
                return {"ok": True, "voice_id": voice_id}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

        @router.delete("/voices/{voice_id}")
        async def delete_voice(voice_id: str) -> dict:
            ok = await self._tm.delete_custom_voice(voice_id)
            return {"ok": ok}

        # Figures --------------------------------------------------------
        @router.get("/figures")
        async def list_figures() -> dict:
            return {"ok": True, "figures": await self._tm.list_figures()}

        @router.post("/figures")
        async def create_figure(body: FigureCreateBody) -> dict:
            fid = await self._tm.create_figure(
                label=body.label,
                image_path=body.image_path,
                preview_url=body.preview_url,
                oss_url=body.oss_url,
                oss_key=body.oss_key,
            )
            return {"ok": True, "figure_id": fid}

        @router.delete("/figures/{fig_id}")
        async def delete_figure(fig_id: str) -> dict:
            ok = await self._tm.delete_figure(fig_id)
            return {"ok": ok}

        # Prompt helpers -------------------------------------------------
        @router.get("/prompt-guide")
        async def prompt_guide() -> dict:
            return {
                "ok": True,
                "templates": PROMPT_TEMPLATES,
                "cameras": CAMERA_KEYWORDS,
                "atmosphere": ATMOSPHERE_KEYWORDS,
                "formulas": MODE_FORMULAS,
            }

        @router.post("/prompt-optimize")
        async def prompt_optimize_route(body: PromptOptimizeBody) -> dict:
            if not self._api.has_permission("brain.access"):
                return {"ok": False, "error": "missing brain.access permission"}
            brain = self._api.get_brain()
            if not brain:
                return {"ok": False, "error": "brain unavailable"}
            try:
                result = await optimize_prompt(
                    brain=brain,
                    user_prompt=body.prompt,
                    mode=body.mode,
                    model_id=body.model_id,
                    duration=body.duration,
                    ratio=body.aspect_ratio,
                    resolution=body.resolution,
                    asset_summary=body.asset_summary,
                    level=body.level,
                )
                return {"ok": True, "result": result}
            except PromptOptimizeError as e:
                return {"ok": False, "error": str(e)}

        # SSE ------------------------------------------------------------
        @router.get("/sse")
        async def sse_endpoint():
            from fastapi.responses import StreamingResponse

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
            self._sse_subscribers.append(queue)

            async def gen():
                try:
                    while True:
                        try:
                            msg = await asyncio.wait_for(
                                queue.get(), timeout=15.0
                            )
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                        body = json.dumps(
                            {
                                "event": msg.get("event"),
                                "data": msg.get("data") or {},
                            },
                            ensure_ascii=False,
                        )
                        yield f"event: {msg.get('event')}\ndata: {body}\n\n"
                finally:
                    if queue in self._sse_subscribers:
                        self._sse_subscribers.remove(queue)

            return StreamingResponse(gen(), media_type="text/event-stream")

        # System deps (FFmpeg installer) --------------------------------
        @router.get("/system/components")
        async def system_components() -> dict:
            return {"ok": True, "items": self._sysdeps.list_components()}

        @router.post("/system/{dep_id}/install")
        async def system_install(dep_id: str, body: SystemInstallBody) -> dict:
            try:
                return await self._sysdeps.start_install(
                    dep_id, method_index=body.method_index
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @router.get("/system/{dep_id}/status")
        async def system_status(dep_id: str) -> dict:
            try:
                return self._sysdeps.status(dep_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── /upload handler (factored out so tests can target it) ─────────

    async def _upload_handler(self, file: UploadFile) -> dict[str, Any]:
        """Persist an uploaded file under ``uploads/<kind>/<uuid>_<name>``,
        push it to OSS when configured, and return an asset row that the
        UI can drop directly into a CreateTaskBody.
        """
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif"}
        VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
        AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
        MAX_BYTES = 200 * 1024 * 1024  # OSS-backed → big files OK
        content = await file.read()
        if len(content) > MAX_BYTES:
            return {
                "ok": False,
                "error": "file_too_large",
                "size_mb": round(len(content) / 1048576, 1),
                "max_mb": 200,
            }
        ext = Path(file.filename or "file").suffix.lower()
        if ext in IMAGE_EXTS:
            kind, subdir = "image", "images"
        elif ext in VIDEO_EXTS:
            kind, subdir = "video", "videos"
        elif ext in AUDIO_EXTS:
            kind, subdir = "audio", "audios"
        else:
            return {
                "ok": False,
                "error": "unsupported_type",
                "ext": ext or "(none)",
            }

        uploads_dir = self._data_dir / "uploads" / subdir
        uploads_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:8]}_{file.filename or 'file'}"
        local_path = uploads_dir / filename
        local_path.write_bytes(content)
        rel_path = f"{subdir}/{filename}"
        preview_url = build_preview_url(PLUGIN_ID, rel_path)

        oss_url = ""
        oss_key = ""
        if self._oss.is_configured():
            try:
                oss_key = self._oss.build_object_key(
                    scope=f"uploads/{subdir}", filename=filename
                )
                oss_url = await asyncio.to_thread(
                    self._oss.upload_file, local_path, key=oss_key
                )
            except OssUploadError as exc:
                logger.warning("happyhorse-video: OSS upload failed: %s", exc)

        asset_row = await self._tm.create_asset(
            type=kind,
            file_path=str(local_path),
            original_name=file.filename,
            size_bytes=len(content),
        )
        return {
            "ok": True,
            "kind": kind,
            "size_bytes": len(content),
            "preview_url": preview_url,
            "oss_url": oss_url,
            "oss_key": oss_key,
            "local_path": str(local_path),
            "asset": asset_row,
        }
