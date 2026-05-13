"""No-op memory store used when SQLite memory is unavailable."""

from __future__ import annotations

import inspect

from .unified_store import UnifiedStore


def _empty_for_name(name: str):
    if name.startswith(("query_", "search_", "load_", "get_recent_", "get_session_")):
        return []
    if name.startswith(("get_", "find_")):
        return None
    if name.startswith(("delete_", "update_")):
        return False
    if name.startswith(("count_", "cleanup_", "link_", "complete_", "dequeue_")):
        return 0
    if name.startswith("save_"):
        return ""
    if name == "get_stats":
        return {"memory_count": 0, "search_backend": "noop", "search_available": False}
    return None


class NoopMemoryStorage:
    _conn = None
    _db_path = None

    def close(self) -> None:
        return None

    def checkpoint_and_close(self, *args, **kwargs) -> None:
        return None


class NoopSearchBackend:
    backend_type = "noop"
    available = False

    def search(self, *args, **kwargs) -> list:
        return []

    def add(self, *args, **kwargs) -> bool:
        return False

    def delete(self, *args, **kwargs) -> bool:
        return False

    def batch_add(self, *args, **kwargs) -> int:
        return 0


class NoopUnifiedStore:
    """No-op replacement for UnifiedStore.

    Public methods are installed dynamically below so CI can compare method
    coverage against UnifiedStore.
    """

    def __init__(self) -> None:
        self.db = NoopMemoryStorage()
        self.search = NoopSearchBackend()
        self._fts5_fallback = None


def _make_noop_method(name: str):
    def method(self, *args, **kwargs):
        return _empty_for_name(name)

    method.__name__ = name
    return method


for _name, _member in inspect.getmembers(UnifiedStore, predicate=inspect.isfunction):
    if _name.startswith("_") or hasattr(NoopUnifiedStore, _name):
        continue
    setattr(NoopUnifiedStore, _name, _make_noop_method(_name))


class NoopRetrievalEngine:
    def __init__(self) -> None:
        self._external_sources = []
        self._plugin_hooks = None

    def set_scope_context(self, *args, **kwargs) -> None:
        return None

    def retrieve(self, *args, **kwargs) -> str:
        return ""

    def retrieve_candidates(self, *args, **kwargs) -> list:
        return []
