"""
Single read/write entry point for the external skill allowlist (data/skills.json).

Goals:
- All API/tool/background modules must read/write skills.json through this module
  to avoid races or format drift from multiple write paths.
- Writes are atomic (write to temp file + os.replace), preventing half-written files on crash.
- In-process concurrent writes are serialized via ``_WRITE_LOCK``.

Return conventions:
- ``external_allowlist is None`` means ``data/skills.json`` does not exist or has no
  allowlist declared (business semantics: all skills enabled).
- ``external_allowlist is set()`` means the user explicitly disabled all external skills.

This module itself does **not** trigger cache invalidation or agent notifications;
callers must invoke ``Agent.propagate_skill_change`` after writing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.RLock()


def _skills_json_path() -> Path:
    """Resolve the path to data/skills.json for the current workspace."""
    try:
        from ..config import settings

        return Path(settings.project_root) / "data" / "skills.json"
    except Exception:
        return Path.cwd() / "data" / "skills.json"


def read_allowlist() -> tuple[Path, set[str] | None]:
    """Read ``external_allowlist`` from ``data/skills.json``.

    Returns:
        A (path, allowlist) tuple:
        - path: absolute path to the workspace skills.json
        - allowlist: the explicit allowlist read from the file; ``None`` when
          the file does not exist, is corrupt, or has no allowlist declared
    """
    path = _skills_json_path()
    if not path.exists():
        return path, None
    try:
        raw = path.read_text(encoding="utf-8")
        cfg = json.loads(raw) if raw.strip() else {}
        al = cfg.get("external_allowlist", None)
        if isinstance(al, list):
            return path, {str(x).strip() for x in al if str(x).strip()}
        return path, None
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return path, None


def _atomic_write_json(path: Path, content: dict) -> None:
    """Atomically write JSON to path: write to a temp file first, then os.replace to overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(content, ensure_ascii=False, indent=2) + "\n"

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=".skills.", suffix=".json.tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(serialized)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise


def _compose_content(allowlist: set[str]) -> dict:
    return {
        "version": 1,
        "external_allowlist": sorted(allowlist),
        "updated_at": datetime.now().isoformat(),
    }


def overwrite_allowlist(allowlist: set[str] | None) -> Path:
    """Overwrite ``data/skills.json`` with the full allowlist.

    Args:
        allowlist: the target allowlist set; passing ``None`` is treated as
          an empty set (disable all external skills).

    Returns:
        The actual file path written.
    """
    path = _skills_json_path()
    final = set(allowlist) if allowlist else set()
    with _WRITE_LOCK:
        _atomic_write_json(path, _compose_content(final))
    logger.info("[skills.json] overwrite allowlist (%d ids) -> %s", len(final), path)
    return path


def upsert_skill_ids(skill_ids: set[str]) -> Path | None:
    “””Atomically merge given skill_ids into the existing allowlist.

    - If skills.json does not exist: **no** new file is created; returns ``None``
      (semantics: undeclared allowlist = all enabled; newly installed skills are
      enabled by default, no need to write to disk).
    - If skills.json exists but has no external_allowlist field: same as above,
      returns ``None``.
    - If skills.json already has external_allowlist: merge skill_ids and write back atomically.
    “””
    if not skill_ids:
        return None

    with _WRITE_LOCK:
        path = _skills_json_path()
        if not path.exists():
            return None

        try:
            raw = path.read_text(encoding="utf-8")
            cfg = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            logger.warning("skills.json unreadable, skip upsert: %s", e)
            return None

        current = cfg.get("external_allowlist", None)
        if not isinstance(current, list):
            return None

        merged = {str(x).strip() for x in current if str(x).strip()} | {
            s.strip() for s in skill_ids if s and s.strip()
        }
        _atomic_write_json(path, _compose_content(merged))

    logger.info("[skills.json] upsert %d skill id(s): %s", len(skill_ids), sorted(skill_ids))
    return path


def remove_skill_ids(skill_ids: set[str]) -> Path | None:
    """Remove given skill_ids from the existing allowlist (uninstall scenario).

    Returns ``None`` (no-op) when skills.json does not exist or has no allowlist.
    """
    if not skill_ids:
        return None

    with _WRITE_LOCK:
        path = _skills_json_path()
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            cfg = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            logger.warning("skills.json unreadable, skip remove: %s", e)
            return None

        current = cfg.get("external_allowlist", None)
        if not isinstance(current, list):
            return None

        remaining = {str(x).strip() for x in current if str(x).strip()} - {
            s.strip() for s in skill_ids if s
        }
        _atomic_write_json(path, _compose_content(remaining))

    logger.info("[skills.json] remove %d skill id(s): %s", len(skill_ids), sorted(skill_ids))
    return path
