"""Safe preview routes for uploaded files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def register_upload_preview_routes(
    router: APIRouter,
    uploads_root: str | Path,
    *,
    prefix: str = "/uploads",
) -> None:
    root = Path(uploads_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    @router.get(f"{prefix}/{{rel_path:path}}")
    async def preview_upload(rel_path: str) -> FileResponse:
        target = (root / rel_path).resolve()
        if root != target and root not in target.parents:
            raise HTTPException(status_code=400, detail="Invalid upload path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Upload not found")
        if target.stat().st_size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Upload is too large to preview")
        return FileResponse(target)

