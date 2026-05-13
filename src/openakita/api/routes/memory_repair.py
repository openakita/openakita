"""Memory database repair endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openakita.config import settings
from openakita.memory.storage import MemoryStorage
from openakita.memory.telemetry import emit_memory_health_event

from .health import mark_memory_repair_completed_restart_required

router = APIRouter(prefix="/api/memory/repair")

_repair_lock = asyncio.Lock()
_tokens: dict[str, float] = {}
_TOKEN_TTL_SECONDS = 300


class RepairRestoreRequest(BaseModel):
    source: str
    confirmation_token: str


class RepairTokenRequest(BaseModel):
    confirmation_token: str


def _memory_dir() -> Path:
    return Path(settings.data_dir) / "memory"


def _db_path() -> Path:
    return _memory_dir() / "openakita.db"


def _audit(action: str, result: str, **extra: Any) -> None:
    try:
        path = _memory_dir() / "repair_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "result": result,
            **extra,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _cleanup_old_recovery_pending(days: int = 30) -> None:
    root = _memory_dir() / ".recovery_pending"
    if not root.exists():
        return
    cutoff = time.time() - days * 24 * 60 * 60
    for path in root.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
        except Exception:
            pass


def _agent_memory_manager(request: Request):
    agent = getattr(request.app.state, "agent", None)
    return getattr(agent, "memory_manager", None) if agent is not None else None


def _require_degraded(request: Request):
    mm = _agent_memory_manager(request)
    if mm is None or not getattr(mm, "degraded", False):
        raise HTTPException(status_code=409, detail="memory subsystem is not degraded")
    return mm


def _desktop_token_expected() -> str:
    return os.environ.get("OPENAKITA_DESKTOP_SESSION_TOKEN", "")


def _verify_desktop_token(request: Request) -> None:
    expected = _desktop_token_expected()
    if not expected:
        # Dev/web mode compatibility. Confirmation token still prevents accidental
        # destructive calls, but production desktop should always set this token.
        return
    supplied = request.headers.get("X-OpenAkita-Desktop-Token", "")
    if not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="invalid desktop session token")


def _issue_confirmation_token() -> str:
    now = time.time()
    for token, expires in list(_tokens.items()):
        if expires < now:
            _tokens.pop(token, None)
    token = secrets.token_urlsafe(32)
    _tokens[token] = now + _TOKEN_TTL_SECONDS
    return token


def _consume_confirmation_token(token: str) -> str:
    expires = _tokens.pop(token, None)
    if expires is None or expires < time.time():
        raise HTTPException(status_code=403, detail="invalid or expired confirmation token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@contextmanager
def _file_repair_lock():
    lock_path = _memory_dir() / ".repair.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > 30 * 60:
                lock_path.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail="repair_in_progress") from e
    try:
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        yield
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _triplet(path: Path) -> list[Path]:
    return [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]


def _move_triplet(src: Path, dst_dir: Path) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in _triplet(src):
            if item.exists():
                target = dst_dir / item.name
                shutil.move(str(item), str(target))
                moved.append((item, target))
        return moved
    except Exception:
        for original, target in reversed(moved):
            try:
                if target.exists():
                    shutil.move(str(target), str(original))
            except Exception:
                pass
        raise


def _restore_moved_triplet(moved: list[tuple[Path, Path]]) -> None:
    for original, target in reversed(moved):
        try:
            if target.exists():
                if original.exists():
                    original.unlink(missing_ok=True)
                shutil.move(str(target), str(original))
        except Exception:
            pass


def _remove_wal_shm(path: Path) -> None:
    for item in _triplet(path)[1:]:
        item.unlink(missing_ok=True)


def _validate_db(path: Path) -> None:
    tmp = MemoryStorage(path, _register=False)
    try:
        tmp.quick_check_or_raise()
    finally:
        tmp.close()


def _list_candidates(pattern: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted(_memory_dir().glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        integrity = "unchecked"
        try:
            with sqlite3.connect(str(path)) as conn:
                row = conn.execute("PRAGMA quick_check").fetchone()
                integrity = "ok" if str(row[0] if row else "").lower() == "ok" else "corrupt"
        except Exception:
            integrity = "corrupt"
        out.append(
            {
                "filename": path.name,
                "created_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime)
                ),
                "size_bytes": path.stat().st_size,
                "integrity": integrity,
            }
        )
    return out


def _recover_method() -> str:
    candidates: list[Path] = []
    if os.environ.get("OPENAKITA_SQLITE3_EXE"):
        candidates.append(Path(os.environ["OPENAKITA_SQLITE3_EXE"]))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAkita" / "sqlite3.exe")
    if os.environ.get("PROGRAMFILES"):
        candidates.append(Path(os.environ["PROGRAMFILES"]) / "OpenAkita" / "sqlite3.exe")
    if any(p.exists() for p in candidates):
        return "bundled_sqlite3"
    if shutil.which("sqlite3"):
        return "bundled_sqlite3"
    return "python_iterdump"


def _sqlite3_executable() -> str | None:
    if os.environ.get("OPENAKITA_SQLITE3_EXE") and Path(os.environ["OPENAKITA_SQLITE3_EXE"]).exists():
        return os.environ["OPENAKITA_SQLITE3_EXE"]
    for env_name in ("LOCALAPPDATA", "PROGRAMFILES"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidate = Path(root) / "OpenAkita" / "sqlite3.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which("sqlite3")


@router.get("/status")
async def repair_status(request: Request):
    mm = _agent_memory_manager(request)
    degraded = bool(mm is not None and getattr(mm, "degraded", False))
    db = _db_path()
    return {
        "status": "degraded" if degraded else "healthy",
        "reason": getattr(mm, "degraded_reason", None) if mm else None,
        "details": getattr(mm, "degraded_details", None) if mm else None,
        "db_path": str(db),
        "db_size_bytes": db.stat().st_size if db.exists() else 0,
        "backups": _list_candidates("openakita.db.bak.*"),
        "snapshots": _list_candidates("openakita.db.snapshot.*"),
        "recover_method": _recover_method(),
        "desktop_token_required": bool(_desktop_token_expected()),
        "confirmation_token": _issue_confirmation_token(),
    }


async def _run_repair(request: Request, action: str, token: str, fn):
    _require_degraded(request)
    _verify_desktop_token(request)
    token_hash = _consume_confirmation_token(token)
    if _repair_lock.locked():
        raise HTTPException(status_code=409, detail="repair_in_progress")
    async with _repair_lock:
        with _file_repair_lock():
            try:
                result = await asyncio.to_thread(fn)
                mm = _agent_memory_manager(request)
                if mm is not None:
                    mm.repair_completed_restart_required = True
                mark_memory_repair_completed_restart_required()
                _audit(action, "success", token_hash=token_hash)
                emit_memory_health_event("repair_success", {"action": action})
                return result
            except HTTPException:
                _audit(action, "failed", token_hash=token_hash, error="http_exception")
                raise
            except Exception as e:
                _audit(action, "failed", token_hash=token_hash, error=str(e))
                raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/restore")
async def restore_backup(payload: RepairRestoreRequest, request: Request):
    def work():
        db = _db_path()
        source = (_memory_dir() / payload.source).resolve()
        if source.parent != _memory_dir().resolve() or not source.exists():
            raise HTTPException(status_code=404, detail="backup not found")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        quarantine = _memory_dir() / f".quarantine.{stamp}"
        moved = _move_triplet(db, quarantine)
        try:
            shutil.copy2(source, db)
            _remove_wal_shm(db)
            _validate_db(db)
        except Exception:
            for path in _triplet(db):
                path.unlink(missing_ok=True)
            _restore_moved_triplet(moved)
            raise
        return {"ok": True, "status": "repair_completed_restart_required"}

    return await _run_repair(request, "restore", payload.confirmation_token, work)


@router.post("/recreate")
async def recreate_database(payload: RepairTokenRequest, request: Request):
    def work():
        db = _db_path()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        quarantine = _memory_dir() / f".quarantine.{stamp}"
        _move_triplet(db, quarantine)
        tmp = MemoryStorage(db, _register=False)
        tmp.close()
        return {"ok": True, "status": "repair_completed_restart_required"}

    return await _run_repair(request, "recreate", payload.confirmation_token, work)


@router.post("/run-recover")
async def run_recover(payload: RepairTokenRequest, request: Request):
    def work():
        db = _db_path()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        recovered = _memory_dir() / f"openakita.recovered.{stamp}.db"
        sqlite3_exe = _sqlite3_executable()
        if sqlite3_exe:
            sql_path = _memory_dir() / f"openakita.recovered.{stamp}.sql"
            with sql_path.open("w", encoding="utf-8") as out:
                subprocess.run([sqlite3_exe, str(db), ".recover"], stdout=out, check=True)
            with sql_path.open("r", encoding="utf-8") as src:
                subprocess.run([sqlite3_exe, str(recovered)], stdin=src, check=True)
        else:
            with sqlite3.connect(str(db)) as src, sqlite3.connect(str(recovered)) as dst:
                for line in src.iterdump():
                    try:
                        dst.execute(line)
                    except Exception:
                        pass
        _validate_db(recovered)
        quarantine = _memory_dir() / f".quarantine.{stamp}"
        moved = _move_triplet(db, quarantine)
        try:
            shutil.move(str(recovered), str(db))
            _remove_wal_shm(db)
            _validate_db(db)
        except Exception:
            for path in _triplet(db):
                path.unlink(missing_ok=True)
            if recovered.exists():
                recovered.unlink(missing_ok=True)
            _restore_moved_triplet(moved)
            raise
        return {"ok": True, "status": "repair_completed_restart_required"}

    return await _run_repair(request, "run_recover", payload.confirmation_token, work)
