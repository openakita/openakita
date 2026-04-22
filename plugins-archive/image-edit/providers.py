"""image-edit — multi-provider routing layer.

Strategy: try providers in priority order, falling back if the active one
is unavailable (no API key) or returns a retryable error.

Providers implement a uniform ``edit()`` async method:

    async def edit(self, *, image_path: Path, mask_path: Path | None,
                   prompt: str, negative_prompt: str = "",
                   size: str = "1024x1024", n: int = 1) -> EditResult

Where ``EditResult`` carries the local path(s) of the edited image(s).

Providers:
- ``OpenAIGptImageProvider`` — OpenAI ``gpt-image-1`` (best quality, paid).
- ``DashScopeWanxProvider``  — Alibaba 通义万相 (cheap, Chinese-friendly).

All providers reuse ``BaseVendorClient`` from the SDK for retry / timeout /
cancel-token plumbing.
"""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import base64
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from _shared import BaseVendorClient, VendorError

logger = logging.getLogger(__name__)


@dataclass
class EditResult:
    """Standard return shape across all providers."""

    provider: str
    output_paths: list[Path]
    raw: dict[str, Any]


# ── OpenAI gpt-image-1 ─────────────────────────────────────────────────


class OpenAIGptImageProvider(BaseVendorClient):
    """OpenAI ``gpt-image-1`` images.edits endpoint.

    Endpoint: ``POST /v1/images/edits`` (multipart/form-data)
    Docs: https://platform.openai.com/docs/api-reference/images/createEdit
    """

    name = "openai-gpt-image-1"

    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com",
                 timeout: float = 180.0) -> None:
        super().__init__(base_url=base_url, timeout=timeout)
        self._api_key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False  # OpenAI images.edits is synchronous, nothing to cancel server-side

    @classmethod
    def from_env(cls) -> "OpenAIGptImageProvider | None":
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
        if not key:
            return None
        return cls(api_key=key,
                   base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com"))

    async def edit(
        self,
        *,
        image_path: Path,
        mask_path: Path | None,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        n: int = 1,
        output_dir: Path,
    ) -> EditResult:
        if not prompt.strip():
            raise VendorError("prompt is empty")
        files: list[tuple[str, Any]] = [("image", (image_path.name, image_path.read_bytes(), "image/png"))]
        if mask_path and mask_path.exists():
            files.append(("mask", (mask_path.name, mask_path.read_bytes(), "image/png")))
        data = {
            "model": "gpt-image-1",
            "prompt": prompt + ((" Avoid: " + negative_prompt) if negative_prompt else ""),
            "size": size,
            "n": str(n),
            "response_format": "b64_json",
        }
        url = self.base_url.rstrip("/") + "/v1/images/edits"
        headers = self.auth_headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, data=data, files=files)
        except httpx.HTTPError as e:
            raise VendorError(f"openai network error: {e}", retryable=True) from e

        if resp.status_code >= 400:
            raise VendorError(
                f"openai HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
                retryable=resp.status_code in (429, 500, 502, 503, 504),
            )
        body = resp.json()
        out_paths: list[Path] = []
        for i, item in enumerate(body.get("data") or []):
            b64 = item.get("b64_json")
            if not b64:
                continue
            blob = base64.b64decode(b64)
            out = output_dir / f"{uuid.uuid4().hex[:12]}_{i}.png"
            out.write_bytes(blob)
            out_paths.append(out)
        return EditResult(provider=self.name, output_paths=out_paths, raw=body)


# ── DashScope (通义万相) image edit ─────────────────────────────────────


class DashScopeWanxProvider(BaseVendorClient):
    """Alibaba DashScope wanx-image-edit (mask-based inpaint).

    Endpoint: ``POST /api/v1/services/aigc/image2image/image-synthesis``
    Docs: https://help.aliyun.com/zh/dashscope/developer-reference/image-edit-api
    """

    name = "dashscope-wanx-edit"

    def __init__(self, *, api_key: str,
                 base_url: str = "https://dashscope.aliyuncs.com",
                 timeout: float = 180.0) -> None:
        super().__init__(base_url=base_url, timeout=timeout)
        self._api_key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}",
                "X-DashScope-Async": "enable"}

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False  # DashScope async tasks cannot be cancelled mid-flight

    @classmethod
    def from_env(cls) -> "DashScopeWanxProvider | None":
        key = os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            return None
        return cls(api_key=key)

    async def edit(
        self,
        *,
        image_path: Path,
        mask_path: Path | None,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        n: int = 1,
        output_dir: Path,
    ) -> EditResult:
        # DashScope expects publicly-reachable URLs; for local files we'd need
        # to upload to OSS first.  This implementation accepts URLs directly
        # passed via the ``image_url_override`` extra param at the call site.
        # For local-only mode we error out and let the caller fall back.
        raise VendorError(
            "DashScope wanx-edit requires a publicly accessible image URL. "
            "Please configure OSS upload or use the OpenAI provider.",
            retryable=False,
        )


# ── stub local provider (always available, for testing & dev) ──────────


class StubLocalProvider:
    """A no-op provider that copies the source image unchanged.

    Lets the plugin run end-to-end without any API key — handy for UI
    development, demos, and pytest.
    """

    name = "stub-local"

    async def edit(
        self,
        *,
        image_path: Path,
        mask_path: Path | None,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        n: int = 1,
        output_dir: Path,
    ) -> EditResult:
        out_paths: list[Path] = []
        for i in range(max(1, n)):
            out = output_dir / f"{uuid.uuid4().hex[:12]}_stub_{i}.png"
            out.write_bytes(image_path.read_bytes())
            out_paths.append(out)
        return EditResult(provider=self.name, output_paths=out_paths,
                          raw={"prompt": prompt, "note": "stub copy, no AI applied"})


# ── chooser ────────────────────────────────────────────────────────────


def select_provider(preferred: str = "auto") -> Any:
    """Pick a provider with degradation.

    Order:
    1. Explicit ``preferred`` name (errors out if unavailable)
    2. Auto: openai → dashscope → stub
    """
    if preferred == "openai":
        p = OpenAIGptImageProvider.from_env()
        if not p:
            raise VendorError("OPENAI_API_KEY is not set", retryable=False)
        return p
    if preferred == "dashscope":
        p = DashScopeWanxProvider.from_env()
        if not p:
            raise VendorError("DASHSCOPE_API_KEY is not set", retryable=False)
        return p
    if preferred == "stub":
        return StubLocalProvider()

    # auto
    return (
        OpenAIGptImageProvider.from_env()
        or DashScopeWanxProvider.from_env()
        or StubLocalProvider()
    )
