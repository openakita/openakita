"""
L4: File snapshot and rollback system

Automatically creates snapshots before modifying files in the controlled area,
and supports rollback by checkpoint ID.
Snapshots are stored in data/checkpoints/ and the most recent N are kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CheckpointEntry:
    """Snapshot record for a single file"""

    original_path: str
    backup_path: str
    file_hash: str
    size: int
    existed: bool


@dataclass
class Checkpoint:
    """A single snapshot"""

    checkpoint_id: str
    timestamp: float
    tool_name: str
    description: str
    entries: list[CheckpointEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointManager:
    """Manages the creation and rollback of file snapshots"""

    def __init__(
        self,
        snapshot_dir: str = "data/checkpoints",
        max_snapshots: int = 50,
    ) -> None:
        self._base_dir = Path(snapshot_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_snapshots = max_snapshots
        self._manifest_path = self._base_dir / "manifest.json"
        self._checkpoints: list[Checkpoint] = []
        self._load_manifest()

    def _load_manifest(self) -> None:
        if self._manifest_path.exists():
            try:
                data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                for item in data:
                    entries = [CheckpointEntry(**e) for e in item.get("entries", [])]
                    self._checkpoints.append(
                        Checkpoint(
                            checkpoint_id=item["checkpoint_id"],
                            timestamp=item["timestamp"],
                            tool_name=item.get("tool_name", ""),
                            description=item.get("description", ""),
                            entries=entries,
                        )
                    )
            except Exception as e:
                logger.warning(f"[Checkpoint] Failed to load manifest: {e}")

    def _save_manifest(self) -> None:
        try:
            data = [cp.to_dict() for cp in self._checkpoints]
            self._manifest_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[Checkpoint] Failed to save manifest: {e}")

    @staticmethod
    def _file_hash(path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except Exception:
            return ""
        return h.hexdigest()

    def create_checkpoint(
        self,
        file_paths: list[str],
        tool_name: str = "",
        description: str = "",
    ) -> str | None:
        """
        Create a snapshot of the given files before modification.

        Returns checkpoint_id on success, None on failure.
        """
        cp_id = uuid.uuid4().hex[:12]
        cp_dir = self._base_dir / cp_id
        cp_dir.mkdir(parents=True, exist_ok=True)

        entries: list[CheckpointEntry] = []
        for fp_str in file_paths:
            fp = Path(fp_str)
            if not fp.exists():
                entries.append(
                    CheckpointEntry(
                        original_path=str(fp),
                        backup_path="",
                        file_hash="",
                        size=0,
                        existed=False,
                    )
                )
                continue

            if fp.is_dir():
                continue

            try:
                file_hash = self._file_hash(fp)
                backup_name = fp.name
                backup_path = cp_dir / backup_name
                counter = 1
                while backup_path.exists():
                    backup_path = cp_dir / f"{fp.stem}_{counter}{fp.suffix}"
                    counter += 1

                shutil.copy2(fp, backup_path)
                entries.append(
                    CheckpointEntry(
                        original_path=str(fp),
                        backup_path=str(backup_path),
                        file_hash=file_hash,
                        size=fp.stat().st_size,
                        existed=True,
                    )
                )
            except Exception as e:
                logger.warning(f"[Checkpoint] Failed to backup {fp}: {e}")

        if not entries:
            try:
                cp_dir.rmdir()
            except Exception:
                pass
            return None

        checkpoint = Checkpoint(
            checkpoint_id=cp_id,
            timestamp=time.time(),
            tool_name=tool_name,
            description=description,
            entries=entries,
        )
        self._checkpoints.append(checkpoint)

        self._enforce_limit()
        self._save_manifest()

        logger.info(f"[Checkpoint] Created {cp_id}: {len(entries)} file(s), tool={tool_name}")
        return cp_id

    def rewind_to_checkpoint(self, checkpoint_id: str) -> bool:
        """Restore files from a checkpoint."""
        cp = next(
            (c for c in self._checkpoints if c.checkpoint_id == checkpoint_id),
            None,
        )
        if not cp:
            logger.warning(f"[Checkpoint] Not found: {checkpoint_id}")
            return False

        restored = 0
        for entry in cp.entries:
            if not entry.existed:
                fp = Path(entry.original_path)
                if fp.exists():
                    try:
                        fp.unlink()
                        restored += 1
                    except Exception as e:
                        logger.warning(f"[Checkpoint] Failed to remove {fp}: {e}")
                continue

            if not entry.backup_path:
                continue

            try:
                backup = Path(entry.backup_path)
                if backup.exists():
                    target = Path(entry.original_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                    restored += 1
            except Exception as e:
                logger.warning(f"[Checkpoint] Failed to restore {entry.original_path}: {e}")

        logger.info(f"[Checkpoint] Restored {restored} file(s) from {checkpoint_id}")
        return True

    def list_checkpoints(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent checkpoints."""
        return [
            {
                "checkpoint_id": cp.checkpoint_id,
                "timestamp": cp.timestamp,
                "tool_name": cp.tool_name,
                "description": cp.description,
                "file_count": len(cp.entries),
            }
            for cp in reversed(self._checkpoints[-limit:])
        ]

    def _enforce_limit(self) -> None:
        while len(self._checkpoints) > self._max_snapshots:
            old = self._checkpoints.pop(0)
            cp_dir = self._base_dir / old.checkpoint_id
            if cp_dir.exists():
                try:
                    shutil.rmtree(cp_dir)
                except Exception:
                    pass


_global_checkpoint_mgr: CheckpointManager | None = None


def get_checkpoint_manager() -> CheckpointManager:
    global _global_checkpoint_mgr
    if _global_checkpoint_mgr is None:
        try:
            from .policy import get_policy_engine

            cfg = get_policy_engine().config.checkpoint
            _global_checkpoint_mgr = CheckpointManager(
                snapshot_dir=cfg.snapshot_dir,
                max_snapshots=cfg.max_snapshots,
            )
        except Exception:
            _global_checkpoint_mgr = CheckpointManager()
    return _global_checkpoint_mgr
