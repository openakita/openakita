"""DashScope wan2.7-image client for manga-studio panel generation.

Forked from ``plugins/avatar-studio/avatar_dashscope_client.py`` (1100+
lines, covers 8 endpoints) with the surface stripped down to manga-studio's
single image-generation use case:

- ``submit_image``     — submit a panel for ``wan2.7-image[-pro]``. Supports
                         0-9 reference images so the same call covers both
                         text-to-image (``ref_images_url=[]``) and the
                         reference-image-driven character-consistency mode
                         (Phase 2.5 prompt_assembler will pass character
                         reference sheet URLs here).
- ``query_task``       — single-shot DashScope async task probe.
- ``poll_until_done``  — long-poll helper with progress callback (mirrors
                         direct_ark_client.poll_until_done so the pipeline
                         can use one signature for both vendors).
- ``extract_output_image_url`` — multi-shape probe (``output.image_url`` /
                                 ``output.results[*].url`` /
                                 ``output.results.url``) — DashScope
                                 response shape varies between model
                                 versions (Pixelle ComfyUI three-shape
                                 lesson).

A single ``asyncio.Semaphore(1)`` guards every ``submit_*`` call because
DashScope async tasks share a per-key "1 in flight" cap. Polling is NOT
gated.

Hot config reload (Pixelle A10): the constructor takes a ``read_settings``
callable. Each request re-reads ``dashscope_api_key`` / ``dashscope_region``
so editing them in Settings takes effect without a plugin reload.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from manga_inline.vendor_client import (
    ERROR_KIND_CLIENT,
    ERROR_KIND_SERVER,
    ERROR_KIND_UNKNOWN,
    BaseVendorClient,
    VendorError,
)

logger = logging.getLogger(__name__)

# DashScope deploys two regions; users in mainland China hit the BJ
# endpoint, users on international plans hit Singapore.
DASHSCOPE_BASE_URL_BJ = "https://dashscope.aliyuncs.com"
DASHSCOPE_BASE_URL_SG = "https://dashscope-intl.aliyuncs.com"

# Multi-modal generation endpoint (the one that accepts wan2.7-image with
# 0-9 reference images). The simpler ``aigc/text2image`` endpoint exists
# but is wired to wanx-* (older, less consistent) and we deliberately
# don't expose it.
PATH_WAN_IMAGE = "/api/v1/services/aigc/multimodal-generation/generation"
PATH_TASK_QUERY = "/api/v1/tasks/{id}"

DEFAULT_MODEL = "wan2.7-image"
DEFAULT_MODEL_PRO = "wan2.7-image-pro"

# DashScope async submissions need this header.
_ASYNC_HEADER = {"X-DashScope-Async": "enable"}


# Manga-only error kinds that classify_dashscope_body promotes the
# generic ``client`` / ``server`` codes to. See manga_models.ERROR_HINTS
# for the bilingual user-facing copy.
ERROR_KIND_QUOTA = "quota"
ERROR_KIND_DEPENDENCY = "dependency"
ERROR_KIND_CONTENT_VIOLATION = "content_violation"


def _classify_dashscope_body(body: Any, fallback_kind: str) -> str:
    """Promote ``client`` / ``server`` to the manga-studio-specific kinds.

    Mirrors avatar-studio's classifier so the two plugins share the same
    error taxonomy for the same DashScope responses.
    """
    if not isinstance(body, dict):
        return fallback_kind
    code = str(body.get("code") or body.get("error_code") or "").lower()
    msg = str(body.get("message") or body.get("error_message") or "").lower()
    if "quota" in code or "balance" in code or "insufficient" in msg or "balance" in msg:
        return ERROR_KIND_QUOTA
    norm_code = code.replace(".", "").replace("_", "")
    if (
        "datainspection" in norm_code
        or "moderationerror" in norm_code
        or "content" in code
        and "policy" in code
    ):
        return ERROR_KIND_CONTENT_VIOLATION
    if "dependency" in code or "humanoid" in msg or ("human" in msg and "detect" in msg):
        return ERROR_KIND_DEPENDENCY
    return fallback_kind


def _is_async_done(status: str) -> bool:
    return status.upper() in {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}


def _is_async_ok(status: str) -> bool:
    return status.upper() == "SUCCEEDED"


ReadSettings = Callable[[], dict[str, Any]]


class MangaWanxiangClient(BaseVendorClient):
    """One client instance per plugin process (per Pixelle A10).

    All ``submit_*`` calls are serialised by ``self._submit_lock`` so we
    never violate DashScope's per-key "1 task in flight" cap on async
    endpoints. ``query_task`` and ``cancel_task`` are not serialised so
    the pipeline can poll while the next user submission queues up.
    """

    def __init__(
        self,
        *,
        read_settings: ReadSettings | None = None,
        max_retries: int = 2,
    ) -> None:
        super().__init__(timeout=60.0, max_retries=max_retries)
        self._read_settings = read_settings
        self._submit_lock = asyncio.Semaphore(1)
        # Prime base_url from settings so the first ``request()`` already
        # has the right URL prefix even if auth_headers() isn't touched.
        self._refresh_settings()

    # ── settings + auth ───────────────────────────────────────────────

    def _current_settings(self) -> dict[str, Any]:
        if self._read_settings is None:
            return {}
        try:
            return self._read_settings() or {}
        except Exception as exc:  # noqa: BLE001 - never raise from read
            logger.warning(
                "manga-studio: read_settings raised %s; falling back to defaults",
                exc,
            )
            return {}

    def _refresh_settings(self) -> dict[str, Any]:
        """Re-read settings + push to inherited ``base_url`` / ``timeout``.

        ``dashscope_base_url`` (set by the plugin layer when a relay
        endpoint is active) wins over the region-based default — that's
        how relay station overrides actually take effect on the wire.
        """
        cur = self._current_settings()
        relay_base = str(cur.get("dashscope_base_url") or "").strip()
        if relay_base:
            self.base_url = relay_base
        else:
            region = str(cur.get("dashscope_region") or "beijing").lower()
            self.base_url = (
                DASHSCOPE_BASE_URL_SG
                if region in ("sg", "singapore", "intl")
                else DASHSCOPE_BASE_URL_BJ
            )
        try:
            t = float(cur.get("dashscope_timeout") or 60.0)
            if t > 0:
                self.timeout = t
        except (TypeError, ValueError):
            pass
        return cur

    async def request(self, method: str, path: str, **kw: Any) -> Any:  # type: ignore[override]
        # Pixelle A10: re-read Settings before EVERY request so a Settings
        # change (api_key / region / timeout) takes effect immediately,
        # even mid-pipeline.
        self._refresh_settings()
        return await super().request(method, path, **kw)

    def auth_headers(self) -> dict[str, str]:
        cur = self._refresh_settings()
        api_key = str(cur.get("dashscope_api_key") or "").strip()
        if not api_key:
            raise VendorError(
                "DashScope API key not configured (Settings → dashscope_api_key)",
                kind="auth",
                status=401,
            )
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def has_api_key(self) -> bool:
        return bool(str(self._current_settings().get("dashscope_api_key") or "").strip())

    async def close(self) -> None:
        """No-op — BaseVendorClient creates per-call httpx clients."""

    # ── Image submission (text-to-image OR image-to-image with refs) ──

    async def submit_image(
        self,
        *,
        prompt: str,
        ref_images_url: list[str] | None = None,
        n: int = 1,
        size: str | None = None,
        model: str = DEFAULT_MODEL,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> str:
        """Submit one panel-generation request.

        Args:
            prompt:           Positive prompt, already assembled by
                              prompt_assembler. Required.
            ref_images_url:   Reference image URLs (0..9). Empty list is
                              the text-to-image mode; non-empty drives
                              wan2.7-image's reference-image character
                              consistency mode.
            n:                How many panels to render in one go (1..4).
                              Most callers pass 1 — the pipeline batches
                              by spawning multiple tasks instead so a
                              moderation rejection on one panel doesn't
                              kill the rest.
            size:             ``"1024*1024"`` style string. ``None`` lets
                              DashScope infer from the prompt aspect.
            model:            ``wan2.7-image`` (¥0.20/img) or
                              ``wan2.7-image-pro`` (¥0.50/img).
            negative_prompt:  Style negative prompt (visual style fragment
                              from manga_models.VISUAL_STYLES_BY_ID).
            seed:             Optional reproducibility seed.

        Returns:
            DashScope ``task_id`` to feed into ``query_task`` /
            ``poll_until_done``.
        """
        if not prompt:
            raise ValueError("prompt is required for image submission")
        refs = list(ref_images_url or [])
        if len(refs) > 9:
            raise VendorError(
                f"wan2.7-image accepts 0..9 reference images, got {len(refs)}",
                status=422,
                retryable=False,
                kind=ERROR_KIND_CLIENT,
            )
        if not 1 <= n <= 4:
            raise ValueError(f"n must be in 1..4, got {n}")
        if model not in {DEFAULT_MODEL, DEFAULT_MODEL_PRO}:
            raise ValueError(
                f"unknown model {model!r}; expected {DEFAULT_MODEL!r} or {DEFAULT_MODEL_PRO!r}"
            )
        supported = self._current_settings().get("dashscope_supported_models") or []
        if supported and model.strip().lower() not in {
            str(m or "").strip().lower() for m in supported
        }:
            raise VendorError(
                f"中转站模型目录不包含 DashScope 图像模型 {model!r}",
                kind=ERROR_KIND_CLIENT,
                status=422,
            )

        content: list[dict[str, str]] = [{"text": prompt}]
        for url in refs:
            if not isinstance(url, str) or not url:
                raise ValueError("reference image URLs must be non-empty strings")
            content.append({"image": url})

        params: dict[str, Any] = {"n": n}
        if size:
            params["size"] = size
        if negative_prompt:
            params["negative_prompt"] = negative_prompt
        if seed is not None:
            params["seed"] = int(seed)

        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }
        return await self._submit_async(PATH_WAN_IMAGE, body)

    async def _submit_async(self, path: str, body: dict[str, Any]) -> str:
        """Serialise submissions and return the DashScope ``task_id``."""
        async with self._submit_lock:
            try:
                resp = await self.post_json(
                    path,
                    json_body=body,
                    timeout=60.0,
                    extra_headers=_ASYNC_HEADER,
                )
            except VendorError as exc:
                exc.kind = _classify_dashscope_body(exc.body, exc.kind)
                raise
        out = _coerce_dict(resp.get("output"))
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

    # ── Task query + poll loop ────────────────────────────────────────

    async def query_task(self, task_id: str) -> dict[str, Any]:
        """Single-shot DashScope task query (no polling — caller loops)."""
        try:
            resp = await self.request(
                "GET",
                PATH_TASK_QUERY.format(id=task_id),
                timeout=20.0,
                max_retries=1,
            )
        except VendorError as exc:
            exc.kind = _classify_dashscope_body(exc.body, exc.kind)
            raise
        out = _coerce_dict(resp.get("output"))
        usage = _coerce_dict(resp.get("usage"))
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
            url, kind = self.extract_output_image_url(out)
            result["output_url"] = url
            result["output_kind"] = kind
        if status == "FAILED":
            result["error_kind"] = _classify_dashscope_body(out, ERROR_KIND_SERVER)
            result["error_message"] = out.get("message") or out.get("error_message") or ""
        return result

    async def poll_until_done(
        self,
        task_id: str,
        *,
        timeout_sec: float = 180.0,
        poll_interval: float = 2.0,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Poll DashScope until the task hits a terminal status."""
        deadline = asyncio.get_event_loop().time() + max(1.0, timeout_sec)
        last: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            last = await self.query_task(task_id)
            if on_progress is not None:
                try:
                    await on_progress(last)
                except Exception as exc:  # noqa: BLE001 - progress is best-effort
                    logger.debug("on_progress callback failed: %s", exc)
            if last["is_done"]:
                return last
            await asyncio.sleep(poll_interval)
        raise VendorError(
            f"DashScope poll timed out after {timeout_sec:.0f}s (task_id={task_id!r})",
            kind="timeout",
        )

    # ── Output URL probe ──────────────────────────────────────────────

    @staticmethod
    def extract_output_image_url(
        out: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Multi-shape probe — tries all known DashScope payload styles.

        Inspired by the Pixelle ComfyUI lesson: never bind to a single
        field path because DashScope's response shape varies between
        models. Returns ``(url, kind)`` where ``kind ∈ {"image","video"}``
        or ``(None, None)`` if no URL is present.
        """
        # Shape 1: ``output.image_url``
        i = out.get("image_url")
        if isinstance(i, str) and i:
            return i, "image"
        # Shape 2: ``output.video_url``
        v = out.get("video_url")
        if isinstance(v, str) and v:
            return v, "video"
        # Shape 3: ``output.results`` — list (image batch APIs) or dict
        # (single-image APIs).
        results = out.get("results")
        if isinstance(results, dict):
            u = results.get("url") or results.get("image_url") or results.get("video_url")
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


def _coerce_dict(v: Any) -> dict[str, Any]:
    """Return ``v`` if it's a dict, else an empty dict.

    DashScope sometimes returns a top-level ``output: null`` or
    ``output: ""`` when a request is rejected before the model runs;
    ``out.get(...)`` would crash without this guard.
    """
    return v if isinstance(v, dict) else {}
