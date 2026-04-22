"""Async storage stats with file-count cap and pagination.

Vendored from ``openakita_plugin_sdk.contrib.storage_stats`` (SDK 0.6.0) into
tongyi-image in 0.7.0; see ``tongyi_inline/__init__.py``. Replaces the
synchronous ``Path.rglob()`` pattern used in the original tongyi code path
which blocks the event loop and walks unbounded trees.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StorageStats:
    """Aggregate storage usage for one or more directories."""

    total_files: int = 0
    total_bytes: int = 0
    by_extension: dict[str, dict[str, int]] = field(default_factory=dict)
    truncated: bool = False    # ``True`` if we hit ``max_files``
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
    s = StorageStats()
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
        except (OSError, PermissionError):
            continue
        for p in iterator:
            try:
                if skip_hidden and any(part.startswith(".") for part in p.parts):
                    continue
                if not p.is_file():
                    continue
                size = p.stat().st_size
            except (OSError, PermissionError):
                continue
            ext = (p.suffix.lower().lstrip(".") or "(none)")
            bucket = s.by_extension.setdefault(ext, {"count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += size
            s.total_files += 1
            s.total_bytes += size
            if len(s.sampled_paths) < sample_paths:
                s.sampled_paths.append(str(p))
            if s.total_files >= max_files:
                s.truncated = True
                return s
    return s


async def collect_storage_stats(
    roots: str | Path | Iterable[str | Path],
    *,
    max_files: int = 5000,
    sample_paths: int = 5,
    skip_hidden: bool = True,
) -> StorageStats:
    """Walk one or more directories and collect storage stats off the loop."""
    roots_list = (
        [Path(roots)] if isinstance(roots, (str, Path))
        else [Path(r) for r in roots]
    )

    return await asyncio.to_thread(
        _walk_sync, roots_list,
        max_files=max(1, int(max_files)),
        sample_paths=max(0, int(sample_paths)),
        skip_hidden=bool(skip_hidden),
    )
