"""Safe file-preview route helper for upload-handling plugins.

Vendored from ``openakita_plugin_sdk.contrib.upload_preview`` (SDK 0.6.0) into
subtitle-craft in 1.0.0 (forked from ``plugins/clip-sense/clip_sense_inline``);
see ``subtitle_craft_inline/__init__.py``. Mitigates issue
#479 ("uploaded image not visible in UI"): plugin POST ``/upload`` handlers
historically returned an absolute *server-side* path which the browser cannot
fetch.  This helper registers a tightly scoped GET endpoint (default:
``/uploads/{rel_path:path}``) on the caller's ``APIRouter`` so the UI can
render a previously uploaded asset back to the user with a normal
``<img src="/api/plugins/<id>/uploads/<file>">`` element.

Hardening:

- All paths are resolved against the canonical ``base_dir``.  Anything that
  escapes (``..`` traversal, absolute paths, symlinks pointing outside)
  is rejected with HTTP 403.
- Optional ``allowed_extensions`` filter (case-insensitive); requests for
  other extensions return 404 — not 403 — to avoid leaking existence info.
- Optional ``max_bytes`` cap; oversized files return 413.
- ``Cache-Control`` header set so static assets are revalidated cheaply.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

DEFAULT_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "avif"})
DEFAULT_AV_EXTENSIONS = frozenset({"mp4", "webm", "mov", "mkv", "wav", "mp3", "m4a", "ogg", "flac"})
DEFAULT_PREVIEW_EXTENSIONS = DEFAULT_IMAGE_EXTENSIONS | DEFAULT_AV_EXTENSIONS

__all__ = [
    "DEFAULT_AV_EXTENSIONS",
    "DEFAULT_IMAGE_EXTENSIONS",
    "DEFAULT_PREVIEW_EXTENSIONS",
    "add_upload_preview_route",
    "build_preview_url",
]


def _normalize_extensions(
    exts: Iterable[str] | None,
) -> frozenset[str] | None:
    if exts is None:
        return None
    return frozenset(e.lower().lstrip(".") for e in exts if e)


def build_preview_url(plugin_id: str, rel_path: str | Path) -> str:
    """Build the canonical preview URL for a stored upload.

    The URL is server-absolute (starts with ``/``) so it can be embedded
    directly in HTML attributes regardless of the page's current location.
    """
    rel_str = str(rel_path).replace("\\", "/").lstrip("/")
    return f"/api/plugins/{plugin_id}/uploads/{rel_str}"


def add_upload_preview_route(
    router: Any,
    *,
    base_dir: Path | str,
    route_path: str = "/uploads/{rel_path:path}",
    allowed_extensions: Iterable[str] | None = DEFAULT_PREVIEW_EXTENSIONS,
    max_bytes: int | None = 50 * 1024 * 1024,
    cache_seconds: int = 300,
) -> Callable[[str | Path], str]:
    """Register a GET route on ``router`` that safely streams ``base_dir`` files.

    Args:
        router: A FastAPI ``APIRouter``.
        base_dir: Absolute directory to serve files from.  Resolved once for
            canonical comparison; created if missing.
        route_path: The route path; default exposes files at
            ``/uploads/<rel>`` under the plugin's API prefix.
        allowed_extensions: Iterable of allowed extensions (no dot, case-
            insensitive).  Pass ``None`` to allow everything (not advised).
        max_bytes: Reject files larger than this with HTTP 413.  Pass
            ``None`` to disable the cap.
        cache_seconds: ``Cache-Control: max-age=...`` value (default 5 min).

    Returns:
        A ``make_url(rel_path)`` helper.  Plugins should call this from their
        own POST ``/upload`` handler to populate a ``url`` field in the JSON
        response — the field the UI then binds to ``<img src=...>``.
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    allowed = _normalize_extensions(allowed_extensions)

    @router.get(route_path)
    async def _serve(rel_path: str) -> FileResponse:
        if not rel_path or "\x00" in rel_path:
            raise HTTPException(status_code=404, detail="not found")
        try:
            candidate = (base / rel_path).resolve()
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=403, detail="forbidden") from e

        try:
            candidate.relative_to(base)
        except ValueError as e:
            raise HTTPException(status_code=403, detail="forbidden") from e

        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="not found")

        if allowed is not None:
            ext = candidate.suffix.lower().lstrip(".")
            if ext not in allowed:
                raise HTTPException(status_code=404, detail="not found")

        if max_bytes is not None:
            try:
                size = candidate.stat().st_size
            except OSError as e:
                raise HTTPException(status_code=404, detail="not found") from e
            if size > max_bytes:
                raise HTTPException(status_code=413, detail="file too large")

        return FileResponse(
            candidate,
            headers={"Cache-Control": f"public, max-age={int(cache_seconds)}"},
        )

    def make_url(rel_path: str | Path) -> str:
        rel_str = str(rel_path).replace("\\", "/").lstrip("/")
        prefix = route_path.split("{", 1)[0].rstrip("/")
        return f"{prefix}/{rel_str}"

    return make_url
