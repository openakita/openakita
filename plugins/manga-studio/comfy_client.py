"""manga-studio workflow backend — ComfyKit wrapper for RunningHub + local ComfyUI.

What this module is for
-----------------------
``MangaComfyClient`` is the *workflow* sibling of the direct-vendor clients
(``MangaWanxiangClient`` for image gen, ``MangaArkClient`` for image-to-video).
When the user picks ``backend="runninghub"`` or ``backend="comfyui_local"``
on POST /episodes, the pipeline routes panel-image and image-to-video work
through this client instead of the vendor-direct ones.

Three operations are supported, each driven by a workflow_id/path the user
configured in Settings:

- ``generate_image``   — single-shot image gen (replaces wan2.7-image).
                         Accepts 0..N reference image URLs so the
                         IP-Adapter / character-consistency node in the
                         workflow can pull them.
- ``generate_i2v``     — image-to-video (replaces Seedance 1.0 Lite I2V).
- ``generate_t2v``     — text-to-video fallback (when face-moderation
                         rejects the panel image).

Plus a ``probe_backend`` smoke test the Workflows tab + ``manga_workflow_test``
tool both call.

Why not direct HTTP to RunningHub?
----------------------------------
RunningHub's REST surface is shaped around the ComfyUI workflow model
(node graphs, parameter overrides, asynchronous polling). ``comfykit`` is
the SDK that mirrors that surface and already handles polling / output
extraction / error mapping for both RunningHub and a local ComfyUI install.
We wrap it (rather than re-implement) for two reasons:

1. ComfyKit is the de-facto SDK on these endpoints; rolling our own
   would burn engineering time on an undifferentiated stack layer.
2. RunningHub bumps their workflow schema occasionally (LTX 2.x, video
   nodes, etc.). Tracking those upstream is comfykit's job, not ours.

The dependency is *optional*: ``import comfykit`` is lazy. Plugins load
fine without it, and the user only sees the dependency error on the
first request that needs the workflow backend.

Anti-patterns avoided
---------------------
- Pixelle A1 (silent task drop): every code path raises a typed
  ``WorkflowError`` with a ``kind`` that maps to a manga_models error
  hint, so the UI can show bilingual copy.
- Pixelle A6 (over-eager imports): comfykit is imported inside
  ``_get_or_create_kit``; manga-studio loads with no comfykit installed.
- Pixelle A10 (config caching): ``_hash_config`` invalidates the
  ComfyKit instance whenever the relevant settings change, so editing
  the API key in the UI takes effect on the next request.
- Pipeline-blocking sync calls: ``kit.execute`` is sync and can run for
  minutes; we wrap it in ``asyncio.to_thread`` with an outer
  ``asyncio.wait_for`` so the pipeline's progress tick stays alive.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# ─── Error vocabulary ────────────────────────────────────────────────────
# Keys here line up with manga_models.ERROR_HINTS so the UI doesn't need
# a separate translation layer for workflow errors.

ERROR_KIND_DEPENDENCY = "dependency"
ERROR_KIND_CONFIG = "config"
ERROR_KIND_NETWORK = "network"
ERROR_KIND_TIMEOUT = "timeout"
ERROR_KIND_WORKFLOW = "workflow"


class WorkflowError(Exception):
    """Workflow-execution failure with a typed ``kind``.

    The ``kind`` is one of ``manga_models.ERROR_HINTS`` keys (or close to;
    the pipeline maps unknowns to ``unknown``). ``retryable`` tells the
    pipeline whether to retry transparently.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = ERROR_KIND_WORKFLOW,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "retryable": self.retryable,
        }


# ─── Backend constants ───────────────────────────────────────────────────

_BACKEND_RUNNINGHUB = "runninghub"
_BACKEND_COMFYUI_LOCAL = "comfyui_local"
_VALID_BACKENDS = frozenset({_BACKEND_RUNNINGHUB, _BACKEND_COMFYUI_LOCAL})

# Workflow modes — each maps to a settings key the user fills in. See
# ``_resolve_workflow_ref``.
MODE_IMAGE = "image"
MODE_ANIMATE = "animate"
MODE_T2V = "t2v"
_VALID_MODES = frozenset({MODE_IMAGE, MODE_ANIMATE, MODE_T2V})


_RH_KEY_BY_MODE = {
    MODE_IMAGE: "runninghub_workflow_image",
    MODE_ANIMATE: "runninghub_workflow_animate",
    MODE_T2V: "runninghub_workflow_t2v",
}
_LOCAL_KEY_BY_MODE = {
    MODE_IMAGE: "comfyui_workflow_image",
    MODE_ANIMATE: "comfyui_workflow_animate",
    MODE_T2V: "comfyui_workflow_t2v",
}


# ─── Client ──────────────────────────────────────────────────────────────


class MangaComfyClient:
    """ComfyKit wrapper specialised for manga-studio's image + video flows.

    Args:
        read_settings: A zero-arg callable that returns the current
            settings dict. The plugin layer wires this to
            ``self._load_settings`` so every call sees a fresh snapshot
            (Pixelle A10 — hot reload without plugin restart).
    """

    def __init__(self, read_settings: Callable[[], dict[str, Any]]) -> None:
        self._read_settings = read_settings
        self._kit: Any | None = None
        self._kit_backend: str = ""
        self._config_hash: str = ""

    # ── Public API ───────────────────────────────────────────────────────

    async def generate_image(
        self,
        *,
        prompt: str,
        ref_image_urls: list[str] | None = None,
        negative_prompt: str = "",
        size: str = "",
        seed: int | None = None,
        timeout_sec: float = 240.0,
    ) -> dict[str, Any]:
        """Run the user's image workflow once.

        Returns ``{image_url, raw}``. Raises ``WorkflowError`` on every
        failure mode; callers treat ``image_url`` as guaranteed-non-empty.
        """
        if not prompt:
            raise WorkflowError("prompt must be non-empty", kind=ERROR_KIND_CONFIG)
        workflow_ref = self._resolve_workflow_ref(MODE_IMAGE)
        params: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "ref_images": list(ref_image_urls or []),
        }
        if size:
            params["size"] = size
        if seed is not None:
            params["seed"] = int(seed)
        result = await self._execute(workflow_ref, params, timeout_sec=timeout_sec)
        url = self._extract_image_url(result)
        if not url:
            raise WorkflowError(
                f"image workflow {workflow_ref!r} finished but produced no image URL",
                kind=ERROR_KIND_WORKFLOW,
            )
        return {"image_url": url, "raw": result}

    async def generate_i2v(
        self,
        *,
        image_url: str,
        prompt: str,
        duration_sec: int = 5,
        ratio: str = "9:16",
        timeout_sec: float = 360.0,
    ) -> dict[str, Any]:
        """Run the user's image-to-video workflow once.

        Returns ``{video_url, raw}``. The pipeline's i2v step calls this
        once per panel.
        """
        if not image_url:
            raise WorkflowError("image_url must be non-empty", kind=ERROR_KIND_CONFIG)
        if not prompt:
            raise WorkflowError("prompt must be non-empty", kind=ERROR_KIND_CONFIG)
        workflow_ref = self._resolve_workflow_ref(MODE_ANIMATE)
        params: dict[str, Any] = {
            "image_url": image_url,
            "prompt": prompt,
            "duration": max(1, int(duration_sec)),
            "ratio": ratio,
        }
        result = await self._execute(workflow_ref, params, timeout_sec=timeout_sec)
        url = self._extract_video_url(result)
        if not url:
            raise WorkflowError(
                f"animate workflow {workflow_ref!r} finished but produced no video URL",
                kind=ERROR_KIND_WORKFLOW,
            )
        return {"video_url": url, "raw": result}

    async def generate_t2v(
        self,
        *,
        prompt: str,
        duration_sec: int = 5,
        ratio: str = "9:16",
        timeout_sec: float = 360.0,
    ) -> dict[str, Any]:
        """Run the user's text-to-video workflow once.

        The pipeline only calls this when the i2v path was rejected by
        face moderation (``moderation_face`` error kind), so it is the
        last-resort fallback rather than the default video path.
        """
        if not prompt:
            raise WorkflowError("prompt must be non-empty", kind=ERROR_KIND_CONFIG)
        workflow_ref = self._resolve_workflow_ref(MODE_T2V)
        params: dict[str, Any] = {
            "prompt": prompt,
            "duration": max(1, int(duration_sec)),
            "ratio": ratio,
        }
        result = await self._execute(workflow_ref, params, timeout_sec=timeout_sec)
        url = self._extract_video_url(result)
        if not url:
            raise WorkflowError(
                f"t2v workflow {workflow_ref!r} finished but produced no video URL",
                kind=ERROR_KIND_WORKFLOW,
            )
        return {"video_url": url, "raw": result}

    async def probe_backend(self) -> dict[str, Any]:
        """Smoke-test the configured backend.

        Never raises — the dict's ``ok`` field is the source of truth.
        Output shape: ``{ok, backend, message}``.

        Probe semantics:

        - RunningHub: check api_key is set + comfykit can be imported +
          a kit can be constructed. We don't issue a real workflow run
          here because that would cost the user money on every probe.
        - Local ComfyUI: HTTP GET ``{comfyui_local_url}/system_stats``
          (the standard ComfyUI status endpoint).
        """
        cfg = self._read_settings()
        try:
            backend = self._resolve_backend(cfg)
        except WorkflowError as exc:
            return {"ok": False, "backend": "unknown", "message": str(exc)}

        if backend == _BACKEND_RUNNINGHUB:
            return await self._probe_runninghub(cfg)
        return await self._probe_comfyui_local(cfg)

    # ── Internals — backend resolution & kit construction ──────────────

    def _resolve_backend(self, cfg: dict[str, Any]) -> str:
        backend = str(cfg.get("comfy_backend") or _BACKEND_RUNNINGHUB).strip().lower()
        if backend not in _VALID_BACKENDS:
            raise WorkflowError(
                f"unknown comfy_backend {backend!r}; expected one of {sorted(_VALID_BACKENDS)}",
                kind=ERROR_KIND_CONFIG,
            )
        return backend

    def _hash_config(self, cfg: dict[str, Any]) -> str:
        """Stable signature of the config bits the kit cares about.

        We hash every field that would change the kit's behaviour, so a
        user editing their API key in Settings invalidates the cached
        kit and forces a fresh construction on the next call.
        """
        parts = [
            str(cfg.get("comfy_backend", "")),
            str(cfg.get("runninghub_api_key", "")),
            str(cfg.get("comfyui_local_url", "")),
        ]
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()

    def _resolve_workflow_ref(self, mode: str) -> str:
        """Pull the workflow_id (RunningHub) or path (local ComfyUI) for ``mode``."""
        if mode not in _VALID_MODES:
            raise WorkflowError(
                f"unknown workflow mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
                kind=ERROR_KIND_CONFIG,
            )
        cfg = self._read_settings()
        backend = self._resolve_backend(cfg)
        keymap = _RH_KEY_BY_MODE if backend == _BACKEND_RUNNINGHUB else _LOCAL_KEY_BY_MODE
        ref = str(cfg.get(keymap[mode]) or "").strip()
        if not ref:
            raise WorkflowError(
                f"no workflow configured for {backend}.{mode!r} "
                f"(settings key {keymap[mode]!r}); fill it in Settings",
                kind=ERROR_KIND_CONFIG,
            )
        return ref

    def _get_or_create_kit(self) -> Any:
        """Lazy ComfyKit construction, with config-hash invalidation.

        Splits in two so unit tests can stub ``_construct_kit`` and
        verify the cache + invalidation logic without monkeypatching
        the comfykit import.
        """
        cfg = self._read_settings()
        h = self._hash_config(cfg)
        if self._kit is not None and h == self._config_hash:
            return self._kit
        backend = self._resolve_backend(cfg)
        kit = self._construct_kit(backend, cfg)
        self._kit = kit
        self._kit_backend = backend
        self._config_hash = h
        logger.info("manga-studio: ComfyKit constructed for backend=%s", backend)
        return kit

    def _construct_kit(self, backend: str, cfg: dict[str, Any]) -> Any:
        """Instantiate ``ComfyKit``. Override-able for tests."""
        try:
            from comfykit import ComfyKit  # type: ignore[import-untyped, import-not-found]
        except ImportError as exc:
            raise WorkflowError(
                "comfykit is not installed. "
                "Run: pip install 'comfykit>=0.1.12' to enable the workflow backend.",
                kind=ERROR_KIND_DEPENDENCY,
            ) from exc

        kit_cfg: dict[str, Any] = {}
        if backend == _BACKEND_RUNNINGHUB:
            api_key = str(cfg.get("runninghub_api_key") or "").strip()
            if not api_key:
                raise WorkflowError(
                    "runninghub_api_key is empty; configure it in Settings.",
                    kind=ERROR_KIND_CONFIG,
                )
            kit_cfg["runninghub_api_key"] = api_key
        else:
            url = str(cfg.get("comfyui_local_url") or "").strip()
            if not url:
                raise WorkflowError(
                    "comfyui_local_url is empty; configure it in Settings.",
                    kind=ERROR_KIND_CONFIG,
                )
            kit_cfg["comfyui_url"] = url

        try:
            return ComfyKit(**kit_cfg)
        except Exception as exc:  # noqa: BLE001 - upstream may raise anything
            raise WorkflowError(
                f"failed to instantiate ComfyKit({backend}): {exc}",
                kind=ERROR_KIND_DEPENDENCY,
            ) from exc

    # ── Internals — execution & status validation ───────────────────────

    async def _execute(
        self,
        workflow_ref: str,
        params: dict[str, Any],
        *,
        timeout_sec: float,
    ) -> Any:
        """Run ``kit.execute(workflow_ref, params)`` off-loop with a timeout."""
        kit = self._get_or_create_kit()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(kit.execute, workflow_ref, params),
                timeout=timeout_sec,
            )
        except TimeoutError as exc:
            # Note: the underlying thread keeps running; comfykit doesn't
            # expose a cancel hook. The next request will start fresh.
            raise WorkflowError(
                f"workflow {workflow_ref!r} did not finish within {timeout_sec:.0f}s",
                kind=ERROR_KIND_TIMEOUT,
                retryable=True,
            ) from exc
        except WorkflowError:
            raise
        except Exception as exc:  # noqa: BLE001 - comfykit / SDK errors
            raise WorkflowError(
                f"workflow {workflow_ref!r} crashed: {type(exc).__name__}: {exc}",
                kind=ERROR_KIND_WORKFLOW,
                retryable=True,
            ) from exc
        return self._validate_status(result, workflow_ref)

    @staticmethod
    def _validate_status(result: Any, workflow_ref: str) -> Any:
        """Reject results whose status field clearly indicates failure.

        ComfyKit returns objects with either an attribute or dict key
        named ``status``. Common terminal states from RunningHub are
        ``completed`` / ``succeeded`` / ``success``; anything else is
        treated as failure.
        """
        status = MangaComfyClient._field(result, "status")
        if not status:
            return result  # No status field → assume success and let extractor decide.
        normalised = str(status).lower()
        if normalised not in {"completed", "succeeded", "success"}:
            msg = (
                MangaComfyClient._field(result, "msg")
                or MangaComfyClient._field(result, "message")
                or ""
            )
            raise WorkflowError(
                f"workflow {workflow_ref!r} reported status={status!r} msg={msg!r}",
                kind=ERROR_KIND_WORKFLOW,
            )
        return result

    @staticmethod
    def _field(obj: Any, name: str) -> Any:
        """Read ``obj.name`` or ``obj[name]`` — comfykit returns either shape."""
        v = getattr(obj, name, None)
        if v is not None:
            return v
        if isinstance(obj, dict):
            return obj.get(name)
        return None

    @staticmethod
    def _extract_image_url(result: Any) -> str | None:
        """Walk the result for an image URL across the shapes comfykit returns.

        Tries a list of candidate fields (singular + plural + ``outputs``)
        and accepts both bare strings and dicts that contain a ``url`` /
        ``image_url`` key. Mirrors avatar-studio's three-shape probe so
        the same workflows work in both plugins.
        """
        for attr in ("images", "image_urls", "image_url", "outputs", "output_url"):
            val = MangaComfyClient._field(result, attr)
            url = _first_url(val, ("url", "image_url"))
            if url:
                return url
        return None

    @staticmethod
    def _extract_video_url(result: Any) -> str | None:
        for attr in ("videos", "video_urls", "video_url", "outputs", "output_url"):
            val = MangaComfyClient._field(result, attr)
            url = _first_url(val, ("url", "video_url"))
            if url:
                return url
        return None

    # ── Internals — probes ─────────────────────────────────────────────

    async def _probe_runninghub(self, cfg: dict[str, Any]) -> dict[str, Any]:
        api_key = str(cfg.get("runninghub_api_key") or "").strip()
        if not api_key:
            return {
                "ok": False,
                "backend": _BACKEND_RUNNINGHUB,
                "message": "runninghub_api_key is empty; configure it in Settings.",
            }
        try:
            self._get_or_create_kit()
        except WorkflowError as exc:
            return {"ok": False, "backend": _BACKEND_RUNNINGHUB, "message": str(exc)}
        return {
            "ok": True,
            "backend": _BACKEND_RUNNINGHUB,
            "message": "RunningHub credentials accepted (kit constructed; no test job dispatched).",
        }

    async def _probe_comfyui_local(self, cfg: dict[str, Any]) -> dict[str, Any]:
        url = str(cfg.get("comfyui_local_url") or "").strip()
        if not url:
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": "comfyui_local_url is empty; configure it in Settings.",
            }
        try:
            import httpx
        except ImportError:
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": "httpx is not installed; cannot probe local ComfyUI.",
            }
        probe_url = f"{url.rstrip('/')}/system_stats"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(probe_url)
        except httpx.ConnectError as exc:
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": f"connect failed at {probe_url}: {exc}",
            }
        except httpx.TimeoutException:
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": f"timeout probing {probe_url}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": f"probe failed: {type(exc).__name__}: {exc}",
            }
        if resp.status_code != 200:
            return {
                "ok": False,
                "backend": _BACKEND_COMFYUI_LOCAL,
                "message": f"HTTP {resp.status_code} from {probe_url}",
            }
        return {
            "ok": True,
            "backend": _BACKEND_COMFYUI_LOCAL,
            "message": f"ComfyUI reachable at {url}",
        }


# ─── Helpers ─────────────────────────────────────────────────────────────


def _first_url(val: Any, dict_keys: tuple[str, ...]) -> str | None:
    """Return the first string URL inside ``val`` matching the shapes
    comfykit emits.

    Accepted shapes:

    - ``"https://..."``                          → the URL itself.
    - ``["https://..."]``                        → first element.
    - ``[{"url": "https://..."}]``               → first element's url.
    - ``[{"video_url": "https://..."}]``         → first element's video_url.
    """
    if val is None:
        return None
    if isinstance(val, str):
        return val if val.strip() else None
    if isinstance(val, (list, tuple)):
        if not val:
            return None
        first = val[0]
        if isinstance(first, str):
            return first if first.strip() else None
        if isinstance(first, dict):
            for k in dict_keys:
                v = first.get(k)
                if isinstance(v, str) and v.strip():
                    return v
    if isinstance(val, dict):
        for k in dict_keys:
            v = val.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return None
