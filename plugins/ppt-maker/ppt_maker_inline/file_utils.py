"""File and path helpers for ppt-maker."""

from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path
from uuid import uuid4

PLUGIN_DATA_DIRNAME = "ppt-maker"
MAX_FILENAME_LEN = 120


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_plugin_data_root(base_dir: str | Path) -> Path:
    """Return the canonical plugin data root under ``api.get_data_dir()``."""
    base = ensure_dir(base_dir)
    if base.name == PLUGIN_DATA_DIRNAME:
        return base
    return ensure_dir(base / PLUGIN_DATA_DIRNAME)


def slugify(value: str, *, fallback: str = "item", max_len: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    chars = []
    last_dash = False
    for char in normalized.strip().lower():
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    slug = "".join(chars).strip("-")
    return (slug[:max_len].strip("-") or fallback) if max_len > 0 else (slug or fallback)


def safe_name(name: str, *, fallback: str = "file") -> str:
    normalized = unicodedata.normalize("NFKC", name or "")
    raw = Path(normalized).name
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw.strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) <= MAX_FILENAME_LEN:
        return cleaned
    suffix = Path(cleaned).suffix[:16]
    stem_len = max(16, MAX_FILENAME_LEN - len(suffix) - 9)
    return f"{Path(cleaned).stem[:stem_len]}-{uuid4().hex[:8]}{suffix}"


def unique_child(parent: str | Path, filename: str) -> Path:
    root = ensure_dir(parent)
    base = safe_name(filename)
    candidate = root / base
    if not candidate.exists():
        return candidate
    stem = candidate.stem or "file"
    suffix = candidate.suffix
    return root / f"{stem}-{uuid4().hex[:8]}{suffix}"


def assert_within_root(root: str | Path, child: str | Path) -> Path:
    root_path = Path(root).resolve()
    child_path = Path(child).resolve()
    child_path.relative_to(root_path)
    return child_path


def safe_remove(root: str | Path, child: str | Path) -> bool:
    target = assert_within_root(root, child)
    if not target.exists():
        return False
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return True


def project_dir(data_root: str | Path, project_id: str) -> Path:
    return ensure_dir(Path(data_root) / "projects" / safe_name(project_id))


def dataset_dir(data_root: str | Path, dataset_id: str) -> Path:
    return ensure_dir(Path(data_root) / "datasets" / safe_name(dataset_id))


def template_dir(data_root: str | Path, template_id: str) -> Path:
    return ensure_dir(Path(data_root) / "templates" / safe_name(template_id))

