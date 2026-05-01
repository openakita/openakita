"""Async storage stats with file-count cap and pagination."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StorageStats:
    total_files: int = 0
    total_bytes: int = 0
    by_extension: dict[str, dict[str, int]] = field(default_factory=dict)
    truncated: bool = False
    sampled_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "by_extension": dict(self.by_extension),
            "truncated": self.truncated,
            "sampled_paths": list(self.sampled_paths),
        }


def _walk_sync(
    roots: list[Path],
    *,
    max_files: int,
    sample_paths: int,
    skip_hidden: bool,
) -> StorageStats:
    stats = StorageStats()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if skip_hidden and any(part.startswith(".") for part in path.parts):
                    continue
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except (OSError, PermissionError):
                continue
            ext = path.suffix.lower().lstrip(".") or "(none)"
            bucket = stats.by_extension.setdefault(ext, {"count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += size
            stats.total_files += 1
            stats.total_bytes += size
            if len(stats.sampled_paths) < sample_paths:
                stats.sampled_paths.append(str(path))
            if stats.total_files >= max_files:
                stats.truncated = True
                return stats
    return stats


async def collect_storage_stats(
    roots: str | Path | Iterable[str | Path],
    *,
    max_files: int = 5000,
    sample_paths: int = 5,
    skip_hidden: bool = True,
) -> StorageStats:
    roots_list = [Path(roots)] if isinstance(roots, (str, Path)) else [Path(r) for r in roots]
    return await asyncio.to_thread(
        _walk_sync,
        roots_list,
        max_files=max(1, int(max_files)),
        sample_paths=max(0, int(sample_paths)),
        skip_hidden=bool(skip_hidden),
    )

