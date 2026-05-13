"""Runtime registry for installed/enabled/loaded skill state.

The registry is deliberately disk-first. Background installs or updates can
record a pending revision without mutating the current in-memory skill catalog;
the next reload/restart promotes the on-disk state to loaded state.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from openakita.runtime_env import _get_openakita_root


def _registry_path() -> Path:
    return _get_openakita_root() / "skills" / "installed_skills.json"


def _read() -> dict[str, Any]:
    try:
        raw = _registry_path().read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _deps_hash(deps: list[str] | tuple[str, ...] | None) -> str:
    deps_t = sorted({str(dep).strip() for dep in deps or [] if str(dep).strip()})
    if not deps_t:
        return ""
    return hashlib.sha256("\n".join(deps_t).encode("utf-8")).hexdigest()[:16]


def mark_skill_loaded(
    skill_id: str,
    *,
    source_path: str,
    enabled: bool = True,
    dependencies: list[str] | tuple[str, ...] | None = None,
) -> None:
    data = _read()
    skills = data.setdefault("skills", {})
    current = skills.get(skill_id) if isinstance(skills.get(skill_id), dict) else {}
    now = int(time.time())
    current.update(
        {
            "skill_id": skill_id,
            "source_path": source_path,
            "installed": True,
            "enabled": enabled,
            "loaded": True,
            "dependencies": list(dependencies or []),
            "deps_hash": _deps_hash(dependencies),
            "last_loaded_at": now,
            "pending_update_revision": "",
            "pending_update_at": 0,
            "reload_required": False,
        }
    )
    current.setdefault("installed_at", now)
    current.setdefault("update_policy", "disk-only")
    skills[skill_id] = current
    _write(data)


def mark_skill_pending_update(
    skill_id: str,
    *,
    revision: str,
    source_path: str = "",
) -> None:
    data = _read()
    skills = data.setdefault("skills", {})
    current = skills.get(skill_id) if isinstance(skills.get(skill_id), dict) else {}
    current.update(
        {
            "skill_id": skill_id,
            "source_path": source_path or current.get("source_path", ""),
            "installed": True,
            "loaded": bool(current.get("loaded")),
            "pending_update_revision": revision,
            "pending_update_at": int(time.time()),
            "update_policy": "disk-only",
            "reload_required": True,
        }
    )
    skills[skill_id] = current
    _write(data)


def read_skill_runtime_registry() -> dict[str, Any]:
    return _read()
