"""L2 Component Tests: Media handling (storage, audio utils)."""

import pytest
from pathlib import Path

from openakita.channels.media.storage import MediaStorage
from openakita.channels.media.audio_utils import is_silk_file


class TestMediaStorage:
    @pytest.fixture
    def storage(self, tmp_path):
        return MediaStorage(base_path=tmp_path / "media", max_age_days=7)

    def test_create_storage(self, storage):
        assert storage is not None

    def test_get_path(self, storage):
        path = storage.get_path(channel="telegram", filename="photo.jpg")
        assert isinstance(path, Path)
        assert "telegram" in str(path)

    def test_get_stats(self, storage):
        stats = storage.get_stats()
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_cleanup_empty(self, storage):
        result = await storage.cleanup()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent(self, storage):
        result = await storage.retrieve("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, storage):
        result = await storage.delete("nonexistent-id")
        assert isinstance(result, bool)


class TestAudioUtils:
    def test_is_silk_file_non_silk(self, tmp_path):
        f = tmp_path / "test.wav"
        f.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        assert is_silk_file(str(f)) is False

    def test_is_silk_file_nonexistent(self, tmp_path):
        result = is_silk_file(str(tmp_path / "missing.silk"))
        assert result is False
