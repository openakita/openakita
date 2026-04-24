"""Upload + preview FastAPI route helpers — Phase 0 placeholder.

Phase 4 wires this into ``plugin.py`` to expose ``POST /upload`` and
``GET /uploads/{rel_path}`` for local-file workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def add_upload_preview_routes(
    router: Any,
    *,
    upload_dir: Path,
    api: Any,
    rel_prefix: str = "uploads",
) -> None:
    """Register upload + preview routes on the given APIRouter.

    Phase 4 implementation will:
      1. ``POST /upload`` → multipart, dedupe by sha256, return rel_path.
      2. ``GET /uploads/{rel_path:path}`` → ``api.create_file_response``.
    """

    _ = (router, upload_dir, api, rel_prefix)  # mark as intentionally unused


__all__ = ["add_upload_preview_routes"]
