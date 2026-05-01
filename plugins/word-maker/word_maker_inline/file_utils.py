"""File and path helpers for word-maker."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_name(name: str, *, fallback: str = "file") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


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

