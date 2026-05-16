"""DashScope async client for happyhorse-video — registry-driven dispatch.

Inherits :class:`happyhorse_inline.vendor_client.BaseVendorClient` for
retry / timeout / content-moderation / 9-class ``ERROR_KIND_*``. Adds
business methods that cover all 12 modes via a single backend
(Aliyun DashScope / Bailian).

Endpoint dispatch
-----------------

The client never branches on ``if model_id == ...``; it always asks
:func:`happyhorse_model_registry.by_model_id` for the
``ModelEntry`` and reads ``endpoint_family`` / ``protocol_version`` /
``size_format`` / ``forbidden_params`` from there. This lets a single
``submit_video_synth`` method serve both HappyHorse 1.0 family (new
async, ``resolution: "720P"``, forbids ``with_audio``/``size``/...) and
Wan 2.6 family (legacy async, ``size: "1280*720"``).

Mode → method dispatch table:

==================  =====================================================
mode                method
==================  =====================================================
t2v / i2v / r2v /
video_edit / i2v_end /
video_extend / long_video
                    submit_video_synth (HappyHorse + Wan 2.6/2.7)
photo_speak         face_detect → submit_s2v
video_relip         submit_videoretalk
video_reface        submit_animate_mix
pose_drive          submit_animate_move
avatar_compose      submit_image_edit_wan27 → face_detect → submit_s2v
==================  =====================================================

Auxiliary helpers stay in the same file for cohesion:

- ``synth_voice``        cosyvoice-v2 TTS (lazy-imports the dashscope SDK)
- ``clone_voice``        cosyvoice-v2 custom voice enrollment
- ``caption_with_qwen_vl``  qwen-vl-max prompt-writer
- ``query_task``         polls a DashScope async task; ``_extract_output_url``
                         accepts new and legacy payload shapes
- ``cancel_task``        records id in ``_cancelled`` + best-effort remote
                         cancel via ``POST /tasks/{id}/cancel``

Concurrency: a single ``asyncio.Semaphore(1)`` guards every
``submit_*`` because DashScope async tasks share a per-key "1 in flight"
cap. Polling calls (``query_task``) are NOT gated.

Hot config reload (Pixelle A10): the constructor takes a
``read_settings`` callable. Each call re-reads ``api_key`` / ``timeout``
/ ``base_url``, so saving Settings in the UI takes effect without
re-instantiating the client.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from happyhorse_inline.llm_json_parser import parse_llm_json_object
from happyhorse_inline.vendor_client import (
    ERROR_KIND_AUTH,
    ERROR_KIND_CLIENT,
    ERROR_KIND_SERVER,
    ERROR_KIND_UNKNOWN,
    BaseVendorClient,
    VendorError,
)
from happyhorse_model_registry import (
    REGISTRY_BY_KEY,
    ModelEntry,
    by_model_id,
    default_model,
)

logger = logging.getLogger(__name__)


# ─── DashScope endpoint paths (centralised for vendor URL changes) ───────

DASHSCOPE_BASE_URL_BJ = "https://dashscope.aliyuncs.com"
DASHSCOPE_BASE_URL_SG = "https://dashscope-intl.aliyuncs.com"

# HappyHorse 1.0 + Wan 2.6/2.7 video synthesis (new async + legacy async
# share the same submit path; the body / parameters differ).
PATH_VIDEO_SYNTHESIS = "/api/v1/services/aigc/video-generation/video-synthesis"

# wan2.2-s2v / wan2.2-s2v-detect / videoretalk / wan2.2-animate-* all live
# under image2video on DashScope (legacy async).
PATH_S2V_DETECT = "/api/v1/services/aigc/image2video/face-detect"
PATH_S2V_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_VIDEORETALK_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_ANIMATE_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"

# wan2.5-i2i-preview legacy image-edit endpoint.
PATH_I2I_SUBMIT = "/api/v1/services/aigc/image2image/image-synthesis"

# wan2.7-image / wan2.7-image-pro multimodal-generation endpoint.
PATH_WAN27_IMAGE = "/api/v1/services/aigc/multimodal-generation/generation"
PATH_IMAGE_GEN = "/api/v1/services/aigc/image-generation/generation"
PATH_BG_GEN = "/api/v1/services/aigc/background-generation/generation/"
PATH_OUTPAINT = "/api/v1/services/aigc/image2image/out-painting"

# qwen-vl-max prompt-writer.
PATH_QWEN_VL = "/api/v1/services/aigc/multimodal-generation/generation"

PATH_TASK_QUERY = "/api/v1/tasks/{id}"
PATH_TASK_CANCEL = "/api/v1/tasks/{id}/cancel"

MODEL_S2V_DETECT = "wan2.2-s2v-detect"
MODEL_S2V = "wan2.2-s2v"
MODEL_VIDEORETALK = "videoretalk"
MODEL_ANIMATE_MIX = "wan2.2-animate-mix"
MODEL_ANIMATE_MOVE = "wan2.2-animate-move"
MODEL_I2I_LEGACY = "wan2.5-i2i-preview"
MODEL_WAN27_IMAGE = "wan2.7-image"
MODEL_WAN27_IMAGE_PRO = "wan2.7-image-pro"
MODEL_QWEN_VL = "qwen-vl-max"
MODEL_COSYVOICE_V2 = "cosyvoice-v2"


# ─── Settings shape ────────────────────────────────────────────────────


def make_default_settings() -> dict[str, Any]:
    return {
        "api_key": "",
        "base_url": DASHSCOPE_BASE_URL_BJ,
        "timeout": 60.0,
        "timeout_sec": 60.0,
        "max_retries": 2,
    }


# happyhorse-video specific error kinds (extend the vendor base).
ERROR_KIND_QUOTA = "quota"
ERROR_KIND_DEPENDENCY = "dependency"
ERROR_KIND_ASSET_REJECTED = "asset_rejected"


# Regex that matches HappyHorse's strict 720P / 1080P (uppercase P) format.
_RE_RES_P_UPPER = re.compile(r"^\d+P$")


def _classify_dashscope_body(body: Any, fallback_kind: str) -> str:
    """Promote ``client`` / ``server`` to ``quota`` / ``dependency`` /
    ``asset_rejected`` when the DashScope error payload matches a
    known pattern. Falls back to the input kind otherwise.
    """
    if not isinstance(body, dict):
        return fallback_kind
    code = str(body.get("code") or body.get("error_code") or "").lower()
    msg = str(body.get("message") or body.get("error_message") or "").lower()
    if (
        "quota" in code
        or "balance" in code
        or "insufficient" in msg
        or "balance" in msg
    ):
        return ERROR_KIND_QUOTA
    if (
        "humanoid" in msg
        or ("human" in msg and "detect" in msg)
        or ("face" in msg and "detect" in msg)
    ):
        return ERROR_KIND_ASSET_REJECTED
    if (
        "datainspection" in code.replace(".", "").replace("_", "")
        or ("duration" in msg and ("exceed" in msg or "too long" in msg))
        or "dependency" in code
    ):
        return ERROR_KIND_DEPENDENCY
    return fallback_kind


def _is_async_done(status: str) -> bool:
    return status.upper() in {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}


def _is_async_ok(status: str) -> bool:
    return status.upper() == "SUCCEEDED"


def _is_async_call_unsupported(exc: VendorError) -> bool:
    """Detect DashScope keys that can call image APIs only synchronously."""
    body = exc.body if isinstance(exc.body, dict) else {}
    code = str(body.get("code") or body.get("error_code") or "").lower()
    message = str(body.get("message") or body.get("error_message") or exc).lower()
    return (
        exc.status == 403
        and ("accessdenied" in code or "access denied" in message)
        and "synchronous" in message
        and "asynchronous" in message
    )


# ─── Aspect → W*H helpers (Wan 2.6 legacy size format) ────────────────


def _aspect_to_size(aspect: str, base_height: int = 720) -> str:
    """Convert "16:9" → "1280*720", "9:16" → "720*1280", etc.

    Wan 2.6 expects the dimensions joined by an asterisk (yes, really —
    the API doc spells it "1280*720" not "1280x720"). The first value is
    the *width*, the second is the height.
    """
    if not aspect or ":" not in aspect:
        return f"1280*{base_height}"
    try:
        a, b = aspect.split(":", 1)
        a_v, b_v = float(a), float(b)
        if a_v <= 0 or b_v <= 0:
            return f"1280*{base_height}"
        if a_v >= b_v:
            w = int(round(base_height * a_v / b_v))
            h = base_height
        else:
            h = int(round(base_height * b_v / a_v))
            w = base_height
        return f"{w}*{h}"
    except (TypeError, ValueError):
        return f"1280*{base_height}"


def _resolution_to_height(resolution: str) -> int:
    s = (resolution or "").upper().strip()
    if s.endswith("P"):
        try:
            return int(s.rstrip("P"))
        except ValueError:
            return 720
    return 720


# ─── Client ────────────────────────────────────────────────────────────


ReadSettings = Callable[[], dict[str, Any]]


class HappyhorseDashScopeClient(BaseVendorClient):
    """One client instance per plugin process. All ``submit_*`` calls are
    serialised by ``self._submit_lock`` so we never violate DashScope's
    per-key "1 task in flight" cap. ``query_task`` / ``cancel_task`` are
    not serialised so the pipeline can poll while the next user
    submission queues up legitimately.
    """

    _ASYNC_HEADER: dict[str, str] = {"X-DashScope-Async": "enable"}

    def __init__(
        self,
        read_settings: ReadSettings,
        *,
        max_retries: int = 2,
    ) -> None:
        super().__init__(timeout=60.0, max_retries=max_retries)
        self._read_settings = read_settings
        self._submit_lock = asyncio.Semaphore(1)
        self._cancelled: set[str] = set()
        self._image_async_supported: bool | None = None
        self._last_settings: dict[str, Any] = {}
        # Prime base_url / timeout from settings so the first ``request()``
        # call already has the right URL prefix even if the caller never
        # touches ``auth_headers()`` first.
        self._settings()

    async def request(  # type: ignore[override]
        self,
        method: str,
        path: str,
        **kw: Any,
    ) -> Any:
        # Pixelle A10: re-read Settings before EVERY request so a Settings
        # change (api_key / base_url / timeout) takes effect immediately,
        # even mid-pipeline.
        self._settings()
        return await super().request(method, path, **kw)

    # ── settings + auth ───────────────────────────────────────────────

    def _settings(self) -> dict[str, Any]:
        try:
            cur = self._read_settings() or {}
        except Exception as e:  # noqa: BLE001 — never raise from read
            logger.warning("read_settings raised %s; falling back to defaults", e)
            cur = {}
        merged = make_default_settings()
        merged.update({k: v for k, v in cur.items() if v not in (None, "")})
        try:
            self.timeout = float(merged.get("timeout_sec") or merged.get("timeout") or 60.0)
        except (TypeError, ValueError):
            pass
        try:
            self.max_retries = max(0, int(merged.get("max_retries") or self.max_retries))
        except (TypeError, ValueError):
            pass
        self.base_url = str(merged.get("base_url") or DASHSCOPE_BASE_URL_BJ)
        self._last_settings = merged
        return merged

    def auth_headers(self) -> dict[str, str]:
        s = self._settings()
        api_key = str(s.get("api_key") or "").strip()
        return {
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "Content-Type": "application/json",
        }

    def update_api_key(self, api_key: str) -> None:
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        self._last_settings["api_key"] = api_key.strip()

    def has_api_key(self) -> bool:
        return bool(self._settings().get("api_key"))

    async def ping_api_key(self, api_key: str | None = None) -> dict[str, Any]:
        """Cheap liveness probe — hit DashScope's OpenAI-compatible
        ``/v1/models`` endpoint with the supplied key and return
        ``{ok, status, message}``.
        """
        try:
            import httpx
        except ImportError as e:
            return {"ok": False, "status": None, "message": f"httpx missing: {e}"}

        key = api_key if api_key is not None else self._settings().get("api_key") or ""
        key = str(key).strip()
        if not key:
            return {"ok": False, "status": None, "message": "API Key is empty"}

        settings = self._settings()
        base_url = str(settings.get("base_url") or DASHSCOPE_BASE_URL_BJ).rstrip("/")
        url = f"{base_url}/compatible-mode/v1/models"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return {"ok": False, "status": None, "message": "请求超时（10s）"}
        except httpx.NetworkError as e:
            return {"ok": False, "status": None, "message": f"网络错误: {e}"}

        if resp.status_code == 200:
            return {"ok": True, "status": 200, "message": "OK"}
        if resp.status_code in (401, 403):
            return {
                "ok": False,
                "status": resp.status_code,
                "message": f"API Key 无效或权限不足 (HTTP {resp.status_code})",
            }
        return {
            "ok": False,
            "status": resp.status_code,
            "message": f"DashScope 响应异常 (HTTP {resp.status_code})",
        }

    # ── cancellation ──────────────────────────────────────────────────

    def mark_cancelled(self, task_id: str) -> None:
        self._cancelled.add(task_id)

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self._cancelled

    def clear_cancelled(self, task_id: str) -> None:
        self._cancelled.discard(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        self.mark_cancelled(task_id)
        try:
            await self.request(
                "POST",
                PATH_TASK_CANCEL.format(id=task_id),
                timeout=10.0,
                max_retries=0,
            )
            return True
        except VendorError as e:
            logger.info("cancel_task %s returned %s (non-fatal)", task_id, e.kind)
            return False

    async def aclose(self) -> None:
        """No persistent HTTP client to close — :class:`BaseVendorClient`
        opens an :class:`httpx.AsyncClient` per request. The plugin's
        ``on_unload`` still calls this for symmetry with other vendor
        clients, so we expose a tidy no-op instead of raising
        ``AttributeError`` and relying on the outer try/except to swallow
        it.
        """
        return None

    # ── Registry-driven video-synthesis dispatch ───────────────────────

    async def submit_video_synth(
        self,
        *,
        mode: str,
        model_id: str,
        prompt: str,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        reference_urls: list[str] | None = None,
        source_video_url: str | None = None,
        resolution: str | None = None,
        aspect: str | None = None,
        duration: float | None = None,
        task_type: str | None = None,
        driving_audio_url: str | None = None,
        extra_parameters: dict[str, Any] | None = None,
    ) -> str:
        """Submit a HappyHorse 1.0 / Wan 2.6 / Wan 2.7 video-generation job.

        The endpoint, body shape and forbidden-parameter list are all
        looked up from :mod:`happyhorse_model_registry`. Returns the
        DashScope task id.
        """
        entry = REGISTRY_BY_KEY.get((mode, model_id)) or by_model_id(model_id)
        if entry is None:
            raise VendorError(
                f"unknown model_id {model_id!r} for mode {mode!r}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        if entry.endpoint_family != "video_synthesis":
            raise VendorError(
                f"model_id {model_id!r} ({entry.endpoint_family}) is not a "
                "video_synthesis model — use the dedicated submit_* method",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )

        # Validate forbidden params (HappyHorse 1.0 family).
        if entry.forbidden_params and extra_parameters:
            bad = set(entry.forbidden_params) & set(extra_parameters)
            if bad:
                raise VendorError(
                    f"model {model_id!r} forbids parameter(s): {sorted(bad)} "
                    "(HappyHorse 1.0 rejects with_audio/size/quality/fps/audio)",
                    status=422,
                    retryable=False,
                    kind=ERROR_KIND_CLIENT,
                )

        params: dict[str, Any] = {}
        if extra_parameters:
            params.update(extra_parameters)

        # ── size / resolution dispatch (size_format) ─────────────────
        if entry.size_format == "resolution_p":
            res = (resolution or entry.resolutions[0] or "720P").upper()
            if not _RE_RES_P_UPPER.match(res):
                raise VendorError(
                    f"model {model_id!r} requires resolution like '720P' / "
                    f"'1080P' (uppercase P), got {resolution!r}",
                    status=422,
                    retryable=False,
                    kind=ERROR_KIND_CLIENT,
                )
            params["resolution"] = res
        elif entry.size_format == "size_star":
            base_h = _resolution_to_height(resolution or entry.resolutions[0] or "720P")
            params["size"] = _aspect_to_size(aspect or "16:9", base_height=base_h)
            # Wan 2.6 supports an explicit ``audio: true`` flag.
            params.setdefault("audio", True)
        elif entry.size_format == "size_x":
            base_h = _resolution_to_height(resolution or entry.resolutions[0] or "720P")
            params["size"] = _aspect_to_size(aspect or "16:9", base_height=base_h).replace("*", "x")

        if duration is not None and "duration" not in (entry.forbidden_params or ()):
            try:
                params["duration"] = int(round(float(duration)))
            except (TypeError, ValueError):
                pass

        # task_type only applies to url_fields-style entries that
        # declare task_types. media_array entries (wan2.7-i2v) ignore
        # task_type — the sub-task is encoded in which media[].type
        # entries appear.
        if entry.input_protocol == "url_fields":
            if task_type:
                if entry.task_types and task_type not in entry.task_types:
                    raise VendorError(
                        f"task_type {task_type!r} not allowed for {model_id!r}; "
                        f"accepted: {list(entry.task_types)}",
                        status=422,
                        retryable=False,
                        kind=ERROR_KIND_CLIENT,
                    )
                params["task_type"] = task_type
            elif entry.task_types and entry.protocol_version == "new_async":
                params.setdefault("task_type", entry.task_types[0])

        # ── input dispatch (per input_protocol) ──────────────────────
        input_obj: dict[str, Any] = {}
        if prompt:
            input_obj["prompt"] = prompt

        if entry.input_protocol == "media_array":
            # wan2.7-i2v family: pack URLs into input.media[]. Order of
            # types in the array is not significant per the official
            # docs, but we use first_frame → last_frame → first_clip →
            # driving_audio for readable bodies. The DashScope service
            # rejects duplicate ``type`` entries (each may appear once
            # at most), and our SDK callers already pass at most one of
            # each, so we don't dedupe here.
            media: list[dict[str, str]] = []
            if first_frame_url:
                media.append({"type": "first_frame", "url": first_frame_url})
            if last_frame_url:
                media.append({"type": "last_frame", "url": last_frame_url})
            if source_video_url:
                media.append({"type": "first_clip", "url": source_video_url})
            if driving_audio_url:
                media.append({"type": "driving_audio", "url": driving_audio_url})
            if not media:
                raise VendorError(
                    f"model {model_id!r} requires at least one of "
                    "first_frame_url / last_frame_url / source_video_url "
                    "/ driving_audio_url (got none)",
                    status=422,
                    retryable=False,
                    kind=ERROR_KIND_CLIENT,
                )
            if reference_urls:
                # wan2.7-i2v does not accept reference_urls — those go to
                # wan2.6-r2v / happyhorse-r2v. Fail loudly so the UI
                # doesn't silently drop the user's reference images.
                raise VendorError(
                    f"model {model_id!r} does not accept reference_urls; "
                    "use a wan2.6-r2v / happyhorse-1.0-r2v model for "
                    "multi-character reference videos.",
                    status=422,
                    retryable=False,
                    kind=ERROR_KIND_CLIENT,
                )
            input_obj["media"] = media
        else:
            if first_frame_url:
                input_obj["first_frame_url"] = first_frame_url
            if last_frame_url:
                input_obj["last_frame_url"] = last_frame_url
            if reference_urls:
                input_obj["reference_urls"] = list(reference_urls)
            if source_video_url:
                # Wan 2.6 legacy uses ``video_url`` (video_edit /
                # video-to-video). New-async non-media_array variants
                # (e.g. happyhorse video_edit) also use ``video_url``.
                input_obj["video_url"] = source_video_url
            if driving_audio_url:
                # Wan 2.6 audio injection — official spec calls it
                # ``audio_url`` (see audio_url section of the t2v doc).
                input_obj["audio_url"] = driving_audio_url

        body = {
            "model": model_id,
            "input": input_obj,
            "parameters": params,
        }
        return await self._submit_async(PATH_VIDEO_SYNTHESIS, body)

    # ── Digital-human flows ────────────────────────────────────────────

    async def face_detect(self, image_url: str) -> dict[str, Any]:
        """Run ``wan2.2-s2v-detect``; returns ``{check_pass, humanoid}``.
        Raises ``VendorError(asset_rejected)`` if the image isn't a usable
        human face — saves the user from a wasted s2v charge.
        """
        body = {"model": MODEL_S2V_DETECT, "input": {"image_url": image_url}}
        try:
            resp = await self.post_json(PATH_S2V_DETECT, json_body=body, timeout=30.0)
        except VendorError as e:
            e.kind = _classify_dashscope_body(e.body, e.kind)
            raise
        out = self._coerce_dict(resp.get("output"))
        check_pass = bool(out.get("check_pass") or out.get("pass"))
        humanoid = bool(out.get("humanoid") or out.get("is_human"))
        if not (check_pass and humanoid):
            raise VendorError(
                f"face-detect rejected the input "
                f"(check_pass={check_pass}, humanoid={humanoid})",
                status=200,
                body=out,
                retryable=False,
                kind=ERROR_KIND_ASSET_REJECTED,
            )
        return {"check_pass": check_pass, "humanoid": humanoid, "raw": out}

    async def submit_s2v(
        self,
        *,
        image_url: str,
        audio_url: str,
        resolution: str = "480P",
        duration: float | None = None,
    ) -> str:
        params: dict[str, Any] = {"resolution": resolution}
        if duration is not None:
            params["duration"] = int(round(float(duration)))
        body = {
            "model": MODEL_S2V,
            "input": {"image_url": image_url, "audio_url": audio_url},
            "parameters": params,
        }
        return await self._submit_async(PATH_S2V_SUBMIT, body)

    async def submit_videoretalk(
        self,
        *,
        video_url: str,
        audio_url: str,
        ref_image_url: str = "",
        video_extension: bool = True,
    ) -> str:
        for label, u in (("video_url", video_url), ("audio_url", audio_url)):
            if not u or not str(u).strip():
                raise VendorError(
                    f"videoretalk requires a non-empty {label} (got empty)",
                    status=422,
                    retryable=False,
                    kind=ERROR_KIND_CLIENT,
                )
        input_obj: dict[str, Any] = {"video_url": video_url, "audio_url": audio_url}
        if ref_image_url:
            input_obj["ref_image_url"] = ref_image_url
        body = {
            "model": MODEL_VIDEORETALK,
            "input": input_obj,
            "parameters": {"video_extension": bool(video_extension)},
        }
        return await self._submit_async(PATH_VIDEORETALK_SUBMIT, body)

    async def submit_animate_mix(
        self,
        *,
        image_url: str,
        video_url: str,
        mode_pro: bool = False,
        watermark: bool = False,
    ) -> str:
        body = {
            "model": MODEL_ANIMATE_MIX,
            "input": {"image_url": image_url, "video_url": video_url},
            "parameters": {
                "mode": "wan-pro" if mode_pro else "wan-std",
                "watermark": bool(watermark),
            },
        }
        return await self._submit_async(PATH_ANIMATE_SUBMIT, body)

    async def submit_animate_move(
        self,
        *,
        image_url: str,
        video_url: str,
        mode_pro: bool = False,
        watermark: bool = False,
    ) -> str:
        body = {
            "model": MODEL_ANIMATE_MOVE,
            "input": {
                "image_url": image_url,
                "video_url": video_url,
                "watermark": bool(watermark),
            },
            "parameters": {"mode": "wan-pro" if mode_pro else "wan-std"},
        }
        return await self._submit_async(PATH_ANIMATE_SUBMIT, body)

    async def submit_image_edit(
        self,
        *,
        prompt: str,
        ref_images_url: list[str],
        size: str | None = None,
        model: str = MODEL_I2I_LEGACY,
    ) -> str:
        """Submit a wan2.5-i2i-preview image-edit job (legacy 1..3 ref images)."""
        if not 1 <= len(ref_images_url) <= 3:
            raise VendorError(
                f"wan2.5-i2i-preview accepts 1..3 reference images, got {len(ref_images_url)}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        params: dict[str, Any] = {"n": 1}
        if size:
            params["size"] = size
        body = {
            "model": model,
            "input": {"prompt": prompt, "images": list(ref_images_url)},
            "parameters": params,
        }
        return await self._submit_async(PATH_I2I_SUBMIT, body)

    async def submit_image_edit_wan27(
        self,
        *,
        prompt: str,
        ref_images_url: list[str],
        size: str | None = None,
        model: str = MODEL_WAN27_IMAGE,
    ) -> str:
        """Submit a wan2.7-image edit via the multimodal-generation endpoint
        (1..9 reference images allowed)."""
        if not 1 <= len(ref_images_url) <= 9:
            raise VendorError(
                f"wan2.7-image accepts 1..9 reference images, got {len(ref_images_url)}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        content: list[dict[str, str]] = [{"text": prompt}]
        for url in ref_images_url:
            content.append({"image": url})
        params: dict[str, Any] = {"n": 1}
        if size:
            params["size"] = size
        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }
        return await self._submit_async(PATH_WAN27_IMAGE, body)

    # ── Built-in image generation (ported from tongyi-image) ────────────

    async def submit_image_multimodal(
        self,
        *,
        prompt: str,
        model: str = MODEL_WAN27_IMAGE_PRO,
        images: list[str] | None = None,
        size: str | None = None,
        n: int = 1,
        negative_prompt: str = "",
        prompt_extend: bool | None = None,
        watermark: bool = False,
        seed: int | None = None,
        thinking_mode: bool | None = None,
        enable_sequential: bool | None = None,
        async_mode: bool = True,
    ) -> dict[str, Any]:
        content: list[dict[str, str]] = [{"text": prompt}]
        for image_url in images or []:
            if image_url:
                content.append({"image": image_url})
        params: dict[str, Any] = {"n": max(1, int(n or 1)), "watermark": bool(watermark)}
        if size:
            params["size"] = size
        if negative_prompt:
            params["negative_prompt"] = negative_prompt
        if prompt_extend is not None:
            params["prompt_extend"] = bool(prompt_extend)
        if seed is not None:
            params["seed"] = int(seed)
        if thinking_mode is not None:
            params["thinking_mode"] = bool(thinking_mode)
        if enable_sequential is not None:
            params["enable_sequential"] = bool(enable_sequential)
        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }
        # Both sync and async share the multimodal-generation/generation
        # endpoint because the body is already in messages-shape (which is
        # what that endpoint expects). Earlier code routed the async branch
        # to image-generation/generation, but that endpoint expects the
        # legacy {input: {prompt: ...}} shape and would reject the
        # messages payload, breaking wan2.7-image / wan2.6-image.
        if async_mode and self._image_async_supported is not False:
            try:
                task_id = await self._submit_async(PATH_WAN27_IMAGE, body)
                self._image_async_supported = True
                return {"task_id": task_id, "async": True}
            except VendorError as exc:
                if not _is_async_call_unsupported(exc):
                    raise
                self._image_async_supported = False
                logger.info(
                    "DashScope account does not support async image calls; "
                    "falling back to synchronous %s",
                    model,
                )
        # Tag sync results with ``async=False`` so callers can branch on
        # one consistent key instead of "if not result.get('async')".
        sync_result = await self.request("POST", PATH_WAN27_IMAGE, json_body=body)
        if isinstance(sync_result, dict) and "async" not in sync_result:
            sync_result = {**sync_result, "async": False}
        return sync_result

    async def submit_style_repaint(
        self,
        *,
        image_url: str,
        style_index: int = 0,
        style_ref_url: str | None = None,
    ) -> str:
        inp: dict[str, Any] = {"image_url": image_url, "style_index": int(style_index)}
        if style_ref_url and int(style_index) == -1:
            inp["style_ref_url"] = style_ref_url
        body = {"model": "wanx-style-repaint-v1", "input": inp}
        return await self._submit_async(PATH_IMAGE_GEN, body)

    async def submit_background_generation(
        self,
        *,
        base_image_url: str,
        ref_prompt: str = "",
        ref_image_url: str = "",
        n: int = 1,
        noise_level: int = 300,
        ref_prompt_weight: float = 0.5,
    ) -> str:
        inp: dict[str, Any] = {"base_image_url": base_image_url}
        if ref_prompt:
            inp["ref_prompt"] = ref_prompt
        if ref_image_url:
            inp["ref_image_url"] = ref_image_url
        params: dict[str, Any] = {"model_version": "v3", "n": max(1, int(n or 1))}
        if ref_image_url:
            params["noise_level"] = int(noise_level)
        if ref_prompt and ref_image_url:
            params["ref_prompt_weight"] = float(ref_prompt_weight)
        body = {"model": "wanx-background-generation-v2", "input": inp, "parameters": params}
        return await self._submit_async(PATH_BG_GEN, body)

    async def submit_outpaint(
        self,
        *,
        image_url: str,
        output_ratio: str | None = None,
        x_scale: float | None = None,
        y_scale: float | None = None,
        best_quality: bool = False,
    ) -> str:
        params: dict[str, Any] = {"best_quality": bool(best_quality), "limit_image_size": True}
        if output_ratio:
            params["output_ratio"] = output_ratio
        if x_scale is not None:
            params["x_scale"] = float(x_scale)
        if y_scale is not None:
            params["y_scale"] = float(y_scale)
        body = {"model": "image-out-painting", "input": {"image_url": image_url}, "parameters": params}
        return await self._submit_async(PATH_OUTPAINT, body)

    async def submit_sketch_to_image(
        self,
        *,
        sketch_image_url: str,
        prompt: str,
        style: str = "<watercolor>",
        size: str = "768*768",
        n: int = 1,
        sketch_weight: int = 3,
    ) -> str:
        body = {
            "model": "wanx-sketch-to-image-lite",
            "input": {"sketch_image_url": sketch_image_url, "prompt": prompt},
            "parameters": {
                "size": size,
                "n": max(1, int(n or 1)),
                "sketch_weight": int(sketch_weight),
                "style": style,
            },
        }
        return await self._submit_async(PATH_I2I_SUBMIT, body)

    # ── Polling / output extraction ───────────────────────────────────

    async def query_task(self, task_id: str) -> dict[str, Any]:
        """Single-shot DashScope task query (no polling — pipeline loops)."""
        try:
            resp = await self.request(
                "GET",
                PATH_TASK_QUERY.format(id=task_id),
                timeout=20.0,
                max_retries=1,
            )
        except VendorError as e:
            e.kind = _classify_dashscope_body(e.body, e.kind)
            raise

        out = self._coerce_dict(resp.get("output"))
        usage = self._coerce_dict(resp.get("usage"))
        status = str(out.get("task_status") or out.get("status") or "UNKNOWN").upper()
        result: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "is_done": _is_async_done(status),
            "is_ok": _is_async_ok(status),
            "usage": usage,
            "raw": resp,
        }
        if _is_async_ok(status):
            url, kind = self._extract_output_url(out)
            result["output_url"] = url
            result["output_kind"] = kind
            # Some HappyHorse / Wan2.7 outputs include ``last_frame_url`` so
            # the caller can chain into next-shot generation.
            last = out.get("last_frame_url") or out.get("last_image_url")
            if isinstance(last, str) and last:
                result["last_frame_url"] = last
        if status == "FAILED":
            result["error_kind"] = _classify_dashscope_body(out, ERROR_KIND_SERVER)
            result["error_message"] = out.get("message") or out.get("error_message") or ""
        return result

    # ── TTS (cosyvoice-v2 — SDK only) ─────────────────────────────────

    async def synth_voice(
        self,
        *,
        text: str,
        voice_id: str,
        format: str = "mp3",
    ) -> dict[str, Any]:
        """Synthesise speech via cosyvoice-v2; returns ``{audio_bytes, format,
        duration_sec}``. The dashscope SDK is **lazy-imported** here.
        """
        try:
            import dashscope
            from dashscope.audio.tts_v2 import (
                AudioFormat,
                SpeechSynthesizer,
            )
        except ImportError as e:
            import sys

            raise VendorError(
                "未安装 cosyvoice-v2 TTS 所需的 dashscope SDK。"
                f"请在 OpenAkita 运行的 Python 环境中执行：\n"
                f"    {sys.executable} -m pip install dashscope\n"
                "（happyhorse-video 仅在调用 cosyvoice-v2 TTS 时才需要此 SDK；"
                "其他模式与「上传现成音频 / Edge-TTS」流程不受影响。）",
                status=None,
                retryable=False,
                kind=ERROR_KIND_DEPENDENCY,
            ) from e

        s = self._settings()
        api_key = str(s.get("api_key") or "").strip()
        if not api_key:
            raise VendorError(
                "DashScope API Key is empty; configure it in Settings",
                status=401,
                retryable=False,
                kind=ERROR_KIND_AUTH,
            )

        # The dashscope SDK reads credentials from a *module-level* global
        # (``dashscope.api_key``) — hot-set on every call to follow A10.
        dashscope.api_key = api_key

        fmt_candidates = {
            "mp3": (
                "MP3_22050HZ_MONO_256KBPS",
                "MP3_24000HZ_MONO_256KBPS",
                "MP3_44100HZ_MONO_256KBPS",
                "MP3_16000HZ_MONO_128KBPS",
            ),
            "wav": (
                "WAV_22050HZ_MONO_16BIT",
                "WAV_24000HZ_MONO_16BIT",
                "WAV_16000HZ_MONO_16BIT",
            ),
            "pcm": ("PCM_22050HZ_MONO_16BIT", "PCM_24000HZ_MONO_16BIT"),
        }
        fmt_const = None
        for name in fmt_candidates.get(format.lower(), ()):
            fmt_const = getattr(AudioFormat, name, None)
            if fmt_const is not None:
                break
        if fmt_const is None:
            fmt_const = getattr(AudioFormat, "DEFAULT", None)

        synth = SpeechSynthesizer(
            model=MODEL_COSYVOICE_V2,
            voice=voice_id,
            format=fmt_const,
        )
        loop = asyncio.get_running_loop()
        try:
            audio_bytes = await loop.run_in_executor(None, lambda: synth.call(text))
        except Exception as e:  # noqa: BLE001
            raise VendorError(
                f"cosyvoice-v2 synth failed: {e}",
                retryable=False,
                kind=ERROR_KIND_SERVER,
            ) from e

        if not audio_bytes:
            raise VendorError(
                "cosyvoice-v2 returned empty audio",
                retryable=False,
                kind=ERROR_KIND_DEPENDENCY,
            )

        head = audio_bytes[:16]
        detected: str | None = None
        if head.startswith(b"ID3") or (
            len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
        ):
            detected = "mp3"
        elif head.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE":
            detected = "wav"
        elif head.startswith(b"OggS"):
            detected = "ogg"
        elif head.startswith(b"fLaC"):
            detected = "flac"

        if detected is None:
            audio_bytes = _wrap_pcm_as_wav(audio_bytes)
            detected = "wav"
            logger.warning(
                "cosyvoice-v2 returned headerless audio (%d bytes); "
                "wrapped as WAV 22050Hz mono 16bit",
                len(audio_bytes),
            )

        return {"audio_bytes": audio_bytes, "format": detected, "duration_sec": None}

    async def clone_voice(
        self,
        *,
        sample_url: str,
        prefix: str = "happyhorse",
        language: str = "zh",
    ) -> dict[str, Any]:
        """Train a custom cosyvoice-v2 voice from a single sample URL.

        Returns ``{"voice_id": ..., "request_id": ...}``. Uses the
        synchronous ``VoiceEnrollmentService`` SDK call wrapped in
        ``asyncio.to_thread`` so the FastAPI loop stays responsive.
        """
        try:
            import dashscope
            from dashscope.audio.tts_v2 import VoiceEnrollmentService
        except ImportError as e:
            raise VendorError(
                "未安装 cosyvoice-v2 所需的 dashscope SDK；"
                "请在 Settings → Python 依赖 中一键安装",
                status=500,
                retryable=False,
                kind=ERROR_KIND_DEPENDENCY,
            ) from e

        api_key = str(self._settings().get("api_key") or "").strip()
        if not api_key:
            raise VendorError(
                "DashScope API Key 未配置；无法克隆音色",
                status=400,
                retryable=False,
                kind=ERROR_KIND_AUTH,
            )
        if not sample_url:
            raise VendorError(
                "clone_voice requires sample_url (an OSS signed URL)",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        dashscope.api_key = api_key

        def _sync() -> tuple[str, str | None]:
            svc = VoiceEnrollmentService()
            vid = svc.create_voice(
                target_model=MODEL_COSYVOICE_V2,
                prefix=str(prefix)[:10] or "happyhorse",
                url=sample_url,
                language_hints=[language] if language else None,
            )
            try:
                req_id = svc.get_last_request_id()
            except Exception:  # noqa: BLE001
                req_id = None
            return str(vid), req_id

        try:
            voice_id, req_id = await asyncio.to_thread(_sync)
        except VendorError:
            raise
        except Exception as e:  # noqa: BLE001
            raise VendorError(
                f"VoiceEnrollmentService.create_voice failed: {e}",
                status=500,
                retryable=True,
                kind=ERROR_KIND_SERVER,
            ) from e
        if not voice_id:
            raise VendorError(
                "VoiceEnrollmentService returned an empty voice_id",
                status=500,
                retryable=True,
                kind=ERROR_KIND_SERVER,
            )
        return {"voice_id": voice_id, "request_id": req_id}

    async def caption_with_qwen_vl(
        self,
        *,
        image_urls: list[str],
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """qwen-vl-max prompt-writer; output runs through llm_json_parser."""
        body = {
            "model": MODEL_QWEN_VL,
            "input": {
                "messages": [
                    {"role": "system", "content": [{"text": system_prompt}]},
                    {
                        "role": "user",
                        "content": [
                            *[{"image": u} for u in image_urls],
                            {"text": user_prompt},
                        ],
                    },
                ]
            },
            "parameters": {"result_format": "message"},
        }
        try:
            resp = await self.post_json(PATH_QWEN_VL, json_body=body, timeout=60.0)
        except VendorError as e:
            e.kind = _classify_dashscope_body(e.body, e.kind)
            raise

        out = self._coerce_dict(resp.get("output"))
        choices = out.get("choices") or []
        text_chunks: list[str] = []
        if choices:
            msg = self._coerce_dict(choices[0].get("message"))
            content = msg.get("content")
            if isinstance(content, list):
                text_chunks = [
                    str(c.get("text", "")) for c in content if isinstance(c, dict)
                ]
            elif isinstance(content, str):
                text_chunks = [content]
        text = "\n".join(s for s in text_chunks if s)
        parsed = parse_llm_json_object(text, fallback={"prompt": text.strip()})
        return {"text": text, "parsed": parsed, "usage": resp.get("usage", {})}

    # ── internals ─────────────────────────────────────────────────────

    async def _submit_async(self, path: str, body: dict[str, Any]) -> str:
        """Serialise submissions and return the DashScope ``task_id``."""
        async with self._submit_lock:
            try:
                resp = await self.post_json(
                    path,
                    json_body=body,
                    timeout=60.0,
                    extra_headers=self._ASYNC_HEADER,
                )
            except VendorError as e:
                e.kind = _classify_dashscope_body(e.body, e.kind)
                raise
        out = self._coerce_dict(resp.get("output"))
        task_id = str(out.get("task_id") or "").strip()
        if not task_id:
            raise VendorError(
                "DashScope did not return a task_id",
                status=200,
                body=resp,
                retryable=False,
                kind=ERROR_KIND_UNKNOWN,
            )
        return task_id

    @staticmethod
    def _extract_output_url(out: dict[str, Any]) -> tuple[str | None, str | None]:
        """Multi-shape probe — accepts video_url / image_url / results.{url,
        video_url, image_url}. Returns ``(url, kind)`` where
        ``kind ∈ {"video","image"}`` or ``(None, None)``.
        """
        v = out.get("video_url")
        if isinstance(v, str) and v:
            return v, "video"
        i = out.get("image_url")
        if isinstance(i, str) and i:
            return i, "image"
        results = out.get("results")
        if isinstance(results, dict):
            u = results.get("video_url") or results.get("url") or results.get("image_url")
            if isinstance(u, str) and u:
                kind = "video" if u.lower().endswith((".mp4", ".webm", ".mov")) else "image"
                return u, kind
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                u = first.get("url") or first.get("image_url") or first.get("video_url")
                if isinstance(u, str) and u:
                    kind = "video" if u.lower().endswith((".mp4", ".webm", ".mov")) else "image"
                    return u, kind
        return None, None

    @staticmethod
    def _coerce_dict(v: Any) -> dict[str, Any]:
        return v if isinstance(v, dict) else {}

    # ── Convenience: model lookup helpers ──────────────────────────────

    def resolve_model(self, mode: str, model_id: str | None) -> ModelEntry:
        """Look up the registry entry for ``(mode, model_id)`` falling back
        to the per-mode default. Raises ``VendorError`` if neither
        resolves to a known model.
        """
        if model_id:
            entry = REGISTRY_BY_KEY.get((mode, model_id)) or by_model_id(model_id)
            if entry is not None:
                return entry
        d = default_model(mode)
        if d is None:
            raise VendorError(
                f"no model configured for mode {mode!r}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        return d


def _wrap_pcm_as_wav(
    pcm: bytes,
    *,
    sample_rate: int = 22050,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Prepend a minimal RIFF/WAVE header to raw little-endian PCM."""
    import struct

    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_len = len(pcm)
    riff_len = 36 + data_len
    header = (
        b"RIFF"
        + struct.pack("<I", riff_len)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
        + b"data"
        + struct.pack("<I", data_len)
    )
    return header + pcm
