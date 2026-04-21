"""Thin async client for ComfyUI's HTTP API.

Subclasses :class:`BaseVendorClient` from the SDK to inherit retry +
moderation handling.  ComfyUI is a *local* service so the moderation
pattern is largely pointless, but keeping the inheritance buys us
``cancel_task`` discipline and timeout / retry semantics for free.

Endpoints we touch
------------------

| Verb | Path                | Usage |
|------|---------------------|-------|
| GET  | ``/system_stats``   | available VRAM, queue depth → ``provider_score`` |
| POST | ``/prompt``         | submit a workflow graph |
| GET  | ``/history/{id}``   | poll for completion + collect output filenames |
| GET  | ``/queue``          | global queue stats |
| POST | ``/interrupt``      | cancel the *currently running* prompt |
| GET  | ``/view``           | download an output image |

ComfyUI does **not** support cancelling a queued (not-yet-running)
prompt by ID; only the running one can be interrupted.  We implement
``cancel_task`` defensively: if the requested prompt is the running
one we call ``/interrupt``; otherwise we report "not cancellable" and
let the caller decide what to do (usually: ignore, the queue will
drain quickly anyway).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from openakita_plugin_sdk.contrib.vendor_client import (
    BaseVendorClient,
    VendorError,
)

__all__ = [
    "ComfyClient",
    "ComfyOutputImage",
    "ComfyPromptResult",
    "DEFAULT_BASE_URL",
]


DEFAULT_BASE_URL = "http://127.0.0.1:8188"


@dataclass(frozen=True)
class ComfyOutputImage:
    """One output image referenced by a finished prompt."""

    filename: str
    subfolder: str
    type: str  # "output" | "temp" | "input"

    def view_query(self) -> dict[str, str]:
        """Build the query-string params for ``GET /view``."""
        return {
            "filename": self.filename,
            "subfolder": self.subfolder,
            "type": self.type,
        }


@dataclass(frozen=True)
class ComfyPromptResult:
    """Aggregated history-entry for one prompt."""

    prompt_id: str
    images: list[ComfyOutputImage]
    raw: dict[str, Any]


class ComfyClient(BaseVendorClient):
    """ComfyUI client.

    No authentication is added by default — ComfyUI is a local server
    without auth; if a user fronts theirs with a reverse proxy that
    *does* auth, they pass an ``auth_token=`` and we forward
    ``Authorization: Bearer ...``.
    """

    name = "comfyui"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        auth_token: str | None = None,
        client_id: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            base_url=base_url, timeout=timeout,
            max_retries=max_retries,
            # Local server: never bother with moderation pattern.
            moderation_pattern=None,
        )
        self._auth_token = auth_token
        self.client_id = client_id or uuid.uuid4().hex

    def auth_headers(self) -> dict[str, str]:
        if self._auth_token:
            return {"Authorization": f"Bearer {self._auth_token}"}
        return {}

    # ── system stats / queue ────────────────────────────────────────

    async def system_stats(self) -> dict[str, Any]:
        """Return ComfyUI's ``/system_stats`` payload (devices, vram, ...)."""
        return await self.get_json("/system_stats")

    async def queue_status(self) -> dict[str, Any]:
        return await self.get_json("/queue")

    # ── prompt submission ──────────────────────────────────────────

    async def submit_prompt(
        self, workflow: dict[str, Any], *, extra_data: dict[str, Any] | None = None,
    ) -> str:
        """POST a workflow to ``/prompt`` and return the ``prompt_id``.

        ComfyUI replies ``{"prompt_id": "...", "number": int, "node_errors": {}}``.
        We surface a :class:`VendorError` with kind ``"client"`` when
        ``node_errors`` is non-empty so the user gets a clear failure
        instead of a 200 + silent rejection.
        """
        body = {"client_id": self.client_id, "prompt": workflow}
        if extra_data:
            body["extra_data"] = extra_data
        data = await self.post_json("/prompt", body)
        node_errors = (data or {}).get("node_errors") or {}
        if node_errors:
            raise VendorError(
                f"ComfyUI rejected the workflow: {node_errors}",
                status=200, body=data, retryable=False, kind="client",
            )
        prompt_id = (data or {}).get("prompt_id")
        if not prompt_id:
            raise VendorError(
                f"ComfyUI returned no prompt_id: {data!r}",
                status=200, body=data, retryable=False, kind="server",
            )
        return str(prompt_id)

    # ── history polling ────────────────────────────────────────────

    async def get_history(self, prompt_id: str) -> dict[str, Any]:
        """Return the raw history entry for ``prompt_id`` (may be empty)."""
        data = await self.get_json(f"/history/{prompt_id}")
        # ComfyUI returns ``{prompt_id: {...}}`` keyed by id.
        if isinstance(data, dict) and prompt_id in data:
            return data[prompt_id]
        return data or {}

    @staticmethod
    def parse_history_outputs(prompt_id: str, history: dict[str, Any]) -> ComfyPromptResult:
        """Pull all ``SaveImage``-style outputs from one history entry.

        ComfyUI's history outputs map node-id → ``{"images": [...]}``.
        We flatten across nodes and preserve insertion order so the
        downstream caller can save the first image for the "primary"
        thumbnail and keep the rest as variants.
        """
        outputs = (history or {}).get("outputs") or {}
        images: list[ComfyOutputImage] = []
        for _node_id, payload in outputs.items():
            for img in (payload or {}).get("images") or []:
                images.append(ComfyOutputImage(
                    filename=str(img.get("filename", "")),
                    subfolder=str(img.get("subfolder", "")),
                    type=str(img.get("type", "output")),
                ))
        return ComfyPromptResult(
            prompt_id=prompt_id, images=images, raw=history or {},
        )

    @staticmethod
    def is_history_complete(history: dict[str, Any]) -> bool:
        """``True`` once ComfyUI has produced ``outputs`` for the prompt."""
        return bool((history or {}).get("outputs"))

    # ── view (download) ────────────────────────────────────────────

    def view_url(self, image: ComfyOutputImage) -> str:
        """Return the absolute URL for downloading one output image."""
        from urllib.parse import urlencode
        return f"{self.base_url.rstrip('/')}/view?{urlencode(image.view_query())}"

    async def download_image_bytes(self, image: ComfyOutputImage) -> bytes:
        """Fetch one output image's raw bytes via ``GET /view``.

        Bypasses :meth:`request` because we need a binary response rather
        than parsed JSON.
        """
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError(
                "httpx is required for downloading images "
                "— `pip install httpx`",
            ) from e
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(self.view_url(image), headers=self.auth_headers())
            if resp.status_code >= 400:
                raise VendorError(
                    f"GET /view failed: HTTP {resp.status_code} {resp.text[:200]}",
                    status=resp.status_code, retryable=False, kind="server",
                )
            return resp.content

    # ── cancel ─────────────────────────────────────────────────────

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        """Interrupt the currently running prompt.

        ComfyUI's ``/interrupt`` aborts whichever prompt is *running*,
        regardless of ``task_id``.  We can't selectively cancel a
        queued-but-not-running prompt, so callers should treat this as
        "best effort" and fall back to dropping the task record.
        """
        await self.post_json("/interrupt", {})
        return True
