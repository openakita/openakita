"""Small storage statistics helper for plugin-owned data folders."""

from __future__ import annotations

from pathlib import Path


def collect_storage_stats(root: str | Path) -> dict[str, int]:
    root_path = Path(root)
    files = 0
    dirs = 0
    bytes_total = 0
    if not root_path.exists():
        return {"files": 0, "dirs": 0, "bytes": 0}
    for item in root_path.rglob("*"):
        if item.is_dir():
            dirs += 1
            continue
        if item.is_file():
            files += 1
            try:
                bytes_total += item.stat().st_size
            except OSError:
                continue
    return {"files": files, "dirs": dirs, "bytes": bytes_total}

