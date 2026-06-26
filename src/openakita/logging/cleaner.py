"""
日志清理器

功能:
- 按保留天数清理旧日志
- 按总大小清理（防止磁盘爆满）
- 可集成到每日定时任务
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class LogCleaner:
    """
    日志清理器

    清理策略:
    1. 删除超过 retention_days 天的日志文件
    2. 如果总大小超过 max_total_size_mb，删除最旧的文件
    """

    def __init__(
        self,
        log_dir: Path,
        retention_days: int = 30,
        max_total_size_mb: int = 500,
    ):
        """
        Args:
            log_dir: 日志目录
            retention_days: 保留天数
            max_total_size_mb: 最大总大小（MB）
        """
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days
        self.max_total_size_mb = max_total_size_mb

    def cleanup(self) -> dict:
        """
        执行清理

        Returns:
            清理统计 {"by_age": n, "by_size": n, "freed_mb": float}
        """
        result = {
            "by_age": 0,
            "by_size": 0,
            "freed_mb": 0.0,
        }

        if not self.log_dir.exists():
            return result

        # 1. 按天数清理
        deleted_by_age, freed_by_age = self._cleanup_by_age()
        result["by_age"] = deleted_by_age
        result["freed_mb"] += freed_by_age

        # 2. 按大小清理
        deleted_by_size, freed_by_size = self._cleanup_by_size()
        result["by_size"] = deleted_by_size
        result["freed_mb"] += freed_by_size

        if result["by_age"] > 0 or result["by_size"] > 0:
            logger.info(
                f"Log cleanup completed: deleted {result['by_age']} by age, "
                f"{result['by_size']} by size, freed {result['freed_mb']:.2f} MB"
            )

        return result

    def _cleanup_by_age(self) -> tuple[int, float]:
        """
        按天数清理

        Returns:
            (删除数量, 释放大小 MB)
        """
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted = 0
        freed_bytes = 0

        for file in self._get_log_files():
            try:
                mtime = datetime.fromtimestamp(file.stat().st_mtime)
                if mtime < cutoff:
                    file_size = file.stat().st_size
                    file.unlink()
                    deleted += 1
                    freed_bytes += file_size
                    logger.debug(f"Deleted old log file: {file.name}")
            except Exception as e:
                logger.error(f"Failed to delete {file.name}: {e}")

        return deleted, freed_bytes / (1024 * 1024)

    def _cleanup_by_size(self) -> tuple[int, float]:
        """
        按大小清理（删除最旧的文件直到总大小低于限制）

        Returns:
            (删除数量, 释放大小 MB)
        """
        max_size_bytes = self.max_total_size_mb * 1024 * 1024

        # 获取所有日志文件，按修改时间排序（最旧的在前）
        files = sorted(self._get_log_files(), key=lambda f: f.stat().st_mtime)

        # 计算总大小
        total_size = sum(f.stat().st_size for f in files)

        if total_size <= max_size_bytes:
            return 0, 0.0

        deleted = 0
        freed_bytes = 0

        # 删除最旧的文件直到总大小低于限制
        for file in files:
            if total_size <= max_size_bytes:
                break

            try:
                file_size = file.stat().st_size
                file.unlink()
                total_size -= file_size
                deleted += 1
                freed_bytes += file_size
                logger.debug(f"Deleted log file (by size): {file.name}")
            except Exception as e:
                logger.error(f"Failed to delete {file.name}: {e}")

        return deleted, freed_bytes / (1024 * 1024)

    def _get_log_files(self) -> list[Path]:
        """
        获取所有日志文件

        排除当前正在使用的日志文件（不带日期后缀的）
        """
        files = []

        for pattern in ["*.log.*", "*.log.[0-9]*"]:
            files.extend(self.log_dir.glob(pattern))

        return files

    def get_stats(self) -> dict:
        """
        获取日志统计信息

        Returns:
            统计信息字典
        """
        if not self.log_dir.exists():
            return {
                "file_count": 0,
                "total_size_mb": 0.0,
                "oldest_file": None,
                "newest_file": None,
            }

        files = list(self.log_dir.glob("*.log*"))

        if not files:
            return {
                "file_count": 0,
                "total_size_mb": 0.0,
                "oldest_file": None,
                "newest_file": None,
            }

        total_size = sum(f.stat().st_size for f in files)

        # 按修改时间排序
        files_sorted = sorted(files, key=lambda f: f.stat().st_mtime)

        return {
            "file_count": len(files),
            "total_size_mb": total_size / (1024 * 1024),
            "oldest_file": files_sorted[0].name if files_sorted else None,
            "newest_file": files_sorted[-1].name if files_sorted else None,
        }
