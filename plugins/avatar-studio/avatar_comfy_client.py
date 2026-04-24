"""ComfyKit wrapper with the same API shape as AvatarDashScopeClient.

Supports RunningHub (cloud) and local ComfyUI backends via comfykit.
The client is constructed lazily — only when a non-DashScope backend
is actually used — and reconstructed when the relevant settings change.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    """Raised when a ComfyKit workflow execution fails."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class AvatarComfyClient:
    """ComfyKit wrapper — lazy construction, config-hash invalidation."""

    def __init__(self, read_settings: Callable[[], dict[str, Any]]):
        self._read_settings = read_settings
        self._kit: Any | None = None
        self._config_hash: str = ""

    def _hash_config(self, cfg: dict[str, Any]) -> str:
        parts = [
            str(cfg.get("backend", "")),
            str(cfg.get("rh_api_key", "")),
            str(cfg.get("rh_instance_type", "")),
            str(cfg.get("comfyui_url", "")),
            str(cfg.get("comfyui_api_key", "")),
        ]
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    def _get_or_create_kit(self) -> Any:
        cfg = self._read_settings()
        h = self._hash_config(cfg)
        if self._kit is not None and h == self._config_hash:
            return self._kit

        try:
            from comfykit import ComfyKit  # type: ignore[import-untyped]
        except ImportError:
            raise WorkflowError(
                "comfykit is not installed. Run: pip install comfykit>=0.1.12",
                retryable=False,
            )

        backend = cfg.get("backend", "dashscope")
        kit_cfg: dict[str, Any] = {}
        if backend == "runninghub":
            kit_cfg["runninghub_api_key"] = str(cfg.get("rh_api_key") or "")
            inst = str(cfg.get("rh_instance_type") or "").strip()
            if inst:
                kit_cfg["runninghub_instance_type"] = inst
        elif backend == "comfyui_local":
            kit_cfg["comfyui_url"] = str(
                cfg.get("comfyui_url") or "http://127.0.0.1:8188"
            )
            api_key = str(cfg.get("comfyui_api_key") or "").strip()
            if api_key:
                kit_cfg["api_key"] = api_key
        else:
            raise WorkflowError(
                f"ComfyKit not applicable for backend={backend!r}",
                retryable=False,
            )

        self._kit = ComfyKit(**kit_cfg)
        self._config_hash = h
        logger.info("ComfyKit client created for backend=%s", backend)
        return self._kit

    async def submit_workflow(
        self,
        mode_id: str,
        workflow_ref: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a workflow and return the result synchronously.

        ``workflow_ref`` is a RunningHub workflow_id string or a local
        file path, depending on the backend.

        Returns ``{"status": str, "video_url": str|None, ...}``.
        """
        if not workflow_ref:
            raise WorkflowError(
                f"No workflow_id configured for mode {mode_id!r}",
                retryable=False,
            )
        kit = self._get_or_create_kit()
        try:
            result = kit.execute(workflow_ref, params)
        except Exception as exc:
            raise WorkflowError(str(exc), retryable=True) from exc

        status = getattr(result, "status", None) or "unknown"
        if status != "completed":
            msg = getattr(result, "msg", None) or getattr(result, "message", None) or ""
            raise WorkflowError(
                f"Workflow execution failed: status={status}, msg={msg}",
                retryable=False,
            )

        video_url = None
        for attr in ("videos", "video_url", "output_url"):
            val = getattr(result, attr, None)
            if val:
                video_url = val[0] if isinstance(val, (list, tuple)) else str(val)
                break

        return {
            "status": status,
            "video_url": video_url,
            "raw": result,
        }

    async def probe_backend(self) -> dict[str, Any]:
        """Test whether the configured backend is reachable."""
        cfg = self._read_settings()
        backend = cfg.get("backend", "dashscope")
        try:
            if backend == "runninghub":
                kit = self._get_or_create_kit()
                return {"ok": True, "backend": backend, "message": "RunningHub connected"}
            elif backend == "comfyui_local":
                import httpx
                url = str(cfg.get("comfyui_url") or "http://127.0.0.1:8188")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{url.rstrip('/')}/system_stats")
                    if resp.status_code == 200:
                        return {"ok": True, "backend": backend, "message": "ComfyUI connected"}
                    return {"ok": False, "backend": backend, "message": f"HTTP {resp.status_code}"}
            else:
                return {"ok": False, "backend": backend, "message": "not a ComfyKit backend"}
        except Exception as exc:
            return {"ok": False, "backend": backend, "message": str(exc)}
