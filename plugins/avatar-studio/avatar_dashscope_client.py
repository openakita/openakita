"""DashScope async client for avatar-studio.

Inherits :class:`avatar_studio_inline.vendor_client.BaseVendorClient` (the
vendored copy of the SDK 0.6.0 ``BaseVendorClient``) for retry / timeout /
content-moderation / 9-class ``ERROR_KIND_*``. Adds avatar-studio-specific
business methods covering the four DashScope flows:

==============  ===================================================
mode            DashScope endpoint(s) used
==============  ===================================================
photo_speak     wan2.2-s2v-detect → wan2.2-s2v
video_relip     videoretalk
video_reface    wan2.2-animate-mix
avatar_compose  wan2.5-i2i-preview → wan2.2-s2v-detect → wan2.2-s2v
==============  ===================================================

Plus ancillary helpers:

- ``synth_voice``       cosyvoice-v2 TTS (lazy-imports the dashscope SDK,
                        Pixelle C4: never module-level import an optional
                        SDK).
- ``caption_with_qwen_vl``  qwen-vl-max (vision LLM) for the optional
                        avatar_compose prompt-writer; output goes through
                        ``avatar_studio_inline.llm_json_parser`` to absorb
                        non-JSON wrappers (Pixelle A6).
- ``query_task``         polls a DashScope async task; ``_extract_output_url``
                        accepts three known payload shapes
                        (``output.video_url`` / ``output.results[*].url`` /
                        ``output.image_url``) — the "ComfyUI three-shape
                        probe" lesson from Pixelle.

Concurrency: a single ``asyncio.Semaphore(1)`` guards every ``submit_*``
because DashScope async tasks share a per-key "1 in flight" cap. Polling
calls are NOT gated.

Hot config reload (Pixelle A10): the constructor takes a
``read_settings()`` callable. Each call re-reads ``api_key`` /
``timeout`` / ``base_url``, so updating Settings in the UI takes effect
without re-instantiating the client. ``update_api_key`` exists as a fast
path used by the ``PUT /settings`` route.

Cancellation: ``cancel_task(task_id)`` records the id in ``_cancelled``;
the pipeline polling loop checks it every iteration and aborts early.
The remote DashScope task is also cancelled best-effort via
``DELETE /api/v1/services/aigc/.../{id}``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from avatar_studio_inline.llm_json_parser import parse_llm_json_object
from avatar_studio_inline.vendor_client import (
    ERROR_KIND_CLIENT,
    ERROR_KIND_SERVER,
    ERROR_KIND_UNKNOWN,
    BaseVendorClient,
    VendorError,
)

logger = logging.getLogger(__name__)


# Default DashScope base URLs; set in Settings to switch region.
DASHSCOPE_BASE_URL_BJ = "https://dashscope.aliyuncs.com"
DASHSCOPE_BASE_URL_SG = "https://dashscope-intl.aliyuncs.com"

# Endpoint paths (kept centralised so a vendor URL change is one edit).
PATH_S2V_DETECT = "/api/v1/services/aigc/image2video/face-detect"
PATH_S2V_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_VIDEORETALK_SUBMIT = "/api/v1/services/aigc/video-generation/video-retalk"
PATH_ANIMATE_MIX_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_I2I_SUBMIT = "/api/v1/services/aigc/image2image/image-synthesis"
PATH_QWEN_VL = "/api/v1/services/aigc/multimodal-generation/generation"
PATH_TASK_QUERY = "/api/v1/tasks/{id}"
PATH_TASK_CANCEL = "/api/v1/tasks/{id}/cancel"

# Models — mode → DashScope model id.
MODEL_S2V_DETECT = "wan2.2-s2v-detect"
MODEL_S2V = "wan2.2-s2v"
MODEL_VIDEORETALK = "videoretalk"
MODEL_ANIMATE_MIX = "wan2.2-animate-mix"
MODEL_I2I = "wan2.5-i2i-preview"
MODEL_QWEN_VL = "qwen-vl-max"
MODEL_COSYVOICE_V2 = "cosyvoice-v2"


# ─── Settings shape (what read_settings returns) ────────────────────────


class DashScopeSettings(dict):
    """Lightweight typed-ish dict; tolerant of partial overrides."""

    api_key: str
    base_url: str
    timeout: float


def make_default_settings() -> dict[str, Any]:
    return {
        "api_key": "",
        "base_url": DASHSCOPE_BASE_URL_BJ,
        "timeout": 60.0,
    }


# ─── Avatar-studio specific error kinds (extends vendor_client base) ────

ERROR_KIND_QUOTA = "quota"
ERROR_KIND_DEPENDENCY = "dependency"


def _classify_dashscope_body(body: Any, fallback_kind: str) -> str:
    """Promote a generic ``client`` / ``server`` ``ERROR_KIND_*`` to the
    avatar-studio-specific ``quota`` / ``dependency`` when the DashScope
    error code matches a known pattern. Falls back to the input kind."""
    if not isinstance(body, dict):
        return fallback_kind
    code = str(body.get("code") or body.get("error_code") or "").lower()
    msg = str(body.get("message") or body.get("error_message") or "").lower()
    if "quota" in code or "balance" in code or "insufficient" in msg or "balance" in msg:
        return ERROR_KIND_QUOTA
    if (
        "humanoid" in msg
        or "human" in msg
        and "detect" in msg
        or "duration" in msg
        and ("exceed" in msg or "too long" in msg)
        or "dependency" in code
    ):
        return ERROR_KIND_DEPENDENCY
    return fallback_kind


def _is_async_done(status: str) -> bool:
    return status.upper() in {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}


def _is_async_ok(status: str) -> bool:
    return status.upper() == "SUCCEEDED"


# ─── Client ────────────────────────────────────────────────────────────


ReadSettings = Callable[[], dict[str, Any]]


class AvatarDashScopeClient(BaseVendorClient):
    """One client instance per plugin process.

    All ``submit_*`` calls are serialised by ``self._submit_lock`` so we
    never violate DashScope's per-key "1 task in flight" cap. ``query_*``
    and ``cancel_*`` are not serialised so the pipeline can poll while the
    next user submission queues up legitimately.
    """

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
        # Cache the last seen settings so logs / tests can introspect.
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
        # even mid-pipeline. URL construction in the base class reads
        # ``self.base_url`` BEFORE ``auth_headers()``, so we must refresh
        # first.
        self._settings()
        return await super().request(method, path, **kw)

    # ── settings + auth ───────────────────────────────────────────────

    def _settings(self) -> dict[str, Any]:
        try:
            cur = self._read_settings() or {}
        except Exception as e:  # noqa: BLE001 - never raise from read
            logger.warning("read_settings raised %s; falling back to defaults", e)
            cur = {}
        merged = make_default_settings()
        merged.update({k: v for k, v in cur.items() if v not in (None, "")})
        # Live-update inherited fields so retry/timeout reflect Settings.
        try:
            self.timeout = float(merged.get("timeout") or 60.0)
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
            "X-DashScope-Async": "enable",
            "Content-Type": "application/json",
        }

    def update_api_key(self, api_key: str) -> None:
        """Fast path used by ``PUT /settings``; the next call also re-reads."""
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        self._last_settings["api_key"] = api_key.strip()

    def has_api_key(self) -> bool:
        return bool(self._settings().get("api_key"))

    # ── cancellation ──────────────────────────────────────────────────

    def mark_cancelled(self, task_id: str) -> None:
        """Pipeline-side flag; checked by ``query_task`` polling loops."""
        self._cancelled.add(task_id)

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self._cancelled

    def clear_cancelled(self, task_id: str) -> None:
        self._cancelled.discard(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Best-effort remote cancel via ``POST /tasks/{id}/cancel``."""
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

    # ── 8 business methods ────────────────────────────────────────────

    async def face_detect(self, image_url: str) -> dict[str, Any]:
        """Run ``wan2.2-s2v-detect``; returns ``{check_pass, humanoid}``.

        Raises :class:`VendorError` of kind ``dependency`` if the input is
        not a usable human face (instead of letting it slip through into
        a wasted s2v charge).
        """
        body = {
            "model": MODEL_S2V_DETECT,
            "input": {"image_url": image_url},
        }
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
                f"face-detect rejected the input (check_pass={check_pass}, humanoid={humanoid})",
                status=200,
                body=out,
                retryable=False,
                kind=ERROR_KIND_DEPENDENCY,
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
        """Submit a ``wan2.2-s2v`` async job; returns the DashScope task id.

        ``duration`` should be the TTS audio length (Pixelle P1: TTS drives
        video length). When unset DashScope picks the audio length itself,
        but we still pass it for explicit billing transparency.
        """
        params: dict[str, Any] = {"resolution": resolution}
        if duration is not None:
            params["duration"] = float(duration)
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
    ) -> str:
        body = {
            "model": MODEL_VIDEORETALK,
            "input": {"video_url": video_url, "audio_url": audio_url},
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
        return await self._submit_async(PATH_ANIMATE_MIX_SUBMIT, body)

    async def submit_image_edit(
        self,
        *,
        prompt: str,
        ref_images_url: list[str],
        size: str | None = None,
    ) -> str:
        if not 1 <= len(ref_images_url) <= 3:
            raise VendorError(
                f"wan2.5-i2i-preview accepts 1..3 reference images, got {len(ref_images_url)}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        params: dict[str, Any] = {}
        if size:
            params["size"] = size
        body = {
            "model": MODEL_I2I,
            "input": {"prompt": prompt, "ref_images_url": list(ref_images_url)},
            "parameters": params,
        }
        return await self._submit_async(PATH_I2I_SUBMIT, body)

    async def query_task(self, task_id: str) -> dict[str, Any]:
        """Single-shot DashScope task query (no polling here — pipeline loops)."""
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
        if status in {"FAILED"}:
            result["error_kind"] = _classify_dashscope_body(out, ERROR_KIND_SERVER)
            result["error_message"] = out.get("message") or out.get("error_message") or ""
        return result

    async def synth_voice(
        self,
        *,
        text: str,
        voice_id: str,
        format: str = "mp3",
    ) -> dict[str, Any]:
        """Synthesise speech via cosyvoice-v2; returns ``{bytes, duration_sec}``.

        The dashscope SDK is **lazy-imported** here (Pixelle C4: never block
        plugin load if an optional vendor SDK is missing — surface the lack
        only at call time as a clear ``ImportError`` the UI can map to the
        ``dependency`` error_kind).
        """
        try:
            from dashscope.audio.tts_v2 import (  # type: ignore[import-not-found]
                AudioFormat,
                SpeechSynthesizer,
            )
        except ImportError as e:
            raise VendorError(
                "dashscope SDK is required for cosyvoice-v2 TTS — `pip install dashscope`",
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
                kind="auth",
            )

        fmt_const = getattr(AudioFormat, f"{format.upper()}_24000HZ_MONO_16BIT", None)
        synth = SpeechSynthesizer(model=MODEL_COSYVOICE_V2, voice=voice_id)
        # The SDK is sync; offload to a thread.
        loop = asyncio.get_running_loop()
        try:
            audio_bytes = await loop.run_in_executor(
                None,
                lambda: synth.call(text, format=fmt_const) if fmt_const else synth.call(text),
            )
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
        return {
            "audio_bytes": audio_bytes,
            "format": format,
            # Caller computes the precise duration after writing the file
            # (mp3 frame counting); here we return None so we never bluff.
            "duration_sec": None,
        }

    async def caption_with_qwen_vl(
        self,
        *,
        image_urls: list[str],
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """qwen-vl-max prompt-writer; output is JSON-fallback parsed (A6)."""
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
                text_chunks = [str(c.get("text", "")) for c in content if isinstance(c, dict)]
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
                resp = await self.post_json(path, json_body=body, timeout=60.0)
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
        """Three-shape probe — tries the three known DashScope payload styles.

        Inspired by the Pixelle ComfyUI lesson: never bind to a single field
        path because DashScope's response shape varies between models.
        Returns ``(url, kind)`` where ``kind ∈ {"video","image"}`` or
        ``(None, None)`` if no URL is present.
        """
        # Shape 1: ``output.video_url``
        v = out.get("video_url")
        if isinstance(v, str) and v:
            return v, "video"
        # Shape 2: ``output.image_url``
        i = out.get("image_url")
        if isinstance(i, str) and i:
            return i, "image"
        # Shape 3: ``output.results[*].url`` (image batch APIs)
        results = out.get("results")
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
