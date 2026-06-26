"""L1 Unit Tests: StickerEngine initialization and search."""

import pytest
from pathlib import Path

from openakita.tools.sticker import StickerEngine


@pytest.fixture
def sticker_engine(tmp_path):
    return StickerEngine(data_dir=tmp_path / "stickers")


class TestStickerEngineInit:
    def test_create_engine(self, sticker_engine):
        assert sticker_engine is not None

    def test_create_with_string_path(self, tmp_path):
        engine = StickerEngine(data_dir=str(tmp_path / "stickers"))
        assert engine is not None


class TestStickerSearch:
    @pytest.mark.asyncio
    async def test_search_returns_list(self, sticker_engine):
        await sticker_engine.initialize()
        results = await sticker_engine.search("开心")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_with_limit(self, sticker_engine):
        await sticker_engine.initialize()
        results = await sticker_engine.search("哈哈", limit=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_with_category(self, sticker_engine):
        await sticker_engine.initialize()
        results = await sticker_engine.search("感谢", category="happy")
        assert isinstance(results, list)


class TestStickerMoodMapping:
    @pytest.mark.asyncio
    async def test_get_random_by_mood(self, sticker_engine):
        await sticker_engine.initialize()
        result = await sticker_engine.get_random_by_mood("happy")
        assert result is None or isinstance(result, dict)
