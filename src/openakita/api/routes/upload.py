"""
Upload route: POST /api/upload, GET /api/uploads/{filename}

文件/图片/语音上传和下载端点。
"""

from __future__ import annotations

import logging
import mimetypes
import os
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
        import os

        root = os.environ.get("OPENAKITA_ROOT", "").strip()
        base = Path(root) if root else Path.home() / ".openakita"
        UPLOAD_DIR = base / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_AUDIO_UPLOAD_SIZE = 512 * 1024 * 1024  # 512 MB
UPLOAD_CHUNK_SIZE = 1024 * 1024
_AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".amr",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".weba",
    ".webm",
    ".wma",
}
BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".sh", ".ps1"}


def _configured_size(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(float(raw) * 1024 * 1024))
    except ValueError:
        logger.warning("Invalid %s=%r, using default", name, raw)
        return default


def _is_audio_upload(filename: str | None, content_type: str | None) -> bool:
    if (content_type or "").lower().startswith("audio/"):
        return True
    return Path(filename or "").suffix.lower() in _AUDIO_EXTENSIONS


def _upload_limit_for(filename: str | None, content_type: str | None) -> int:
    if _is_audio_upload(filename, content_type):
        return _configured_size("OPENAKITA_MAX_AUDIO_UPLOAD_MB", MAX_AUDIO_UPLOAD_SIZE)
    return _configured_size("OPENAKITA_MAX_UPLOAD_MB", MAX_UPLOAD_SIZE)


def resolve_upload_path(url_or_filename: str) -> Path | None:
    """Return the local path for an uploaded file URL or filename.

    Desktop attachments are served through /api/uploads/*, but tools need a
    filesystem path. Keep this resolver in the upload module so all callers use
    the same path traversal checks as the download route.
    """
    value = (url_or_filename or "").strip()
    if not value:
        return None
    filename = value.rsplit("/", 1)[-1].split("?", 1)[0]
    if not filename:
        return None
    upload_dir = get_upload_dir().resolve()
    filepath = (upload_dir / filename).resolve()
    try:
        filepath.relative_to(upload_dir)
    except ValueError:
        return None
    if not filepath.is_file():
        return None
    return filepath


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

    # Save file in chunks so large voice notes do not have to fit in memory.
    max_size = _upload_limit_for(file.filename, file.content_type)
    size = 0
    try:
        with filepath.open("wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    out.close()
                    filepath.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"文件过大: {size / 1024 / 1024:.1f} MB"
                            f"（当前类型最大 {max_size // 1024 // 1024} MB）"
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        filepath.unlink(missing_ok=True)
        logger.exception("Failed to save upload: %s", file.filename)
        raise HTTPException(status_code=500, detail="文件上传失败，请稍后重试。")

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]

    return {
        "status": "ok",
        "upload_id": unique_name,
        "filename": unique_name,
        "original_name": file.filename,
        "size": size,
        "content_type": content_type,
        "mime_type": content_type,
        "url": f"/api/uploads/{unique_name}",
        "local_path": str(filepath),
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
