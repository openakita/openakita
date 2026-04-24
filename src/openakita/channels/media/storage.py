"""
媒体存储

管理媒体文件的存储和缓存:
- 本地文件存储
- 文件清理
- 缓存管理
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..types import MediaFile, MediaStatus

logger = logging.getLogger(__name__)


class MediaStorage:
    """
    媒体存储管理

    功能:
    - 按通道组织存储
    - 自动清理过期文件
    - 文件去重（基于 hash）
    """

    def __init__(
        self,
        base_path: Path | None = None,
        max_age_days: int = 7,
        max_size_mb: int = 1024,
    ):
        """
        Args:
            base_path: 存储根目录
            max_age_days: 文件最大保留天数
            max_size_mb: 最大存储空间（MB）
        """
        self.base_path = Path(base_path) if base_path else Path("data/media")
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.max_age_days = max_age_days
        self.max_size_mb = max_size_mb

        # 索引文件
        self.index_file = self.base_path / "index.json"
        self._index: dict[str, dict] = {}

        self._load_index()

    def get_path(self, channel: str, filename: str) -> Path:
        """获取文件存储路径"""
        channel_dir = self.base_path / channel
        channel_dir.mkdir(parents=True, exist_ok=True)
        return channel_dir / filename

    async def store(
        self,
        media: MediaFile,
        channel: str,
        data: bytes,
    ) -> Path:
        """
        存储媒体文件

        Args:
            media: 媒体文件信息
            channel: 来源通道
            data: 文件数据

        Returns:
            存储路径
        """
        # 计算 hash 用于去重
        file_hash = hashlib.md5(data).hexdigest()

        # 检查是否已存在相同文件
        existing = self._find_by_hash(file_hash)
        if existing:
            logger.debug(f"File already exists: {existing}")
            media.local_path = existing
            media.status = MediaStatus.READY
            return Path(existing)

        # 生成文件名（避免冲突）
        ext = media.extension
        filename = f"{media.id}.{ext}"

        # 存储文件
        path = self.get_path(channel, filename)
        path.write_bytes(data)

        # 更新媒体信息
        media.local_path = str(path)
        media.status = MediaStatus.READY

        # 更新索引
        self._index[media.id] = {
            "path": str(path),
            "hash": file_hash,
            "size": len(data),
            "channel": channel,
            "created_at": datetime.now().isoformat(),
        }
        self._save_index()

        logger.info(f"Stored media: {filename} ({len(data)} bytes)")
        return path

    async def retrieve(self, media_id: str) -> bytes | None:
        """
        获取媒体文件数据

        Args:
            media_id: 媒体 ID

        Returns:
            文件数据或 None
        """
        info = self._index.get(media_id)
        if not info:
            return None

        path = Path(info["path"])
        if not path.exists():
            del self._index[media_id]
            self._save_index()
            return None

        return path.read_bytes()

    async def delete(self, media_id: str) -> bool:
        """
        删除媒体文件

        Args:
            media_id: 媒体 ID

        Returns:
            是否成功
        """
        info = self._index.get(media_id)
        if not info:
            return False

        path = Path(info["path"])
        if path.exists():
            path.unlink()

        del self._index[media_id]
        self._save_index()

        logger.info(f"Deleted media: {media_id}")
        return True

    async def cleanup(self) -> dict[str, int]:
        """
        清理过期和超限文件

        Returns:
            {deleted_count, freed_bytes}
        """
        deleted_count = 0
        freed_bytes = 0

        cutoff_date = datetime.now() - timedelta(days=self.max_age_days)

        # 清理过期文件
        for media_id, info in list(self._index.items()):
            created_at = datetime.fromisoformat(info["created_at"])

            if created_at < cutoff_date:
                path = Path(info["path"])
                size = info.get("size", 0)

                if path.exists():
                    path.unlink()
                    freed_bytes += size

                del self._index[media_id]
                deleted_count += 1

        # 检查总大小
        total_size = sum(info.get("size", 0) for info in self._index.values())
        max_bytes = self.max_size_mb * 1024 * 1024

        if total_size > max_bytes:
            # 按创建时间排序，删除最老的
            sorted_items = sorted(self._index.items(), key=lambda x: x[1]["created_at"])

            for media_id, info in sorted_items:
                if total_size <= max_bytes * 0.8:  # 清理到 80%
                    break

                path = Path(info["path"])
                size = info.get("size", 0)

                if path.exists():
                    path.unlink()
                    freed_bytes += size
                    total_size -= size

                del self._index[media_id]
                deleted_count += 1

        self._save_index()

        logger.info(
            f"Cleanup: deleted {deleted_count} files, freed {freed_bytes / 1024 / 1024:.2f} MB"
        )

        return {
            "deleted_count": deleted_count,
            "freed_bytes": freed_bytes,
        }

    def get_stats(self) -> dict:
        """获取存储统计"""
        total_size = sum(info.get("size", 0) for info in self._index.values())

        by_channel = {}
        for info in self._index.values():
            channel = info.get("channel", "unknown")
            by_channel[channel] = by_channel.get(channel, 0) + 1

        return {
            "total_files": len(self._index),
            "total_size_mb": total_size / 1024 / 1024,
            "max_size_mb": self.max_size_mb,
            "usage_percent": (total_size / (self.max_size_mb * 1024 * 1024)) * 100,
            "by_channel": by_channel,
        }

    def _find_by_hash(self, file_hash: str) -> str | None:
        """通过 hash 查找已存在的文件"""
        for info in self._index.values():
            if info.get("hash") == file_hash:
                path = Path(info["path"])
                if path.exists():
                    return str(path)
        return None

    def _load_index(self) -> None:
        """加载索引"""
        if not self.index_file.exists():
            return

        try:
            with open(self.index_file, encoding="utf-8") as f:
                self._index = json.load(f)
            logger.info(f"Loaded media index: {len(self._index)} files")
        except Exception as e:
            logger.error(f"Failed to load media index: {e}")

    def _save_index(self) -> None:
        """保存索引"""
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save media index: {e}")

