"""Safe file-preview route helper for word-maker uploads and exports."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

DEFAULT_DOCUMENT_EXTENSIONS = frozenset({"docx", "md", "txt", "json", "csv", "xlsx", "pptx", "pdf"})


def _normalize_extensions(exts: Iterable[str] | None) -> frozenset[str] | None:
    if exts is None:
        return None
    return frozenset(e.lower().lstrip(".") for e in exts if e)


def build_preview_url(plugin_id: str, rel_path: str | Path) -> str:
    rel_str = str(rel_path).replace("\\", "/").lstrip("/")
    return f"/api/plugins/{plugin_id}/files/{rel_str}"


def add_upload_preview_route(
    router: Any,
    *,
    base_dir: Path | str,
    route_path: str = "/files/{rel_path:path}",
    allowed_extensions: Iterable[str] | None = DEFAULT_DOCUMENT_EXTENSIONS,
    max_bytes: int | None = 100 * 1024 * 1024,
    cache_seconds: int = 300,
) -> Callable[[str | Path], str]:
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    allowed = _normalize_extensions(allowed_extensions)

    @router.get(route_path, response_class=FileResponse)
    async def _serve(rel_path: str):
        if not rel_path or "\x00" in rel_path:
            raise HTTPException(status_code=404, detail="not found")
        try:
            candidate = (base / rel_path).resolve()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=403, detail="forbidden") from exc
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="forbidden") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="not found")
        if allowed is not None and candidate.suffix.lower().lstrip(".") not in allowed:
            raise HTTPException(status_code=404, detail="not found")
        if max_bytes is not None:
            try:
                if candidate.stat().st_size > max_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
            except OSError as exc:
                raise HTTPException(status_code=404, detail="not found") from exc
        response = FileResponse(candidate)
        response.headers["Cache-Control"] = f"private, max-age={int(cache_seconds)}"
        return response

    def _make_url(rel_path: str | Path) -> str:
        return build_preview_url("word-maker", rel_path)

    return _make_url

