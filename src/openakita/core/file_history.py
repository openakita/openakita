"""
文件历史与回滚系统

参考 Claude Code 的 fileHistory.ts 设计:
- 每次文件编辑前自动备份到 data/file-history/{session_id}/
- 每轮对话结束时创建快照点
- 支持按消息 ID 批量回滚到任意历史快照
- 最多保留 100 个快照
- 同一快照内同一文件只备份一次
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
    """单个文件的备份信息"""

    original_path: str
    backup_path: str
    existed: bool  # 文件在备份时是否存在（不存在 = 新创建的文件）


@dataclass
class FileSnapshot:
    """一个快照点"""

    snapshot_id: str
    message_id: str
    tracked_files: dict[str, BackupInfo] = field(default_factory=dict)


class FileHistoryManager:
    """管理文件编辑历史和快照。"""

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
        """在文件编辑前备份。

        同一 snapshot 内同一文件只备份一次（首次编辑时备份原始版本）。

        Args:
            file_path: 被编辑的文件路径
            snapshot_id: 当前快照 ID

        Returns:
            BackupInfo，如果已备份过则返回 None
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
        """创建一个快照点。

        Args:
            message_id: 关联的消息 ID

        Returns:
            快照 ID
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
            snapshot_id, message_id, len(snapshot.tracked_files),
        )
        return snapshot_id

    def rewind(self, target_message_id: str) -> list[str]:
        """回滚到目标消息时的文件状态。

        找到目标消息之后的所有快照，按逆序恢复文件。

        Args:
            target_message_id: 回滚到此消息时的状态

        Returns:
            被恢复的文件路径列表
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
        snapshots_to_rewind = self._snapshots[target_idx + 1:]

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
            target_message_id, len(restored), len(snapshots_to_rewind),
        )
        return restored

    def get_snapshot_for_message(self, message_id: str) -> FileSnapshot | None:
        """获取指定消息关联的快照。"""
        for snap in self._snapshots:
            if snap.message_id == message_id:
                return snap
        return None

    def _cleanup_snapshot_files(self, snapshot: FileSnapshot) -> None:
        """清理快照关联的备份文件。"""
        for info in snapshot.tracked_files.values():
            try:
                backup = Path(info.backup_path)
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
