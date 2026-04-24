"""Per-task storage usage helpers — Phase 0 placeholder.

Phase 4 fills in real ``Path.glob`` aggregation so the Settings UI can
show breakdown size by sub-directory (frames / audio / video / json).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StorageBucket:
    label: str
    bytes_used: int = 0
    file_count: int = 0


@dataclass
class StorageReport:
    root: Path
    total_bytes: int = 0
    total_files: int = 0
    buckets: list[StorageBucket] = field(default_factory=list)


def collect_storage_stats(
    root: Path,
    *,
    bucket_globs: dict[str, str] | None = None,
) -> StorageReport:
    """Compute storage usage; Phase 4 fleshes out real implementation."""

    return StorageReport(
        root=Path(root),
        total_bytes=0,
        total_files=0,
        buckets=[StorageBucket(label=label) for label in (bucket_globs or {})],
    )


__all__ = ["StorageBucket", "StorageReport", "collect_storage_stats"]
