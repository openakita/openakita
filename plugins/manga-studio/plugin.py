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
from pathlib import Path
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase
from pydantic import BaseModel, ConfigDict, Field

from manga_task_manager import MangaTaskManager

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
    # Workflow backend (Phase 3)
    "comfy_backend": "runninghub",  # "runninghub" | "comfyui_local"
    "runninghub_api_key": "",
    "runninghub_workflow_image": "",
    "runninghub_workflow_animate": "",
    "comfyui_local_url": "",
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

        # Phase 2 / Phase 3 placeholders — kept ``None`` until the matching
        # client files land. Routes that depend on them must guard with a
        # 503.
        self._direct_ark = None
        self._direct_wan = None
        self._tts = None
        self._comfy_client = None
        self._oss = None

        # Background task registry — populated by ``api.spawn_task`` from
        # the pipeline once Phase 2 lands. ``on_unload`` cancels everything
        # in here, mirroring avatar-studio's pattern.
        self._poll_tasks: dict[str, asyncio.Task] = {}

        self._register_routes(api)
        self._register_tools(api)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log("Manga Studio plugin loaded (phase 1: skeleton)")

    async def _async_init(self) -> None:
        """Initialise the SQLite schema. Called via ``api.spawn_task``."""
        try:
            await self._tm.init()
        except Exception as exc:  # noqa: BLE001 - never crash on_load
            self._api.log(f"manga-studio: task manager init failed: {exc!r}", "error")

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

    async def _handle_tool(self, tool_name: str, args: dict) -> str:
        """Phase 1 placeholder. Phase 2 wires real handlers per tool name."""
        return (
            f"manga-studio tool {tool_name!r} is registered but not yet "
            f"wired (phase 1 skeleton). Args: {args!r}"
        )

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
                "phase": 1,
                "backends_ready": {
                    "direct_ark": self._direct_ark is not None,
                    "direct_wan": self._direct_wan is not None,
                    "tts": self._tts is not None,
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

        api.register_api_routes(router)
        # Hold a reference for the next phase's CRUD routes to extend.
        self._router = router
