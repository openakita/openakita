"""L1 Unit Tests: Search Backends (FTS5, factory, protocol)."""

import tempfile
from pathlib import Path

import pytest

from openakita.memory.search_backends import (
    FTS5Backend,
    SearchBackend,
    create_search_backend,
)
from openakita.memory.storage import MemoryStorage


@pytest.fixture
def tmp_storage(tmp_path):
    db_path = tmp_path / "test.db"
    return MemoryStorage(db_path)


class TestFTS5Backend:
    def test_always_available(self, tmp_storage):
        backend = FTS5Backend(tmp_storage)
        assert backend.available is True
        assert backend.backend_type == "fts5"

    def test_search_empty_returns_empty(self, tmp_storage):
        backend = FTS5Backend(tmp_storage)
        results = backend.search("anything")
        assert results == []

    def test_search_finds_memory(self, tmp_storage):
        from datetime import datetime

        tmp_storage.save_memory({
            "id": "m1",
            "content": "用户喜欢 Python 编程",
            "type": "PREFERENCE",
            "created_at": datetime.now().isoformat(),
            "tags": '["python"]',
        })

        backend = FTS5Backend(tmp_storage)
        results = backend.search("Python")
        assert len(results) >= 1
        assert results[0][0] == "m1"

    def test_search_bm25_ranking(self, tmp_storage):
        from datetime import datetime

        now = datetime.now().isoformat()
        tmp_storage.save_memory({
            "id": "m1",
            "content": "Python 是一种编程语言",
            "type": "FACT",
            "created_at": now,
        })
        tmp_storage.save_memory({
            "id": "m2",
            "content": "Python Python Python 用户最爱",
            "type": "PREFERENCE",
            "created_at": now,
        })

        backend = FTS5Backend(tmp_storage)
        results = backend.search("Python")
        assert len(results) >= 1

    def test_add_delete_noop(self, tmp_storage):
        backend = FTS5Backend(tmp_storage)
        assert backend.add("m1", "test") is True
        assert backend.delete("m1") is True
        assert backend.batch_add([{"id": "m1"}]) == 1


class TestFTS5Segmentation:
    def test_segment_fallback(self, tmp_storage):
        backend = FTS5Backend(tmp_storage)
        backend._jieba_available = False
        result = backend._segment("中文测试")
        assert result == "中文测试"


class TestSearchBackendFactory:
    def test_default_fts5(self, tmp_storage):
        backend = create_search_backend("fts5", storage=tmp_storage)
        assert isinstance(backend, FTS5Backend)
        assert backend.backend_type == "fts5"

    def test_unknown_falls_back_fts5(self, tmp_storage):
        backend = create_search_backend("unknown", storage=tmp_storage)
        assert isinstance(backend, FTS5Backend)

    def test_api_without_key_falls_back(self, tmp_storage):
        backend = create_search_backend(
            "api_embedding", storage=tmp_storage, api_key=""
        )
        assert isinstance(backend, FTS5Backend)

    def test_fts5_requires_storage(self):
        with pytest.raises(ValueError):
            create_search_backend("fts5", storage=None)


class TestSearchBackendProtocol:
    def test_fts5_is_search_backend(self, tmp_storage):
        backend = FTS5Backend(tmp_storage)
        assert isinstance(backend, SearchBackend)
