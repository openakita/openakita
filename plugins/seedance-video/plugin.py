"""Seedance Video Generator — full-stack plugin for AI video generation.

Backend entry point providing all REST API endpoints for the frontend UI.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from ark_client import ArkClient
from fastapi import APIRouter, File, HTTPException, UploadFile
from long_video import (
    ChainGenerator,
    concat_videos,
    decompose_storyboard,
    ffmpeg_available,
)
from models import (
    RESOLUTION_PIXEL_MAP,
    SEEDANCE_MODELS,
    get_model,
    model_to_dict,
)
from prompt_optimizer import (
    ATMOSPHERE_KEYWORDS,
    CAMERA_KEYWORDS,
    MODE_FORMULAS,
    PROMPT_TEMPLATES,
    PromptOptimizeError,
    optimize_prompt,
)
from pydantic import BaseModel, Field
from seedance_inline.storage_stats import collect_storage_stats
from seedance_inline.system_deps import SystemDepsManager
from seedance_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from seedance_inline.vendor_client import VendorError
from task_manager import TaskManager

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


def _normalize_base_url(value: str | None, *, field: str = "Base URL") -> str:
    base_url = (value or "").strip().rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"{field} 必须以 http:// 或 https:// 开头")
    return base_url


# ── Request / Response models ──

class CreateTaskBody(BaseModel):
    prompt: str = ""
    mode: str = "t2v"
    model: str = "2.0"
    ratio: str = "16:9"
    duration: int = 5
    resolution: str = "720p"
    n: int = 1
    generate_audio: bool = True
    seed: int = -1
    watermark: bool = False
    camera_fixed: bool = False
    return_last_frame: bool = False
    web_search: bool = False
    service_tier: str = "default"
    callback_url: str | None = None
    execution_expires_after: int | None = None
    client_request_id: str = ""
    content: list[dict] | None = None

class VideoUrlTaskBody(BaseModel):
    prompt: str = ""
    source_video_url: str
    model: str = "2.0"
    ratio: str = "16:9"
    duration: int = 5
    resolution: str = "720p"
    generate_audio: bool = True
    seed: int = -1
    watermark: bool = False
    service_tier: str = "default"
    callback_url: str | None = None
    execution_expires_after: int | None = None
    client_request_id: str = ""
    next_scene_prompt: str = ""

class DraftConfirmBody(BaseModel):
    resolution: str = "720p"
    watermark: bool = False
    return_last_frame: bool = False

class ConfigUpdateBody(BaseModel):
    updates: dict[str, str]

class PromptOptimizeBody(BaseModel):
    prompt: str
    mode: str = "t2v"
    duration: int = 5
    ratio: str = "16:9"
    asset_summary: str = "无"
    level: str = "professional"


class StoryboardDecomposeBody(BaseModel):
    story: str
    total_duration: int = 60
    segment_duration: int = 10
    ratio: str = "16:9"
    style: str = "电影级画质"


class LongVideoCreateBody(BaseModel):
    segments: list[dict] = Field(default_factory=list)
    model: str = "2.0"
    ratio: str = "16:9"
    resolution: str = "720p"
    mode: str = "serial"
    transition: str = "none"
    fade_duration: float = 0.5


class ConcatBody(BaseModel):
    task_ids: list[str]
    transition: str = "none"
    fade_duration: float = 0.5
    output_name: str = ""


class SystemInstallBody(BaseModel):
    method_index: int = 0


class SystemUninstallBody(BaseModel):
    method_index: int = 0


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        self._tm = TaskManager(data_dir / "seedance.db")
        self._ark: ArkClient | None = None
        self._poll_task: asyncio.Task | None = None
        self._brain = None
        # In-plugin replacement for the retired SDK 0.6.x DependencyGate —
        # see seedance_inline/system_deps.py module docstring for rationale.
        self._sysdeps = SystemDepsManager()
        # Active long-video chains (Sprint 8 / V2). Keyed by ``group_id``,
        # each value is ``{signature, started_at, segments_total, task,
        # mode, model}``. Used to (a) prevent duplicate submissions of the
        # same storyboard within a single process and (b) let the UI poll
        # progress via ``/long-video/active-chains`` after a tab refresh.
        self._active_chains: dict[str, dict[str, Any]] = {}
        # In-process idempotency guard for CreateTab submissions. Without
        # this, a slow POST /tasks plus a retry/double-click can create two
        # identical Ark jobs (and therefore charge twice). Keyed by the
        # browser-supplied client_request_id.
        self._pending_create_requests: dict[str, asyncio.Future] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([
            {
                "name": "seedance_create",
                "description": (
                    "Create a Seedance video generation task. Returns JSON: "
                    "{ok, task_id, status, mode, video_url, video_path, "
                    "last_frame_url, asset_ids}. Often used as a 'video output' "
                    "workbench node in org orchestration — set from_asset_ids "
                    "to the upstream image workbench's asset_ids to feed "
                    "first_frame/last_frame/reference_image directly without "
                    "rehosting."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Video generation prompt"},
                        "mode": {"type": "string", "enum": ["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"]},
                        "duration": {"type": "integer", "default": 5},
                        "ratio": {"type": "string", "default": "16:9"},
                        "from_asset_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Asset Bus IDs produced by upstream workbenches "
                                "(e.g. tongyi-image). Expanded into content[].image_url "
                                "before submission. Role assignment by index + mode: "
                                "i2v → first_frame (0), reference_image (1+); "
                                "i2v_end → first_frame (0), last_frame (1+); "
                                "multimodal → first_frame (0), reference_image (1+)."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "seedance_status",
                "description": (
                    "Check status of a Seedance video generation task. "
                    "Returns JSON: {ok, task_id, status, mode, video_url, "
                    "video_path, last_frame_url, asset_ids, error_message}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "seedance_edit",
                "description": (
                    "Edit an existing Seedance video. Requires source_video_url "
                    "to be a public http(s) cloud video URL, usually the video_url "
                    "returned by a previous seedance task. Local files/base64 are rejected."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Edit instruction"},
                        "source_video_url": {"type": "string", "description": "Public http(s) video URL"},
                        "model": {"type": "string", "default": "2.0"},
                        "duration": {"type": "integer", "default": 5},
                        "ratio": {"type": "string", "default": "16:9"},
                        "resolution": {"type": "string", "default": "720p"},
                    },
                    "required": ["prompt", "source_video_url"],
                },
            },
            {
                "name": "seedance_extend",
                "description": (
                    "Extend/continue an existing Seedance video. Requires source_video_url "
                    "to be a public http(s) cloud video URL, usually the video_url returned "
                    "by a previous seedance task."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Continuation instruction"},
                        "source_video_url": {"type": "string", "description": "Public http(s) video URL"},
                        "model": {"type": "string", "default": "2.0"},
                        "duration": {"type": "integer", "default": 5},
                        "ratio": {"type": "string", "default": "16:9"},
                        "resolution": {"type": "string", "default": "720p"},
                    },
                    "required": ["prompt", "source_video_url"],
                },
            },
            {
                "name": "seedance_transition",
                "description": (
                    "Generate an AI transition/bridge clip from the first source video "
                    "toward the next scene. Uses Seedance cloud generation rather than "
                    "ffmpeg hard concatenation. Requires source_video_url as public http(s)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Transition instruction"},
                        "source_video_url": {"type": "string", "description": "Public http(s) video URL to extend from"},
                        "next_scene_prompt": {"type": "string", "description": "Description of the target next scene"},
                        "model": {"type": "string", "default": "2.0"},
                        "duration": {"type": "integer", "default": 4},
                        "ratio": {"type": "string", "default": "16:9"},
                        "resolution": {"type": "string", "default": "720p"},
                    },
                    "required": ["prompt", "source_video_url"],
                },
            },
            {
                "name": "seedance_list",
                "description": (
                    "List recent Seedance video generation tasks. Returns JSON: "
                    "{ok, total, tasks: [{task_id, status, mode, prompt, "
                    "video_url, video_path, asset_ids, created_at}, ...]}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
        ], handler=self._handle_tool)

        api.spawn_task(self._async_init(), name="seedance-video:init")
        api.log("Seedance Video plugin loaded")

    async def _async_init(self) -> None:
        await self._tm.init()
        config = await self._tm.get_all_config()
        api_key = config.get("ark_api_key", "")
        if api_key:
            self._ark = ArkClient(api_key, base_url=config.get("ark_base_url") or None)
        self._start_polling()

    async def on_unload(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("seedance-video poll task drain error: %s", exc)
        # Cancel any in-flight chain workers so unload doesn't leak running
        # background coroutines into the next plugin load.
        for gid, info in list(self._active_chains.items()):
            task = info.get("task")
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(
                        "seedance-video chain %s drain error: %s", gid, exc,
                    )
        self._active_chains.clear()
        for fut in list(self._pending_create_requests.values()):
            if not fut.done():
                fut.cancel()
        self._pending_create_requests.clear()
        if self._ark is not None:
            try:
                await self._ark.close()
            except Exception as exc:
                logger.warning("seedance-video Ark client close error: %s", exc)
        try:
            await self._sysdeps.aclose()
        except Exception as exc:
            logger.warning("seedance-video system deps close error: %s", exc)
        try:
            await self._tm.close()
        except Exception as exc:
            logger.warning("seedance-video task manager close error: %s", exc)

    # ── Tool handler ──

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        """LLM-facing tool entry. Returns JSON so OrgRuntime's workbench
        hook can detect produced artifacts (local_paths / video_url /
        asset_ids) and register them as task attachments."""
        import json as _json

        if tool_name == "seedance_create":
            try:
                task = await self._create_task_internal(args)
                task = await self._maybe_wait_for_tool_task(task, args)
            except HTTPException as e:
                return _json.dumps(
                    {
                        "ok": False,
                        "error": e.detail if isinstance(e.detail, str) else str(e.detail),
                        "status_code": e.status_code,
                        "terminal": self._is_terminal_create_error(e),
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return _json.dumps(
                    {"ok": False, "error": str(e), "terminal": True}, ensure_ascii=False
                )
            return _json.dumps(self._task_to_tool_payload(task), ensure_ascii=False)

        if tool_name in ("seedance_edit", "seedance_extend", "seedance_transition"):
            try:
                mode = "edit" if tool_name == "seedance_edit" else "extend"
                task_args = self._build_video_url_create_args(args, mode=mode)
                task = await self._create_task_internal(task_args)
                task = await self._maybe_wait_for_tool_task(task, args)
                payload = self._task_to_tool_payload(task)
                if tool_name == "seedance_transition":
                    payload["transition_strategy"] = "seedance_extend"
                    payload["message"] = (
                        "已通过 Seedance 云端 extend 生成过渡/续写片段；"
                        "如需最终长片，可再用本地 concat 合成。"
                    )
                return _json.dumps(payload, ensure_ascii=False)
            except HTTPException as e:
                return _json.dumps(
                    {
                        "ok": False,
                        "error": e.detail if isinstance(e.detail, str) else str(e.detail),
                        "status_code": e.status_code,
                        "terminal": self._is_terminal_create_error(e),
                    },
                    ensure_ascii=False,
                )
            except Exception as e:
                return _json.dumps(
                    {"ok": False, "error": str(e), "terminal": True},
                    ensure_ascii=False,
                )

        if tool_name == "seedance_status":
            tid = args.get("task_id") or ""
            task = await self._tm.get_task(tid) if tid else None
            if not task:
                return _json.dumps(
                    {"ok": False, "task_id": tid, "error": "task not found"},
                    ensure_ascii=False,
                )
            # 视频生成本身就是分钟量级的工作（10s 视频常需 3~5 分钟），
            # 这里不再做"超过 N 秒就标 timeout"的硬性自动失败——那会把云端
            # 后期才完成的任务也误杀。改为只回传当前 DB 状态；上层若觉得卡死，
            # 应该使用 org_escalate / 用户手动 force_close_chain 处理。
            return _json.dumps(self._task_to_tool_payload(task), ensure_ascii=False)

        if tool_name == "seedance_list":
            tasks, total = await self._tm.list_tasks(limit=args.get("limit", 10))
            return _json.dumps(
                {
                    "ok": True,
                    "total": total,
                    "tasks": [
                        self._task_to_tool_payload(t, brief=True) for t in tasks
                    ],
                },
                ensure_ascii=False,
            )
        return _json.dumps(
            {"ok": False, "error": f"Unknown tool: {tool_name}"}, ensure_ascii=False
        )

    @staticmethod
    def _task_to_tool_payload(task: dict, *, brief: bool = False) -> dict:
        """Project a task record into the JSON shape expected by the LLM and
        by ``OrgRuntime._record_plugin_asset_output``. The runtime looks for
        ``local_paths`` / ``video_url`` / ``asset_ids`` to auto-register
        produced media as task attachments — keep these keys stable.
        """
        local_paths: list[str] = []
        vp = task.get("local_video_path")
        if vp:
            local_paths.append(vp)
        lf_local = task.get("last_frame_local_path")
        if lf_local:
            local_paths.append(lf_local)
        terminal_failures = {"failed", "timeout", "cancelled"}
        status_str = task.get("status") or ""
        base = {
            "ok": status_str not in terminal_failures,
            "task_id": task.get("id"),
            "status": status_str,
            "mode": task.get("mode"),
            "video_url": task.get("video_url") or "",
            "video_path": vp or "",
            "last_frame_url": task.get("last_frame_url") or "",
            "last_frame_path": lf_local or "",
            "local_paths": local_paths,
            "asset_ids": list(task.get("asset_ids") or []),
        }
        if status_str in terminal_failures:
            base["terminal"] = True
        if task.get("error_message"):
            base["error_message"] = task["error_message"]
        # 等待超时但云端还在跑 → 透传 wait_hint 让 LLM 知道"先轮询 status，
        # 不要重新 create"。即便 status=running 也会带回这个字段。
        if task.get("wait_hint"):
            base["wait_hint"] = task["wait_hint"]
        # 云端 succeeded 但本地下载未完成（asset_ids=[] / local_paths=[]）：
        # workbench 节点必须有 asset_ids 才能交付，给 LLM 一个明确的兜底建议，
        # 否则它会以为任务失败而循环重新生成。
        if (
            status_str == "succeeded"
            and base["video_url"]
            and not local_paths
            and not base["asset_ids"]
        ):
            base["download_warning"] = (
                "云端任务已成功（video_url 可用）但本地素材下载/发布失败。"
                "workbench 节点应直接使用 video_url 作为交付物（org_submit_deliverable "
                "时把 video_url 写进 deliverable 内容），**不要**再调用 seedance_create "
                "重新生成；如需重试下载请用 seedance_status 几秒后再查询，后台会补抓。"
            )
        if brief:
            base["prompt"] = (task.get("prompt") or "")[:200]
            base["created_at"] = task.get("created_at")
        return base

    async def _maybe_wait_for_tool_task(self, task: dict, args: dict) -> dict:
        """Wait for LLM/workbench tool calls to finish before returning.

        REST/UI callers still get an immediate running task from `/tasks`;
        this helper is only used by the LLM-facing tool handler.  Without it,
        org workbench nodes can create a Seedance job, return `running`, and
        then remain busy forever if the model does not keep polling.
        """
        if args.get("wait_for_completion") is False:
            return task
        task_id = task.get("id")
        if not task_id or task.get("status") not in ("pending", "running"):
            return task

        # 默认 30 分钟，足以覆盖 12 秒视频 + 1080p 高负载场景；同步等待退出后
        # **不会**改写 DB 里的状态——后台 ``_poll_running_tasks`` 仍会继续轮询
        # 云端任务，云端在 30 分钟之外完成时下载逻辑会自然把 video_url /
        # local_video_path / asset_ids 补齐。
        timeout_s = int(args.get("wait_timeout_s") or 1800)
        deadline = time.time() + max(60, timeout_s)
        while time.time() < deadline:
            await self._poll_running_tasks()
            latest = await self._tm.get_task(task_id)
            if latest:
                task = latest
                if latest.get("status") not in ("pending", "running"):
                    return latest
            await asyncio.sleep(10)

        # 等待超时——但任务在云端可能仍在生成。不再硬性把 DB 状态推成 ``timeout``，
        # 那会让后台轮询不再补抓云端最终结果（导致视频永久丢失）。这里只附带一个
        # 软性 ``wait_hint`` 字段告诉 LLM "已超过同步等待时长但任务仍可能在跑，
        # 请稍后用 seedance_status 查询，**不要**重新 seedance_create"，
        # ``_task_to_tool_payload`` 拿到 status=running 时 ok=true，从而抑制
        # LLM 的重试创建冲动（之前观察到的 producer 死循环主要诱因）。
        task = await self._tm.get_task(task_id) or task
        task = dict(task)
        wait_hint = (
            "同步等待已超过 {mins} 分钟，**任务仍在云端处理中**（DB 状态未变更）。"
            "请使用 seedance_status(task_id=\"{tid}\") 查询，**不要**再调用 "
            "seedance_create 重新生成；后台轮询会在云端完成时自动补齐 "
            "video_url / asset_ids。如需放弃，请直接通过 org_escalate 上报。"
        ).format(mins=max(1, int(timeout_s / 60)), tid=task_id)
        task["wait_hint"] = wait_hint
        return task

    @staticmethod
    def _is_terminal_create_error(exc: HTTPException) -> bool:
        """Return True when retrying the same create call cannot make progress."""
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        markers = (
            "API Key",
            "AuthenticationError",
            "首帧模式需要",
            "首尾帧模式需要",
            "多模态模式至少",
            "编辑模式需要",
            "延长模式需要",
            "公网 http(s) 视频 URL",
            "from_asset_ids",
            "Asset Bus",
            "模型",
            "不支持",
        )
        return exc.status_code in (400, 401, 403, 413) or any(m in detail for m in markers)

    @staticmethod
    def _build_video_url_create_args(args: dict, *, mode: str) -> dict:
        """Build a canonical seedance_create payload for edit/extend wrappers."""
        source_url = (
            args.get("source_video_url")
            or args.get("video_url")
            or args.get("url")
            or ""
        )
        if not isinstance(source_url, str) or not source_url.strip():
            label = "编辑" if mode == "edit" else "延长/过渡"
            raise HTTPException(status_code=400, detail=f"{label}模式缺少 source_video_url")
        source_url = source_url.strip()
        if not source_url.lower().startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail=(
                    "编辑/延长模式必须使用 Seedance 返回的云端 http(s) video_url，"
                    "不能直接上传本地视频或 base64。"
                ),
            )
        prompt = (args.get("prompt") or "").strip()
        if mode == "extend" and args.get("next_scene_prompt"):
            prompt = (
                f"{prompt}\n\n请自然过渡到下一场景：{args.get('next_scene_prompt')}"
                if prompt else f"请自然过渡到下一场景：{args.get('next_scene_prompt')}"
            )
        if not prompt:
            prompt = "保持原视频风格，进行自然续写。"
        return {
            **args,
            "prompt": prompt,
            "mode": mode,
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "video_url",
                    "video_url": {"url": source_url},
                    "role": "reference_video",
                },
            ],
        }

    # ── Long-video chain bookkeeping (Sprint 8 / V2) ──

    @staticmethod
    def _chain_signature(segments: list[dict]) -> str:
        """Stable fingerprint of a storyboard so we can detect duplicate
        submissions of the same chain across tab switches / reloads.

        We hash ``index|prompt|duration`` for each segment.  Two payloads
        with identical prompts in the same order produce the same hash —
        the user reported "同一段分镜出现两次 / 生成两次" exactly because
        nothing was de-duplicating these.
        """
        parts: list[str] = []
        for seg in segments:
            parts.append(
                f"{seg.get('index', 0)}|"
                f"{(seg.get('prompt') or '').strip()}|"
                f"{seg.get('duration', 0)}"
            )
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _request_signature(params: dict) -> str:
        """Stable fingerprint for single create/edit/extend requests."""
        relevant = {
            "prompt": (params.get("prompt") or "").strip(),
            "mode": params.get("mode", "t2v"),
            "model": params.get("model", "2.0"),
            "ratio": params.get("ratio", "16:9"),
            "duration": params.get("duration", 5),
            "resolution": params.get("resolution", "720p"),
            "from_asset_ids": list(params.get("from_asset_ids") or []),
            "content": params.get("content") or [],
        }
        raw = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _infer_duration_from_prompt(prompt: str, *, asset_count: int, current: Any) -> int:
        """Recover per-shot duration when an org prompt says 30s/3 shots.

        LLM tool calls often omit `duration`, which would fall back to the
        plugin's UI default of 5 seconds. In org video workflows the task text
        usually carries the real shot duration, so infer the safer value.
        """
        try:
            current_int = int(current)
        except (TypeError, ValueError):
            current_int = 5
        if current_int not in (0, 5):
            return current_int

        text = prompt or ""
        import re

        ranges = re.findall(r"(\d{1,3})\s*[-~—到至]\s*(\d{1,3})\s*秒", text)
        durations = [max(0, int(end) - int(start)) for start, end in ranges]
        durations = [d for d in durations if d > 0]
        if asset_count <= 1 and durations:
            return durations[0]

        each_match = re.search(r"(?:每(?:个|段|镜头).*?|单(?:个|段|镜头).*?)(\d{1,3})\s*秒", text)
        if each_match:
            return int(each_match.group(1))

        total_match = re.search(r"(?:总时长|合计|共|生成)?\s*(\d{1,3})\s*秒", text)
        shot_match = re.search(r"(\d{1,2})\s*个镜头", text)
        if total_match and shot_match:
            shots = max(1, int(shot_match.group(1)))
            return max(1, round(int(total_match.group(1)) / shots))

        return current_int or 5

    async def _find_recent_duplicate_task(self, request_signature: str) -> dict | None:
        if not request_signature:
            return None
        try:
            listed = await self._tm.list_tasks(limit=80)
        except Exception:
            logger.debug("duplicate lookup skipped: list_tasks failed", exc_info=True)
            return None
        if isinstance(listed, tuple) and len(listed) >= 1:
            tasks = listed[0]
        elif isinstance(listed, list):
            tasks = listed
        else:
            return None
        now = time.time()
        for task in tasks:
            params = task.get("params") or {}
            if params.get("request_signature") != request_signature:
                continue
            # Keep the duplicate window short so deliberate re-renders remain possible.
            created_at = float(task.get("created_at") or 0)
            if created_at and now - created_at > 30 * 60:
                continue
            if task.get("status") in ("pending", "running", "succeeded"):
                return task
        return None

    async def _run_chain_bg(
        self,
        group_id: str,
        body: LongVideoCreateBody,
    ) -> None:
        """Background worker for ``/long-video/generate`` (fire-and-forget).

        We can no longer rely on the request to remain open while the
        chain runs — chain generation can take several minutes per
        segment, which routinely exceeded HTTP timeouts and led to users
        re-clicking "开始生成" and producing the duplicate task rows the
        bug report cites.  Running detached, with progress queryable via
        ``GET /long-video/tasks/{group_id}``, is the durable fix.
        """
        try:
            if not self._ark:
                logger.warning("Chain %s aborted: API Key not configured", group_id)
                return
            chain = ChainGenerator(self._ark, self._tm)
            model = get_model(body.model)
            model_id = model.model_id if model else body.model
            await chain.generate_chain(
                segments=body.segments,
                model_id=model_id,
                ratio=body.ratio,
                resolution=body.resolution,
                mode=body.mode,
                chain_group=group_id,
            )
        except asyncio.CancelledError:
            logger.info("Chain %s cancelled", group_id)
            raise
        except Exception as exc:
            logger.exception("Chain %s crashed: %s", group_id, exc)
        finally:
            # Always pop on completion so the user can re-submit the same
            # storyboard later (e.g. after editing one segment).
            self._active_chains.pop(group_id, None)
            try:
                self._api.broadcast_ui_event(
                    "chain_update",
                    {"group_id": group_id, "status": "finished"},
                )
            except Exception as exc:
                logger.warning("chain_update broadcast failed: %s", exc)

    # ── Internal task creation ──

    async def _create_task_internal(self, params: dict) -> dict:
        params = dict(params)
        if not self._ark:
            raise HTTPException(
                status_code=400,
                detail="尚未配置 API Key — 请到「设置 → API Key」填写火山引擎 Ark 密钥",
            )

        client_request_id = (params.get("client_request_id") or "").strip()
        create_future: asyncio.Future | None = None
        create_future_owner = False
        if client_request_id:
            # Post-reload / late retry safety net: if the first request
            # already made it into SQLite, return that task instead of
            # spending money on a second Ark job.
            finder = getattr(self._tm, "get_task_by_client_request_id", None)
            if finder is not None:
                existing = await finder(client_request_id)
                if existing:
                    logger.info(
                        "create_task deduped by persisted client_request_id=%s task=%s",
                        client_request_id, existing.get("id"),
                    )
                    return existing

            # In-flight guard: two concurrent HTTP requests with the same
            # client id (bridge fallback, double-click, flaky browser retry)
            # must share the first Ark submission result.
            existing_future = self._pending_create_requests.get(client_request_id)
            if existing_future is not None:
                logger.info("create_task awaiting in-flight duplicate %s", client_request_id)
                return await existing_future
            create_future = asyncio.get_running_loop().create_future()
            create_future.add_done_callback(
                lambda fut: fut.exception() if fut.done() and not fut.cancelled() else None
            )
            self._pending_create_requests[client_request_id] = create_future
            create_future_owner = True

        try:
            model_info = get_model(params.get("model", "2.0"))
            if not model_info:
                raise HTTPException(
                    status_code=400,
                    detail=f"未知模型 {params.get('model')!r} — 请在创建页重新选择",
                )

            mode = params.get("mode", "t2v")
            if mode not in model_info.modes:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"模型 {model_info.name} 不支持 {mode} 模式，"
                        f"仅支持: {', '.join(model_info.modes)}"
                    ),
                )

            content = params.get("content") or [{"type": "text", "text": params.get("prompt", "")}]

            # 工作台编排：from_asset_ids 把上游工作台（例如通义生图）的产物
            # 通过 Asset Bus 注入到当前任务的 content[]，让 LLM 不必再手动
            # 上传图片到 Ark。展开角色取决于 mode：
            #   i2v        → first_frame (0), reference_image (1+)
            #   i2v_end    → first_frame (0), last_frame (1+)
            #   multimodal → first_frame (0), reference_image (1+)
            #   其他       → reference_image
            from_asset_ids = params.get("from_asset_ids") or []
            if from_asset_ids and isinstance(from_asset_ids, list):
                expanded = await self._expand_from_asset_ids(from_asset_ids, mode)
                if expanded:
                    if not isinstance(content, list):
                        content = []
                    content = list(content) + expanded
                elif mode in ("i2v", "i2v_end", "multimodal"):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "from_asset_ids 未能展开为可提交给 Ark 的图片素材。"
                            "请确认上游工作台返回的是有效 asset_ids，且 seedance-video "
                            "已获得 assets.consume 权限。"
                        ),
                    )

            # ── Mode validation (Sprint 8 / V1) ──
            # User-reported bug: "i2v / multimodal / edit / extend 都生不了任务".
            # Root cause was the UI never wired uploaded assets into the create
            # body, so backend always saw text-only content and Volcengine
            # silently mis-handled it.  These guards raise 400 with a Chinese
            # hint *before* spending money on the Ark call so the user sees a
            # clear "需要先上传 XX" message in the UI's error banner.
            if not isinstance(content, list) or not content:
                raise HTTPException(status_code=400, detail="content 不能为空")

            def _has(content_type: str) -> bool:
                return any(
                    isinstance(c, dict) and c.get("type") == content_type
                    for c in content
                )

            def _image_with_role(role: str) -> bool:
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "image_url":
                        continue
                    img = c.get("image_url") or {}
                    item_role = c.get("role")
                    nested_role = img.get("role") if isinstance(img, dict) else None
                    if item_role == role or nested_role == role:
                        return True
                return False

            if mode == "i2v" and not _has("image_url"):
                raise HTTPException(
                    status_code=400,
                    detail="首帧模式需要先上传首帧图片",
                )
            if mode == "i2v_end":
                # Either the UI sends two image_url with explicit first/last
                # roles (preferred) or two un-tagged image_urls — accept both
                # as long as we get at least 2 images.
                image_count = sum(
                    1 for c in content
                    if isinstance(c, dict) and c.get("type") == "image_url"
                )
                has_explicit_pair = (
                    _image_with_role("first_frame")
                    and _image_with_role("last_frame")
                )
                if not has_explicit_pair and image_count < 2:
                    raise HTTPException(
                        status_code=400,
                        detail="首尾帧模式需要分别上传首帧和尾帧两张图片",
                    )
            if mode == "multimodal":
                media_count = sum(
                    1 for c in content
                    if isinstance(c, dict) and c.get("type") in ("image_url", "video_url")
                )
                if media_count < 1:
                    raise HTTPException(
                        status_code=400,
                        detail="多模态模式至少上传 1 个参考素材（图片/视频/音频）",
                    )
            if mode in ("edit", "extend") and not _has("video_url"):
                label = "编辑" if mode == "edit" else "延长"
                raise HTTPException(
                    status_code=400,
                    detail=f"{label}模式需要先上传源视频",
                )

            def _normalize_content_roles(items: list[dict]) -> list[dict]:
                """Move media roles to Ark's expected top-level field.

                Older UI builds put ``role`` under ``image_url``. Ark ignores
                that and returns: "role must be specified for image contents".
                Video reference modes likewise require top-level
                ``role=reference_video``; older UI builds sent ``role=edit``.
                Normalize here so existing browser caches / tool calls do not
                create failing requests.
                """
                image_idx = 0
                normalized: list[dict] = []
                for item in items:
                    if not isinstance(item, dict) or item.get("type") not in ("image_url", "video_url"):
                        normalized.append(item)
                        continue
                    copied = dict(item)
                    media_key = "image_url" if copied.get("type") == "image_url" else "video_url"
                    media_payload = copied.get(media_key)
                    if isinstance(media_payload, dict):
                        media_payload = dict(media_payload)
                        nested_role = media_payload.pop("role", None)
                        copied[media_key] = media_payload
                    else:
                        nested_role = None

                    role = copied.get("role") or nested_role
                    if copied.get("type") == "video_url":
                        if role in (None, "", "edit", "extend", mode):
                            role = "reference_video"
                    elif not role:
                        if mode == "i2v":
                            role = "first_frame"
                        elif mode == "i2v_end":
                            role = "first_frame" if image_idx == 0 else "last_frame"
                        else:
                            role = "reference_image"
                    copied["role"] = role
                    if copied.get("type") == "image_url":
                        image_idx += 1
                    normalized.append(copied)
                return normalized

            content = _normalize_content_roles(content)
            params["content"] = content
            if from_asset_ids:
                params["from_asset_ids"] = list(from_asset_ids)

            inferred_duration = self._infer_duration_from_prompt(
                params.get("prompt", ""),
                asset_count=len(from_asset_ids) if isinstance(from_asset_ids, list) else 0,
                current=params.get("duration", 5),
            )
            params["duration"] = max(
                model_info.duration_range[0],
                min(model_info.duration_range[1], inferred_duration),
            )

            request_signature = self._request_signature(params)
            existing_duplicate = await self._find_recent_duplicate_task(request_signature)
            if existing_duplicate:
                logger.info(
                    "create_task deduped by request_signature=%s task=%s",
                    request_signature, existing_duplicate.get("id"),
                )
                return existing_duplicate
            params["request_signature"] = request_signature

            for item in content:
                if not isinstance(item, dict) or item.get("type") != "video_url":
                    continue
                if item.get("role") != "reference_video":
                    continue
                video_payload = item.get("video_url") or {}
                url = video_payload.get("url", "") if isinstance(video_payload, dict) else ""
                if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
                    label = "编辑/延长" if mode in ("edit", "extend") else "视频参考"
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{label}模式的视频参考必须使用公网 http(s) 视频 URL，"
                            "不能使用本地上传文件或 base64。请使用任务详情里的云端视频 URL，"
                            "或先把视频上传到 Ark 可访问的公网地址后再提交。"
                        ),
                    )

            config = await self._tm.get_all_config()
            service_tier = params.get("service_tier", config.get("service_tier_default", "default"))
            callback_url = params.get("callback_url") or config.get("callback_url") or None
            expires = params.get("execution_expires_after")
            if service_tier == "flex" and not expires:
                expires = 172800

            try:
                result = await self._ark.create_task(
                    model=model_info.model_id,
                    content=content,
                    ratio=params.get("ratio", "16:9"),
                    duration=params.get("duration", 5),
                    resolution=params.get("resolution", "720p"),
                    n=params.get("n", 1),
                    generate_audio=params.get("generate_audio", True),
                    seed=params.get("seed", -1),
                    watermark=params.get("watermark", False),
                    camera_fixed=params.get("camera_fixed", False),
                    draft=params.get("draft", False),
                    return_last_frame=params.get("return_last_frame", False),
                    tools=[{"type": "web_search"}] if params.get("web_search") else None,
                    service_tier=service_tier,
                    callback_url=callback_url,
                    execution_expires_after=expires,
                )
            except VendorError as e:
                logger.error("Ark API error: %s (kind=%s)", e, e.kind)
                raise HTTPException(status_code=502, detail=f"Ark API error: {e}")
            except Exception as e:
                logger.error("Ark API unexpected error: %s", e)
                raise HTTPException(status_code=502, detail=f"Ark API error: {e}")

            ark_task_id = result.get("id", "")
            task = await self._tm.create_task(
                ark_task_id=ark_task_id,
                status="running",
                prompt=params.get("prompt", ""),
                mode=params.get("mode", "t2v"),
                model=params.get("model", "2.0"),
                params=params,
                service_tier=service_tier,
                is_draft=params.get("draft", False),
                callback_url=callback_url,
            )
            if create_future_owner and create_future is not None and not create_future.done():
                create_future.set_result(task)
            return task
        except Exception as exc:
            if create_future_owner and create_future is not None and not create_future.done():
                create_future.set_exception(exc)
            raise
        finally:
            if create_future_owner and client_request_id:
                self._pending_create_requests.pop(client_request_id, None)

    # ── Polling ──

    def _start_polling(self) -> None:
        self._poll_task = self._api.spawn_task(
            self._poll_loop(), name="seedance-video:poll"
        )

    async def _poll_loop(self) -> None:
        while True:
            try:
                interval = int(await self._tm.get_config("poll_interval") or "15")
                await asyncio.sleep(max(interval, 5))
                await self._poll_running_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Poll error: %s", e)
                await asyncio.sleep(15)

    async def _poll_running_tasks(self) -> None:
        if not self._ark:
            return
        tasks = await self._tm.get_running_tasks()
        for task in tasks:
            if not task.get("ark_task_id"):
                continue
            try:
                result = await self._ark.get_task(task["ark_task_id"])
                status = result.get("status", "")
                if status == "succeeded":
                    video_url = ""
                    revised_prompt = ""
                    last_frame_url = ""

                    content = result.get("content", {})
                    if isinstance(content, dict):
                        video_url = content.get("video_url", "") or ""
                        last_frame_url = (
                            content.get("last_frame_url", "")
                            or content.get("image_url", "")
                            or ""
                        )
                        revised_prompt = content.get("revised_prompt", "") or ""

                    if not video_url:
                        output = result.get("output", {})
                        if isinstance(output, dict):
                            content_list = output.get("content", [])
                            if isinstance(content_list, list):
                                for item in content_list:
                                    if isinstance(item, dict) and item.get("type") == "video_url":
                                        video_url = item.get("video_url", {}).get("url", "")
                                    if isinstance(item, dict) and item.get("type") == "image_url":
                                        if not last_frame_url:
                                            last_frame_url = item.get("image_url", {}).get("url", "")
                            if not revised_prompt:
                                revised_prompt = output.get("revised_prompt", "")
                            if not last_frame_url:
                                last_frame_url = output.get("last_frame_url", "") or ""

                    updates: dict[str, Any] = {"status": "succeeded", "video_url": video_url}
                    if revised_prompt:
                        updates["revised_prompt"] = revised_prompt
                    if last_frame_url:
                        updates["last_frame_url"] = last_frame_url
                    await self._tm.update_task(task["id"], **updates)

                    # 工作台编排必须拿到本地路径 + asset_ids 才能让 OrgRuntime
                    # hook 把视频/末帧登记为任务附件，所以**强制**下载+publish
                    # 一次（不再受 auto_download 配置影响）。失败时降级为
                    # 仅 URL，由 hook 的远端下载兜底。
                    if video_url:
                        await self._download_and_publish_video(
                            task["id"], video_url, last_frame_url=last_frame_url,
                            prompt=task.get("prompt") or "",
                        )

                    self._broadcast_update(task["id"], "succeeded")

                elif status == "failed":
                    error = result.get("error", {})
                    error_msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
                    await self._tm.update_task(task["id"], status="failed", error_message=error_msg)
                    self._broadcast_update(task["id"], "failed")

            except Exception as e:
                logger.debug("Poll task %s error: %s", task["id"], e)

    async def _expand_from_asset_ids(
        self,
        asset_ids: list[str],
        mode: str,
    ) -> list[dict]:
        """Turn upstream workbench ``asset_ids`` into Ark content items.

        Each successful Asset Bus lookup is materialised as an
        ``{"type": "image_url", "image_url": {"url": ...}, "role": ...}``
        entry whose role is derived from the **0-indexed position** within
        ``asset_ids`` together with ``mode``.

        Unknown / unreadable asset_ids are skipped; callers that require
        media validate the resulting list and raise a structured 400.
        """
        if not asset_ids:
            return []
        expanded: list[dict] = []
        for idx, aid in enumerate(asset_ids):
            try:
                asset = await self._api.consume_asset(aid)
            except Exception as exc:
                logger.warning("seedance-video: consume_asset(%s) failed: %s", aid, exc)
                continue
            if not asset:
                continue
            source_path = asset.get("source_path") or ""
            preview_url = asset.get("preview_url") or ""
            url = ""
            if isinstance(source_path, str) and source_path:
                data_url = self._local_file_to_data_url(source_path)
                if data_url:
                    url = data_url
            if not url and isinstance(preview_url, str) and preview_url:
                url = preview_url
            if not url and isinstance(source_path, str):
                url = source_path
            if not isinstance(url, str) or not url:
                continue
            if mode == "i2v_end":
                role = "first_frame" if idx == 0 else "last_frame"
            elif mode in ("i2v", "multimodal"):
                role = "first_frame" if idx == 0 else "reference_image"
            else:
                role = "reference_image"
            expanded.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": role,
                }
            )
        return expanded

    @staticmethod
    def _local_file_to_data_url(path_value: str) -> str:
        """Convert a local image file to a data URI for Ark image inputs."""
        try:
            path = Path(path_value)
            if not path.is_file():
                return ""
            mime = mimetypes.guess_type(path.name)[0] or ""
            if not mime.startswith("image/"):
                return ""
            max_bytes = 50 * 1024 * 1024
            if path.stat().st_size > max_bytes:
                return ""
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{data}"
        except Exception:
            return ""

    async def _download_video(self, task_id: str, url: str) -> None:
        """Download video to local output directory."""
        try:
            import httpx
            config = await self._tm.get_all_config()
            output_dir = config.get("output_dir") or str(Path.home() / "seedance-output")
            subdir_mode = config.get("output_subdir_mode", "date")
            naming = config.get("output_naming_rule", "{date}_{prompt_prefix}")

            out_path = Path(output_dir)
            if subdir_mode == "date":
                import datetime
                out_path = out_path / datetime.date.today().isoformat()
            out_path.mkdir(parents=True, exist_ok=True)

            task = await self._tm.get_task(task_id)
            if not task:
                return

            prompt_prefix = (task.get("prompt", "")[:20] or "video").strip()
            safe_prefix = "".join(c if c.isalnum() or c in "-_ " else "_" for c in prompt_prefix)
            filename = naming.format(
                task_id=task_id,
                date=time.strftime("%Y%m%d"),
                prompt_prefix=safe_prefix,
                mode=task.get("mode", "t2v"),
                seq=task_id[:6],
            ) + ".mp4"

            filepath = out_path / filename
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                filepath.write_bytes(resp.content)

            await self._tm.update_task(task_id, local_video_path=str(filepath))
            logger.info("Downloaded video for task %s to %s", task_id, filepath)
        except Exception as e:
            logger.warning("Failed to download video for task %s: %s", task_id, e)

    async def _download_and_publish_video(
        self,
        task_id: str,
        video_url: str,
        *,
        last_frame_url: str = "",
        prompt: str = "",
    ) -> None:
        """Download the produced video (and optional last-frame image),
        persist them under the plugin data dir, then publish each to the
        Asset Bus so downstream workbenches can consume the asset_ids
        (e.g. as the first_frame for a follow-up Seedance edit). Errors
        are non-fatal — the LLM still sees ``video_url`` even when local
        materialisation fails."""
        if not video_url:
            return

        downloads_dir = self._api.get_data_dir() / "downloads" / task_id
        try:
            downloads_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(
                "seedance-video: failed to create download dir %s: %s",
                downloads_dir, exc,
            )
            return

        try:
            import httpx
        except ImportError:
            logger.warning("seedance-video: httpx unavailable, skip download")
            return

        async def _download_with_retries(
            client: "httpx.AsyncClient",
            url: str,
            target: Path,
            label: str,
            *,
            attempts: int = 3,
            backoff_base: float = 1.5,
        ) -> bool:
            """Download ``url`` to ``target`` with exponential backoff.

            Returns True on success, False after exhausting retries.  Common
            transient failures here are DNS hiccups (``[Errno 11001]
            getaddrinfo failed`` was the one observed in production) and
            short TLS resets — both of which usually clear within a few
            seconds.  Without retries one bad lookup leaves ``asset_ids=[]``
            forever and the LLM enters an infinite ``seedance_create`` loop.
            """
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    target.write_bytes(resp.content)
                    if attempt > 1:
                        logger.info(
                            "seedance-video: %s download succeeded on retry %d",
                            label, attempt,
                        )
                    return True
                except Exception as exc:
                    last_exc = exc
                    if attempt < attempts:
                        delay = backoff_base ** attempt
                        logger.warning(
                            "seedance-video: %s download attempt %d/%d failed: %s — "
                            "retry in %.1fs",
                            label, attempt, attempts, exc, delay,
                        )
                        try:
                            await asyncio.sleep(delay)
                        except asyncio.CancelledError:
                            raise
                    else:
                        logger.warning(
                            "seedance-video: %s download exhausted %d attempts: %s",
                            label, attempts, exc,
                        )
            _ = last_exc  # 留作 logger 上下文（已在循环中输出）。
            return False

        video_path: Path | None = None
        last_frame_path: Path | None = None
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as http:
                video_target = downloads_dir / f"{task_id}.mp4"
                if await _download_with_retries(http, video_url, video_target, "video"):
                    video_path = video_target

                if last_frame_url:
                    ext = ".png"
                    low = last_frame_url.lower()
                    if ".jpg" in low or ".jpeg" in low:
                        ext = ".jpg"
                    elif ".webp" in low:
                        ext = ".webp"
                    lf_target = downloads_dir / f"{task_id}_last_frame{ext}"
                    if await _download_with_retries(
                        http, last_frame_url, lf_target, "last-frame",
                    ):
                        last_frame_path = lf_target
        except Exception as exc:
            logger.warning("seedance-video: download session error: %s", exc)

        asset_ids: list[str] = []
        # 视频资源：优先发布带本地文件的 asset；本地下载失败时回退为 URL-only
        # asset（仅 preview_url），让下游 workbench 仍能拿到 ``asset_ids`` 进行
        # 后续处理（剪映 / 字幕 / 长视频拼接），避免 LLM 因 asset_ids=[] 误判
        # 失败而循环重新生成（线上 wb-seedance-video DNS 失败死循环的直接成因）。
        try:
            aid = await self._api.publish_asset(
                asset_kind="video",
                source_path=str(video_path) if video_path is not None else None,
                preview_url=video_url,
                metadata={
                    "task_id": task_id,
                    "prompt": (prompt or "")[:500],
                    "origin": "seedance-video",
                    "role": "video",
                    "url_only": video_path is None,
                },
                shared_with=["*"],
                ttl_seconds=86400,
            )
            if aid:
                asset_ids.append(aid)
        except Exception as exc:
            logger.warning("seedance-video: publish_asset(video) failed: %s", exc)
        if last_frame_url:
            # 末帧同样支持 URL-only 回退（即便本地下载失败也保留可访问 URL）。
            try:
                aid = await self._api.publish_asset(
                    asset_kind="image",
                    source_path=(
                        str(last_frame_path) if last_frame_path is not None else None
                    ),
                    preview_url=last_frame_url,
                    metadata={
                        "task_id": task_id,
                        "prompt": (prompt or "")[:500],
                        "origin": "seedance-video",
                        "role": "last_frame",
                        "url_only": last_frame_path is None,
                    },
                    shared_with=["*"],
                    ttl_seconds=86400,
                )
                if aid:
                    asset_ids.append(aid)
            except Exception as exc:
                logger.warning(
                    "seedance-video: publish_asset(last_frame) failed: %s", exc,
                )

        updates: dict[str, Any] = {"asset_ids": asset_ids}
        if video_path is not None:
            updates["local_video_path"] = str(video_path)
        if last_frame_path is not None:
            updates["last_frame_local_path"] = str(last_frame_path)
        try:
            await self._tm.update_task(task_id, **updates)
        except Exception as exc:
            logger.warning(
                "seedance-video: persist asset metadata failed for %s: %s",
                task_id, exc,
            )
        logger.info(
            "seedance-video: task %s materialised → video=%s(local) last_frame=%s(local) "
            "assets=%d (url-only fallback when local=False)",
            task_id,
            bool(video_path),
            bool(last_frame_path),
            len(asset_ids),
        )

    def _broadcast_update(self, task_id: str, status: str) -> None:
        try:
            self._api.broadcast_ui_event(
                "task_update", {"task_id": task_id, "status": status}
            )
        except Exception as exc:
            # Don't let UI broadcast failures bring down the polling loop, but
            # log them so an operator can spot a stuck WebSocket / event bus.
            logger.warning("broadcast_ui_event failed for %s: %s", task_id, exc)

    # ── Route registration ──

    def _register_routes(self, router: APIRouter) -> None:

        # Sprint 7 / C1 (issue #479) — register a safe GET /uploads/{rel_path:path}
        # so the UI can preview uploaded reference images via
        # <img src="/api/plugins/seedance-video/uploads/<file>"> after upload.
        # base_dir is plugin-owned data so the URL stays stable even if the
        # user later changes assets_dir in /settings.
        add_upload_preview_route(
            router,
            base_dir=self._api.get_data_dir() / "uploads",
        )

        # --- Tasks CRUD ---

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict:
            task = await self._create_task_internal(body.model_dump())
            return {"ok": True, "task": task}

        @router.post("/tasks/edit")
        async def create_edit_task(body: VideoUrlTaskBody) -> dict:
            params = self._build_video_url_create_args(body.model_dump(), mode="edit")
            task = await self._create_task_internal(params)
            return {"ok": True, "task": task}

        @router.post("/tasks/extend")
        async def create_extend_task(body: VideoUrlTaskBody) -> dict:
            params = self._build_video_url_create_args(body.model_dump(), mode="extend")
            task = await self._create_task_internal(params)
            return {"ok": True, "task": task}

        @router.post("/tasks/transition")
        async def create_transition_task(body: VideoUrlTaskBody) -> dict:
            params = self._build_video_url_create_args(body.model_dump(), mode="extend")
            task = await self._create_task_internal(params)
            return {
                "ok": True,
                "task": task,
                "transition_strategy": "seedance_extend",
                "message": "已通过 Seedance 云端 extend 创建过渡/续写片段。",
            }

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            is_draft: bool | None = None,
            service_tier: str | None = None,
            offset: int = 0,
            limit: int = 20,
        ) -> dict:
            tasks, total = await self._tm.list_tasks(
                status=status, is_draft=is_draft, service_tier=service_tier,
                offset=offset, limit=limit,
            )
            return {"ok": True, "tasks": tasks, "total": total}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            return {"ok": True, "task": task}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict:
            await self._tm.delete_task(task_id)
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            new_task = await self._create_task_internal(task.get("params", {}))
            return {"ok": True, "task": new_task}

        # --- Draft mode ---

        @router.post("/tasks/draft")
        async def create_draft(body: CreateTaskBody) -> dict:
            params = body.model_dump()
            params["draft"] = True
            task = await self._create_task_internal(params)
            return {"ok": True, "task": task}

        @router.post("/tasks/draft/{draft_task_id}/confirm")
        async def confirm_draft(draft_task_id: str, body: DraftConfirmBody) -> dict:
            draft = await self._tm.get_task(draft_task_id)
            if not draft:
                raise HTTPException(status_code=404, detail="Draft task not found")
            if draft["status"] != "succeeded":
                raise HTTPException(status_code=400, detail="Draft not yet completed")
            if not self._ark:
                raise HTTPException(status_code=400, detail="API Key not configured")

            model_info = get_model(draft["model"])
            if not model_info:
                raise HTTPException(status_code=400, detail="Unknown model")

            content = [{"type": "draft_task", "draft_task": {"id": draft["ark_task_id"]}}]
            result = await self._ark.create_task(
                model=model_info.model_id,
                content=content,
                resolution=body.resolution,
                watermark=body.watermark,
                return_last_frame=body.return_last_frame,
                ratio=draft["params"].get("ratio", "16:9"),
                duration=draft["params"].get("duration", 5),
            )
            task = await self._tm.create_task(
                ark_task_id=result.get("id", ""),
                status="running",
                prompt=draft["prompt"],
                mode=draft["mode"],
                model=draft["model"],
                params={**draft["params"], "draft_parent_id": draft_task_id},
                draft_parent_id=draft_task_id,
            )
            return {"ok": True, "task": task}

        # --- File operations ---

        @router.post("/upload")
        async def upload_file(file: UploadFile = File(...)) -> dict:
            # Sprint 7 / C1 — uploads now land in plugin data dir so the
            # preview GET route above can serve them back without exposing
            # the user's home directory.  Old files in legacy assets_dir
            # remain accessible by absolute path (asset table stores it).
            #
            # Sprint 8 / V1 (issue: i2v/edit/extend/multimodal modes never
            # made it to Ark) — the ONLY way the UI can pass an uploaded
            # asset to the Ark API today is via a base64 data URI in
            # content[].image_url.url / video_url.url, because Volcengine
            # cannot reach our local /api/plugins/.../uploads/<x>.  So we
            # MUST keep the base64 attached to the response for files up
            # to MAX_UPLOAD_BYTES (50 MB).  Anything larger is rejected
            # outright with a friendly hint instead of silently dropping
            # the base64 (which used to surface as "i2v doesn't generate"
            # because the FE thought the upload succeeded but had no
            # payload to send).
            MAX_UPLOAD_BYTES = 50 * 1024 * 1024
            IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif"}
            VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
            AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

            content = await file.read()
            size_bytes = len(content)
            ext = Path(file.filename or "file").suffix.lower()

            if size_bytes > MAX_UPLOAD_BYTES:
                size_mb = round(size_bytes / 1024 / 1024, 1)
                # Don't write to disk, don't insert into asset table —
                # an oversize file is a hard failure the UI surfaces as a
                # red badge under the upload zone.
                return {
                    "ok": False,
                    "error": "file_too_large",
                    "size_mb": size_mb,
                    "max_mb": 50,
                    "message": (
                        f"文件 {size_mb} MB 超过 50 MB 上限 — "
                        f"火山 Ark API 通过 base64 接收上传，过大会被拒。"
                        f"请先压缩或裁剪后再试。"
                    ),
                }

            if ext in IMAGE_EXTS:
                subdir = "images"
                atype = "image"
            elif ext in VIDEO_EXTS:
                subdir = "videos"
                atype = "video"
            elif ext in AUDIO_EXTS:
                subdir = "audios"
                atype = "audio"
            else:
                # Reject unknown extensions so the UI can show a clear hint
                # ("we only accept these formats") instead of letting the
                # user upload junk that Ark would later reject anyway.
                allowed = sorted(IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS)
                return {
                    "ok": False,
                    "error": "unsupported_type",
                    "ext": ext or "(none)",
                    "message": (
                        f"不支持的文件类型 {ext or '(无扩展名)'} — "
                        f"仅支持图片（jpg/png/webp/gif…）、视频（mp4/mov/webm…）、"
                        f"音频（wav/mp3/m4a…）"
                    ),
                    "allowed": allowed,
                }

            uploads_dir = self._api.get_data_dir() / "uploads"
            dest_dir = uploads_dir / subdir
            dest_dir.mkdir(parents=True, exist_ok=True)

            import uuid as _uuid
            filename = f"{_uuid.uuid4().hex[:8]}_{file.filename or 'file'}"
            filepath = dest_dir / filename
            filepath.write_bytes(content)
            rel_path = f"{subdir}/{filename}"

            b64 = base64.b64encode(content).decode("ascii")
            mime = file.content_type or {
                "image": "image/jpeg",
                "video": "video/mp4",
                "audio": "audio/mpeg",
            }.get(atype, "application/octet-stream")
            data_uri = f"data:{mime};base64,{b64}"

            asset = await self._tm.create_asset(
                type=atype,
                file_path=str(filepath),
                original_name=file.filename,
                size_bytes=size_bytes,
            )
            return {
                "ok": True,
                "asset": asset,
                "kind": atype,
                "size_bytes": size_bytes,
                "url": build_preview_url("seedance-video", rel_path),
                # base64 is required by Ark because Volcengine cannot reach
                # the local preview URL — never strip it within the cap.
                "base64": data_uri,
            }

        @router.get("/videos/{task_id}")
        async def proxy_video(task_id: str, download: int = 0):
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            local_path = task.get("local_video_path")
            video_url = task.get("video_url")
            source = local_path if (local_path and Path(local_path).is_file()) else video_url
            if not source:
                raise HTTPException(status_code=404, detail="No video available")

            prompt_prefix = (task.get("prompt", "") or "video")[:30].strip() or "video"
            safe_prefix = "".join(
                c if c.isalnum() or c in "-_ " else "_" for c in prompt_prefix
            ).strip(" .") or "video"
            fname = f"seedance_{safe_prefix}.mp4"

            return self._api.create_file_response(
                source,
                filename=fname,
                media_type="video/mp4",
                as_download=bool(download),
            )

        @router.get("/videos/{task_id}/download")
        async def download_video(task_id: str) -> dict:
            task = await self._tm.get_task(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            video_url = task.get("video_url")
            if not video_url:
                raise HTTPException(status_code=404, detail="No video available")
            await self._download_video(task_id, video_url)
            updated = await self._tm.get_task(task_id)
            return {"ok": True, "task": updated}

        # --- Config (use /settings to avoid collision with generic /config in routes/plugins.py) ---

        @router.get("/settings")
        async def get_settings() -> dict:
            cfg = await self._tm.get_all_config()
            cfg.setdefault("ark_api_key", "")
            cfg.setdefault("ark_base_url", "")
            return {"ok": True, "config": cfg}

        @router.put("/settings")
        async def update_settings(body: ConfigUpdateBody) -> dict:
            # ── Pre-flight validation ────────────────────────────────────
            # Trim every value so a key like "  sk-xxx  " (a common
            # copy-paste mishap) does not silently get stored with the
            # surrounding whitespace and then make Ark calls fail with
            # an opaque "invalid api key" later.
            cleaned: dict[str, str] = {k: (v or "").strip() for k, v in body.updates.items()}
            if "ark_base_url" in cleaned:
                cleaned["ark_base_url"] = _normalize_base_url(
                    cleaned["ark_base_url"], field="ARK Base URL",
                )

            if "ark_api_key" in cleaned and not cleaned["ark_api_key"]:
                raise HTTPException(
                    status_code=400,
                    detail="ARK API Key 不能为空白 — 请粘贴有效的密钥（前往 console.volcengine.com/ark 获取）",
                )

            await self._tm.set_configs(cleaned)

            # ── Read-back verify: catch silent storage failures early ────
            # If the DB write succeeded but the value isn't readable
            # afterwards (corrupt sqlite, race, etc.), tell the UI
            # straight away instead of letting it pretend to succeed.
            saved = await self._tm.get_all_config()
            for k, expected in cleaned.items():
                if saved.get(k, "") != expected:
                    logger.error(
                        "settings.update mismatch key=%s expected_len=%d got_len=%d",
                        k, len(expected), len(saved.get(k, "") or ""),
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"保存失败 — 配置项 {k} 写入后回读不一致，请检查插件数据目录权限",
                    )

            if "ark_api_key" in cleaned and cleaned["ark_api_key"]:
                key = cleaned["ark_api_key"]
                # Log only length + redacted prefix so secrets do not
                # land in plaintext logs but operators can verify "yes,
                # the key the user thinks they saved is actually saved".
                logger.info(
                    "settings.update ark_api_key saved (len=%d, prefix=%s***)",
                    len(key), key[:4],
                )

            if "ark_api_key" in cleaned or "ark_base_url" in cleaned:
                key = saved.get("ark_api_key", "")
                base_url = saved.get("ark_base_url", "") or None
                if self._ark:
                    if key:
                        self._ark.update_api_key(key)
                        self._ark.update_base_url(base_url)
                    else:
                        await self._ark.close()
                        self._ark = None
                elif key:
                    self._ark = ArkClient(key, base_url=base_url)

            return {"ok": True, "config": saved}

        @router.get("/models")
        async def list_models() -> dict:
            return {
                "ok": True,
                "models": [model_to_dict(m) for m in SEEDANCE_MODELS],
            }

        @router.get("/models/{model_id}/capabilities")
        async def model_capabilities(model_id: str) -> dict:
            m = get_model(model_id)
            if not m:
                raise HTTPException(status_code=404, detail="Model not found")
            return {"ok": True, "model": model_to_dict(m)}

        @router.get("/resolution-map")
        async def resolution_map() -> dict:
            return {"ok": True, "map": RESOLUTION_PIXEL_MAP}

        # --- Prompt ---

        @router.get("/prompt-guide")
        async def get_prompt_guide() -> dict:
            return {
                "ok": True,
                "cameras": CAMERA_KEYWORDS,
                "atmosphere": ATMOSPHERE_KEYWORDS,
                "formulas": MODE_FORMULAS,
            }

        @router.get("/prompt-templates")
        async def get_prompt_templates() -> dict:
            return {"ok": True, "templates": PROMPT_TEMPLATES}

        @router.get("/prompt-formulas")
        async def get_prompt_formulas(mode: str = "t2v") -> dict:
            formula = MODE_FORMULAS.get(mode, MODE_FORMULAS["t2v"])
            return {"ok": True, "mode": mode, "formula": formula}

        @router.post("/prompt-optimize")
        async def optimize_prompt_endpoint(body: PromptOptimizeBody) -> dict:
            # Distinguish "permission not granted" (fixable in-app via the
            # /permissions/grant button) from "host has no brain configured"
            # (needs the user to set up an LLM in main settings). Both look
            # identical via get_brain()==None but have very different fixes.
            if not self._api.has_permission("brain.access"):
                return {
                    "ok": False,
                    "error": "missing_permission",
                    "permission": "brain.access",
                    "message": (
                        "AI 优化未授权：插件缺少 brain.access 权限。"
                        "请到「设置 → 系统组件 → 权限」点「一键授予」，"
                        "或到「设置中心 → 插件管理 → 即梦工作室 → 权限」勾选保存。"
                    ),
                }
            brain = self._api.get_brain()
            if not brain:
                return {
                    "ok": False,
                    "error": "brain_unavailable",
                    "message": "LLM 不可用：主进程未注入 brain（请确认 OpenAkita 已正常配置 LLM）。",
                }
            try:
                result = await optimize_prompt(
                    brain=brain,
                    user_prompt=body.prompt,
                    mode=body.mode,
                    duration=body.duration,
                    ratio=body.ratio,
                    asset_summary=body.asset_summary,
                    level=body.level,
                )
                return {"ok": True, "result": result}
            except PromptOptimizeError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:
                logger.error("Prompt optimize endpoint error: %s", e)
                return {"ok": False, "error": f"优化失败: {e}"}

        # --- Assets ---

        @router.get("/assets")
        async def list_assets(
            type: str | None = None,
            offset: int = 0,
            limit: int = 50,
        ) -> dict:
            assets, total = await self._tm.list_assets(
                asset_type=type, offset=offset, limit=limit
            )
            uploads_dir = (self._api.get_data_dir() / "uploads").resolve()
            for asset in assets:
                file_path = Path(asset.get("file_path") or "")
                try:
                    rel_path = file_path.resolve().relative_to(uploads_dir)
                    asset["preview_url"] = build_preview_url("seedance-video", rel_path)
                except (OSError, ValueError):
                    # Legacy rows may point outside the plugin upload dir.
                    # Keep the row visible, but don't expose arbitrary local
                    # paths to the browser.
                    asset["preview_url"] = ""
            return {"ok": True, "assets": assets, "total": total}

        @router.get("/assets/{asset_id}/payload")
        async def get_asset_payload(asset_id: str) -> dict:
            asset = await self._tm.get_asset(asset_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Asset not found")

            uploads_dir = (self._api.get_data_dir() / "uploads").resolve()
            file_path = Path(asset.get("file_path") or "")
            try:
                resolved = file_path.resolve()
                rel_path = resolved.relative_to(uploads_dir)
            except (OSError, ValueError) as e:
                raise HTTPException(status_code=403, detail="Asset is not reusable") from e

            if not resolved.is_file():
                raise HTTPException(status_code=404, detail="Asset file not found")

            size_bytes = resolved.stat().st_size
            max_bytes = 50 * 1024 * 1024
            if size_bytes > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="素材文件超过 50 MB，无法作为 base64 发送给 Ark",
                )

            mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            b64 = base64.b64encode(resolved.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "asset": asset,
                "kind": asset.get("type") or "file",
                "original_name": asset.get("original_name") or resolved.name,
                "size_bytes": size_bytes,
                "preview_url": build_preview_url("seedance-video", rel_path),
                "base64": f"data:{mime};base64,{b64}",
            }

        @router.delete("/assets/{asset_id}")
        async def delete_asset(asset_id: str) -> dict:
            asset = await self._tm.get_asset(asset_id)
            if asset:
                fpath = Path(asset.get("file_path", ""))
                if fpath.is_file():
                    fpath.unlink(missing_ok=True)
            await self._tm.delete_asset(asset_id)
            return {"ok": True}

        # --- Webhook callback ---

        @router.post("/webhook/callback")
        async def webhook_callback(body: dict) -> dict:
            task_id = body.get("id", "")
            status = body.get("status", "")
            if task_id and status:
                tasks, _ = await self._tm.list_tasks()
                for t in tasks:
                    if t.get("ark_task_id") == task_id:
                        if status == "succeeded":
                            await self._poll_running_tasks()
                        elif status == "failed":
                            await self._tm.update_task(
                                t["id"], status="failed",
                                error_message=body.get("error", {}).get("message", ""),
                            )
                        self._broadcast_update(t["id"], status)
                        break
            return {"ok": True}

        # --- Storage management ---

        @router.get("/storage/stats")
        async def storage_stats() -> dict:
            # Sprint 7 / C4 — switched to SDK collect_storage_stats so the
            # walk runs off-loop and is hard-capped at max_files (avoids UI
            # stalls when users accumulate thousands of generated videos).
            config = await self._tm.get_all_config()
            stats: dict[str, dict] = {}
            truncated_any = False
            for key, default in [
                ("output_dir", str(Path.home() / "seedance-output")),
                ("assets_dir", str(Path.home() / "seedance-assets")),
                ("cache_dir", str(self._api.get_data_dir() / "cache")),
                ("uploads", str(self._api.get_data_dir() / "uploads")),
            ]:
                d = Path(config.get(key) or default)
                report = await collect_storage_stats(
                    d, max_files=20000, sample_paths=0, skip_hidden=True,
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
        async def storage_cleanup(dir_type: str = "cache") -> dict:
            config = await self._tm.get_all_config()
            if dir_type == "cache":
                d = Path(config.get("cache_dir") or str(self._api.get_data_dir() / "cache"))
            elif dir_type == "assets":
                d = Path(config.get("assets_dir") or str(Path.home() / "seedance-assets"))
            else:
                raise HTTPException(status_code=400, detail="Invalid dir_type")

            removed = 0
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        removed += 1
            return {"ok": True, "removed": removed}

        @router.post("/storage/open-folder")
        async def open_folder(body: dict) -> dict:
            # Resolve target path:
            #   1) explicit `path` (after ~ expansion), OR
            #   2) `key` ∈ {output_dir, assets_dir, cache_dir, uploads}
            #      → user config value, else built-in default (mirrors
            #      /storage/stats so "Open" works even before the user
            #      customizes anything).
            raw_path = (body.get("path") or "").strip()
            key = (body.get("key") or "").strip()

            if not raw_path and not key:
                raise HTTPException(status_code=400, detail="Missing path or key")

            if raw_path:
                target = Path(raw_path).expanduser()
            else:
                defaults = {
                    "output_dir": Path.home() / "seedance-output",
                    "assets_dir": Path.home() / "seedance-assets",
                    "cache_dir": self._api.get_data_dir() / "cache",
                    "uploads": self._api.get_data_dir() / "uploads",
                }
                if key not in defaults:
                    raise HTTPException(status_code=400, detail=f"Unknown key: {key}")
                config = await self._tm.get_all_config()
                cfg_val = (config.get(key) or "").strip()
                target = (Path(cfg_val).expanduser() if cfg_val else defaults[key])

            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Cannot create folder: {exc}",
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
                    detail=f"Cannot open folder: {exc}",
                ) from exc
            return {"ok": True, "path": str(target)}

        # --- In-plugin folder picker (works regardless of Tauri/bridge) ---
        # Backs the FolderPickerModal in the Settings page, so users can
        # navigate the local filesystem without depending on the host's
        # native dialog (which has been unreliable across Tauri versions
        # and capability config).

        @router.get("/storage/list-dir")
        async def list_dir(path: str = "") -> dict:
            import sys
            raw = (path or "").strip()
            # Empty path → return anchor list (Home, common subfolders,
            # and on Windows every available drive letter). The UI can
            # render this as a "starting point" picker.
            if not raw:
                anchors: list[dict] = []
                home = Path.home()
                anchors.append({
                    "name": "Home", "path": str(home), "is_dir": True, "kind": "home",
                })
                for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Movies"):
                    p = home / sub
                    if p.is_dir():
                        anchors.append({
                            "name": sub, "path": str(p), "is_dir": True, "kind": "shortcut",
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
                        "name": "/", "path": "/", "is_dir": True, "kind": "drive",
                    })
                return {
                    "ok": True, "path": "", "parent": None,
                    "items": anchors, "is_anchor": True,
                }

            try:
                target = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            items: list[dict] = []
            try:
                for entry in target.iterdir():
                    name = entry.name
                    # Skip hidden entries (Unix dotfiles, Windows hidden via
                    # name-prefix heuristic). Permission errors on individual
                    # entries are swallowed so one bad child does not blank
                    # the whole listing.
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            items.append({"name": name, "path": str(entry), "is_dir": True})
                    except (PermissionError, OSError):
                        continue
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            items.sort(key=lambda it: it["name"].lower())
            parent_path = str(target.parent) if target.parent != target else None
            return {
                "ok": True, "path": str(target), "parent": parent_path,
                "items": items, "is_anchor": False,
            }

        @router.post("/storage/mkdir")
        async def make_dir(body: dict) -> dict:
            parent = (body.get("parent") or "").strip()
            name = (body.get("name") or "").strip()
            if not parent or not name:
                raise HTTPException(status_code=400, detail="Missing parent or name")
            # Reject anything that could escape the parent dir.
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

        # --- Long video / storyboard ---

        @router.get("/long-video/ffmpeg-check")
        async def check_ffmpeg() -> dict:
            # Route through SystemDepsManager so this endpoint and the
            # Settings page agree on detection state. Falls back to the
            # legacy shutil.which check if for any reason the manager is
            # not yet initialised (defensive — should not happen in prod).
            try:
                snap = self._sysdeps.detect("ffmpeg")
                return {"ok": True, "available": bool(snap.get("found"))}
            except Exception:
                return {"ok": True, "available": ffmpeg_available()}

        # --- System components (in-plugin FFmpeg installer) ---

        @router.get("/system/components")
        async def system_components() -> dict:
            # Snapshot of every system dep this plugin manages (currently
            # only ffmpeg) — drives the Settings > 系统组件 panel in the UI.
            return {"ok": True, "items": self._sysdeps.list_components()}

        @router.post("/system/{dep_id}/install")
        async def system_install(dep_id: str, body: SystemInstallBody) -> dict:
            try:
                result = await self._sysdeps.start_install(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        @router.post("/system/{dep_id}/uninstall")
        async def system_uninstall(dep_id: str, body: SystemUninstallBody) -> dict:
            try:
                result = await self._sysdeps.start_uninstall(
                    dep_id, method_index=body.method_index,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not result.get("ok") and result.get("error") == "requires_sudo":
                raise HTTPException(status_code=422, detail=result)
            return result

        @router.get("/system/{dep_id}/status")
        async def system_status(dep_id: str) -> dict:
            try:
                return self._sysdeps.status(dep_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        # --- Self-service permission check ---
        #
        # Surfaces the gap between manifest.permissions (what the plugin
        # NEEDS) and the runtime granted set (what the host actually gave
        # us). The frontend uses this to render an in-app "Grant" banner
        # so first-time users are not silently broken because they never
        # opened the host's plugin-manager permission dialog.

        @router.get("/permissions/check")
        async def permissions_check() -> dict:
            # Hard-coded list of permissions this plugin's user-facing
            # features actually exercise. Keep in sync with plugin.json.
            required = [
                ("brain.access",   "AI 优化提示词 / 故事板分镜（需要主进程 LLM）"),
                ("routes.register", "插件 HTTP 接口（前端调用）"),
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

        @router.post("/long-video/storyboard")
        async def decompose_storyboard_ep(body: StoryboardDecomposeBody) -> dict:
            brain = self._api.get_brain()
            if not brain:
                return {"ok": False, "error": "LLM not available"}
            result = await decompose_storyboard(
                brain=brain,
                story=body.story,
                total_duration=body.total_duration,
                segment_duration=body.segment_duration,
                ratio=body.ratio,
                style=body.style,
            )
            if "error" in result:
                return {"ok": False, "error": result["error"], "raw": result.get("raw", "")}
            return {"ok": True, "storyboard": result}

        @router.post("/long-video/generate")
        async def generate_long_video(body: LongVideoCreateBody) -> dict:
            """Fire-and-forget chain submission.

            Returns immediately with a ``group_id``; the UI then polls
            ``GET /long-video/tasks/{group_id}`` for progress.  This
            replaces the old synchronous behaviour where the HTTP call
            blocked for minutes and routinely timed out — leading to
            users retrying and producing duplicate DB rows for the same
            storyboard segment (the bug report's image 1).
            """
            if not self._ark:
                raise HTTPException(
                    status_code=400,
                    detail="尚未配置 API Key — 请到「设置 → API Key」填写火山引擎 Ark 密钥",
                )
            if not body.segments:
                raise HTTPException(
                    status_code=400,
                    detail="分镜列表为空 — 请先在编辑页确认至少 1 段分镜",
                )

            signature = self._chain_signature(body.segments)
            for gid, info in self._active_chains.items():
                if info.get("signature") == signature:
                    return {
                        "ok": False,
                        "error": "chain_in_progress",
                        "group_id": gid,
                        "message": (
                            "相同分镜的生成任务正在进行中 — "
                            "请等待完成或在「任务列表」查看进度，请勿重复提交。"
                        ),
                        "started_at": info.get("started_at"),
                        "segments_total": info.get("segments_total"),
                    }

            group_id = uuid.uuid4().hex[:12]
            chain_task = self._api.spawn_task(
                self._run_chain_bg(group_id, body),
                name=f"seedance-video:chain:{group_id}",
            )
            self._active_chains[group_id] = {
                "signature": signature,
                "started_at": time.time(),
                "segments_total": len(body.segments),
                "mode": body.mode,
                "model": body.model,
                "task": chain_task,
            }
            return {
                "ok": True,
                "group_id": group_id,
                "status": "started",
                "segments_total": len(body.segments),
                "message": (
                    f"已开始生成 {len(body.segments)} 段视频，"
                    f"前端将自动轮询进度，可安全切换 Tab 或刷新页面。"
                ),
            }

        @router.get("/long-video/active-chains")
        async def list_active_chains() -> dict:
            """Snapshot of every running chain.

            Used by the StoryboardTab on mount to recover an in-progress
            run after a page refresh / tab switch (the localStorage
            ``chainGroupId`` is cross-checked against this list to drop
            stale IDs).
            """
            now = time.time()
            chains = []
            for gid, info in self._active_chains.items():
                task = info.get("task")
                done = isinstance(task, asyncio.Task) and task.done()
                chains.append({
                    "group_id": gid,
                    "started_at": info.get("started_at"),
                    "elapsed_sec": round(now - (info.get("started_at") or now), 1),
                    "segments_total": info.get("segments_total"),
                    "mode": info.get("mode"),
                    "model": info.get("model"),
                    "done": done,
                })
            return {"ok": True, "chains": chains}

        @router.post("/long-video/cancel/{group_id}")
        async def cancel_chain(group_id: str) -> dict:
            info = self._active_chains.get(group_id)
            if not info:
                raise HTTPException(
                    status_code=404,
                    detail="找不到该分镜任务 — 可能已完成或已取消",
                )
            task = info.get("task")
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
            self._active_chains.pop(group_id, None)
            return {"ok": True, "group_id": group_id, "cancelled": True}

        @router.post("/long-video/concat")
        async def concat_task_videos(body: ConcatBody) -> dict:
            if not ffmpeg_available():
                raise HTTPException(
                    status_code=400,
                    detail="ffmpeg not installed — please install ffmpeg first",
                )

            video_paths: list[str] = []
            for tid in body.task_ids:
                task = await self._tm.get_task(tid)
                if not task:
                    raise HTTPException(status_code=404, detail=f"Task {tid} not found")
                local = task.get("local_video_path")
                if not local or not Path(local).is_file():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Task {tid} has no local video — download first",
                    )
                video_paths.append(local)

            config = await self._tm.get_all_config()
            output_dir = Path(config.get("output_dir") or str(Path.home() / "seedance-output"))
            output_dir.mkdir(parents=True, exist_ok=True)

            name = body.output_name or f"concat_{time.strftime('%Y%m%d_%H%M%S')}"
            if not name.endswith(".mp4"):
                name += ".mp4"
            output_path = str(output_dir / name)

            try:
                ok = await concat_videos(
                    video_paths, output_path,
                    transition=body.transition,
                    fade_duration=body.fade_duration,
                )
            except Exception as exc:
                logger.exception("ffmpeg concat raised")
                raise HTTPException(
                    status_code=500, detail=f"ffmpeg concat error: {exc}"
                ) from exc
            if not ok:
                raise HTTPException(status_code=500, detail="ffmpeg concat failed")

            return {"ok": True, "output_path": output_path}

        @router.get("/long-video/tasks/{group_id}")
        async def get_chain_tasks(group_id: str) -> dict:
            """List all segment tasks belonging to a chain generation group,
            with an aggregated ``progress`` block the UI uses to decide
            when polling can stop and the results page can render.
            """
            # Pull a generous window so a long chain (e.g. 12 segments)
            # is fully visible.  We could narrow with a JSON query later
            # if perf becomes a concern.
            tasks, _ = await self._tm.list_tasks(limit=500)
            chain = [
                t for t in tasks
                if isinstance(t.get("params"), dict)
                and t["params"].get("chain_group") == group_id
            ]
            chain.sort(key=lambda t: t.get("params", {}).get("segment_index", 0))

            # Progress aggregation — UI stops polling once
            # pending + running == 0 *and* we know the chain background
            # task itself is no longer active.
            buckets = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "other": 0}
            for t in chain:
                key = t.get("status") or "other"
                if key not in buckets:
                    key = "other"
                buckets[key] += 1
            buckets["total"] = len(chain)

            info = self._active_chains.get(group_id) or {}
            chain_task = info.get("task")
            chain_done = (
                not info
                or (isinstance(chain_task, asyncio.Task) and chain_task.done())
            )
            return {
                "ok": True,
                "group_id": group_id,
                "tasks": chain,
                "progress": buckets,
                "chain_active": bool(info) and not chain_done,
                "segments_total": info.get("segments_total") or len(chain),
                "started_at": info.get("started_at"),
            }
