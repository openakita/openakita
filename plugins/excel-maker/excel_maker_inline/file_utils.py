"""Filesystem helpers for excel-maker.

All generated files stay under the plugin data root unless the user explicitly
sets an export directory. These helpers centralize path safety rules.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def resolve_plugin_data_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for child in ("uploads", "workbooks", "projects", "exports", "templates", "cache"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def safe_name(name: str, fallback: str = "file") -> str:
    value = Path(name).name.strip() or fallback
    value = _SAFE_NAME_RE.sub("_", value)
    value = value.strip("._ ") or fallback
    return value[:160]


def unique_child(parent: str | Path, filename: str) -> Path:
    parent_path = Path(parent)
    parent_path.mkdir(parents=True, exist_ok=True)
    clean = safe_name(filename)
    stem = Path(clean).stem or "file"
    suffix = Path(clean).suffix
    candidate = parent_path / clean
    counter = 1
    while candidate.exists():
        candidate = parent_path / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def ensure_child(root: str | Path, path: str | Path) -> Path:
    root_path = Path(root).resolve()
    raw = Path(path).expanduser()
    target = raw.resolve() if raw.is_absolute() else (root_path / raw).resolve()
    if root_path != target and root_path not in target.parents:
        raise ValueError("Path is outside the plugin data directory")
    return target


def project_dir(data_root: str | Path, project_id: str) -> Path:
    path = Path(data_root) / "projects" / safe_name(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_dir(data_root: str | Path, project_id: str) -> Path:
    path = Path(data_root) / "exports" / safe_name(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_into(source: str | Path, target_dir: str | Path, filename: str | None = None) -> Path:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    target = unique_child(target_dir, filename or source_path.name)
    shutil.copy2(source_path, target)
    return target


def write_probe(directory: str | Path) -> Path:
    path = Path(directory).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".excel_maker_probe_{int(time.time() * 1000)}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    return path

