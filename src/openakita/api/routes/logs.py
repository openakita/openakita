"""
Logs routes: GET /api/logs/service

远程模式下，前端通过此 API 获取后端服务日志，替代 Tauri 本地文件读取。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter()


def _log_file_path() -> Path:
    """Return the main service log file path from settings."""
    try:
        from openakita.config import settings

        return settings.log_file_path
    except Exception:
        # Fallback: cwd/logs/openakita.log
        return Path.cwd() / "logs" / "openakita.log"


@router.get("/api/logs/service")
async def service_log(
    tail_bytes: int = Query(default=60000, ge=0, le=400000, description="读取尾部字节数"),
):
    """
    读取后端服务日志文件尾部内容。

    返回格式与 Tauri openakita_service_log 命令一致：
    { path, content, truncated }
    """
    log_path = _log_file_path()
    path_str = str(log_path)

    if not log_path.exists():
        return {"path": path_str, "content": "", "truncated": False}

    try:
        file_size = log_path.stat().st_size
        start = max(0, file_size - tail_bytes)
        truncated = start > 0

        with open(log_path, "rb") as f:
            if start > 0:
                f.seek(start)
            raw = f.read()

        # Decode with lossy handling for non-UTF-8 bytes
        content = raw.decode("utf-8", errors="replace")

        return {"path": path_str, "content": content, "truncated": truncated}
    except Exception as e:
        logger.error(f"Failed to read service log: {e}")
        return {"path": path_str, "content": "", "truncated": False, "error": str(e)}
