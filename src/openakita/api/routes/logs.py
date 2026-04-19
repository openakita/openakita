"""
Logs routes:
- GET  /api/logs/service   — Backend service log tail
- POST /api/logs/frontend  — Frontend log upload (Web/Capacitor mode)
- GET  /api/logs/frontend  — Frontend log tail
- GET  /api/logs/combined  — Combined backend + frontend logs (for log export)

In remote mode, the frontend uses these APIs to fetch/upload logs,
replacing Tauri's local file reads.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Frontend log file settings ──
_FRONTEND_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_FRONTEND_LOG_BACKUP_COUNT = 5
_frontend_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_file_path() -> Path:
    """Return the main service log file path from settings."""
    try:
        from openakita.config import settings

        return settings.log_file_path
    except Exception:
        return Path.cwd() / "logs" / "openakita.log"


def _frontend_log_path() -> Path:
    """Return the frontend log file path."""
    try:
        from openakita.config import settings

        return settings.log_dir_path / "frontend.log"
    except Exception:
        return Path.cwd() / "logs" / "frontend.log"


def _read_log_tail(log_path: Path, tail_bytes: int) -> dict:
    """Read the tail of a log file. Shared logic for service/frontend log reading."""
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
        content = raw.decode("utf-8", errors="replace")
        return {"path": path_str, "content": content, "truncated": truncated}
    except Exception as e:
        logger.error("Failed to read log %s: %s", log_path, e)
        return {"path": path_str, "content": "", "truncated": False, "error": str(e)}


def _rotate_frontend_log(path: Path) -> None:
    """Simple size-based rotation: frontend.log → frontend.log.1 → .2 …"""
    if not path.exists():
        return
    try:
        if path.stat().st_size < _FRONTEND_LOG_MAX_BYTES:
            return
    except OSError:
        return

    # Shift existing backups: .4→delete, .3→.4, .2→.3, .1→.2
    for i in range(_FRONTEND_LOG_BACKUP_COUNT, 0, -1):
        old = path.parent / f"{path.name}.{i}"
        if i == _FRONTEND_LOG_BACKUP_COUNT:
            old.unlink(missing_ok=True)
        elif old.exists():
            old.rename(path.parent / f"{path.name}.{i + 1}")

    # Rename current to .1
    try:
        path.rename(path.parent / f"{path.name}.1")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _resolve_tail_bytes(
    tail_bytes: int,
    tail: int | None,
    *,
    upper: int = 400_000,
) -> int:
    """Resolve the effective byte count from the ``tail_bytes`` / ``tail`` parameters.

    - ``tail_bytes`` takes precedence (legacy contract, used by existing callers).
    - ``tail`` is accepted as an alias (convenient for CLI testing and new callers),
      interpreted the same way — as a byte count.
    - Out-of-range values (negative or above ``upper``) are clamped to ``[0, upper]``
      to avoid 422 errors from FastAPI's own validator.
    """
    raw = tail_bytes if tail is None else tail
    try:
        n = int(raw)
    except Exception:
        n = 60_000
    if n < 0:
        n = 0
    if n > upper:
        n = upper
    return n


@router.get("/api/logs/service")
async def service_log(
    tail_bytes: int = Query(default=60000, ge=0, le=400000, description="Number of tail bytes to read"),
    tail: int | None = Query(default=None, description="Alias for tail_bytes; convenient for CLI and ad-hoc usage"),
):
    """Read the tail of the backend service log file."""
    return _read_log_tail(_log_file_path(), _resolve_tail_bytes(tail_bytes, tail))


class FrontendLogPayload(BaseModel):
    lines: list[str] = Field(..., max_length=100)


@router.post("/api/logs/frontend")
async def receive_frontend_log(request: Request):
    """
    Receive batched frontend logs and append them to logs/frontend.log.

    Supports both JSON body and sendBeacon (beacon requests may not use
    application/json content-type, so raw body parsing is also handled).
    """
    try:
        body = await request.json()
        lines = body.get("lines", [])
    except Exception:
        return {"ok": False, "error": "invalid JSON"}

    if not isinstance(lines, list) or len(lines) == 0:
        return {"ok": True, "written": 0}

    # Cap at 100 lines per request
    lines = lines[:100]

    log_path = _frontend_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with _frontend_log_lock:
            _rotate_frontend_log(log_path)
            with open(log_path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(str(line) + "\n")
    except Exception as e:
        logger.error("Failed to write frontend log: %s", e)
        return {"ok": False, "error": str(e)}

    return {"ok": True, "written": len(lines)}


@router.get("/api/logs/frontend")
async def frontend_log(
    tail_bytes: int = Query(default=60000, ge=0, le=400000, description="Number of tail bytes to read"),
    tail: int | None = Query(default=None, description="Alias for tail_bytes; convenient for CLI and ad-hoc usage"),
):
    """Read the tail of the frontend log file."""
    return _read_log_tail(_frontend_log_path(), _resolve_tail_bytes(tail_bytes, tail))


@router.get("/api/logs/combined")
async def combined_log(
    tail_bytes: int = Query(default=60000, ge=0, le=200000, description="Tail bytes to read per section"),
    tail: int | None = Query(default=None, description="Alias for tail_bytes; convenient for CLI and ad-hoc usage"),
):
    """
    Return combined tail of backend service log + frontend log,
    for the frontend's exportLogs() to fetch in a single request.
    """
    n = _resolve_tail_bytes(tail_bytes, tail, upper=200_000)
    return {
        "backend": _read_log_tail(_log_file_path(), n),
        "frontend": _read_log_tail(_frontend_log_path(), n),
    }
