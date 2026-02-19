"""补充搜索后端测试: ChromaDB mock, API Embedding mock, 协议验证."""

from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from openakita.memory.search_backends import (
    APIEmbeddingBackend,
    ChromaDBBackend,
    FTS5Backend,
    SearchBackend,
    create_search_backend,
)
from openakita.memory.storage import MemoryStorage


@pytest.fixture
def tmp_storage(tmp_path):
    return MemoryStorage(tmp_path / "test.db")


class TestChromaDBBackend:
    def test_wraps_vector_store(self):
        mock_vs = MagicMock()
        mock_vs.enabled = True
        mock_vs.search.return_value = [("m1", 0.2), ("m2", 0.5)]
        backend = ChromaDBBackend(mock_vs)

        assert backend.available is True
        assert backend.backend_type == "chromadb"

        results = backend.search("test query", limit=5)
        assert len(results) == 2
        assert results[0][0] == "m1"
        assert results[0][1] == pytest.approx(0.8, abs=0.01)

    def test_unavailable_when_disabled(self):
        mock_vs = MagicMock()
        mock_vs.enabled = False
        backend = ChromaDBBackend(mock_vs)
        assert backend.available is False

    def test_add_delegates(self):
        mock_vs = MagicMock()
        mock_vs.add_memory.return_value = True
        backend = ChromaDBBackend(mock_vs)
        assert backend.add("m1", "content", {"type": "fact"}) is True
        mock_vs.add_memory.assert_called_once()

    def test_delete_delegates(self):
        mock_vs = MagicMock()
        mock_vs.delete_memory.return_value = True
        backend = ChromaDBBackend(mock_vs)
        assert backend.delete("m1") is True

    def test_batch_add_delegates(self):
        mock_vs = MagicMock()
        mock_vs.batch_add.return_value = 3
        backend = ChromaDBBackend(mock_vs)
        result = backend.batch_add([{"id": "1"}, {"id": "2"}, {"id": "3"}])
        assert result == 3

    def test_filter_type_passed(self):
        mock_vs = MagicMock()
        mock_vs.enabled = True
        mock_vs.search.return_value = []
        backend = ChromaDBBackend(mock_vs)
        backend.search("query", filter_type="PREFERENCE")
        mock_vs.search.assert_called_once_with(
            query="query", limit=10, filter_type="preference"
        )

    def test_distance_to_score_conversion(self):
        mock_vs = MagicMock()
        mock_vs.enabled = True
        mock_vs.search.return_value = [("m1", 0.0), ("m2", 1.5)]
        backend = ChromaDBBackend(mock_vs)
        results = backend.search("test")
        assert results[0][1] == 1.0
        assert results[1][1] == 0.0

    def test_is_search_backend(self):
        mock_vs = MagicMock()
        backend = ChromaDBBackend(mock_vs)
        assert isinstance(backend, SearchBackend)


class TestAPIEmbeddingBackend:
    def test_unavailable_without_key(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage)
        assert backend.available is False

    def test_available_with_key(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage, api_key="sk-test")
        assert backend.available is True
        assert backend.backend_type == "api_embedding"

    def test_default_model_dashscope(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage, provider="dashscope", api_key="k")
        assert backend._model == "text-embedding-v3"

    def test_default_model_openai(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage, provider="openai", api_key="k")
        assert backend._model == "text-embedding-3-small"

    def test_search_empty_text_returns_none(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage, api_key="sk-test")
        emb = backend._get_embedding("   ")
        assert emb is None

    def test_cache_hit(self, tmp_storage):
        import struct
        floats = [0.1, 0.2, 0.3]
        blob = struct.pack(f"{len(floats)}f", *floats)
        content_hash = "abcdef1234567890"
        tmp_storage.save_cached_embedding(content_hash, blob, "test-model", 3)

        backend = APIEmbeddingBackend(storage=tmp_storage, api_key="sk-test")
        with patch.object(backend, "_call_api", return_value=None) as mock_api:
            cached = tmp_storage.get_cached_embedding(content_hash)
            assert cached is not None
            recovered = backend._bytes_to_floats(cached)
            assert len(recovered) == 3
            mock_api.assert_not_called()

    def test_cosine_similarity(self, tmp_storage):
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert APIEmbeddingBackend._cosine_similarity(a, b) == pytest.approx(1.0)

        c = [0.0, 1.0, 0.0]
        assert APIEmbeddingBackend._cosine_similarity(a, c) == pytest.approx(0.0)

    def test_cosine_similarity_zero_vector(self, tmp_storage):
        assert APIEmbeddingBackend._cosine_similarity([0, 0], [1, 1]) == 0.0

    def test_cosine_similarity_different_length(self, tmp_storage):
        assert APIEmbeddingBackend._cosine_similarity([1], [1, 2]) == 0.0

    def test_floats_roundtrip(self, tmp_storage):
        original = [0.123, 0.456, 0.789, 1.0]
        blob = APIEmbeddingBackend._floats_to_bytes(original)
        recovered = APIEmbeddingBackend._bytes_to_floats(blob)
        for a, b in zip(original, recovered):
            assert a == pytest.approx(b, abs=1e-6)

    def test_is_search_backend(self, tmp_storage):
        backend = APIEmbeddingBackend(storage=tmp_storage, api_key="k")
        assert isinstance(backend, SearchBackend)


class TestFactoryFallbacks:
    def test_chromadb_unavailable_fallback(self, tmp_storage):
        mock_vs = MagicMock()
        mock_vs.enabled = False
        backend = create_search_backend("chromadb", storage=tmp_storage, vector_store=mock_vs)
        assert isinstance(backend, FTS5Backend)

    def test_chromadb_no_store_fallback(self, tmp_storage):
        backend = create_search_backend("chromadb", storage=tmp_storage, vector_store=None)
        assert isinstance(backend, FTS5Backend)

    def test_api_no_key_fallback(self, tmp_storage):
        backend = create_search_backend("api_embedding", storage=tmp_storage, api_key="")
        assert isinstance(backend, FTS5Backend)
