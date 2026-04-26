"""Storage statistics for excel-maker settings UI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def collect_storage_stats(root: str | Path, groups: dict[str, str | Path] | None = None) -> dict[str, Any]:
    root_path = Path(root)
    selected = groups or {
        "uploads": root_path / "uploads",
        "workbooks": root_path / "workbooks",
        "projects": root_path / "projects",
        "exports": root_path / "exports",
        "templates": root_path / "templates",
        "cache": root_path / "cache",
    }

    result: dict[str, Any] = {"root": str(root_path), "total_bytes": 0, "total_files": 0, "groups": {}}
    for name, folder in selected.items():
        folder_path = Path(folder)
        bytes_total = 0
        file_count = 0
        by_ext: Counter[str] = Counter()
        if folder_path.exists():
            for item in folder_path.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    size = item.stat().st_size
                except OSError:
                    continue
                bytes_total += size
                file_count += 1
                by_ext[item.suffix.lower() or "<none>"] += 1
        result["total_bytes"] += bytes_total
        result["total_files"] += file_count
        result["groups"][name] = {
            "path": str(folder_path),
            "bytes": bytes_total,
            "files": file_count,
            "extensions": dict(sorted(by_ext.items())),
        }
    return result

