"""Volcengine Ark API client for manga-studio Seedance video generation.

Forked from ``plugins/seedance-video/ark_client.py`` (132 lines, that file
is essentially complete) with two manga-specific extensions:

1. ``submit_seedance_i2v`` — one-call image-to-video helper that wraps
   ``create_task`` with the ``content=[{"type":"text", "text": prompt},
   {"type":"image_url", "image_url":{"url": image_url}}]`` body shape
   Seedance expects. Used by the pipeline's panel→video step.
2. ``submit_seedance_t2v`` — one-call text-to-video fallback used when
   the I2V call is rejected by Seedance's notoriously strict face
   moderation (Pixelle N1.4 — auto-fallback path).
3. ``poll_until_done`` — long-polling helper with caller-supplied
   progress callback. Mirrors the seedance-video poll loop but exposes
   it as a single coroutine the pipeline can await.

Uses ``BaseVendorClient`` from the vendored ``manga_inline.vendor_client``
helper; every HTTP call goes through ``self.request()`` /
``self.post_json()`` / ``self.get_json()`` which create a fresh
``httpx.AsyncClient`` per call. No long-lived connection to manage —
that is ``BaseVendorClient``'s design choice (avoids the connection-leak
class of bug we saw in early seedance-video iterations).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from manga_inline.vendor_client import BaseVendorClient, VendorError

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# Default Seedance model. The free-trial endpoint Volcengine ships is
# ``ep-20250116-seedance-1-0-lite-i2v``; users with paid plans usually
# create a custom endpoint and override this via ark_endpoint_id in
# Settings.
DEFAULT_SEEDANCE_I2V = "ep-20250116-seedance-1-0-lite-i2v"
DEFAULT_SEEDANCE_T2V = "ep-20250116-seedance-1-0-lite-t2v"


class MangaArkClient(BaseVendorClient):
    """Async Ark client for Seedance manga-drama generation.

    Args:
        read_settings: Callable returning ``{"ark_api_key": ...,
            "ark_endpoint_id": ...}``. The pipeline / plugin layers
            inject the ``Plugin._read_settings`` bound method here so
            users can edit API keys in the UI without reloading the
            plugin (Pixelle A10 — hot reload).
    """

    def __init__(
        self,
        *,
        read_settings: Callable[[], dict[str, Any]] | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(base_url=ARK_BASE_URL, timeout=60.0)
        self._read_settings = read_settings
        self._explicit_key = api_key

    def _current_settings(self) -> dict[str, Any]:
        if self._read_settings is not None:
            try:
                return self._read_settings() or {}
            except Exception as exc:  # noqa: BLE001 - never crash on settings probe
                logger.warning("manga-studio: read_settings failed: %s", exc)
        return {}

    def _current_api_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key
        return str(self._current_settings().get("ark_api_key") or "")

    def _current_endpoint(self, fallback: str) -> str:
        explicit = str(self._current_settings().get("ark_endpoint_id") or "")
        return explicit or fallback

    def auth_headers(self) -> dict[str, str]:
        # Re-evaluate the relay-resolved base URL on every request.
        # ``_read_settings`` (plugin layer) injects ``ark_base_url`` only
        # when a relay endpoint actively overrides ARK_BASE_URL — falling
        # back to the bare ARK_BASE_URL otherwise. We mutate ``self.base_url``
        # because BaseVendorClient.request reads ``self.base_url`` to
        # build the URL right after calling auth_headers().
        override = str(self._current_settings().get("ark_base_url") or "").strip()
        self.base_url = override or ARK_BASE_URL
        key = self._current_api_key()
        if not key:
            raise VendorError(
                "Ark API key not configured (Settings → ark_api_key)",
                kind="auth",
                status=401,
            )
        return {"Authorization": f"Bearer {key}"}

    def update_api_key(self, api_key: str) -> None:
        self._explicit_key = api_key

    async def close(self) -> None:
        """No-op — BaseVendorClient creates per-call httpx clients."""

    # ── Low-level Ark task surface (1:1 with seedance-video/ark_client) ──

    async def create_task(
        self,
        model: str,
        content: list[dict[str, Any]],
        *,
        ratio: str = "9:16",
        duration: int = 5,
        resolution: str = "480P",
        n: int = 1,
        generate_audio: bool = False,
        seed: int = -1,
        watermark: bool = False,
        camera_fixed: bool = False,
        draft: bool = False,
        return_last_frame: bool = False,
        tools: list[dict] | None = None,
        service_tier: str = "default",
        callback_url: str | None = None,
        execution_expires_after: int | None = None,
    ) -> dict:
        supported = self._current_settings().get("ark_supported_models") or []
        if supported and model.strip().lower() not in {
            str(m or "").strip().lower() for m in supported
        }:
            raise VendorError(
                f"中转站模型目录不包含 Ark 视频模型 {model!r}",
                status=422,
                retryable=False,
                kind="client",
            )
        body: dict[str, Any] = {"model": model, "content": content}
        if ratio:
            body["ratio"] = ratio
        if duration:
            body["duration"] = int(duration)
        if resolution:
            body["resolution"] = resolution
        if n and n > 1:
            body["n"] = n
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if watermark:
            body["watermark"] = watermark
        if seed >= 0:
            body["seed"] = seed
        if camera_fixed:
            body["camera_fixed"] = True
        if draft:
            body["draft"] = True
        if return_last_frame:
            body["return_last_frame"] = True
        if tools:
            body["tools"] = tools
        if service_tier != "default":
            body["service_tier"] = service_tier
        if callback_url:
            body["callback_url"] = callback_url
        if execution_expires_after:
            body["execution_expires_after"] = {"seconds": execution_expires_after}

        return await self.post_json(
            "/contents/generations/tasks",
            json_body=body,
            timeout=120.0,
        )

    async def get_task(self, task_id: str) -> dict:
        return await self.get_json(f"/contents/generations/tasks/{task_id}")

    async def list_tasks(
        self,
        page_num: int = 1,
        page_size: int = 20,
        filter_status: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"page_num": page_num, "page_size": page_size}
        if filter_status:
            params["filter"] = f'{{"status":"{filter_status}"}}'
        return await self.get_json("/contents/generations/tasks", params=params)

    async def delete_task(self, task_id: str) -> dict:
        return await self.request("DELETE", f"/contents/generations/tasks/{task_id}")

    async def cancel_task(self, task_id: str) -> bool:
        await self.request("DELETE", f"/contents/generations/tasks/{task_id}")
        return True

    async def validate_key(self) -> bool:
        """Quick validation by listing one task."""
        try:
            await self.list_tasks(page_size=1)
            return True
        except VendorError as exc:
            logger.warning("Ark key validation failed: %s (kind=%s)", exc, exc.kind)
            return False

    # ── manga-studio helpers ─────────────────────────────────────────────

    async def submit_seedance_i2v(
        self,
        *,
        prompt: str,
        image_url: str,
        ratio: str = "9:16",
        duration: int = 5,
        resolution: str = "480P",
        endpoint_id: str | None = None,
        seed: int = -1,
        camera_fixed: bool = False,
    ) -> dict:
        """One-call Seedance image-to-video — used per panel."""
        if not prompt:
            raise ValueError("prompt is required for I2V submission")
        if not image_url:
            raise ValueError("image_url is required for I2V submission")
        model = endpoint_id or self._current_endpoint(DEFAULT_SEEDANCE_I2V)
        return await self.create_task(
            model=model,
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            seed=seed,
            camera_fixed=camera_fixed,
        )

    async def submit_seedance_t2v(
        self,
        *,
        prompt: str,
        ratio: str = "9:16",
        duration: int = 5,
        resolution: str = "480P",
        endpoint_id: str | None = None,
        seed: int = -1,
        camera_fixed: bool = False,
    ) -> dict:
        """Text-to-video fallback — used when Seedance moderates the I2V
        face. The pipeline catches the moderation_face VendorError from
        ``submit_seedance_i2v`` and re-runs with this method, dropping
        the reference image (we lose character consistency but at least
        the panel video is produced).
        """
        if not prompt:
            raise ValueError("prompt is required for T2V submission")
        # The default I2V endpoint is also accepted by Volcengine for T2V
        # if the content has no image — most users have only one custom
        # endpoint configured. Settings.ark_endpoint_id can override.
        model = endpoint_id or self._current_endpoint(DEFAULT_SEEDANCE_T2V)
        return await self.create_task(
            model=model,
            content=[{"type": "text", "text": prompt}],
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            seed=seed,
            camera_fixed=camera_fixed,
        )

    async def poll_until_done(
        self,
        task_id: str,
        *,
        timeout_sec: float = 300.0,
        poll_interval: float = 3.0,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Poll Ark until ``status in {"succeeded","failed","cancelled"}``.

        Args:
            task_id:        Ark-issued task id from ``submit_seedance_*``.
            timeout_sec:    Max total wait (default 5 min — Seedance Lite
                            typically finishes in 30-90 s; the 300 s cap
                            covers cold-start queue spikes).
            poll_interval:  Seconds between polls. Ark's quotas allow ~20
                            polls/min; default 3 s is safe.
            on_progress:    Optional async callback called with each
                            poll's response dict so the pipeline can
                            broadcast SSE progress events to the UI.

        Returns the final ``get_task`` payload. Raises ``VendorError`` of
        kind ``timeout`` if ``timeout_sec`` elapses without a terminal
        status.
        """
        deadline = asyncio.get_event_loop().time() + max(1.0, timeout_sec)
        last: dict | None = None
        while asyncio.get_event_loop().time() < deadline:
            last = await self.get_task(task_id)
            if on_progress is not None:
                try:
                    await on_progress(last)
                except Exception as exc:  # noqa: BLE001 - progress is best-effort
                    logger.debug("on_progress callback failed: %s", exc)
            status = str(last.get("status") or "").lower()
            if status in {"succeeded", "completed", "failed", "cancelled", "canceled"}:
                return last
            await asyncio.sleep(poll_interval)
        raise VendorError(
            f"Seedance poll timed out after {timeout_sec:.0f}s (task_id={task_id!r})",
            kind="timeout",
        )

    @staticmethod
    def extract_video_url(response: dict) -> str | None:
        """Pull the rendered video URL out of a terminal-state response.

        Volcengine Ark wraps videos under ``content.video_url`` (singular
        for ``n=1``) or ``content.video_urls`` (list for ``n>1``). We try
        both shapes so the caller can stay vendor-agnostic.
        """
        content = response.get("content") or {}
        # Singular (most common for n=1).
        single = content.get("video_url")
        if isinstance(single, str) and single:
            return single
        # Plural — pick the first.
        many = content.get("video_urls")
        if isinstance(many, list) and many:
            head = many[0]
            if isinstance(head, str):
                return head
            if isinstance(head, dict):
                return head.get("url") or head.get("video_url")
        return None
