"""
Workspace backup & restore utilities.

Provides functions to:
- create_backup: pack workspace data into a .zip archive
- restore_backup: unpack a .zip archive into the workspace
- read/write backup_settings.json
- list existing backup archives
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BACKUP_SETTINGS_FILE = "backup_settings.json"
MANIFEST_FILE = "manifest.json"
BACKUP_FORMAT_VERSION = 1

DEFAULT_BACKUP_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "cron": "0 2 * * *",
    "backup_path": "",
    "max_backups": 5,
    "include_userdata": True,
    "include_media": False,
}

# ── Inclusion / exclusion rules ──────────────────────────────────────

_ALWAYS_INCLUDE_FILES = [
    ".env",
    "data/llm_endpoints.json",
    "data/skills.json",
    "data/disabled_views.json",
    "data/runtime_state.json",
    "data/proactive_feedback.json",
    "data/sub_agent_states.json",
]

_ALWAYS_INCLUDE_DIRS = [
    "identity",
    "data/agents",
    "data/sessions",
    "data/scheduler",
    "data/mcp",
    "data/telegram",
    "skills",
    "mcps",
]

_USERDATA_FILES = [
    "data/agent.db",
]

_USERDATA_DIRS = [
    "data/memory",
    "data/retrospects",
    "data/plans",
    "data/docs",
    "data/reports",
    "data/research",
]

_MEDIA_DIRS = [
    "data/generated_images",
    "data/sticker",
    "data/media",
    "data/output",
    "data/screenshots",
]

_ALWAYS_EXCLUDE_DIRS = {
    "logs",
    "data/llm_debug",
    "data/delegation_logs",
    "data/traces",
    "data/react_traces",
    "data/temp",
    "data/tool_overflow",
    "data/selfcheck",
    "data/openakita_docs",
    "identity/runtime",
    "node_modules",
    "Lib",
    "__pycache__",
}

_ALWAYS_EXCLUDE_FILES = {
    "data/backend.heartbeat",
    "package.json",
    "package-lock.json",
}

_EXCLUDE_ROOT_EXTENSIONS = {
    ".docx",
    ".xlsx",
    ".pptx",
    ".js",
    ".py",
    ".exe",
}

_SQLITE_TEMP_EXTENSIONS = {".db-shm", ".db-wal", ".db-journal"}


# ── Settings helpers ─────────────────────────────────────────────────


def read_backup_settings(workspace_path: Path) -> dict[str, Any]:
    """Read backup_settings.json, returning defaults for missing keys."""
    settings_path = workspace_path / "data" / BACKUP_SETTINGS_FILE
    result = dict(DEFAULT_BACKUP_SETTINGS)
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            result.update(data)
        except Exception as exc:
            logger.warning(f"Failed to read {settings_path}: {exc}")
    return result


def write_backup_settings(workspace_path: Path, settings: dict[str, Any]) -> None:
    """Persist backup settings to data/backup_settings.json."""
    settings_path = workspace_path / "data" / BACKUP_SETTINGS_FILE
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ── Backup creation ─────────────────────────────────────────────────


def _should_include(
    rel: str,
    *,
    include_userdata: bool,
    include_media: bool,
) -> bool:
    """Decide whether a relative path should be included in the backup."""
    rel_posix = rel.replace("\\", "/")

    # Always-exclude directories
    for excl in _ALWAYS_EXCLUDE_DIRS:
        if rel_posix == excl or rel_posix.startswith(excl + "/"):
            return False

    # Always-exclude files
    if rel_posix in _ALWAYS_EXCLUDE_FILES:
        return False

    # SQLite temporary/WAL files — transient, recreated on open
    for ext in _SQLITE_TEMP_EXTENSIONS:
        if rel_posix.endswith(ext):
            return False

    # Exclude root-level generated files by extension
    if "/" not in rel_posix:
        _, ext = os.path.splitext(rel_posix)
        if ext.lower() in _EXCLUDE_ROOT_EXTENSIONS:
            return False
        # Also exclude special junk files
        if rel_posix in ("$null", "-p"):
            return False

    # Always-include files
    if rel_posix in _ALWAYS_INCLUDE_FILES:
        return True

    # Always-include directories
    for inc in _ALWAYS_INCLUDE_DIRS:
        if rel_posix == inc or rel_posix.startswith(inc + "/"):
            return True

    # Userdata files & dirs
    if include_userdata:
        if rel_posix in _USERDATA_FILES:
            return True
        for inc in _USERDATA_DIRS:
            if rel_posix == inc or rel_posix.startswith(inc + "/"):
                return True

    # Media dirs
    if include_media:
        for inc in _MEDIA_DIRS:
            if rel_posix == inc or rel_posix.startswith(inc + "/"):
                return True

    # Anything else not explicitly included: skip
    return False


def create_backup(
    workspace_path: Path,
    output_dir: str,
    *,
    include_userdata: bool = True,
    include_media: bool = False,
    max_backups: int = 5,
) -> Path:
    """Create a .zip backup of the workspace.

    Returns the path to the created zip file.
    """
    workspace_path = Path(workspace_path).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    workspace_id = workspace_path.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"openakita-backup-{workspace_id}-{ts}.zip"
    zip_path = out / zip_name

    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "workspace_id": workspace_id,
        "include_userdata": include_userdata,
        "include_media": include_media,
    }

    file_count = 0
    total_size = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(workspace_path):
            # Skip hidden/excluded directories early for performance
            dirs[:] = [d for d in dirs if d not in ("node_modules", "Lib", "__pycache__", ".git")]

            for fname in files:
                full = Path(root) / fname
                try:
                    rel = full.relative_to(workspace_path).as_posix()
                except ValueError:
                    continue

                if _should_include(
                    rel,
                    include_userdata=include_userdata,
                    include_media=include_media,
                ):
                    try:
                        zf.write(full, rel)
                        file_count += 1
                        total_size += full.stat().st_size
                    except (PermissionError, OSError) as exc:
                        logger.warning(f"Skipping {rel}: {exc}")

        manifest["file_count"] = file_count
        manifest["total_size_bytes"] = total_size
        zf.writestr(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))

    logger.info(
        f"Backup created: {zip_path} ({file_count} files, "
        f"{total_size / 1024 / 1024:.1f} MB uncompressed)"
    )

    # Rotate old backups
    if max_backups > 0:
        _rotate_backups(out, workspace_id, max_backups)

    return zip_path


def _rotate_backups(backup_dir: Path, workspace_id: str, max_backups: int) -> None:
    """Remove oldest backup files exceeding max_backups."""
    pattern = re.compile(rf"^openakita-backup-{re.escape(workspace_id)}-\d{{8}}_\d{{6}}\.zip$")
    backups = sorted(
        (f for f in backup_dir.iterdir() if f.is_file() and pattern.match(f.name)),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old in backups[max_backups:]:
        try:
            old.unlink()
            logger.info(f"Rotated old backup: {old.name}")
        except OSError as exc:
            logger.warning(f"Failed to remove old backup {old.name}: {exc}")


# ── Backup restoration ───────────────────────────────────────────────


def restore_backup(zip_path: str, workspace_path: Path) -> dict[str, Any]:
    """Restore a workspace from a .zip backup.

    Returns metadata about what was restored.
    """
    zip_path_obj = Path(zip_path).resolve()
    workspace_path = Path(workspace_path).resolve()

    if not zip_path_obj.exists():
        raise FileNotFoundError(f"Backup file not found: {zip_path}")

    with zipfile.ZipFile(zip_path_obj, "r") as zf:
        # Read & validate manifest
        if MANIFEST_FILE not in zf.namelist():
            raise ValueError("Invalid backup: missing manifest.json")

        manifest = json.loads(zf.read(MANIFEST_FILE).decode("utf-8"))
        fmt_ver = manifest.get("format_version", 0)
        if fmt_ver > BACKUP_FORMAT_VERSION:
            raise ValueError(
                f"Backup format version {fmt_ver} is newer than supported "
                f"version {BACKUP_FORMAT_VERSION}"
            )

        # Safety check: reject any paths with ".." or absolute components
        for name in zf.namelist():
            if name == MANIFEST_FILE:
                continue
            normalized = os.path.normpath(name)
            if normalized.startswith("..") or os.path.isabs(normalized):
                raise ValueError(f"Unsafe path in backup: {name}")

        restored_count = 0
        skipped: list[str] = []
        for member in zf.infolist():
            if member.filename == MANIFEST_FILE:
                continue
            if member.is_dir():
                (workspace_path / member.filename).mkdir(parents=True, exist_ok=True)
                continue

            # Skip SQLite temporary files — they are transient and will be
            # recreated automatically; on Windows they may be memory-mapped
            # and cannot be overwritten (Errno 22 / EINVAL).
            if any(member.filename.endswith(ext) for ext in _SQLITE_TEMP_EXTENSIONS):
                logger.debug(f"Skipping SQLite temp file: {member.filename}")
                continue

            target = workspace_path / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored_count += 1
            except OSError as exc:
                skipped.append(member.filename)
                logger.warning(f"Skipping {member.filename} during restore: {exc}")

    if skipped:
        logger.warning(
            f"Restored {restored_count} files, skipped {len(skipped)} "
            f"(locked/inaccessible): {skipped[:10]}"
        )
    else:
        logger.info(f"Restored {restored_count} files from {zip_path_obj.name}")

    return {
        "restored_count": restored_count,
        "skipped_count": len(skipped),
        "skipped_files": skipped[:20],
        "manifest": manifest,
    }


# ── List existing backups ────────────────────────────────────────────


def list_backups(backup_path: str) -> list[dict[str, Any]]:
    """List .zip backup files in the given directory, newest first."""
    d = Path(backup_path)
    if not d.is_dir():
        return []

    pattern = re.compile(r"^openakita-backup-.+-\d{8}_\d{6}\.zip$")
    result: list[dict[str, Any]] = []

    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not f.is_file() or not pattern.match(f.name):
            continue

        stat = f.stat()
        info: dict[str, Any] = {
            "filename": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

        # Try to read manifest for extra metadata
        try:
            with zipfile.ZipFile(f, "r") as zf:
                if MANIFEST_FILE in zf.namelist():
                    m = json.loads(zf.read(MANIFEST_FILE).decode("utf-8"))
                    info["manifest"] = m
        except Exception:
            pass

        result.append(info)

    return result
