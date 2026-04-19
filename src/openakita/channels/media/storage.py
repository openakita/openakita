"""
Media Storage

Manages storage and caching of media files:
- Local file storage
- File cleanup
- Cache management
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
    Media storage manager

    Features:
    - Organize storage by channel
    - Automatic cleanup of expired files
    - File deduplication (hash-based)
    """

    def __init__(
        self,
        base_path: Path | None = None,
        max_age_days: int = 7,
        max_size_mb: int = 1024,
    ):
        """
        Args:
            base_path: Storage root directory
            max_age_days: Maximum days to retain files
            max_size_mb: Maximum storage space in MB
        """
        self.base_path = Path(base_path) if base_path else Path("data/media")
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.max_age_days = max_age_days
        self.max_size_mb = max_size_mb

        # Index file
        self.index_file = self.base_path / "index.json"
        self._index: dict[str, dict] = {}

        self._load_index()

    def get_path(self, channel: str, filename: str) -> Path:
        """Get the storage path for a file"""
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
        Store a media file.

        Args:
            media: Media file information
            channel: Source channel
            data: File data

        Returns:
            Storage path
        """
        # Compute hash for deduplication
        file_hash = hashlib.md5(data).hexdigest()

        # Check if an identical file already exists
        existing = self._find_by_hash(file_hash)
        if existing:
            logger.debug(f"File already exists: {existing}")
            media.local_path = existing
            media.status = MediaStatus.READY
            return Path(existing)

        # Generate filename (avoid collisions)
        ext = media.extension
        filename = f"{media.id}.{ext}"

        # Write the file
        path = self.get_path(channel, filename)
        path.write_bytes(data)

        # Update media info
        media.local_path = str(path)
        media.status = MediaStatus.READY

        # Update index
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
        Retrieve media file data.

        Args:
            media_id: Media ID

        Returns:
            File data, or None if not found
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
        Delete a media file.

        Args:
            media_id: Media ID

        Returns:
            Whether the deletion succeeded
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
        Clean up expired and oversized files.

        Returns:
            {deleted_count, freed_bytes}
        """
        deleted_count = 0
        freed_bytes = 0

        cutoff_date = datetime.now() - timedelta(days=self.max_age_days)

        # Clean up expired files
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

        # Check total size
        total_size = sum(info.get("size", 0) for info in self._index.values())
        max_bytes = self.max_size_mb * 1024 * 1024

        if total_size > max_bytes:
            # Sort by creation time, delete the oldest first
            sorted_items = sorted(self._index.items(), key=lambda x: x[1]["created_at"])

            for media_id, info in sorted_items:
                if total_size <= max_bytes * 0.8:  # Clean down to 80%
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
        """Get storage statistics"""
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
        """Find an existing file by hash"""
        for info in self._index.values():
            if info.get("hash") == file_hash:
                path = Path(info["path"])
                if path.exists():
                    return str(path)
        return None

    def _load_index(self) -> None:
        """Load the index"""
        if not self.index_file.exists():
            return

        try:
            with open(self.index_file, encoding="utf-8") as f:
                self._index = json.load(f)
            logger.info(f"Loaded media index: {len(self._index)} files")
        except Exception as e:
            logger.error(f"Failed to load media index: {e}")

    def _save_index(self) -> None:
        """Save the index"""
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save media index: {e}")
