"""
File history and rollback system

Modeled after Claude Code's fileHistory.ts design:
- Before each file edit, automatically back up to data/file-history/{session_id}/
- Create a snapshot point at the end of each conversation turn
- Support batch rollback to any historical snapshot by message ID
- Retain at most 100 snapshots
- Within a single snapshot, each file is backed up only once
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS = 100
HISTORY_BASE_DIR = Path("data/file-history")


@dataclass
class BackupInfo:
    """Backup information for a single file."""

    original_path: str
    backup_path: str
    existed: bool  # Whether the file existed at backup time (False = newly created file)


@dataclass
class FileSnapshot:
    """A snapshot point."""

    snapshot_id: str
    message_id: str
    tracked_files: dict[str, BackupInfo] = field(default_factory=dict)


class FileHistoryManager:
    """Manage file edit history and snapshots."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._history_dir = HISTORY_BASE_DIR / session_id
        self._snapshots: list[FileSnapshot] = []
        self._current_snapshot_id: str | None = None
        self._current_tracked: dict[str, BackupInfo] = {}

    @property
    def history_dir(self) -> Path:
        return self._history_dir

    @property
    def snapshot_count(self) -> int:
        return len(self._snapshots)

    def track_edit(self, file_path: str, snapshot_id: str) -> BackupInfo | None:
        """Back up a file before it is edited.

        Within the same snapshot, each file is backed up only once
        (the original version is backed up on first edit).

        Args:
            file_path: Path of the file being edited
            snapshot_id: Current snapshot ID

        Returns:
            BackupInfo, or None if the file was already backed up
        """
        if self._current_snapshot_id != snapshot_id:
            self._current_snapshot_id = snapshot_id
            self._current_tracked = {}

        abs_path = str(Path(file_path).resolve())
        if abs_path in self._current_tracked:
            return None

        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            source = Path(file_path)
            existed = source.exists()

            safe_name = abs_path.replace("/", "_").replace("\\", "_").replace(":", "_")
            backup_name = f"{snapshot_id}_{safe_name}"
            backup_path = self._history_dir / backup_name

            if existed:
                shutil.copy2(str(source), str(backup_path))
            else:
                backup_path.write_text("", encoding="utf-8")

            info = BackupInfo(
                original_path=abs_path,
                backup_path=str(backup_path),
                existed=existed,
            )
            self._current_tracked[abs_path] = info
            logger.debug("Tracked edit: %s -> %s", file_path, backup_path)
            return info

        except Exception as e:
            logger.warning("Failed to track file edit for %s: %s", file_path, e)
            return None

    def make_snapshot(self, message_id: str) -> str:
        """Create a snapshot point.

        Args:
            message_id: Associated message ID

        Returns:
            Snapshot ID
        """
        import uuid

        snapshot_id = str(uuid.uuid4())[:8]
        snapshot = FileSnapshot(
            snapshot_id=snapshot_id,
            message_id=message_id,
            tracked_files=dict(self._current_tracked),
        )
        self._snapshots.append(snapshot)

        # Enforce max snapshots limit
        while len(self._snapshots) > MAX_SNAPSHOTS:
            old = self._snapshots.pop(0)
            self._cleanup_snapshot_files(old)

        self._current_tracked = {}
        self._current_snapshot_id = None

        logger.debug(
            "Created snapshot %s for message %s (%d files)",
            snapshot_id,
            message_id,
            len(snapshot.tracked_files),
        )
        return snapshot_id

    def rewind(self, target_message_id: str) -> list[str]:
        """Roll back to the file state at the target message.

        Find all snapshots after the target message and restore files
        in reverse order.

        Args:
            target_message_id: Roll back to the state at this message

        Returns:
            List of restored file paths
        """
        target_idx = -1
        for i, snap in enumerate(self._snapshots):
            if snap.message_id == target_message_id:
                target_idx = i
                break

        if target_idx < 0:
            logger.warning("Snapshot for message %s not found", target_message_id)
            return []

        restored: list[str] = []
        snapshots_to_rewind = self._snapshots[target_idx + 1 :]

        for snap in reversed(snapshots_to_rewind):
            for abs_path, info in snap.tracked_files.items():
                try:
                    if info.existed:
                        backup = Path(info.backup_path)
                        if backup.exists():
                            shutil.copy2(str(backup), info.original_path)
                            restored.append(info.original_path)
                    else:
                        target = Path(info.original_path)
                        if target.exists():
                            target.unlink()
                            restored.append(info.original_path)
                except Exception as e:
                    logger.warning("Failed to restore %s: %s", abs_path, e)

        # Remove rewound snapshots
        self._snapshots = self._snapshots[: target_idx + 1]

        logger.info(
            "Rewound to message %s: restored %d files, removed %d snapshots",
            target_message_id,
            len(restored),
            len(snapshots_to_rewind),
        )
        return restored

    def get_snapshot_for_message(self, message_id: str) -> FileSnapshot | None:
        """Get the snapshot associated with the specified message."""
        for snap in self._snapshots:
            if snap.message_id == message_id:
                return snap
        return None

    def _cleanup_snapshot_files(self, snapshot: FileSnapshot) -> None:
        """Clean up backup files associated with a snapshot."""
        for info in snapshot.tracked_files.values():
            try:
                backup = Path(info.backup_path)
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
