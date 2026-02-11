"""
Upload route: POST /api/upload, GET /api/uploads/{filename}

文件/图片/语音上传和下载端点。
"""

from __future__ import annotations

import logging
import mimetypes
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Upload directory (inside workspace data)
UPLOAD_DIR: Path | None = None


def get_upload_dir() -> Path:
    """Get or create the upload directory."""
    global UPLOAD_DIR
    if UPLOAD_DIR is None:
        # Default to ~/.openakita/uploads
        home = Path.home()
        UPLOAD_DIR = home / ".openakita" / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".sh", ".ps1"}


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):  # noqa: B008
    """
    Upload a file (image, audio, document).
    Returns the file URL for use in chat messages.
    """
    upload_dir = get_upload_dir()

    # 安全检查：阻止危险文件扩展名
    ext = Path(file.filename or "file").suffix.lower()
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不允许上传该类型文件: {ext}")

    # Generate unique filename
    unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = upload_dir / unique_name

    # Save file（带大小限制）
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大: {len(content) / 1024 / 1024:.1f} MB（最大 {MAX_UPLOAD_SIZE // 1024 // 1024} MB）",
        )
    filepath.write_bytes(content)

    return {
        "status": "ok",
        "filename": unique_name,
        "original_name": file.filename,
        "size": len(content),
        "content_type": file.content_type,
        "url": f"/api/uploads/{unique_name}",
    }


@router.get("/api/uploads/{filename}")
async def serve_upload(filename: str):
    """
    Serve an uploaded file by its unique filename.
    """
    upload_dir = get_upload_dir()
    filepath = (upload_dir / filename).resolve()

    # 防止路径穿越：确保文件在 upload_dir 内
    # 使用 is_relative_to（比 str.startswith 更安全，避免前缀碰撞如 uploads_evil/）
    upload_dir_resolved = upload_dir.resolve()
    try:
        filepath.relative_to(upload_dir_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # 推断 MIME 类型
    media_type = mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
    return FileResponse(filepath, media_type=media_type)
