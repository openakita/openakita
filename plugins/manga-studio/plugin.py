"""manga-studio — AI manga drama studio (Phase 1 wiring).

Backend entry point. Phase 1 wires only:

- ``MangaTaskManager``  — sqlite3-backed CRUD for characters / series /
  episodes / tasks (4 tables).
- A minimal route surface — ``GET /healthz`` + ``GET/PUT /settings`` —
  so the UI shell can render and we can hot-reload API keys without a
  plugin reload.
- The 11 tools listed in plugin.json::provides.tools, registered with a
  placeholder handler that returns a clear "not yet wired" message.

Phase 2 adds the direct backend (Ark Seedance + DashScope wan2.7-image +
TTS) and the 8-step ``manga_pipeline``. Phase 3 layers the workflow
backend (RunningHub / local ComfyUI via ``comfykit``). Phase 4 ships the
Series / Workflows tabs and full test coverage.

Pixelle hardening checklist (mirrors avatar-studio's wiring):

- C1  ``MangaTaskManager`` is a real SQLite DB on disk (WAL mode), not an
       in-memory dict; survives process restarts.
- C5  Missing API keys at ``on_load`` are a WARN, not a ``raise``; the UI
       surfaces a red dot and the user fixes it in Settings.
- C7  All file paths are resolved from ``api.get_data_dir()`` — never from
       env vars, never from a CWD that the host might change.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from openakita.plugins.api import PluginAPI, PluginBase
from pydantic import BaseModel, ConfigDict, Field

from comfy_client import MangaComfyClient
from direct_ark_client import MangaArkClient
from direct_wanxiang_client import MangaWanxiangClient
from ffmpeg_service import FFmpegService
from manga_inline.storage_stats import collect_storage_stats
from manga_inline.upload_preview import (
    DEFAULT_PREVIEW_EXTENSIONS,
    add_upload_preview_route,
    build_preview_url,
)
from manga_models import (
    DEFAULT_COST_THRESHOLD_CNY,
    VISUAL_STYLES_BY_ID,
    build_catalog,
    estimate_cost,
)
from manga_pipeline import (
    MangaPipeline,
    MangaPipelineConfig,
    PipelineError,
)
from manga_task_manager import MangaTaskManager
from manga_templates import list_templates
from script_writer import MangaScriptWriter
from tts_client import MangaTTSClient

logger = logging.getLogger(__name__)


# ─── Pydantic request models (must be module-level for FastAPI) ──────────
#
# Local-class request models break FastAPI body parsing — the framework
# can't resolve their location (it falls back to "query") and the request
# 422s with "Field required". Keeping them here also means the OpenAPI
# schema picks up sensible names.


class _CharacterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    role_type: str = Field("main", pattern="^(main|support|narrator|villain)$")
    gender: str = "unknown"
    age_range: str = ""
    appearance: dict[str, Any] = Field(default_factory=dict)
    personality: str = ""
    description: str = ""
    ref_images: list[dict[str, Any]] = Field(default_factory=list)
    default_voice_id: str = ""


class _CharacterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    role_type: str | None = Field(None, pattern="^(main|support|narrator|villain)$")
    gender: str | None = None
    age_range: str | None = None
    appearance: dict[str, Any] | None = None
    personality: str | None = None
    description: str | None = None
    ref_images: list[dict[str, Any]] | None = None
    default_voice_id: str | None = None


class _SeriesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=200)
    summary: str = ""
    visual_style: str = "shonen"
    ratio: str = "9:16"
    backend_pref: str = Field("direct", pattern="^(direct|runninghub|comfyui_local)$")
    default_characters: list[str] = Field(default_factory=list)
    cover_url: str = ""


class _SeriesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    summary: str | None = None
    visual_style: str | None = None
    ratio: str | None = None
    backend_pref: str | None = Field(None, pattern="^(direct|runninghub|comfyui_local)$")
    default_characters: list[str] | None = None
    cover_url: str | None = None


class _SettingsUpdate(BaseModel):
    """Loose schema — only known DEFAULT_SETTINGS keys are persisted; the
    rest are filtered out by ``_save_settings`` with a warning log line."""

    model_config = ConfigDict(extra="allow")


class _EpisodeCreate(BaseModel):
    """POST /episodes body — schedules a Phase-2 pipeline run.

    All fields default to sensible values so a UI can fire-and-forget
    a quick drama with just ``story``. Required: ``story``.
    """

    model_config = ConfigDict(extra="forbid")

    story: str = Field(..., min_length=1, max_length=8000)
    series_id: str | None = None
    title: str = ""
    n_panels: int = Field(6, ge=1, le=30)
    seconds_per_panel: int = Field(5, ge=2, le=15)
    visual_style: str = "shonen"
    ratio: str = Field("9:16", pattern=r"^(9:16|16:9|1:1|4:5)$")
    resolution: str = Field("480P", pattern=r"^(480P|720P)$")
    bound_character_ids: list[str] = Field(default_factory=list)
    fallback_voice: str = "zh-CN-XiaoxiaoNeural"
    image_model: str = "wan2.7-image"
    video_model: str = "seedance-1.0-lite-i2v"
    burn_subtitles: bool = True
    bgm_path: str | None = None
    backend: str = Field("direct", pattern=r"^(direct|runninghub|comfyui_local)$")
    confirm_over_threshold: bool = False


class _CostPreviewRequest(BaseModel):
    """POST /cost-preview body — pure estimation, no DB writes."""

    model_config = ConfigDict(extra="forbid")

    n_panels: int = Field(6, ge=1, le=30)
    total_duration_sec: int = Field(30, ge=2, le=300)
    story_chars: int = Field(500, ge=1, le=10000)
    image_model: str = "wan2.7-image"
    video_model: str = "seedance-1.0-lite-i2v"
    resolution: str = "480P"
    tts_engine: str = "edge"
    use_qwen_for_script: bool = True
    qwen_token_estimate: int = 1500


PLUGIN_ID = "manga-studio"
SETTINGS_KEY = "manga_studio_settings"
PLUGIN_DIR = Path(__file__).resolve().parent

# Default settings persisted to ``data/config.json``. Keys mirror what the
# Settings tab will write back via PUT /settings; concrete clients in
# Phase 2 / Phase 3 read this dict via the ``read_settings`` callable
# (Pixelle A10 — hot reload without plugin reload).
DEFAULT_SETTINGS: dict[str, Any] = {
    # Direct backend (Phase 2)
    "ark_api_key": "",
    "ark_endpoint_id": "",
    "dashscope_api_key": "",
    "dashscope_region": "beijing",
    "tts_engine": "edge",  # "edge" | "cosyvoice"
    # Workflow backend (Phase 3) — picked when ``backend != "direct"``
    # on a generation request. ``comfy_backend`` selects between
    # RunningHub (cloud) and a self-hosted ComfyUI install. The three
    # ``*_workflow_*`` keys map workflow modes to either RunningHub
    # workflow IDs (numeric strings RH gives you on the dashboard) or
    # local-file paths to exported workflow JSONs.
    "comfy_backend": "runninghub",  # "runninghub" | "comfyui_local"
    "runninghub_api_key": "",
    "runninghub_workflow_image": "",
    "runninghub_workflow_animate": "",
    "runninghub_workflow_t2v": "",
    "comfyui_local_url": "",
    "comfyui_workflow_image": "",
    "comfyui_workflow_animate": "",
    "comfyui_workflow_t2v": "",
    # OSS — used by both backends to host reference images / panel images
    # / final videos so vendors can fetch via signed URL.
    "oss_endpoint": "",
    "oss_bucket": "",
    "oss_access_key_id": "",
    "oss_access_key_secret": "",
    "oss_url_prefix": "",
    # Cost guard — pipeline pauses for confirmation when the estimate
    # exceeds this. ``0`` disables the guard entirely.
    "cost_threshold_cny": 5.0,
}


class Plugin(PluginBase):
    """Minimal-load entry point. Phase 1 ships a *load-OK* skeleton with
    only the data layer wired."""

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        data_dir = api.get_data_dir()
        if data_dir is None:
            api.log(
                "manga-studio: data.own permission denied; running in degraded mode (no DB)",
                "warning",
            )
            self._data_dir = PLUGIN_DIR / "_runtime"
            self._data_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._data_dir = data_dir
        self._tm = MangaTaskManager(self._data_dir / "manga.db")

        # Phase 2 — direct backend (Ark Seedance + DashScope wan2.7-image
        # + TTS). Phase 3.1 — ``_comfy_client`` (RunningHub + local
        # ComfyUI). All clients are constructed with the
        # ``read_settings`` callable so a Settings change takes effect
        # on the very next request (Pixelle A10).
        self._direct_ark: MangaArkClient | None = None
        self._direct_wan: MangaWanxiangClient | None = None
        self._tts: MangaTTSClient | None = None
        self._ffmpeg: FFmpegService | None = None
        self._writer: MangaScriptWriter | None = None
        self._pipeline: MangaPipeline | None = None
        self._comfy_client: MangaComfyClient | None = None
        self._oss: Any | None = None

        # Background task registry — populated when the pipeline kicks
        # off via POST /episodes. ``on_unload`` cancels everything in
        # here, mirroring avatar-studio's pattern.
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}

        self._register_routes(api)
        self._register_tools(api)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log("Manga Studio plugin loaded (phase 2: direct backend)")

    async def _async_init(self) -> None:
        """Initialise the SQLite schema and Phase-2 collaborators.

        Each client is constructed with a ``read_settings`` callable —
        none of them touch the network at construction time, so a missing
        API key only fails the FIRST request, not plugin load.
        """
        try:
            await self._tm.init()
        except Exception as exc:  # noqa: BLE001 - never crash on_load
            self._api.log(f"manga-studio: task manager init failed: {exc!r}", "error")
            return

        # Construct the Phase-2 clients. ``read_settings`` is a fresh
        # snapshot each call (see ``_read_settings``) — the clients pull
        # the api_key on every request, never cache it.
        try:
            self._direct_ark = MangaArkClient(read_settings=self._read_settings)
            self._direct_wan = MangaWanxiangClient(read_settings=self._read_settings)
            self._tts = MangaTTSClient(read_settings=self._read_settings)
            self._ffmpeg = FFmpegService()
            self._writer = MangaScriptWriter(self._api)
            self._comfy_client = MangaComfyClient(read_settings=self._read_settings)
            self._pipeline = MangaPipeline(
                wanxiang_client=self._direct_wan,
                ark_client=self._direct_ark,
                tts_client=self._tts,
                ffmpeg=self._ffmpeg,
                script_writer=self._writer,
                task_manager=self._tm,
                working_dir=self._data_dir / "episodes",
                comfy_client=self._comfy_client,
            )
        except Exception as exc:  # noqa: BLE001
            self._api.log(f"manga-studio: client wire-up failed: {exc!r}", "error")

    async def on_unload(self) -> None:
        """Cancel any background polling task and close the DB cleanly."""
        for tid, t in list(self._poll_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - keep unload best-effort
                    logger.warning("manga-studio poll task %s drain error: %s", tid, exc)
        try:
            await self._tm.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("manga-studio task manager close error: %s", exc)

    # ── Settings (config.json-backed) ────────────────────────────────────

    def _load_settings(self) -> dict[str, Any]:
        """Read settings, merged on top of ``DEFAULT_SETTINGS``."""
        cfg = self._api.get_config() or {}
        merged: dict[str, Any] = dict(DEFAULT_SETTINGS)
        stored = cfg.get(SETTINGS_KEY, {})
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in DEFAULT_SETTINGS:
                    merged[k] = v
        return merged

    def _save_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge ``updates`` into the stored settings dict and persist.

        Unknown keys are ignored (with a log line) — strict whitelist
        avoids the UI accidentally writing typos that the backend would
        then silently ignore on next read.
        """
        clean: dict[str, Any] = {}
        ignored: list[str] = []
        for k, v in (updates or {}).items():
            if k in DEFAULT_SETTINGS:
                clean[k] = v
            else:
                ignored.append(k)
        if ignored:
            self._api.log(
                f"manga-studio: PUT /settings ignored unknown keys: {ignored!r}",
                "warning",
            )
        cfg = self._api.get_config() or {}
        stored = cfg.get(SETTINGS_KEY, {})
        if not isinstance(stored, dict):
            stored = {}
        stored.update(clean)
        self._api.set_config({SETTINGS_KEY: stored})
        return self._load_settings()

    def _read_settings(self) -> dict[str, Any]:
        """Callable threaded into the Phase 2 / Phase 3 clients (A10).

        Returns a fresh merged settings dict on every call so users can
        edit API keys in Settings without reloading the plugin.
        """
        return self._load_settings()

    # ── Tools ────────────────────────────────────────────────────────────

    def _register_tools(self, api: PluginAPI) -> None:
        api.register_tools(
            self._tool_specs(),
            handler=self._handle_tool,
        )

    def _tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "manga_create_series",
                "description": "Create a new manga drama series with a default visual style.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "visual_style": {"type": "string", "default": "shonen"},
                    },
                    "required": ["title"],
                },
            },
            {
                "name": "manga_create_episode",
                "description": "Create and start generating a single episode of a manga drama.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "story": {"type": "string"},
                        "series_id": {"type": "string"},
                        "characters": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "backend": {
                            "type": "string",
                            "enum": ["direct", "runninghub", "comfyui_local"],
                        },
                        "total_duration": {"type": "integer", "default": 60},
                    },
                    "required": ["story"],
                },
            },
            {
                "name": "manga_episode_status",
                "description": "Check the current generation status of a manga episode.",
                "input_schema": {
                    "type": "object",
                    "properties": {"episode_id": {"type": "string"}},
                    "required": ["episode_id"],
                },
            },
            {
                "name": "manga_list_episodes",
                "description": "List recent manga episodes, optionally filtered by series.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "series_id": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "manga_create_character",
                "description": "Create a reusable manga character card with appearance + voice.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role_type": {
                            "type": "string",
                            "enum": ["main", "support", "narrator", "villain"],
                        },
                        "description": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "manga_list_characters",
                "description": "List all manga character cards in the library.",
                "input_schema": {
                    "type": "object",
                    "properties": {"role_type": {"type": "string"}},
                },
            },
            {
                "name": "manga_quick_drama",
                "description": "Generate a one-shot manga drama from a single story prompt.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "story": {"type": "string"},
                        "visual_style": {"type": "string", "default": "shonen"},
                        "ratio": {"type": "string", "default": "9:16"},
                        "total_duration": {"type": "integer", "default": 60},
                    },
                    "required": ["story"],
                },
            },
            {
                "name": "manga_split_script",
                "description": "Split a story into a structured manga storyboard JSON without generating media.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "story": {"type": "string"},
                        "total_duration": {"type": "integer", "default": 60},
                    },
                    "required": ["story"],
                },
            },
            {
                "name": "manga_render_panel",
                "description": "Render a single storyboard panel (debug helper).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "string"},
                        "panel_index": {"type": "integer"},
                    },
                    "required": ["episode_id", "panel_index"],
                },
            },
            {
                "name": "manga_cost_preview",
                "description": "Estimate the CNY cost of generating a manga episode.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "story_chars": {"type": "integer"},
                        "n_panels": {"type": "integer"},
                        "total_duration": {"type": "integer"},
                        "backend": {"type": "string"},
                    },
                },
            },
            {
                "name": "manga_workflow_test",
                "description": "Test connectivity to the configured RunningHub or local ComfyUI backend.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "backend": {
                            "type": "string",
                            "enum": ["runninghub", "comfyui_local"],
                        },
                    },
                    "required": ["backend"],
                },
            },
        ]

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        """Dispatch a tool call to its handler.

        Each handler is small — just enough to translate the tool's
        ``args`` dict into either a task-manager call or a pipeline
        kick-off, then format the result as a single string the LLM
        can read back to the user.

        Errors propagate as a string starting with ``"error:"`` so the
        caller (Brain ReAct loop) can detect failure without parsing
        the result format.
        """
        try:
            handler = self._TOOL_HANDLERS.get(tool_name)
            if handler is None:
                return f"error: unknown tool {tool_name!r}"
            return await handler(self, args)
        except Exception as exc:  # noqa: BLE001 - tool errors are user-visible
            logger.exception("manga-studio tool %s failed", tool_name)
            return f"error: tool {tool_name} raised {type(exc).__name__}: {exc}"

    # ── Tool handlers ────────────────────────────────────────────────────
    # Each handler returns the user-facing string. Keep them small:
    # heavy logic should live in the pipeline / clients / task manager.

    async def _tool_create_series(self, args: dict[str, Any]) -> str:
        title = str(args.get("title") or "").strip()
        if not title:
            return "error: title is required"
        sid = await self._tm.create_series(
            title=title,
            summary=str(args.get("summary") or ""),
            visual_style=str(args.get("visual_style") or "shonen"),
            ratio=str(args.get("ratio") or "9:16"),
        )
        return f"created series {sid} ({title!r})"

    async def _tool_create_episode(self, args: dict[str, Any]) -> str:
        if self._pipeline is None:
            return "error: Phase-2 pipeline not initialised yet (try again in a sec)"
        story = str(args.get("story") or "").strip()
        if not story:
            return "error: story is required"
        n_panels = int(args.get("n_panels") or 6)
        seconds_per_panel = max(2, (int(args.get("total_duration") or 30)) // max(1, n_panels))
        chars = args.get("characters") or []
        if not isinstance(chars, list):
            chars = []

        episode_id = await self._tm.create_episode(
            series_id=args.get("series_id"),
            title=str(args.get("title") or ""),
            story=story,
            bound_characters=[str(c) for c in chars if c],
        )
        task_id = await self._spawn_pipeline_task(
            episode_id=episode_id,
            config=MangaPipelineConfig(
                story=story,
                n_panels=n_panels,
                seconds_per_panel=seconds_per_panel,
                visual_style=str(args.get("visual_style") or "shonen"),
                ratio=str(args.get("ratio") or "9:16"),
                backend=str(args.get("backend") or "direct"),
                bound_character_ids=[str(c) for c in chars if c],
                series_id=args.get("series_id"),
            ),
        )
        return (
            f"episode {episode_id} queued (task {task_id}); poll GET /tasks/{task_id} for progress"
        )

    async def _tool_episode_status(self, args: dict[str, Any]) -> str:
        ep_id = str(args.get("episode_id") or "").strip()
        if not ep_id:
            return "error: episode_id is required"
        episode = await self._tm.get_episode(ep_id)
        if episode is None:
            return f"error: episode {ep_id} not found"
        # Find the most recent task for this episode (if any).
        tasks = await self._tm.list_tasks(episode_id=ep_id, limit=1)
        if tasks:
            t = tasks[0]
            return (
                f"episode {ep_id}: status={t.get('status')} "
                f"step={t.get('current_step')} progress={t.get('progress')}%"
            )
        if episode.get("final_video_path"):
            return f"episode {ep_id}: ready, final={episode.get('final_video_path')}"
        return f"episode {ep_id}: no task yet (just the row exists)"

    async def _tool_list_episodes(self, args: dict[str, Any]) -> str:
        limit = max(1, min(50, int(args.get("limit") or 20)))
        rows = await self._tm.list_episodes(
            series_id=args.get("series_id"),
            limit=limit,
        )
        if not rows:
            return "no episodes yet"
        lines = [
            f"- {r['id']}: {r.get('title') or '(untitled)'} "
            f"({'ready' if r.get('final_video_path') else 'pending'})"
            for r in rows
        ]
        return "\n".join(lines)

    async def _tool_create_character(self, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            return "error: name is required"
        cid = await self._tm.create_character(
            name=name,
            role_type=str(args.get("role_type") or "main"),
            description=str(args.get("description") or ""),
            appearance=args.get("appearance") if isinstance(args.get("appearance"), dict) else None,
            ref_images=args.get("ref_images") if isinstance(args.get("ref_images"), list) else None,
        )
        return f"created character {cid} ({name!r})"

    async def _tool_list_characters(self, args: dict[str, Any]) -> str:
        rows = await self._tm.list_characters(role_type=args.get("role_type"))
        if not rows:
            return "no characters yet"
        return "\n".join(
            f"- {r['id']}: {r.get('name')} ({r.get('role_type')}, {r.get('gender')})" for r in rows
        )

    async def _tool_quick_drama(self, args: dict[str, Any]) -> str:
        # quick_drama is just create_episode without a series.
        return await self._tool_create_episode(args)

    async def _tool_split_script(self, args: dict[str, Any]) -> str:
        if self._writer is None:
            return "error: script writer not initialised"
        story = str(args.get("story") or "").strip()
        if not story:
            return "error: story is required"
        n_panels = int(args.get("n_panels") or 6)
        result = await self._writer.write_storyboard(
            story=story,
            n_panels=n_panels,
            seconds_per_panel=int(args.get("seconds_per_panel") or 5),
            characters=[],
            visual_style_label=str(args.get("visual_style") or "少年热血"),
        )
        used = "brain" if result.used_brain else "deterministic fallback"
        return (
            f"storyboard ({used}): "
            f"{len(result.data.get('panels', []))} panels, "
            f"title={result.data.get('episode_title')!r}"
        )

    async def _tool_render_panel(self, args: dict[str, Any]) -> str:
        # Dev / debug helper; v1.0 ships this as a not-yet-wired stub
        # so the tool list is complete. Phase 3 fills in re-rendering.
        return (
            f"manga_render_panel for episode={args.get('episode_id')!r} "
            f"panel={args.get('panel_index')!r} — not yet wired (Phase 3)"
        )

    async def _tool_cost_preview(self, args: dict[str, Any]) -> str:
        n_panels = int(args.get("n_panels") or 6)
        total_duration = int(args.get("total_duration") or 30)
        cost = estimate_cost(
            n_panels=n_panels,
            total_duration_sec=total_duration,
            story_chars=int(args.get("story_chars") or 500),
            image_model=str(args.get("image_model") or "wan2.7-image"),
            video_model=str(args.get("video_model") or "seedance-1.0-lite-i2v"),
            resolution=str(args.get("resolution") or "480P"),
            tts_engine=str(args.get("tts_engine") or "edge"),
        )
        warn = " (over threshold)" if cost["exceeds_threshold"] else ""
        return f"estimated cost: {cost['formatted_total']}{warn}"

    async def _tool_workflow_test(self, args: dict[str, Any]) -> str:
        """Probe the configured workflow backend (Phase 3.1).

        ``backend`` arg is informational — the actual backend that gets
        probed is ``comfy_backend`` from settings (so the user always
        gets a probe of what's actually configured). The tool surfaces
        a single-line ``ok=… backend=… msg=…`` string the LLM can read
        back in chat.
        """
        if self._comfy_client is None:
            return "error: workflow client not initialised yet"
        result = await self._comfy_client.probe_backend()
        ok = result.get("ok")
        backend = result.get("backend") or "?"
        msg = result.get("message") or ""
        flag = "ok" if ok else "FAIL"
        return f"{flag} · backend={backend} · {msg}"

    # Map tool name → bound handler. Defined as a class attribute so a
    # subclass can override one entry without copying the rest.
    _TOOL_HANDLERS: dict[str, Callable[[Plugin, dict[str, Any]], Awaitable[str]]] = {  # noqa: F821
        "manga_create_series": _tool_create_series,
        "manga_create_episode": _tool_create_episode,
        "manga_episode_status": _tool_episode_status,
        "manga_list_episodes": _tool_list_episodes,
        "manga_create_character": _tool_create_character,
        "manga_list_characters": _tool_list_characters,
        "manga_quick_drama": _tool_quick_drama,
        "manga_split_script": _tool_split_script,
        "manga_render_panel": _tool_render_panel,
        "manga_cost_preview": _tool_cost_preview,
        "manga_workflow_test": _tool_workflow_test,
    }

    # ── Pipeline dispatcher ──────────────────────────────────────────────

    async def _spawn_pipeline_task(
        self,
        *,
        episode_id: str,
        config: MangaPipelineConfig,
    ) -> str:
        """Create a ``tasks`` row and spawn the background coroutine.

        Returns the task_id so the caller can hand it back to the UI
        for polling. The backend label written to the task row comes
        from ``config.backend`` (Phase 3.3 — the pipeline now branches
        on it for image/video gen).
        """
        backend = config.backend
        if self._pipeline is None:
            raise RuntimeError("Phase-2 pipeline not initialised")
        # Estimate cost for this run so the row carries the breakdown
        # the UI's cost panel reads.
        story_chars = len(config.story)
        total_duration = config.n_panels * config.seconds_per_panel
        try:
            cost = estimate_cost(
                n_panels=config.n_panels,
                total_duration_sec=total_duration,
                story_chars=story_chars,
                image_model=config.image_model,
                video_model=config.video_model,
                resolution=config.resolution,
                tts_engine="edge",
            )
        except Exception:  # noqa: BLE001 - cost estimation is non-fatal
            cost = None

        task_id = await self._tm.create_task(
            mode="episode",
            backend=backend,
            episode_id=episode_id,
            params={
                "story": config.story,
                "n_panels": config.n_panels,
                "seconds_per_panel": config.seconds_per_panel,
                "visual_style": config.visual_style,
                "ratio": config.ratio,
                "resolution": config.resolution,
                "bound_character_ids": list(config.bound_character_ids),
                "burn_subtitles": config.burn_subtitles,
                "bgm_path": config.bgm_path,
            },
            cost_breakdown=cost,
        )

        coro = self._run_pipeline(episode_id=episode_id, task_id=task_id, config=config)
        bg = self._api.spawn_task(coro, name=f"{PLUGIN_ID}:run:{task_id}")
        if bg is not None:
            self._poll_tasks[task_id] = bg
        return task_id

    async def _run_pipeline(
        self,
        *,
        episode_id: str,
        task_id: str,
        config: MangaPipelineConfig,
    ) -> None:
        """Wrapper around ``MangaPipeline.run_episode`` that records
        terminal status into the tasks row.

        Pixelle anti-pattern: never let a background task die silently.
        Even an unexpected exception is recorded so the UI can show
        the user what went wrong.
        """
        if self._pipeline is None:
            return
        try:
            await self._pipeline.run_episode(episode_id=episode_id, config=config, task_id=task_id)
        except PipelineError as exc:
            logger.warning("manga-studio pipeline failed: %s", exc)
            await self._tm.update_task_safe(
                task_id,
                status="failed",
                error_kind=exc.error_kind,
                error_message=str(exc),
                error_hints_json=exc.to_dict(),
            )
        except asyncio.CancelledError:
            await self._tm.update_task_safe(
                task_id, status="cancelled", error_message="cancelled by host"
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("manga-studio pipeline crashed")
            await self._tm.update_task_safe(
                task_id,
                status="failed",
                error_kind="unknown",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._poll_tasks.pop(task_id, None)

    # ── Routes ───────────────────────────────────────────────────────────

    def _register_routes(self, api: PluginAPI) -> None:
        from fastapi import APIRouter, HTTPException

        router = APIRouter()

        # Health probe — doubles as a load-OK indicator and as the
        # backend-readiness map the UI uses to show red/green dots.
        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "phase": 2,
                "backends_ready": {
                    "direct_ark": self._direct_ark is not None,
                    "direct_wan": self._direct_wan is not None,
                    "tts": self._tts is not None,
                    "ffmpeg": self._ffmpeg is not None and self._ffmpeg.is_available(),
                    "pipeline": self._pipeline is not None,
                    # ``comfy`` reports the *client* is constructed; ``ok``
                    # status of a real probe lives at ``POST /workflows/probe``
                    # to avoid a billable call on every healthz poll.
                    "comfy": self._comfy_client is not None,
                    "oss": self._oss is not None,
                },
            }

        _SECRET_KEYS = (
            "ark_api_key",
            "dashscope_api_key",
            "runninghub_api_key",
            "oss_access_key_secret",
        )

        def _redact(s: dict[str, Any]) -> dict[str, Any]:
            """Return a copy with secret keys reduced to a length hint.

            UI never receives the raw secret — it only ever needs to
            know "is this set" and "what's the prefix" so the user can
            tell two keys apart. avatar-studio uses the same pattern.
            """
            out = dict(s)
            for k in _SECRET_KEYS:
                v = out.get(k) or ""
                if isinstance(v, str) and v:
                    out[k] = (
                        v[:4] + "•" * max(0, len(v) - 8) + v[-4:] if len(v) > 8 else "•" * len(v)
                    )
            return out

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            return {"settings": _redact(self._load_settings())}

        @router.put("/settings")
        async def put_settings(payload: _SettingsUpdate) -> dict[str, Any]:
            updates = payload.model_dump(exclude_unset=True)
            try:
                merged = self._save_settings(updates)
            except Exception as exc:  # noqa: BLE001 - surface a 400, not 500
                raise HTTPException(400, f"failed to update settings: {exc!r}") from exc
            return {"ok": True, "settings": _redact(merged)}

        # ── Characters ───────────────────────────────────────────────
        # Reusable character cards. The single most-shared entity across
        # episodes; the reason this plugin exists. Phase 2's pipeline
        # consumes ``ref_images`` here as the IP-Adapter / DashScope
        # multi-reference input that drives consistency.

        @router.post("/characters")
        async def create_character(body: _CharacterCreate) -> dict[str, Any]:
            try:
                cid = await self._tm.create_character(
                    name=body.name,
                    role_type=body.role_type,
                    gender=body.gender,
                    age_range=body.age_range,
                    appearance=body.appearance,
                    personality=body.personality,
                    description=body.description,
                    ref_images=body.ref_images,
                    default_voice_id=body.default_voice_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            row = await self._tm.get_character(cid)
            return {"ok": True, "character_id": cid, "character": row}

        @router.get("/characters")
        async def list_characters(role_type: str | None = None) -> dict[str, Any]:
            try:
                rows = await self._tm.list_characters(role_type=role_type)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "characters": rows}

        @router.get("/characters/{char_id}")
        async def get_character(char_id: str) -> dict[str, Any]:
            row = await self._tm.get_character(char_id)
            if row is None:
                raise HTTPException(status_code=404, detail="character not found")
            return {"ok": True, "character": row}

        @router.put("/characters/{char_id}")
        async def update_character(char_id: str, body: _CharacterUpdate) -> dict[str, Any]:
            existing = await self._tm.get_character(char_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="character not found")
            updates: dict[str, Any] = {}
            for k, v in body.model_dump(exclude_unset=True).items():
                if v is None:
                    continue
                # The DB column for dict / list values is named ``*_json``
                # — translate the public-API key here so the whitelist
                # check inside ``update_character_safe`` accepts it.
                if k in {"appearance", "ref_images"}:
                    updates[f"{k}_json"] = v
                else:
                    updates[k] = v
            try:
                changed = await self._tm.update_character_safe(char_id, **updates)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "ok": True,
                "changed": changed,
                "character": await self._tm.get_character(char_id),
            }

        @router.delete("/characters/{char_id}")
        async def delete_character(char_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_character(char_id)
            if not ok:
                raise HTTPException(status_code=404, detail="character not found")
            return {"ok": True}

        # ── Series ──────────────────────────────────────────────────
        # Multi-episode container. Phase 2's pipeline reads the default
        # character list / visual style / ratio / backend from the parent
        # series row when the user creates an episode under it.

        @router.post("/series")
        async def create_series(body: _SeriesCreate) -> dict[str, Any]:
            try:
                sid = await self._tm.create_series(
                    title=body.title,
                    summary=body.summary,
                    visual_style=body.visual_style,
                    ratio=body.ratio,
                    backend_pref=body.backend_pref,
                    default_characters=body.default_characters,
                    cover_url=body.cover_url,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            row = await self._tm.get_series(sid)
            return {"ok": True, "series_id": sid, "series": row}

        @router.get("/series")
        async def list_series(limit: int = 100, offset: int = 0) -> dict[str, Any]:
            rows = await self._tm.list_series(limit=limit, offset=offset)
            return {"ok": True, "series": rows}

        @router.get("/series/{ser_id}")
        async def get_series(ser_id: str) -> dict[str, Any]:
            row = await self._tm.get_series(ser_id)
            if row is None:
                raise HTTPException(status_code=404, detail="series not found")
            return {"ok": True, "series": row}

        @router.put("/series/{ser_id}")
        async def update_series(ser_id: str, body: _SeriesUpdate) -> dict[str, Any]:
            existing = await self._tm.get_series(ser_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="series not found")
            updates: dict[str, Any] = {}
            for k, v in body.model_dump(exclude_unset=True).items():
                if v is None:
                    continue
                if k == "default_characters":
                    updates["default_characters_json"] = v
                else:
                    updates[k] = v
            try:
                changed = await self._tm.update_series_safe(ser_id, **updates)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "ok": True,
                "changed": changed,
                "series": await self._tm.get_series(ser_id),
            }

        @router.delete("/series/{ser_id}")
        async def delete_series(ser_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_series(ser_id)
            if not ok:
                raise HTTPException(status_code=404, detail="series not found")
            return {"ok": True}

        # ── Episodes (read-only in Phase 1) ─────────────────────────
        # Episode rows are CREATED by the Phase 2 pipeline (kicked off
        # via POST /episodes — added in Phase 2 alongside the pipeline).
        # Phase 1 only exposes the read side so the UI shell can list
        # already-generated episodes after a process restart.

        @router.get("/episodes")
        async def list_episodes(
            series_id: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> dict[str, Any]:
            rows = await self._tm.list_episodes(series_id=series_id, limit=limit, offset=offset)
            return {"ok": True, "episodes": rows}

        @router.get("/episodes/{ep_id}")
        async def get_episode(ep_id: str) -> dict[str, Any]:
            row = await self._tm.get_episode(ep_id)
            if row is None:
                raise HTTPException(status_code=404, detail="episode not found")
            return {"ok": True, "episode": row}

        @router.post("/episodes")
        async def create_episode(body: _EpisodeCreate) -> dict[str, Any]:
            """Schedule a Phase-2 pipeline run.

            Returns ``{episode_id, task_id, cost_preview}``. The pipeline
            runs in the background; poll ``GET /tasks/{task_id}`` for
            live progress.

            If the estimated cost exceeds the configured threshold and
            ``confirm_over_threshold`` is False, returns 402 Payment
            Required with the cost breakdown so the UI can show a
            confirmation modal.
            """
            if self._pipeline is None:
                raise HTTPException(
                    status_code=503,
                    detail="Phase-2 pipeline not initialised yet (try again in a moment)",
                )
            if body.visual_style not in VISUAL_STYLES_BY_ID:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown visual_style {body.visual_style!r}",
                )

            total_duration = body.n_panels * body.seconds_per_panel
            cost = estimate_cost(
                n_panels=body.n_panels,
                total_duration_sec=total_duration,
                story_chars=len(body.story),
                image_model=body.image_model,
                video_model=body.video_model,
                resolution=body.resolution,
                tts_engine=str(self._read_settings().get("tts_engine") or "edge"),
                threshold=float(
                    self._read_settings().get("cost_threshold_cny") or DEFAULT_COST_THRESHOLD_CNY
                ),
            )
            if cost["exceeds_threshold"] and not body.confirm_over_threshold:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "ok": False,
                        "reason": "cost_over_threshold",
                        "cost_preview": cost,
                        "hint_zh": (
                            f"预估成本 {cost['formatted_total']} 超过阈值 ¥{cost['threshold']:.2f}，"
                            f"请勾选确认后再次提交"
                        ),
                        "hint_en": (
                            f"Estimated cost {cost['formatted_total']} exceeds the "
                            f"¥{cost['threshold']:.2f} threshold; submit again with "
                            f"confirm_over_threshold=true"
                        ),
                    },
                )

            episode_id = await self._tm.create_episode(
                series_id=body.series_id,
                title=body.title,
                story=body.story,
                bound_characters=body.bound_character_ids,
            )
            cfg = MangaPipelineConfig(
                story=body.story,
                n_panels=body.n_panels,
                seconds_per_panel=body.seconds_per_panel,
                visual_style=body.visual_style,
                ratio=body.ratio,
                resolution=body.resolution,
                backend=body.backend,
                bound_character_ids=body.bound_character_ids,
                fallback_voice=body.fallback_voice,
                image_model=body.image_model,
                video_model=body.video_model,
                burn_subtitles=body.burn_subtitles,
                bgm_path=body.bgm_path,
                title_hint=body.title,
                series_id=body.series_id,
            )
            task_id = await self._spawn_pipeline_task(episode_id=episode_id, config=cfg)
            return {
                "ok": True,
                "episode_id": episode_id,
                "task_id": task_id,
                "cost_preview": cost,
            }

        @router.delete("/episodes/{ep_id}")
        async def delete_episode(ep_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_episode(ep_id)
            if not ok:
                raise HTTPException(status_code=404, detail="episode not found")
            return {"ok": True}

        # ── Tasks ───────────────────────────────────────────────────
        # Lightweight read-only surface so the UI can poll a running
        # episode's progress without refetching the full episode row.

        @router.get("/tasks")
        async def list_tasks(
            episode_id: str | None = None,
            status: str | None = None,
            limit: int = 50,
        ) -> dict[str, Any]:
            rows = await self._tm.list_tasks(episode_id=episode_id, status=status, limit=limit)
            return {"ok": True, "tasks": rows}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            return {"ok": True, "task": row}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            bg = self._poll_tasks.get(task_id)
            if bg is not None and not bg.done():
                bg.cancel()
            await self._tm.update_task_safe(
                task_id, status="cancelled", error_message="cancelled by user"
            )
            return {"ok": True}

        # ── Catalog & cost preview ──────────────────────────────────
        # Static UI seed (visual_styles / voices / option lists) +
        # cost estimator that the Studio tab calls before submit.

        @router.get("/catalog")
        async def get_catalog() -> dict[str, Any]:
            cat = build_catalog()
            return {
                "ok": True,
                "catalog": {
                    "visual_styles": cat.visual_styles,
                    "ratios": cat.ratios,
                    "duration_options": cat.duration_options,
                    "seconds_per_panel_options": cat.seconds_per_panel_options,
                    "character_roles": cat.character_roles,
                    "backends": cat.backends,
                    "voices": cat.voices,
                    "cost_threshold": cat.cost_threshold,
                },
            }

        # ── Story templates (Phase 4.4) ─────────────────────────────
        # Read-only catalogue of curated quick-start story prompts.
        # Pinned to the plugin version (see manga_templates.py); the UI
        # fetches the whole list once and indexes locally so picking a
        # template is purely client-side. Each item carries a default
        # visual_style / ratio / panel layout so applying a template
        # populates the Studio form without further round-trips.

        @router.get("/templates")
        async def get_templates() -> dict[str, Any]:
            return {"ok": True, "templates": list_templates()}

        # ── Workflows ───────────────────────────────────────────────
        # Phase 3.1 — probe RunningHub or local ComfyUI without billing
        # the user. The Workflows tab UI calls this on backend change
        # to render the green / red dot + message. ``manga_workflow_test``
        # tool delegates here too (via _tool_workflow_test).

        @router.post("/workflows/probe")
        async def workflows_probe() -> dict[str, Any]:
            if self._comfy_client is None:
                raise HTTPException(
                    status_code=503,
                    detail="workflow client not initialised yet (try again in a moment)",
                )
            result = await self._comfy_client.probe_backend()
            return {"ok": True, "probe": result}

        @router.post("/cost-preview")
        async def cost_preview(body: _CostPreviewRequest) -> dict[str, Any]:
            try:
                cost = estimate_cost(
                    n_panels=body.n_panels,
                    total_duration_sec=body.total_duration_sec,
                    story_chars=body.story_chars,
                    image_model=body.image_model,
                    video_model=body.video_model,
                    resolution=body.resolution,
                    tts_engine=body.tts_engine,
                    use_qwen_for_script=body.use_qwen_for_script,
                    qwen_token_estimate=body.qwen_token_estimate,
                    threshold=float(
                        self._read_settings().get("cost_threshold_cny")
                        or DEFAULT_COST_THRESHOLD_CNY
                    ),
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"ok": True, "cost_preview": cost}

        # ── Upload (issue #479: serve back what the user uploaded) ──
        # Phase 1 lands the local-preview half of the upload flow:
        #
        #   POST /upload         — accept a multipart file, save it under
        #                          ``data/uploads/{kind}/<uuid>.<ext>`` and
        #                          return a preview URL the UI can render
        #                          inside an <img>/<video>/<audio> tag.
        #   GET  /uploads/{path} — wired by ``add_upload_preview_route``
        #                          (sandboxed against base_dir; see issue
        #                          #479).
        #
        # OSS upload (so DashScope / RunningHub can fetch the file via
        # signed URL) lands in Phase 2 once the OssUploader is wired —
        # the route's response shape already includes ``oss_url`` =
        # ``None`` so the UI can branch on it without breaking.
        from fastapi import File

        uploads_dir = self._data_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        add_upload_preview_route(
            router,
            base_dir=uploads_dir,
            allowed_extensions=DEFAULT_PREVIEW_EXTENSIONS,
        )

        _UPLOAD_KINDS: tuple[str, ...] = (
            "character_ref",  # character reference sheet (front / side / pose)
            "panel",  # already-generated storyboard panel image
            "video",  # episode video / clip
            "audio",  # voice clone reference / BGM
            "other",
        )

        @router.post("/upload")
        async def upload_file(
            file: UploadFile = File(...),
            kind: str = "character_ref",
        ) -> dict[str, Any]:
            if kind not in _UPLOAD_KINDS:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown kind {kind!r}; allowed={list(_UPLOAD_KINDS)}",
                )
            ext = Path(file.filename or "file").suffix.lower().lstrip(".") or ""
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="empty file")

            subdir = uploads_dir / kind
            subdir.mkdir(parents=True, exist_ok=True)

            import uuid as _uuid

            fname = f"{_uuid.uuid4().hex[:12]}.{ext}" if ext else _uuid.uuid4().hex[:12]
            local_path = subdir / fname
            local_path.write_bytes(content)
            rel = f"{kind}/{fname}"
            preview_url = build_preview_url(PLUGIN_ID, rel)

            return {
                "ok": True,
                "kind": kind,
                "filename": fname,
                "rel_path": rel,
                "size_bytes": len(content),
                "preview_url": preview_url,
                # Phase 2 fills these in once OSS is wired up; keep them in
                # the response so the UI can shape its state from day one.
                "oss_url": None,
                "oss_key": None,
                "oss_error": "OSS not configured (Phase 2 wiring pending)",
            }

        # ── Storage stats ────────────────────────────────────────────
        # The Settings tab's storage panel polls this — knowing how much
        # disk the plugin has burned is a real concern for users running
        # locally on a small SSD; the helper caps the walk at 5000 files
        # and runs off the loop so a deep tree can't stall a request.

        @router.get("/storage")
        async def storage_stats() -> dict[str, Any]:
            stats = await collect_storage_stats(
                self._data_dir,
                max_files=5000,
                sample_paths=10,
                skip_hidden=True,
            )
            return {
                "ok": True,
                "data_dir": str(self._data_dir),
                "stats": stats.to_dict(),
            }

        api.register_api_routes(router)
        # Hold a reference for Phase 2's pipeline routes to extend.
        self._router = router
