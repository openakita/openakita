"""
Log Cleaner

Features:
- Clean old logs by retention days
- Clean by total size (prevent disk from filling up)
- Can be integrated into daily scheduled tasks
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class LogCleaner:
    """
    Log Cleaner

    Cleanup strategy:
    1. Delete log files older than retention_days
    2. If total size exceeds max_total_size_mb, delete the oldest files
    """

    def __init__(
        self,
        log_dir: Path,
        retention_days: int = 30,
        max_total_size_mb: int = 500,
    ):
        """
        Args:
            log_dir: Log directory
            retention_days: Retention period in days
            max_total_size_mb: Maximum total size (MB)
        """
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days
        self.max_total_size_mb = max_total_size_mb

    def cleanup(self) -> dict:
        """
        Run cleanup

        Returns:
            Cleanup stats {"by_age": n, "by_size": n, "freed_mb": float}
        """
        result = {
            "by_age": 0,
            "by_size": 0,
            "freed_mb": 0.0,
        }

        if not self.log_dir.exists():
            return result

        # 1. Cleanup by age
        deleted_by_age, freed_by_age = self._cleanup_by_age()
        result["by_age"] = deleted_by_age
        result["freed_mb"] += freed_by_age

        # 2. Cleanup by size
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
        Cleanup by age

        Returns:
            (number deleted, freed size in MB)
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
        Cleanup by size (delete oldest files until total size is below limit)

        Returns:
            (number deleted, freed size in MB)
        """
        max_size_bytes = self.max_total_size_mb * 1024 * 1024

        # Get all log files, sorted by modification time (oldest first)
        files = sorted(self._get_log_files(), key=lambda f: f.stat().st_mtime)

        # Calculate total size
        total_size = sum(f.stat().st_size for f in files)

        if total_size <= max_size_bytes:
            return 0, 0.0

        deleted = 0
        freed_bytes = 0

        # Delete oldest files until total size is below limit
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
        Get all log files

        Excludes the currently active log file (one without a date suffix)
        """
        files = []

        for pattern in ["*.log.*", "*.log.[0-9]*"]:
            files.extend(self.log_dir.glob(pattern))

        return files

    def get_stats(self) -> dict:
        """
        Get log statistics

        Returns:
            Statistics dictionary
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

        # Sort by modification time
        files_sorted = sorted(files, key=lambda f: f.stat().st_mtime)

        return {
            "file_count": len(files),
            "total_size_mb": total_size / (1024 * 1024),
            "oldest_file": files_sorted[0].name if files_sorted else None,
            "newest_file": files_sorted[-1].name if files_sorted else None,
        }
