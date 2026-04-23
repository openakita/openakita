"""avatar-studio — DashScope digital human studio (Phase 4 wiring).

Backend entry point. Wires:

- ``AvatarTaskManager``  — sqlite3-backed CRUD for tasks / voices / figures.
- ``AvatarDashScopeClient`` — DashScope async client (hot reload via
  ``read_settings`` callable).
- ``run_pipeline``        — 8-step linear orchestrator, spawned per task as a
  background ``asyncio.Task`` via ``api.spawn_task``.
- ``add_upload_preview_route`` — vendored upload preview helper (issue #479).

Routes (24):

  Tasks      POST /tasks            POST /cost-preview
             GET  /tasks            POST /tasks/{id}/cancel
             GET  /tasks/{id}       POST /tasks/{id}/retry
             DELETE /tasks/{id}
  Voices     GET  /voices           POST /voices
             DELETE /voices/{id}    POST /voices/{id}/sample
  Figures    GET  /figures          POST /figures
             DELETE /figures/{id}
  System     GET  /settings         PUT  /settings   GET /healthz
             POST /test-connection  POST /capabilities/probe
             POST /cleanup
  Upload     POST /upload           GET  /uploads/{rel_path:path}
  Catalog    GET  /catalog          GET  /prompt-guide
  AI Helper  POST /ai/compose-prompt

Pixelle hardening
-----------------

- C5  Missing API key on ``on_load`` is a WARN (red dot in UI), not a raise.
- C6  Pydantic models reject unknown fields with a 422 + ``ignored`` list.
- A10 ``read_settings`` callable threaded into the DashScope client; PUT
       /settings calls ``client.update_api_key`` for the immediate path.
- C3  ``api.broadcast_ui_event`` is the SSE channel; pipeline ``emit``
       wraps it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from avatar_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    AvatarDashScopeClient,
)
from avatar_models import (
    DEFAULT_COST_THRESHOLD_CNY,
    MODES_BY_ID,
    build_catalog,
    estimate_cost,
)
from avatar_pipeline import (
    AvatarPipelineContext,
    run_pipeline,
)
from avatar_studio_inline.oss_uploader import (
    OssNotConfigured,
    OssUploader,
    OssUploadError,
)
from avatar_studio_inline.upload_preview import (
    add_upload_preview_route,
    build_preview_url,
)
from avatar_studio_inline.vendor_client import VendorError
from avatar_task_manager import AvatarTaskManager
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


PLUGIN_ID = "avatar-studio"
SETTINGS_KEY = "avatar_studio_settings"


def _read_audio_duration(path: Path) -> float | None:
    """Best-effort audio duration probe (mp3 / wav / opus / m4a).

    Used by the pipeline's TTS step to capture cosyvoice-v2's *actual*
    output length so step 6 can pass it to wan2.2-s2v as the
    ``duration`` parameter (DashScope bills per generated second). A
    failed read returns ``None`` and the pipeline falls back to its
    5-second placeholder — which is fine for cost preview but produces
    a video that's exactly 5 s long regardless of how much was said,
    so the placeholder really is a fallback only.
    """
    try:
        from mutagen import File as MutagenFile  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - declared in requirements.txt
        return None
    try:
        info = MutagenFile(str(path))
        if info is None or not getattr(info, "info", None):
            return None
        return float(info.info.length)
    except Exception as e:  # noqa: BLE001 - never break the pipeline
        logger.info("avatar-studio: audio duration probe failed for %s: %s", path, e)
        return None


def _safe_rmtree_path(path: Path) -> None:
    """Remove ``path`` recursively, swallowing FileNotFound and PermErrors.

    Used when scrubbing per-task directories on delete/cleanup — a stuck
    file handle on Windows shouldn't bubble up as a 500 to the UI.
    """
    import shutil
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.info("avatar-studio: rmtree skipped for %s: %s", path, e)


# ─── Pydantic request bodies (Pixelle C6 — strict, 422-on-unknown) ─────


def _strict_model(*, populate_by_name: bool = True) -> ConfigDict:
    """Reject unknown fields (Pixelle C6: never silently drop params)."""
    return ConfigDict(extra="forbid", populate_by_name=populate_by_name)


class CreateTaskBody(BaseModel):
    model_config = _strict_model()

    mode: str
    prompt: str = ""
    text: str = ""
    voice_id: str = ""
    resolution: str = "480P"
    aspect: str = "16:9"
    duration: int | None = None
    mode_pro: bool = False
    watermark: bool = False
    seed: int = -1
    compose_prompt: str = ""
    compose_size: str = ""
    use_qwen_vl: bool = False
    qwen_token_estimate: int = 600
    ref_image_count: int = 1
    video_duration_sec: float | None = None
    audio_duration_sec: float | None = None
    text_chars: int | None = None
    figure_id: str = ""
    cost_approved: bool = False
    assets: dict[str, str] = Field(default_factory=dict)


class CostPreviewBody(BaseModel):
    model_config = _strict_model()

    mode: str
    prompt: str = ""
    text: str = ""
    voice_id: str = ""
    resolution: str = "480P"
    duration: int | None = None
    mode_pro: bool = False
    use_qwen_vl: bool = False
    qwen_token_estimate: int = 600
    ref_image_count: int = 1
    video_duration_sec: float | None = None
    audio_duration_sec: float | None = None
    text_chars: int | None = None


class CreateVoiceBody(BaseModel):
    model_config = _strict_model()

    label: str
    # Local relative path returned by POST /upload (kept so a future
    # cleanup can reach the source on disk). Optional because cosyvoice
    # only needs the OSS URL — a manual API caller may pass just the URL.
    source_audio_path: str = ""
    # ``source_audio_oss_url`` is the *required* trigger for actual
    # cloning — POST /voices used to be a pure DB insert, which produced
    # rows with no real DashScope voice id and a "Voice not found" 400
    # the moment anyone tried to use them. Now the route invokes
    # ``client.clone_voice(sample_url=this)`` and persists the returned id.
    source_audio_oss_url: str = ""
    # If the caller already has a DashScope voice id (e.g. created via
    # the bailian console) they can skip the cloning step by passing it
    # here — we then just write the row.
    dashscope_voice_id: str = ""
    sample_url: str | None = None
    language: str = "zh-CN"
    gender: str = "unknown"


class CreateFigureBody(BaseModel):
    model_config = _strict_model()

    label: str
    image_path: str
    preview_url: str
    # ``oss_url`` and ``oss_key`` come from POST /upload's response.
    # When OSS is configured they're populated; when not, they're empty
    # and the figure ends up in 'pending' detect_status forever (the
    # /figures POST handler emits a clear hint in that case).
    oss_url: str = ""
    oss_key: str = ""
    detect_pass: bool = False
    detect_humanoid: bool = False
    detect_message: str | None = None


class SettingsBody(BaseModel):
    model_config = _strict_model()

    api_key: str | None = None
    base_url: str | None = None
    timeout: float | None = None
    timeout_sec: float | None = None  # UI-friendly alias.
    max_retries: int | None = None
    cost_threshold: float | None = None
    cost_threshold_cny: float | None = None  # UI-friendly alias (CNY-suffixed).
    auto_archive: bool | None = None
    retention_days: int | None = None
    default_resolution: str | None = None
    default_voice: str | None = None
    # ── Aliyun OSS — required for any task that uploads image/video/audio.
    # All four fields must be present together; partial config is rejected at
    # use-time with a 400 + "open Settings → OSS" hint (see OssNotConfigured
    # in avatar_studio_inline/oss_uploader.py). Empty string means "clear",
    # exactly like api_key.
    oss_endpoint: str | None = None
    oss_bucket: str | None = None
    oss_access_key_id: str | None = None
    oss_access_key_secret: str | None = None
    oss_path_prefix: str | None = None  # default "avatar-studio"


class CleanupBody(BaseModel):
    model_config = _strict_model()

    retention_days: int = 30


class TestConnectionBody(BaseModel):
    """Body for ``POST /test-connection``.

    Both fields are optional so the UI can send ``{}`` to probe the
    currently-saved key, or ``{"api_key": "sk-…"}`` to probe a key the
    user just typed but hasn't persisted yet.
    """

    model_config = _strict_model()

    api_key: str | None = None


class AiComposePromptBody(BaseModel):
    model_config = _strict_model()

    ref_images_url: list[str] = Field(default_factory=list)
    hint: str = ""
    user_intent: str = ""


# ─── Static prompt-guide content (mirrors tongyi-image's GET /prompt-guide) ──
# Hand-curated digital-human writing tips. Returned verbatim so the React
# layer can render six <Collapsible> chapters without a network round-trip
# to LLMs. Update keys here only — never inline strings into the React side.

_PROMPT_GUIDE_ZH: dict[str, Any] = {
    "intro": (
        "数字人工作室的输出质量极大依赖输入素材与口播文本。下方按业务场景"
        "整理常用配方、最佳实践与避坑清单，可点击展开。"
    ),
    "mode_formulas": {
        "photo_speak": (
            "正面单人证件照（建议 ≥ 512px，光线均匀） + 口播文本（≤ 1000 字，"
            "建议每 30 字加一个标点） + 系统音色或克隆音色 → wan2.2-s2v"
        ),
        "video_relip": (
            "≤ 30 秒原视频（人物正脸时长 ≥ 60%） + 全新口播音频/文本（音频时长"
            "≤ 视频时长） → videoretalk（仅替换嘴型，保留头部姿态与表情）"
        ),
        "video_reface": (
            "≤ 30 秒动作视频 + 1 张新人物正面照（无遮挡） → wan2.2-animate-mix"
            "（保留场景与肢体动作，仅替换主角面部与发型）。pro 档位价格 2× "
            "但贴合度更高，建议商用首选。"
        ),
        "avatar_compose": (
            "1-3 张参考图（人物 / 服饰 / 场景，建议人物图放第一张） + 中文融合"
            "指令（建议 ≤ 60 字，可让 qwen-vl-max 帮你写） + 口播文本 → "
            "wan2.5-i2i-preview 合成新形象 → wan2.2-s2v 生成口播视频"
        ),
    },
    "best_practices": [
        "口播文本避免英文专有名词单独成句，cosyvoice-v2 可能逐字母拼读。",
        "人物正面照请去除墨镜、口罩、刘海过厚等遮挡，否则 s2v-detect 预检不通过。",
        "16:9 视频建议主体居中且头肩占比 ≥ 40%，避免脸部被裁切。",
        "克隆音色上传 5–30 秒安静的纯人声样本（无背景音乐），效果最佳。",
        "使用 1080P 时单价大幅上升（约 2.5×），如非商用建议 720P 起步。",
    ],
    "voice_tips": [
        "知性温暖：longxiaochun_v2（默认）、longwan_v2",
        "新闻播报：longmiao_v2、longxiaoxuan_v2",
        "活泼少女：longxiaobai_v2、longxiaohui_v2",
        "沉稳男声：longxiaocheng_v2、longhan_v2、longhua_v2",
    ],
    "video_reface_tips": [
        "动作幅度大、频繁转身的视频效果差；建议正面对话/演讲类素材。",
        "新人物图与原视频主角性别、年龄相近时贴合度更高。",
        "若效果不理想，可切换 pro 档位（wan-pro）二次生成。",
    ],
    "compose_examples": [
        '"把第二张图的服饰穿到第一张人物身上，背景换成第三张的咖啡馆"',
        '"参考第一张人物的五官，融合第二张的发型，输出半身像"',
        '"将三张图融合为一张电商套图，主角居中，左右各一件商品"',
    ],
    "faq": [
        ("任务一直 pending？", "请到「设置」检查 API Key 是否填对，或在「任务」里取消后重新提交。"),
        ("提示「内容审核未通过」？", "口播文本或图像中含敏感信息，请修改后重试。"),
        ("提示「dependency 错误」？", "本机缺少 dashscope SDK，请运行 `pip install dashscope`。"),
        ("如何降低费用？", "选择 480P 而非 720P/1080P；缩短口播文本；不使用 pro 档位。"),
    ],
}

_PROMPT_GUIDE_EN: dict[str, Any] = {
    "intro": (
        "Avatar Studio output quality depends heavily on input assets and "
        "speech text. The chapters below summarise recipes, best practices, "
        "and pitfalls — click to expand."
    ),
    "mode_formulas": {
        "photo_speak": (
            "Front-facing portrait (≥ 512px, even lighting) + speech text "
            "(≤ 1000 chars) + system or cloned voice → wan2.2-s2v"
        ),
        "video_relip": (
            "≤ 30s source video (face visible ≥ 60%) + new audio/text "
            "(audio ≤ video duration) → videoretalk (lips only)"
        ),
        "video_reface": (
            "≤ 30s motion video + 1 new portrait → wan2.2-animate-mix "
            "(scene + motion preserved, face replaced). pro tier costs 2×"
        ),
        "avatar_compose": (
            "1–3 references (portrait / outfit / scene) + Chinese merge prompt "
            "(qwen-vl-max can draft it) + speech text → wan2.5-i2i-preview → wan2.2-s2v"
        ),
    },
    "best_practices": [
        "Avoid English jargon as a standalone sentence — cosyvoice-v2 may spell letters.",
        "Remove sunglasses / heavy fringe — s2v-detect will fail otherwise.",
        "For 16:9, keep head-and-shoulders ≥ 40% to avoid face cropping.",
        "Use a 5–30s clean voice sample (no music) for cloning.",
        "1080P costs ~2.5× of 720P — start with 720P unless commercial.",
    ],
    "voice_tips": [
        "Warm: longxiaochun_v2 (default), longwan_v2",
        "Newscast: longmiao_v2, longxiaoxuan_v2",
        "Bright girl: longxiaobai_v2, longxiaohui_v2",
        "Calm male: longxiaocheng_v2, longhan_v2, longhua_v2",
    ],
    "video_reface_tips": [
        "Avoid large pose swings; prefer talking-head sources.",
        "Match gender/age of the new portrait to the original lead.",
        "Re-run on the pro tier if the result feels off.",
    ],
    "compose_examples": [
        '"Apply the outfit from image 2 to person in image 1, set scene to image 3"',
        '"Keep image-1 face, blend image-2 hairstyle, output half-body shot"',
        '"Compose three images into an e-commerce banner, person centred"',
    ],
    "faq": [
        ("Task stays pending?", "Verify the API Key in Settings, or cancel & resubmit."),
        ("'Moderation failed'?", "Sensitive content in text/image; rewrite and retry."),
        ("'Dependency error'?", "dashscope SDK missing — run `pip install dashscope`."),
        ("How to cut cost?", "Pick 480P; shorten text; skip pro tier."),
    ],
}


# ─── Plugin ────────────────────────────────────────────────────────────


class Plugin(PluginBase):
    """SDK 0.7.0-compatible entry point for avatar-studio."""

    # ── lifecycle ─────────────────────────────────────────────────────

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._data_dir = Path(api.get_data_dir() or Path.cwd() / ".avatar-studio")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._tm = AvatarTaskManager(self._data_dir / "avatar_studio.db")
        self._client = AvatarDashScopeClient(read_settings=self._read_settings)
        # Single ``OssUploader`` instance — it lazy-reads settings on every
        # call so a key rotation in Settings takes effect without reload.
        self._oss = OssUploader(read_settings=self._read_settings)
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}
        # Background ``wan2.2-s2v-detect`` jobs spawned from POST /figures.
        # Tracked separately from pipeline polls so DELETE /figures/{id}
        # can cancel a still-running probe before the row goes away,
        # and on_unload can drain them cleanly.
        self._figure_detect_tasks: dict[str, asyncio.Task[Any]] = {}

        # Validate settings — C5: warn, never raise.
        cfg = self._load_settings()
        if not cfg.get("api_key"):
            api.log(
                "avatar-studio: DashScope API Key not configured — set it in "
                "Settings before submitting any task",
                level="warning",
            )

        router = APIRouter()
        # Upload preview route (issue #479).
        add_upload_preview_route(
            router,
            base_dir=self._data_dir / "uploads",
        )
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools(self._tool_definitions(), handler=self._handle_tool)

        api.spawn_task(self._async_init(), name=f"{PLUGIN_ID}:init")
        api.log("avatar-studio loaded (4 modes, 16 routes, 9 tools)")

    async def _async_init(self) -> None:
        await self._tm.init()
        # Resume any detect probe that was interrupted by a process
        # restart — without this, the row is stuck on 'pending' forever
        # because the in-memory task that would have updated it is gone.
        try:
            pending = await self._tm.list_pending_figures()
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar-studio: resume figure detects failed: %s", exc)
            pending = []
        for fig in pending:
            # Prefer the OSS URL — local preview URLs are only useful
            # for the UI img tag, DashScope can't fetch them. If a row
            # has no oss_url at all the detect helper will mark it
            # 'skipped' with a helpful message instead of looping.
            url = (fig.get("oss_url") or "").strip() or fig.get("preview_url") or ""
            self._spawn_figure_detect(fig["id"], url)

    async def on_unload(self) -> None:
        # Cancel every in-flight pipeline task.
        for tid, t in list(self._poll_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning("avatar-studio: pipeline %s cleanup error: %s", tid, exc)
        # Same drain logic for figure pre-check probes — these are short
        # (≤30s) so we await each one rather than fire-and-forget.
        for fid, t in list(self._figure_detect_tasks.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning("avatar-studio: detect %s cleanup error: %s", fid, exc)
        try:
            await self._tm.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar-studio: tm close error: %s", exc)

    # ── figure pre-check ──────────────────────────────────────────────

    def _spawn_figure_detect(self, fig_id: str, image_url: str) -> None:
        """Schedule a background ``wan2.2-s2v-detect`` for a figure row.

        Idempotent: if a probe is already in-flight for this figure id we
        leave it alone (prevents the on_load resume from doubling up with
        a manual re-trigger). The closure captures ``fig_id`` so the
        finally-clause can pop the right key — never iterate over the
        dict here.
        """
        if fig_id in self._figure_detect_tasks and not self._figure_detect_tasks[fig_id].done():
            return
        coro = self._run_figure_detect(fig_id, image_url)
        task = self._api.spawn_task(coro, name=f"{PLUGIN_ID}:detect:{fig_id}")
        self._figure_detect_tasks[fig_id] = task

    async def _run_figure_detect(self, fig_id: str, image_url: str) -> None:
        # Decide upfront whether we *can* run a probe at all. Without an
        # API key DashScope returns 401 immediately, which is misleading
        # noise on the figure card — surface 'skipped' instead so the user
        # knows to configure Settings rather than retry forever.
        if not image_url:
            await self._tm.update_figure_detect(
                fig_id, status="fail", message="missing preview_url"
            )
            self._figure_detect_tasks.pop(fig_id, None)
            return
        if not self._client.has_api_key():
            await self._tm.update_figure_detect(
                fig_id,
                status="skipped",
                message="API Key 未配置，已跳过预检；填入后请重新上传",
            )
            self._figure_detect_tasks.pop(fig_id, None)
            return
        try:
            result = await self._client.face_detect(image_url)
            await self._tm.update_figure_detect(
                fig_id,
                status="pass",
                message="OK",
                humanoid=bool(result.get("humanoid")),
            )
        except asyncio.CancelledError:
            # User clicked 删除 mid-probe; the DELETE handler also wipes
            # the row, so we deliberately do NOT touch the DB here. Just
            # let the cancellation propagate.
            raise
        except VendorError as e:
            # Reuse the same hint logic the pipeline uses so the message
            # the user sees on the card matches what they'd get on a real
            # task failure with the same input.
            msg = f"[{e.kind}] {str(e)[:280]}"
            await self._tm.update_figure_detect(fig_id, status="fail", message=msg)
        except Exception as e:  # noqa: BLE001 - never bubble out of detect
            logger.exception("figure-detect %s crashed", fig_id)
            await self._tm.update_figure_detect(
                fig_id, status="fail", message=f"unexpected: {e!s}"[:500]
            )
        finally:
            self._figure_detect_tasks.pop(fig_id, None)

    # ── settings ──────────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, Any]:
        cfg = self._api.get_config() or {}
        merged: dict[str, Any] = {
            "api_key": "",
            "base_url": DASHSCOPE_BASE_URL_BJ,
            "timeout": 60.0,
            "max_retries": 2,
            "cost_threshold": DEFAULT_COST_THRESHOLD_CNY,
            "auto_archive": False,
            "retention_days": 30,
            "default_resolution": "480P",
            "default_voice": "longxiaochun_v2",
            # OSS — empty string means "not configured yet"; all four
            # *must* be filled in for any task to actually run.
            "oss_endpoint": "",
            "oss_bucket": "",
            "oss_access_key_id": "",
            "oss_access_key_secret": "",
            "oss_path_prefix": "avatar-studio",
        }
        for k in list(merged):
            if k in cfg and cfg[k] not in (None, ""):
                merged[k] = cfg[k]
        # Aliases — accept both ``cost_threshold`` and ``cost_threshold_cny``
        # so the UI can use the suffixed name without breaking older
        # tooling. Same for ``timeout`` / ``timeout_sec``.
        if cfg.get("cost_threshold_cny") not in (None, ""):
            merged["cost_threshold"] = cfg["cost_threshold_cny"]
        if cfg.get("timeout_sec") not in (None, ""):
            merged["timeout"] = cfg["timeout_sec"]
        # Mirror back so the UI reads consistent names regardless of which
        # alias was used to write the value.
        merged["cost_threshold_cny"] = merged["cost_threshold"]
        merged["timeout_sec"] = merged["timeout"]
        return merged

    def _read_settings(self) -> dict[str, Any]:
        """Callable threaded into the DashScope client (Pixelle A10)."""
        return self._load_settings()

    # ── tool handler ──────────────────────────────────────────────────

    def _tool_definitions(self) -> list[dict[str, Any]]:
        common_props = {
            "prompt": {"type": "string"},
            "text": {"type": "string"},
            "voice_id": {"type": "string"},
            "resolution": {"type": "string", "enum": ["480P", "720P"]},
            "assets": {"type": "object"},
        }
        return [
            {
                # mode_id may already carry the ``avatar_`` namespace
                # (e.g. ``avatar_compose``); avoid the awkward
                # ``avatar_avatar_compose`` doubling so tool names line
                # up 1:1 with the plugin.json manifest.
                "name": m.id if m.id.startswith("avatar_") else f"avatar_{m.id}",
                "description": f"{m.label_zh} — {m.description_zh}",
                "input_schema": {
                    "type": "object",
                    "properties": common_props,
                    "required": [],
                },
            }
            for m in MODES_BY_ID.values()
        ] + [
            {
                "name": "avatar_voice_create",
                "description": (
                    "克隆一个自定义 cosyvoice-v2 音色。"
                    "需要先 POST /upload 拿到 source_audio_oss_url。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "source_audio_path": {"type": "string"},
                        "source_audio_oss_url": {
                            "type": "string",
                            "description": (
                                "样本音频的公网 URL — DashScope "
                                "VoiceEnrollmentService 会拉取它来训练音色"
                            ),
                        },
                        "language": {"type": "string", "default": "zh-CN"},
                        "gender": {"type": "string", "default": "unknown"},
                    },
                    "required": ["label", "source_audio_oss_url"],
                },
            },
            {
                "name": "avatar_voice_delete",
                "description": "删除自定义音色",
                "input_schema": {
                    "type": "object",
                    "properties": {"voice_id": {"type": "string"}},
                    "required": ["voice_id"],
                },
            },
            {
                "name": "avatar_figure_create",
                "description": (
                    "把一张人像照添加进形象库（自动跑 face-detect）。"
                    "需要先 POST /upload 拿到 oss_url + preview_url。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "image_path": {
                            "type": "string",
                            "description": "POST /upload 返回的 path 字段",
                        },
                        "preview_url": {
                            "type": "string",
                            "description": "本地预览 URL（UI 展示用）",
                        },
                        "oss_url": {
                            "type": "string",
                            "description": (
                                "OSS 签名 URL — DashScope face-detect 用它，"
                                "缺失则形象会停在 skipped 状态"
                            ),
                        },
                    },
                    "required": ["label", "image_path", "preview_url"],
                },
            },
            {
                "name": "avatar_figure_delete",
                "description": "从形象库删除一个人像",
                "input_schema": {
                    "type": "object",
                    "properties": {"figure_id": {"type": "string"}},
                    "required": ["figure_id"],
                },
            },
            {
                "name": "avatar_cost_preview",
                "description": "估算一次任务的费用（不实际发起任务）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": list(MODES_BY_ID.keys()),
                        },
                        **common_props,
                    },
                    "required": ["mode"],
                },
            },
        ]

    async def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "avatar_cost_preview":
            preview = estimate_cost(
                args.get("mode", "photo_speak"),
                args,
                audio_duration_sec=args.get("audio_duration_sec"),
                text_chars=args.get("text_chars"),
            )
            return f"预估费用 {preview['formatted_total']}（{len(preview['items'])} 项）"

        mode_to_tool = {(m if m.startswith("avatar_") else f"avatar_{m}"): m for m in MODES_BY_ID}
        if tool_name in mode_to_tool:
            mode = mode_to_tool[tool_name]
            task = await self._create_task_internal(mode, args)
            return f"任务已创建：{task['id']}（mode={mode}）"

        if tool_name == "avatar_voice_create":
            voice_id = await self._tm.create_custom_voice(
                label=str(args.get("label", "custom")),
                source_audio_path=str(args.get("source_audio_path", "")),
                dashscope_voice_id=str(args.get("dashscope_voice_id", "")),
            )
            return f"音色已创建：{voice_id}"
        if tool_name == "avatar_voice_delete":
            ok = await self._tm.delete_custom_voice(str(args.get("voice_id", "")))
            return "ok" if ok else "not found"
        if tool_name == "avatar_figure_create":
            preview_url = str(args.get("preview_url", ""))
            oss_url = str(args.get("oss_url", "")).strip()
            fig_id = await self._tm.create_figure(
                label=str(args.get("label", "figure")),
                image_path=str(args.get("image_path", "")),
                preview_url=preview_url,
                oss_url=oss_url,
                detect_status="pending" if oss_url else "skipped",
                detect_message=(
                    None if oss_url
                    else "OSS 未配置或 oss_url 缺失，已跳过预检"
                ),
            )
            # Match the REST endpoint's behaviour — the tool flow must
            # also kick off pre-check (only when we have an OSS URL),
            # otherwise figures created via the AI-tool route would stay
            # in 'pending' forever.
            if oss_url:
                self._spawn_figure_detect(fig_id, oss_url)
            return f"形象已创建：{fig_id}（detect_status={'pending' if oss_url else 'skipped'}）"
        if tool_name == "avatar_figure_delete":
            fig_id = str(args.get("figure_id", ""))
            handle = self._figure_detect_tasks.pop(fig_id, None)
            if handle is not None and not handle.done():
                handle.cancel()
            ok = await self._tm.delete_figure(fig_id)
            return "ok" if ok else "not found"
        return f"unknown tool: {tool_name}"

    # ── task helpers ──────────────────────────────────────────────────

    async def _create_task_internal(self, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        if mode not in MODES_BY_ID:
            raise HTTPException(status_code=422, detail=f"unknown mode: {mode}")
        if not self._client.has_api_key():
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_api_key",
                    "message": "DashScope API Key 未配置；请到「设置」填写后再提交任务",
                },
            )
        # OSS is required for any task that flows assets to DashScope.
        # Photo_speak / video_relip / video_reface / avatar_compose all
        # need at least one image_url / video_url / audio_url, and
        # those URLs MUST be public — DashScope cannot fetch our
        # ``/api/plugins/...`` route. Reject early with a clear pointer
        # to Settings → OSS rather than letting the pipeline blow up
        # 30 seconds in with a confusing 422.
        if not self._oss.is_configured():
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "oss_not_configured",
                    "message": (
                        "Aliyun OSS 未配置；DashScope 无法 fetch 本地 URL。"
                        "请到「设置 → 阿里云 OSS」填入 endpoint / bucket / "
                        "access key / secret 后重试。"
                    ),
                },
            )

        # Resolve `figure_id` (if supplied) into a real public URL so the
        # FigurePicker UI doesn't have to know about OSS — it just sends
        # the figure_id and we look up the OSS URL persisted on POST
        # /figures. Without this, UI would have to re-upload the figure
        # image every time the user picks it from the library.
        params = await self._resolve_figure_id(dict(params))

        # Pre-flight cost preview so the API consumer (UI / tool / curl)
        # always sees the breakdown alongside the new task row.
        preview = estimate_cost(
            mode,
            params,
            audio_duration_sec=params.get("audio_duration_sec"),
            text_chars=params.get("text_chars"),
        )

        task_id = await self._tm.create_task(
            mode=mode,
            prompt=str(params.get("prompt", "")),
            params=dict(params),
            asset_paths={k: str(v) for k, v in (params.get("assets") or {}).items()},
            cost_breakdown=dict(preview),
        )

        ctx = AvatarPipelineContext(task_id=task_id, mode=mode, params=dict(params))
        ctx.cost_approved = bool(params.get("cost_approved"))
        # Stash the OSS upload helper on ctx.params so the TTS step can
        # publish the synthesised audio without importing OssUploader
        # (keeps the pipeline layer free of Aliyun-specific imports).
        ctx.params["_oss_upload_audio"] = self._make_oss_upload_audio(task_id)
        # Spawn the pipeline; tracking handle so on_unload can cancel.
        self._poll_tasks[task_id] = self._api.spawn_task(
            self._run_one_pipeline(ctx),
            name=f"{PLUGIN_ID}:pipeline:{task_id}",
        )

        row = await self._tm.get_task(task_id)
        return row or {"id": task_id, "mode": mode, "status": "pending"}

    def _make_oss_upload_audio(self, task_id: str) -> Any:
        """Return ``async (path, fname) -> public_url`` bound to a task.

        Keeps OSS access tightly scoped and eliminates the temptation to
        let pipeline code reach for ``self._oss`` directly.
        """
        async def _upload(path: Path, fname: str) -> str:
            key = self._oss.build_object_key(
                scope=f"tasks/{task_id}", filename=fname,
            )
            return await asyncio.to_thread(
                self._oss.upload_file, path, key=key
            )
        return _upload

    async def _run_one_pipeline(self, ctx: AvatarPipelineContext) -> None:
        try:
            await run_pipeline(
                ctx,
                tm=self._tm,
                client=self._client,
                emit=self._emit,
                plugin_id=PLUGIN_ID,
                base_data_dir=self._data_dir,
                # Pass the duration helper so step 4 stores the *real*
                # cosyvoice-v2 audio length (the form's
                # ``audio_duration_sec`` is only a UI-side estimate
                # used for the cost gate; s2v needs the actual length).
                get_audio_duration=_read_audio_duration,
            )
        except Exception:
            logger.exception("avatar-studio: pipeline crashed for task %s", ctx.task_id)
        finally:
            self._poll_tasks.pop(ctx.task_id, None)

    async def _resolve_figure_id(self, params: dict[str, Any]) -> dict[str, Any]:
        """Inline the OSS URL of a chosen figure into ``assets.image_url``.

        FigurePicker only sends ``figure_id`` — pipeline code never
        needs to know that figures exist. The figure row carries
        ``oss_url`` (set at POST /figures time when OSS was configured);
        we copy that into the canonical ``assets.image_url`` slot,
        overriding any local URL the form might have left there.

        If the figure was created BEFORE OSS was configured (or if the
        OSS upload failed at the time), ``oss_url`` will be empty and
        we leave assets untouched — the pipeline's URL-shape guard
        then raises a 422 with a clear "re-upload this figure" hint.
        """
        fid = str(params.get("figure_id") or "").strip()
        if not fid:
            return params
        row = await self._tm.get_figure(fid)
        if not row:
            return params
        oss_url = str(row.get("oss_url") or "").strip()
        if not oss_url:
            # No public URL on file — leave assets alone so the pipeline
            # guard surfaces the right error to the user instead of us
            # silently swapping in a stale local URL.
            return params
        assets = dict(params.get("assets") or {})
        # Both fields are valid figure consumers; pick by mode at write
        # time so video_reface picks up the figure-as-target, while
        # photo_speak picks it up as the portrait.
        mode = str(params.get("mode") or "")
        if mode == "avatar_compose":
            existing = assets.get("ref_images_url") or []
            if isinstance(existing, str):
                existing = [existing] if existing else []
            assets["ref_images_url"] = list(existing) + [oss_url]
        else:
            assets["image_url"] = oss_url
        params["assets"] = assets
        return params

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._api.broadcast_ui_event(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar-studio: emit %s failed: %s", event, exc)

    # ── routes ────────────────────────────────────────────────────────

    def _register_routes(self, router: APIRouter) -> None:
        # Tasks ───────────────────────────────────────────────────────

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody) -> dict[str, Any]:
            params = body.model_dump()
            mode = params.pop("mode")
            task = await self._create_task_internal(mode, params)
            return {"ok": True, "task": task}

        @router.get("/tasks")
        async def list_tasks(
            status: str | None = None,
            mode: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> dict[str, Any]:
            tasks = await self._tm.list_tasks(status=status, mode=mode, limit=limit, offset=offset)
            return {"ok": True, "tasks": tasks, "total": len(tasks)}

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            return {"ok": True, "task": row}

        @router.delete("/tasks/{task_id}")
        async def delete_task(task_id: str) -> dict[str, Any]:
            handle = self._poll_tasks.get(task_id)
            if handle and not handle.done():
                handle.cancel()
            ok = await self._tm.delete_task(task_id)
            # Wipe the per-task directory too — previously DELETE only
            # nuked the DB row and left audio/video on disk forever,
            # so the data dir grew unbounded even when the user kept
            # cleaning up the task list. Failure here is non-fatal.
            _safe_rmtree_path(self._data_dir / "tasks" / task_id)
            return {"ok": ok}

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            if row.get("dashscope_id"):
                self._client.mark_cancelled(str(row["dashscope_id"]))
                await self._client.cancel_task(str(row["dashscope_id"]))
            self._client.mark_cancelled(task_id)
            handle = self._poll_tasks.get(task_id)
            if handle and not handle.done():
                handle.cancel()
            await self._tm.update_task_safe(task_id, status="cancelled")
            return {"ok": True}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str) -> dict[str, Any]:
            row = await self._tm.get_task(task_id)
            if not row:
                raise HTTPException(status_code=404, detail="task not found")
            params = row.get("params") or {}
            mode = row.get("mode") or "photo_speak"
            task = await self._create_task_internal(mode, params)
            return {"ok": True, "task": task}

        # Cost preview ────────────────────────────────────────────────

        @router.post("/cost-preview")
        async def cost_preview(body: CostPreviewBody) -> dict[str, Any]:
            d = body.model_dump()
            mode = d.pop("mode")
            preview = estimate_cost(
                mode,
                d,
                audio_duration_sec=d.get("audio_duration_sec"),
                text_chars=d.get("text_chars"),
            )
            return {"ok": True, "preview": preview}

        # Voices ──────────────────────────────────────────────────────

        @router.get("/voices")
        async def list_voices() -> dict[str, Any]:
            from avatar_models import SYSTEM_VOICES

            sys_rows = [{**v.to_dict(), "is_system": True} for v in SYSTEM_VOICES]
            custom_rows = [{**row, "is_system": False} for row in await self._tm.list_voices()]
            return {"ok": True, "voices": sys_rows + custom_rows}

        @router.post("/voices")
        async def create_voice(body: CreateVoiceBody) -> dict[str, Any]:
            data = body.model_dump()
            ds_voice_id = (data.get("dashscope_voice_id") or "").strip()
            sample_oss = (data.get("source_audio_oss_url") or "").strip()

            # Three accepted modes (in priority order):
            #   1. Caller already has a DashScope voice id → skip clone,
            #      just persist the row (admin-style import).
            #   2. Caller supplied an OSS sample URL → call
            #      VoiceEnrollmentService.create_voice and persist the
            #      returned id.
            #   3. Neither → reject with a clear 400. We refuse to
            #      pre-create empty rows the way the old code did,
            #      because that's exactly what produced the
            #      "音色克隆只是塞了条假数据" bug.
            if not ds_voice_id and not sample_oss:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "missing_clone_input",
                        "message": (
                            "需要 source_audio_oss_url（先通过 POST /upload "
                            "拿到 oss_url）或已存在的 dashscope_voice_id"
                        ),
                    },
                )
            if not ds_voice_id:
                if not self._oss.is_configured():
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "oss_not_configured",
                            "message": "克隆音色需要先配置 OSS（DashScope 拉样本）",
                        },
                    )
                try:
                    res = await self._client.clone_voice(
                        sample_url=sample_oss,
                        prefix="avatar",
                        language=("zh" if data["language"].startswith("zh") else "en"),
                    )
                except VendorError as e:
                    raise HTTPException(
                        status_code=400,
                        detail={"kind": e.kind, "message": str(e)},
                    ) from e
                ds_voice_id = res["voice_id"]

            voice_id = await self._tm.create_custom_voice(
                label=data["label"],
                source_audio_path=data.get("source_audio_path") or "",
                dashscope_voice_id=ds_voice_id,
                sample_url=data.get("sample_url"),
                language=data["language"],
                gender=data["gender"],
            )
            return {
                "ok": True,
                "voice_id": voice_id,
                "dashscope_voice_id": ds_voice_id,
            }

        @router.delete("/voices/{voice_id}")
        async def delete_voice(voice_id: str) -> dict[str, Any]:
            ok = await self._tm.delete_custom_voice(voice_id)
            return {"ok": ok}

        @router.post("/voices/{voice_id}/sample")
        async def synth_sample(
            voice_id: str, text: str = "你好，欢迎使用数字人工作室"
        ) -> dict[str, Any]:
            # voice_id can be either:
            #   - a system voice id (longxiaochun_v2 etc.) — used directly
            #   - an internal vc_xxxxxxxx id from create_custom_voice — must
            #     resolve to the DashScope voice id stored on the row
            #     (otherwise cosyvoice-v2 returns "voice not found").
            ds_voice_id = voice_id
            if voice_id.startswith("vc_"):
                row = await self._tm.get_voice(voice_id)
                if row and row.get("dashscope_voice_id"):
                    ds_voice_id = str(row["dashscope_voice_id"])
            try:
                res = await self._client.synth_voice(text=text, voice_id=ds_voice_id)
            except VendorError as e:
                raise HTTPException(
                    status_code=400, detail={"kind": e.kind, "message": str(e)}
                ) from e
            # Write under uploads/ so the preview route can serve it —
            # the previous data_dir/voice_samples location was OUTSIDE
            # the route's base_dir and quietly returned 404, leaving the
            # UI with a 0:00/0:00 audio element.
            sample_dir = self._data_dir / "uploads" / "voice_samples"
            sample_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{voice_id}_{uuid.uuid4().hex[:8]}.{res['format']}"
            file_path = sample_dir / fname
            file_path.write_bytes(res["audio_bytes"])
            url = build_preview_url(PLUGIN_ID, f"voice_samples/{fname}")
            # Surface size + magic in the response payload so the UI (or
            # curl) can spot a wrong-codec save without trawling the log.
            head_hex = res["audio_bytes"][:16].hex(" ")
            return {
                "ok": True,
                "url": url,
                "size_bytes": len(res["audio_bytes"]),
                "format": res["format"],
                "magic": head_hex,
            }

        # Figures ─────────────────────────────────────────────────────

        @router.get("/figures")
        async def list_figures() -> dict[str, Any]:
            return {"ok": True, "figures": await self._tm.list_figures()}

        @router.post("/figures")
        async def create_figure(body: CreateFigureBody) -> dict[str, Any]:
            # ``detect_*`` columns are server-managed: the row starts
            # 'pending' and the background probe (below) flips it to
            # 'pass' / 'fail' / 'skipped'. We deliberately ignore any
            # client-supplied detect_* fields so a stale UI can't mark a
            # bad figure as 'pass' just by POSTing detect_pass=true.
            data = body.model_dump()
            oss_url = (data.get("oss_url") or "").strip()
            oss_key = (data.get("oss_key") or "").strip()

            # Without an OSS URL we can't run face_detect against
            # DashScope (cloud cannot fetch our local /api/... URL).
            # Persist the row in a 'skipped' state with a friendly
            # message rather than spinning forever in 'pending'.
            initial_status = "pending" if oss_url else "skipped"
            initial_msg = (
                None if oss_url
                else "OSS 未配置或上传失败，DashScope 无法 fetch 该图片，已跳过预检"
            )

            fig_id = await self._tm.create_figure(
                label=data["label"],
                image_path=data["image_path"],
                preview_url=data["preview_url"],
                oss_url=oss_url,
                oss_key=oss_key,
                detect_status=initial_status,
                detect_message=initial_msg,
            )
            if oss_url:
                # Probe with the OSS URL — this is the URL DashScope
                # will fetch when the figure is later picked from the
                # library, so verifying it now is the most accurate
                # readiness signal we can give the user.
                self._spawn_figure_detect(fig_id, oss_url)
            return {
                "ok": True,
                "figure_id": fig_id,
                "detect_status": initial_status,
                "oss_configured": bool(oss_url),
            }

        @router.delete("/figures/{fig_id}")
        async def delete_figure(fig_id: str) -> dict[str, Any]:
            # Cancel any in-flight detect probe **fire-and-forget**.
            # An earlier version awaited the cancellation here — that
            # made DELETE block for as long as httpx took to unwind the
            # in-flight POST (often 2-3s, occasionally longer if the
            # remote socket was already half-open), and from the user's
            # POV the trash button "did nothing" until then. Now:
            #   1. Wipe the DB row immediately (so the next /figures
            #      poll returns without it).
            #   2. .cancel() the bg task and walk away. If it manages to
            #      reach update_figure_detect() before unwinding, the
            #      UPDATE will hit zero rows — harmless.
            handle = self._figure_detect_tasks.pop(fig_id, None)
            if handle is not None and not handle.done():
                handle.cancel()
            ok = await self._tm.delete_figure(fig_id)
            return {"ok": ok}

        # System ──────────────────────────────────────────────────────

        @router.get("/settings")
        async def get_settings() -> dict[str, Any]:
            # Echo the api_key back as-is. The Settings tab needs to be able
            # to display it (gated behind a 「显示」 toggle that defaults to
            # masked) so the user can both verify what was saved and copy it
            # out if they're rotating keys. Anyone who can call this endpoint
            # already has the host-issued plugin token, so masking the key
            # here didn't add real defense-in-depth — it only broke the
            # 'click 保存 then field empties' UX without protecting anything.
            cfg = self._load_settings()
            cfg["has_api_key"] = bool(cfg.get("api_key"))
            # Single source of truth for the UI's OSS banner: derived,
            # never persisted, so editing one field at a time can't push
            # us into a half-true state. ``oss_secret_set`` echoes back a
            # bool (not the secret) so the form can render a 「已保存」
            # badge without leaking the value.
            cfg["oss_configured"] = self._oss.is_configured()
            cfg["oss_secret_set"] = bool(str(cfg.get("oss_access_key_secret") or "").strip())
            return {"ok": True, "config": cfg}

        @router.put("/settings")
        async def put_settings(body: SettingsBody) -> dict[str, Any]:
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            self._api.set_config(updates)
            if "api_key" in updates:
                self._client.update_api_key(str(updates["api_key"]))
            return {"ok": True, "config": self._load_settings()}

        @router.get("/healthz")
        async def healthz() -> dict[str, Any]:
            storage_bytes = 0
            try:
                for p in self._data_dir.rglob("*"):
                    if p.is_file():
                        storage_bytes += p.stat().st_size
            except OSError:
                pass
            # ``api_reachable`` here is a *local* heuristic — "is there a
            # non-empty key on disk?". Settings tab's 测试连接 button uses
            # the dedicated /test-connection probe below, which actually
            # round-trips DashScope. Do not promote this flag to mean
            # "credential is valid" without changing the probe semantics.
            return {
                "ok": True,
                "plugin": PLUGIN_ID,
                "ts": time.time(),
                "has_api_key": self._client.has_api_key(),
                "api_reachable": self._client.has_api_key(),
                "oss_configured": self._oss.is_configured(),
                "in_flight": len(self._poll_tasks),
                "storage": {"bytes_used": storage_bytes, "dir": str(self._data_dir)},
            }

        @router.post("/test-connection")
        async def test_connection(body: TestConnectionBody) -> dict[str, Any]:
            # Optional ``api_key`` in the body lets the user test a freshly
            # typed (but not yet persisted) key — handy when rotating creds.
            # An empty/missing key falls back to whatever's saved on disk.
            probe_key = (body.api_key or "").strip() or None
            res = await self._client.ping_api_key(probe_key)
            return {
                "ok": bool(res.get("ok")),
                "status": res.get("status"),
                "message": res.get("message") or "",
            }

        @router.post("/capabilities/probe")
        async def capabilities_probe() -> dict[str, Any]:
            """Probe DashScope to see which of *our* models the key can
            actually invoke, then roll the per-model verdict up to the
            per-mode level so the Settings tab can display
            「照片说话 ✓ 可用 / 视频换人 ✗ 未开通」 directly.
            """
            if not self._client.has_api_key():
                return {
                    "ok": False,
                    "message": "API Key 未配置；先到上方填写并保存后再检测",
                    "models": [],
                    "modes": [],
                }
            results = await self._client.probe_models()
            by_model: dict[str, dict[str, Any]] = {r["model"]: r for r in results}

            # Each plugin mode depends on a known set of models — keep
            # this map next to the dashscope client constants so a new
            # mode added to MODES_BY_ID doesn't silently drop off the
            # availability panel. The first miss wins for the verdict
            # so the worst case (denied > unknown > available) bubbles up.
            MODE_DEPS: dict[str, list[str]] = {
                "photo_speak": ["wan2.2-s2v-detect", "wan2.2-s2v", "cosyvoice-v2"],
                "video_relip": ["videoretalk", "cosyvoice-v2"],
                "video_reface": ["wan2.2-animate-mix"],
                "avatar_compose": [
                    "wan2.5-i2i-preview",
                    "wan2.2-s2v-detect",
                    "wan2.2-s2v",
                    "cosyvoice-v2",
                ],
            }
            modes_out: list[dict[str, Any]] = []
            for mode_id, spec in MODES_BY_ID.items():
                deps = [d for d in MODE_DEPS.get(mode_id, []) if d]
                per_dep: list[dict[str, Any]] = []
                worst = "available"
                for dep in deps:
                    r = by_model.get(dep) or {
                        "model": dep, "status": "unknown",
                        "http": None, "message": "未参与本次探测",
                    }
                    per_dep.append(r)
                    if r["status"] == "denied":
                        worst = "denied"
                    elif r["status"] == "unknown" and worst != "denied":
                        worst = "unknown"
                modes_out.append({
                    "id": mode_id,
                    "label_zh": spec.label_zh,
                    "label_en": spec.label_en,
                    "status": worst,
                    "deps": per_dep,
                })
            return {
                "ok": True,
                "ts": time.time(),
                "models": results,
                "modes": modes_out,
            }

        @router.post("/cleanup")
        async def cleanup(body: CleanupBody) -> dict[str, Any]:
            # Snapshot the IDs we're about to drop so we can scrub their
            # task dirs from disk in the same call — without this, the
            # SQLite row vanished but the mp4/wav/png blobs stayed
            # behind, defeating the whole point of the button.
            cutoff_ids = await self._tm.list_expired_task_ids(
                retention_days=body.retention_days,
            )
            removed = await self._tm.cleanup_expired(retention_days=body.retention_days)
            for tid in cutoff_ids:
                _safe_rmtree_path(self._data_dir / "tasks" / tid)
            return {"ok": True, "removed": removed}

        @router.post("/ai/compose-prompt")
        async def ai_compose_prompt(body: AiComposePromptBody) -> dict[str, Any]:
            # Optional helper — uses qwen-vl-max to draft a "merge" prompt
            # for ``avatar_compose``. Returns 200 with empty prompt if the
            # ``caption_with_qwen_vl`` path is unavailable so the UI can
            # fall back to the manual textarea without surfacing an error.
            if not body.ref_images_url:
                raise HTTPException(status_code=422, detail="ref_images_url required")
            try:
                resp = await self._client.caption_with_qwen_vl(
                    image_urls=list(body.ref_images_url),
                    system_prompt=(
                        "你是一名擅长写图片融合指令的设计师。"
                        '请仅返回 JSON：{"prompt": "..."}，不要解释。'
                    ),
                    user_prompt=(
                        body.user_intent
                        or "请基于这些参考图，写一段不超过 60 字的中文指令，"
                        "用于把它们融合成一张主体人像图。"
                    ),
                )
                parsed = (resp or {}).get("parsed") or {}
                prompt = str(parsed.get("prompt") or resp.get("text") or "").strip()
                return {"ok": True, "prompt": prompt}
            except Exception as exc:  # noqa: BLE001
                logger.info("avatar-studio: ai compose prompt fell back: %s", exc)
                return {"ok": True, "prompt": "", "fallback": True}

        @router.get("/catalog")
        async def catalog() -> dict[str, Any]:
            cat = build_catalog()
            return {"ok": True, "catalog": cat.__dict__}

        @router.get("/prompt-guide")
        async def prompt_guide(locale: str = "zh") -> dict[str, Any]:
            # Static, in-process knowledge base for the "提示词指南" tab.
            # Mirrors tongyi-image's GET /prompt-guide so the React layer
            # can reuse the same `<Collapsible>` rendering loop.
            return {
                "ok": True,
                "locale": locale,
                "guide": _PROMPT_GUIDE_ZH if locale != "en" else _PROMPT_GUIDE_EN,
            }

        # Upload ──────────────────────────────────────────────────────

        @router.post("/upload")
        async def upload_file(
            file: UploadFile = File(...),
            kind: str = "image",
        ) -> dict[str, Any]:
            # Two-stage upload:
            #   1. Persist locally so the UI can render a fast preview
            #      (`preview_url`) and so we can re-upload to OSS later
            #      (e.g. on retry / voice clone) without asking the user
            #      to re-pick the file.
            #   2. Push to Aliyun OSS and sign a 6h URL — this is what
            #      we hand to DashScope (`url`, kept under that name for
            #      backwards compatibility with the UI's `form.image.url`
            #      reads, which is what `buildPayload` puts into `assets`).
            #
            # If OSS isn't configured yet we still return 200 with the
            # local artefact so the UI can warn the user inline rather
            # than fail the upload entirely; the task-creation route
            # then refuses with a clear "configure OSS first" message.
            ext = Path(file.filename or "file").suffix.lower().lstrip(".") or "bin"
            subdir = {
                "image": "images",
                "video": "videos",
                "audio": "audios",
            }.get(kind, "other")
            uploads_dir = self._data_dir / "uploads" / subdir
            uploads_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex[:12]}.{ext}"
            content = await file.read()
            local_path = uploads_dir / fname
            local_path.write_bytes(content)
            rel = f"{subdir}/{fname}"
            preview_url = build_preview_url(PLUGIN_ID, rel)

            oss_url: str | None = None
            oss_key: str | None = None
            oss_error: str | None = None
            if self._oss.is_configured():
                try:
                    oss_key = self._oss.build_object_key(
                        scope=f"uploads/{subdir}", filename=fname
                    )
                    oss_url = await asyncio.to_thread(
                        self._oss.upload_file, local_path, key=oss_key
                    )
                except (OssNotConfigured, OssUploadError) as e:
                    oss_error = str(e)
                    logger.warning("avatar-studio: OSS upload failed: %s", e)

            return {
                "ok": True,
                "path": rel,
                "preview_url": preview_url,
                # ``url`` is the OSS signed URL when configured; falls
                # back to the local preview URL only so the UI doesn't
                # crash — buildPayload still puts whatever's here into
                # ``assets``, and a local URL there will trip the
                # task-creation route's OSS guard with a helpful error.
                "url": oss_url or preview_url,
                "oss_url": oss_url,
                "oss_key": oss_key,
                "oss_error": oss_error,
                "size": len(content),
            }

        # Pydantic models above use ``extra="forbid"``; FastAPI then
        # auto-returns 422 with ``loc=[..., 'unknown_field']`` and
        # ``type='extra_forbidden'`` so the UI can detect Pixelle C6
        # silent-drop violations. We don't add a custom handler here
        # because ``APIRouter`` does not expose ``exception_handler`` —
        # only the top-level ``FastAPI`` app does.
