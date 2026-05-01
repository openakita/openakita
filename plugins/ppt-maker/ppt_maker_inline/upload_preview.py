"""Upload preview route helpers for ppt-maker."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .file_utils import assert_within_root


def register_upload_preview_routes(router: APIRouter, root: str | Path, *, prefix: str = "/files") -> None:
    root_path = Path(root)

    @router.get(f"{prefix}/{{relative_path:path}}", response_class=FileResponse)
    async def preview_file(relative_path: str):
        try:
            target = assert_within_root(root_path, root_path / relative_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid file path") from exc
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(target)

