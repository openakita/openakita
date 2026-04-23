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


def _wrap_pcm_as_wav(
    pcm: bytes,
    *,
    sample_rate: int = 22050,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Prepend a minimal RIFF/WAVE header to raw little-endian PCM.

    cosyvoice-v2 occasionally returns headerless PCM even when MP3 was
    requested (depends on workspace / region). A 44-byte WAV header is
    enough for every browser <audio> element to decode and seek, so we
    wrap rather than reject — silent playback is much worse UX than a
    slightly larger file.
    """
    import struct

    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_len = len(pcm)
    riff_len = 36 + data_len
    header = (
        b"RIFF" + struct.pack("<I", riff_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
        + b"data" + struct.pack("<I", data_len)
    )
    return header + pcm


# Default DashScope base URLs; set in Settings to switch region.
DASHSCOPE_BASE_URL_BJ = "https://dashscope.aliyuncs.com"
DASHSCOPE_BASE_URL_SG = "https://dashscope-intl.aliyuncs.com"

# Endpoint paths (kept centralised so a vendor URL change is one edit).
PATH_S2V_DETECT = "/api/v1/services/aigc/image2video/face-detect"
PATH_S2V_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
# NOTE: videoretalk shares the SAME ``image2video/video-synthesis``
# endpoint as wan2.2-s2v / wan2.2-animate-mix — only ``model`` in the
# request body differentiates them. The previous path
# ``/api/v1/services/aigc/video-generation/video-retalk`` was wrong:
# DashScope responded with the cryptic ``InvalidParameter: url error,
# please check url！`` error (documented as "model name does not match
# API endpoint") instead of a clean 404, which made the regression hard
# to spot. See https://help.aliyun.com/zh/model-studio/videoretalk-api
# (北京区).
PATH_VIDEORETALK_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_ANIMATE_MIX_SUBMIT = "/api/v1/services/aigc/image2video/video-synthesis"
PATH_I2I_SUBMIT = "/api/v1/services/aigc/image2image/image-synthesis"
PATH_WAN27_IMAGE = "/api/v1/services/aigc/multimodal-generation/generation"
PATH_QWEN_VL = "/api/v1/services/aigc/multimodal-generation/generation"
PATH_TASK_QUERY = "/api/v1/tasks/{id}"
PATH_TASK_CANCEL = "/api/v1/tasks/{id}/cancel"

# Models — mode → DashScope model id.
MODEL_S2V_DETECT = "wan2.2-s2v-detect"
MODEL_S2V = "wan2.2-s2v"
MODEL_VIDEORETALK = "videoretalk"
MODEL_ANIMATE_MIX = "wan2.2-animate-mix"
MODEL_ANIMATE_MOVE = "wan2.2-animate-move"
MODEL_I2I = "wan2.5-i2i-preview"
MODEL_WAN27_IMAGE = "wan2.7-image"
MODEL_WAN27_IMAGE_PRO = "wan2.7-image-pro"
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
        # DashScope's content/format moderation gate. Triggers when:
        #   - the URL is unreachable or returns wrong Content-Type
        #     (most common cause for our OSS uploads — usually a
        #     missing extension that defaulted to octet-stream),
        #   - the file is HEIC / AVIF / a too-large still,
        #   - or the content failed safety review.
        # Either way the user can't fix it by retrying — promote to
        # 「dependency」 so the UI surfaces a "fix the input" hint
        # instead of a generic 「服务器错误」.
        or "datainspection" in code.replace(".", "").replace("_", "")
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
            "Content-Type": "application/json",
        }

    def update_api_key(self, api_key: str) -> None:
        """Fast path used by ``PUT /settings``; the next call also re-reads."""
        if not isinstance(api_key, str):
            raise TypeError("api_key must be a string")
        self._last_settings["api_key"] = api_key.strip()

    def has_api_key(self) -> bool:
        return bool(self._settings().get("api_key"))

    async def ping_api_key(self, api_key: str | None = None) -> dict[str, Any]:
        """Cheap liveness probe: hit DashScope's OpenAI-compatible models
        endpoint and report whether the credential is actually accepted.

        We deliberately bypass :meth:`request` (which adds the
        ``X-DashScope-Async`` header and would also retry on 401) and call
        ``httpx`` directly with a *single* attempt. ``GET /v1/models`` returns
        the catalogue — no tokens are billed and the answer is unambiguous:
        HTTP 200 = key valid, 401/403 = key invalid, anything else = network
        or service trouble.

        ``api_key`` lets the caller test a key that has not yet been saved
        to disk; if omitted we test whatever ``self._settings()`` returns.
        Returns ``{ok, status, message}``.
        """
        try:
            import httpx  # local import: keeps cold-start cheap.
        except ImportError as e:
            return {"ok": False, "status": None, "message": f"httpx missing: {e}"}

        key = (api_key if api_key is not None else self._settings().get("api_key") or "")
        key = str(key).strip()
        if not key:
            return {"ok": False, "status": None, "message": "API Key is empty"}

        # Pin to the OpenAI-compatible models endpoint regardless of the
        # configured base_url — that path is stable across regions and never
        # bills tokens. We hard-code the BJ host because the compat endpoint
        # is only published there at the time of writing (2025-Q4); if the
        # user is on the SG region the BJ endpoint still resolves their key
        # because account-scoped credentials are global.
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
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
            # 401 = bad key, 403 = key valid but missing scope. Both mean
            # the user's current credential will not work for our calls.
            return {
                "ok": False,
                "status": resp.status_code,
                "message": f"API Key 无效或权限不足 (HTTP {resp.status_code})",
            }
        # Anything else (5xx, 429, etc.) we can't pin on the user's key.
        return {
            "ok": False,
            "status": resp.status_code,
            "message": f"DashScope 响应异常 (HTTP {resp.status_code})",
        }

    async def probe_models(
        self,
        models: list[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Probe each DashScope model the plugin uses for access.

        ``models`` is a list of ``(model_id, endpoint_path)`` tuples;
        when omitted we probe every model wired up by this plugin
        (s2v-detect, s2v, videoretalk, animate-mix, i2i, qwen-vl,
        cosyvoice-v2).

        How the probe works — there is no DashScope "describe model"
        endpoint, so we POST a deliberately-malformed body
        (``{"model": M, "input": {}}``) to the model's real submission
        URL and read the HTTP status:

        * **400** — DashScope reached the validator, meaning the key
          *can* invoke this model. We map this to ``available``.
        * **401 / 403** — credential or marketplace permission missing.
          Mapped to ``denied`` so the UI can prompt the user to open
          the model's marketplace page.
        * **404** — endpoint moved or the account's region doesn't ship
          this model. Mapped to ``denied`` with a different hint.
        * **200** — we accidentally created a real task (very unlikely
          with an empty input). We immediately POST cancel and report
          ``available`` so the user is never billed by the probe.
        * Anything else (timeout, 5xx, 429) → ``unknown``.

        Probes run in parallel with a 12s wall clock each. cosyvoice-v2
        uses the WebSocket SDK rather than HTTP, so it gets a
        ``compatible-mode/v1/models`` lookup instead.
        """
        try:
            import httpx
        except ImportError as e:
            return [{"model": "*", "status": "unknown", "message": f"httpx missing: {e}"}]

        s = self._settings()
        api_key = str(s.get("api_key") or "").strip()
        if not api_key:
            return [{"model": "*", "status": "unknown", "message": "API Key 未配置"}]
        base_url = str(s.get("base_url") or DASHSCOPE_BASE_URL_BJ).rstrip("/")

        # Default probe set covers every model the plugin actually
        # invokes anywhere in avatar_pipeline.py — keep this in sync if
        # a new mode is added, otherwise the Settings UI will report it
        # as 「未知」 even when the user does have access.
        if models is None:
            models = [
                (MODEL_S2V_DETECT, PATH_S2V_DETECT),
                (MODEL_S2V, PATH_S2V_SUBMIT),
                (MODEL_VIDEORETALK, PATH_VIDEORETALK_SUBMIT),
                (MODEL_ANIMATE_MIX, PATH_ANIMATE_MIX_SUBMIT),
                (MODEL_I2I, PATH_I2I_SUBMIT),
                (MODEL_QWEN_VL, PATH_QWEN_VL),
                (MODEL_COSYVOICE_V2, "__compat_models__"),
            ]

        async def _probe_one(
            client: httpx.AsyncClient, model_id: str, path: str,
        ) -> dict[str, Any]:
            # cosyvoice-v2 uses the WebSocket SDK (no HTTP probe path).
            # We instead look it up in the OpenAI-compat /v1/models list,
            # which DashScope updates when an account gets activated for
            # speech models.
            if path == "__compat_models__":
                # cosyvoice-v2 is a *WebSocket-only* model — DashScope's
                # OpenAI-compatible ``/v1/models`` catalogue has historically
                # NOT listed it (verified 2026-Q1), so the previous strategy
                # of "look it up in the list" produced a permanent 「未知」
                # badge even for fully-working accounts. This was the #1
                # source of "我开通了为什么还是未知?" support tickets.
                #
                # New strategy (审计方案 A):
                #   1. Run the cheap GET /v1/models probe, but use it ONLY to
                #      confirm that the *credentials* are accepted — we don't
                #      care whether cosyvoice is listed.
                #   2. If the key passes auth → mark cosyvoice-v2 as
                #      ``available`` with a clear message that this is a
                #      WebSocket model and only a real synth call can give
                #      a 100% verdict (the UI renders this as 「✓ 已推断
                #      可用，建议试听验证」 alongside a one-click "play
                #      sample" button).
                #   3. If the key fails auth → propagate ``denied`` so the
                #      Settings tab does not lie.
                #   4. Network errors stay ``unknown`` so transient blips
                #      don't downgrade a working setup.
                try:
                    r = await client.get(
                        f"{base_url}/compatible-mode/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    return {"model": model_id, "status": "unknown", "http": None,
                            "message": f"网络错误: {e!s}"}
                if r.status_code in (401, 403):
                    return {"model": model_id, "status": "denied", "http": r.status_code,
                            "message": "API Key 鉴权失败，cosyvoice-v2 无法使用"}
                if r.status_code != 200:
                    return {"model": model_id, "status": "unknown", "http": r.status_code,
                            "message": f"compatible-mode/models 返回 HTTP {r.status_code}"}
                # Credentials accepted — cosyvoice-v2 needs no separate
                # 「开通」 step on Bailian, so an authenticated key is a
                # strong-enough signal to call it 「可用」 in the panel.
                # We surface the "needs WebSocket / try the sample button"
                # caveat in ``message`` so the UI can render an info hint
                # rather than a green-then-broken experience.
                return {
                    "model": model_id,
                    "status": "available",
                    "http": 200,
                    "message": "凭证已通过鉴权（cosyvoice-v2 走 WebSocket，"
                               "无法预探测；点击「试听」可做端到端验证）",
                    "inferred": True,
                }

            url = f"{base_url}{path}"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            }
            body = {"model": model_id, "input": {}}
            try:
                r = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                return {"model": model_id, "status": "unknown", "http": None,
                        "message": "请求超时"}
            except httpx.NetworkError as e:
                return {"model": model_id, "status": "unknown", "http": None,
                        "message": f"网络错误: {e!s}"}
            code = r.status_code
            try:
                payload = r.json()
            except ValueError:
                payload = {}
            err_code = str(payload.get("code") or "")
            err_msg = str(payload.get("message") or "")[:160]
            if code == 200:
                # We accidentally got a task back — cancel it so the
                # user is never billed by the probe.
                tid = ""
                out = payload.get("output") if isinstance(payload, dict) else None
                if isinstance(out, dict):
                    tid = str(out.get("task_id") or "")
                if tid:
                    try:
                        await client.post(
                            f"{base_url}{PATH_TASK_CANCEL.format(id=tid)}",
                            headers=headers,
                        )
                    except (httpx.TimeoutException, httpx.NetworkError):
                        pass
                return {"model": model_id, "status": "available", "http": 200,
                        "message": "可用（探测时意外创建了任务，已自动取消）"}
            if code == 400:
                # The interesting case: DashScope reached its parameter
                # validator, which means the key *can* invoke this
                # endpoint — the request just wouldn't have been a real
                # one. That's exactly what we want to know.
                return {"model": model_id, "status": "available", "http": 400,
                        "message": err_msg or "可用（参数校验拒绝了空输入，符合预期）"}
            if code in (401, 403):
                return {"model": model_id, "status": "denied", "http": code,
                        "message": err_msg or f"未授权 (HTTP {code})；请到百炼控制台开通该模型"}
            if code == 404:
                return {"model": model_id, "status": "denied", "http": 404,
                        "message": err_msg or "该地域/账号下未提供此模型 (HTTP 404)"}
            if code == 429:
                # We probably hit a rate limit — the key is valid but we
                # can't tell about the model. Don't mis-classify.
                return {"model": model_id, "status": "unknown", "http": 429,
                        "message": err_msg or "限流，请稍后重试 (HTTP 429)"}
            return {"model": model_id, "status": "unknown", "http": code,
                    "message": err_msg or f"未知响应 (HTTP {code} {err_code})"}

        async with httpx.AsyncClient(timeout=12.0) as client:
            results = await asyncio.gather(
                *[_probe_one(client, m, p) for m, p in models],
                return_exceptions=True,
            )
        out: list[dict[str, Any]] = []
        for (m, _p), res in zip(models, results, strict=True):
            if isinstance(res, BaseException):
                out.append({"model": m, "status": "unknown", "http": None,
                            "message": f"探测异常: {res!s}"})
            else:
                out.append(res)
        return out

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
        ref_image_url: str = "",
        video_extension: bool = True,
    ) -> str:
        # Pre-flight URL guard: DashScope returns the same generic
        # ``InvalidParameter: url error`` whether the path is wrong, the
        # URL is empty, or the URL is unreachable. Catching the empty
        # case here turns it into an actionable 422 instead of a remote
        # 400 burning a request_id with no diagnostic value.
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
            # ``video_extension=true`` lets DashScope loop the source
            # video back-and-forth when the audio is longer than the
            # video — without it, an audio that overruns the video gets
            # silently truncated, which is almost never what the user
            # wants for a "lip-sync to my new audio" workflow.
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
        return await self._submit_async(PATH_ANIMATE_MIX_SUBMIT, body)

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
            "parameters": {
                "mode": "wan-pro" if mode_pro else "wan-std",
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
        params: dict[str, Any] = {"n": 1}
        if size:
            params["size"] = size
        # NOTE: wan2.5-i2i-preview's official body field is ``images``,
        # NOT ``ref_images_url`` (ref_images_url is wan2.7-image-edit's
        # multimodal-generation schema). Sending the wrong key produced
        # the cryptic upstream error
        # ``image compose failed: images field is required``. The
        # function arg is kept as ``ref_images_url`` for symmetry with
        # the wan2.7 method and the UI's payload key. See
        # https://help.aliyun.com/zh/model-studio/wan2-5-image-edit-api-reference
        body = {
            "model": MODEL_I2I,
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
        """Submit a wan2.7-image edit via the multimodal-generation endpoint."""
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
            import dashscope  # type: ignore[import-not-found]
            from dashscope.audio.tts_v2 import (  # type: ignore[import-not-found]
                AudioFormat,
                SpeechSynthesizer,
            )
        except ImportError as e:
            # Cosyvoice-v2 has no synchronous HTTP endpoint — only
            # the official SDK (which wraps a WebSocket stream).
            # Tell the user to install it into the *same* interpreter
            # that runs OpenAkita, not a global one — `sys.executable`
            # makes that interpreter unambiguous (e.g. .venv path).
            import sys
            raise VendorError(
                "未安装 cosyvoice-v2 TTS 所需的 dashscope SDK。"
                f"请在 OpenAkita 运行的 Python 环境中执行：\n"
                f"    {sys.executable} -m pip install dashscope\n"
                "（avatar-studio 仅在调用 cosyvoice-v2 TTS 时才需要此 SDK；"
                "其他模式与「上传现成音频」流程不受影响。）",
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

        # The dashscope SDK reads credentials from a *module-level* global
        # (`dashscope.api_key`) or the `DASHSCOPE_API_KEY` env var rather
        # than constructor args — so we must hot-set it here every call
        # to follow Pixelle A10 (read settings on every call, never cache).
        dashscope.api_key = api_key

        # Pick a real ``AudioFormat`` constant. Two prior bugs collided here:
        #   1. ``MP3_24000HZ_MONO_16BIT`` does NOT exist — MP3 is bitrate-
        #      indexed, e.g. ``MP3_22050HZ_MONO_256KBPS``. ``getattr(...,
        #      None)`` therefore *always* returned None, so the fallback
        #      branch ran and we synthesised with ``AudioFormat.DEFAULT``.
        #   2. ``format=`` is a constructor argument; ``call()`` only takes
        #      ``(text, timeout_millis)``. Passing it to ``call()`` would
        #      either raise TypeError or be silently dropped depending on
        #      the SDK minor version. Either way it had no effect.
        # Net effect of (1)+(2): the SDK returned raw PCM bytes (the
        # default codec) which we wrote to a ``.mp3`` file — the browser
        # then refused to decode, giving the 0:00/0:00 progress bar.
        fmt_candidates = {
            "mp3": ("MP3_22050HZ_MONO_256KBPS", "MP3_24000HZ_MONO_256KBPS",
                    "MP3_44100HZ_MONO_256KBPS", "MP3_16000HZ_MONO_128KBPS"),
            "wav": ("WAV_22050HZ_MONO_16BIT", "WAV_24000HZ_MONO_16BIT",
                    "WAV_16000HZ_MONO_16BIT"),
            "pcm": ("PCM_22050HZ_MONO_16BIT", "PCM_24000HZ_MONO_16BIT"),
        }
        fmt_const = None
        for name in fmt_candidates.get(format.lower(), ()):
            fmt_const = getattr(AudioFormat, name, None)
            if fmt_const is not None:
                break
        if fmt_const is None:
            # SDK shipped without any expected constant; fall back to the
            # SDK's own DEFAULT and *force* the file extension to .bin so
            # we never lie about the codec to the browser later on.
            fmt_const = getattr(AudioFormat, "DEFAULT", None)
            actual_ext = "bin"
        else:
            actual_ext = format.lower()

        synth = SpeechSynthesizer(
            model=MODEL_COSYVOICE_V2,
            voice=voice_id,
            format=fmt_const,  # MUST go on the constructor — `.call()` ignores it.
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

        # Unconditional diagnostic log so we can tell, post-hoc, exactly
        # what the SDK actually returned for any given preview click —
        # critical for debugging the "0:00/0:00 silent player" class of
        # bug where the file lands on disk but the browser refuses it.
        head = audio_bytes[:16]
        head_hex = head.hex(" ")
        logger.info(
            "cosyvoice-v2 returned %d bytes for voice=%s fmt=%s magic=[%s]",
            len(audio_bytes), voice_id,
            getattr(fmt_const, "name", str(fmt_const)),
            head_hex,
        )

        # Detect the *actual* container we got, regardless of what we asked
        # for. This auto-corrects the file extension when the SDK ignores
        # our format request (which is the most common "silent player"
        # failure mode in production).
        detected: str | None = None
        if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
            detected = "mp3"
        elif head.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE":
            detected = "wav"
        elif head.startswith(b"OggS"):
            detected = "ogg"
        elif head.startswith(b"fLaC"):
            detected = "flac"

        if detected is None:
            # Almost certainly raw PCM (16-bit signed mono @ 22050Hz is the
            # cosyvoice-v2 default). Wrap it in a minimal WAV header so the
            # browser can decode it natively — far better UX than handing
            # the user a .bin they can't play.
            sample_rate, channels, bits = 22050, 1, 16
            try:
                # Use the AudioFormat enum tuple if we asked for PCM; fall
                # back to the cosyvoice-v2 default (22050/mono/16) otherwise.
                tup = getattr(fmt_const, "value", None)
                if isinstance(tup, tuple) and len(tup) >= 4 and tup[0] == "pcm":
                    sample_rate, channels = int(tup[1]), 1
                    bits = int(tup[3])
            except (TypeError, ValueError):
                pass
            audio_bytes = _wrap_pcm_as_wav(
                audio_bytes, sample_rate=sample_rate, channels=channels, bits_per_sample=bits,
            )
            detected = "wav"
            logger.warning(
                "cosyvoice-v2 returned headerless audio (%d bytes); "
                "wrapped as WAV %dHz mono %dbit so the browser can play it",
                len(audio_bytes), sample_rate, bits,
            )

        return {
            "audio_bytes": audio_bytes,
            "format": detected,
            # Caller computes the precise duration after writing the file
            # (mp3 frame counting); here we return None so we never bluff.
            "duration_sec": None,
        }

    async def clone_voice(
        self,
        *,
        sample_url: str,
        prefix: str = "avatar",
        language: str = "zh",
    ) -> dict[str, Any]:
        """Train a custom cosyvoice-v2 voice from a single sample URL.

        Returns ``{"voice_id": <str>, "request_id": <str|None>}``.

        Implementation notes
        --------------------

        * Uses ``dashscope.audio.tts_v2.VoiceEnrollmentService.create_voice``
          (the only documented path for cosyvoice clone — there is no
          REST endpoint for it).  The SDK call is **synchronous**, so we
          run it in a worker thread to keep the FastAPI loop responsive.
        * ``url`` MUST be publicly fetchable by Aliyun — i.e. an OSS
          signed URL minted by ``OssUploader``. A local
          ``/api/plugins/...`` URL will fail at the DashScope side with
          a opaque "url unreachable" error.
        * ``prefix`` becomes part of the returned voice id; we hard-code
          a short fixed prefix ("avatar") because the user-supplied
          label is free-form Chinese which the SDK rejects.
        """
        try:
            import dashscope  # type: ignore[import-not-found]
            from dashscope.audio.tts_v2 import (  # type: ignore[import-not-found]
                VoiceEnrollmentService,
            )
        except ImportError as e:  # pragma: no cover
            raise VendorError(
                "未安装 cosyvoice-v2 所需的 dashscope SDK；"
                "请到插件目录运行 pip install dashscope",
                status=500,
                retryable=False,
                kind="dependency",
            ) from e

        api_key = self._read_settings().get("api_key") or ""
        if not api_key:
            raise VendorError(
                "DashScope API Key 未配置；无法克隆音色",
                status=400,
                retryable=False,
                kind="auth",
            )
        if not sample_url:
            raise VendorError(
                "clone_voice requires sample_url (an OSS signed URL)",
                status=422,
                retryable=False,
                kind="client",
            )
        # Set the SDK-global key (the SDK has no per-call api_key kwarg
        # on this service). Setting it on every call costs nothing and
        # also makes hot-rotation work without a process restart.
        dashscope.api_key = api_key

        def _sync() -> tuple[str, str | None]:
            svc = VoiceEnrollmentService()
            vid = svc.create_voice(
                target_model=MODEL_COSYVOICE_V2,
                prefix=str(prefix)[:10] or "avatar",
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
                kind=_classify_dashscope_body({}, ERROR_KIND_SERVER),
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

    _ASYNC_HEADER: dict[str, str] = {"X-DashScope-Async": "enable"}

    async def _submit_async(self, path: str, body: dict[str, Any]) -> str:
        """Serialise submissions and return the DashScope ``task_id``."""
        async with self._submit_lock:
            try:
                resp = await self.post_json(
                    path, json_body=body, timeout=60.0,
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
        """Multi-shape probe — tries known DashScope payload styles.

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
        # Shape 3: ``output.results`` — can be list OR dict depending on model.
        # wan2.2-s2v returns ``results: {video_url: "..."}`` (dict).
        # Image batch APIs return ``results: [{url: "..."}, ...]`` (list).
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
