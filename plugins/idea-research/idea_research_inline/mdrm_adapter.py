"""MDRM adapter — Phase 0 skeleton.

Wraps the four SDK 0.7 host-service hooks (``api.get_brain``,
``api.get_memory_manager``, ``api.get_vector_store`` plus the optional
``api.register_memory_backend``) behind a single facade.

In Phase 0 we only define the public surface and a graceful-degradation
"detect capabilities" path so the rest of the plugin can already type-
check against the adapter; full implementation lands in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openakita_plugin_sdk import PluginAPI


@dataclass
class MdrmCapabilities:
    has_brain: bool = False
    has_memory_read: bool = False
    has_memory_write: bool = False
    has_vector: bool = False
    vector_ready: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "has_brain": self.has_brain,
            "has_memory_read": self.has_memory_read,
            "has_memory_write": self.has_memory_write,
            "has_vector": self.has_vector,
            "vector_ready": self.vector_ready,
        }


@dataclass
class HookRecord:
    id: str
    hook_type: str
    hook_text: str
    persona: str | None
    platform: str
    score: float
    brand_keywords: list[str] = field(default_factory=list)
    source_task_id: str = ""


class MdrmAdapter:
    """Thin wrapper over brain / memory_manager / vector_store.

    All public coroutines are intentionally tolerant: when a permission
    is missing or the underlying service raises, they return a
    ``"skipped"`` / ``"error"`` marker rather than propagating, so the
    main pipeline is never blocked by MDRM mishaps.
    """

    def __init__(
        self,
        api: PluginAPI,
        *,
        plugin_id: str = "idea-research",
    ) -> None:
        self._api = api
        self._plugin_id = plugin_id
        self._brain: Any = None
        self._memory: Any = None
        self._vector: Any = None
        self._caps = self._detect_caps()

    @property
    def caps(self) -> MdrmCapabilities:
        return self._caps

    def _detect_caps(self) -> MdrmCapabilities:
        caps = MdrmCapabilities()
        try:
            self._brain = self._api.get_brain()
            caps.has_brain = self._brain is not None
        except Exception:
            self._brain = None

        try:
            self._memory = self._api.get_memory_manager()
            caps.has_memory_read = self._memory is not None
            caps.has_memory_write = self._memory is not None
        except Exception:
            self._memory = None

        try:
            self._vector = self._api.get_vector_store()
            caps.has_vector = self._vector is not None
            caps.vector_ready = False
        except Exception:
            self._vector = None
        return caps

    async def write_hook(self, hook: HookRecord) -> dict[str, str]:
        """Phase 3 will perform the real dual-track write."""

        return {
            "vector": "skipped" if not self._caps.has_vector else "ok",
            "memory": "skipped" if not self._caps.has_memory_write else "ok",
        }

    async def search_similar_hooks(
        self,
        query_text: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.5,
    ) -> list[tuple[HookRecord, float]]:
        """Phase 3 will hit ``vector.search`` with a 2 s hard timeout."""

        _ = (query_text, limit, min_similarity)
        return []

    async def think_fallback(self, prompt: str, system: str = "") -> str | None:
        """Phase 3 will route through ``brain.think`` when available."""

        _ = (prompt, system)
        return None

    async def stats(self) -> dict[str, Any]:
        return {
            "caps": self._caps.as_dict(),
            "hook_count": 0,
            "vector_indexed": 0,
            "last_write_at": None,
            "missing_perms": [
                name
                for name, ok in (
                    ("brain.access", self._caps.has_brain),
                    ("vector.access", self._caps.has_vector),
                    ("memory.write", self._caps.has_memory_write),
                )
                if not ok
            ],
        }

    async def clear_all(self) -> dict[str, Any]:
        return {
            "vector": "skipped",
            "memory": "skipped",
            "hook_library": "skipped",
        }

    async def reindex_all_breakdowns(self, *, from_days_ago: int = 30) -> dict[str, int]:
        _ = from_days_ago
        return {"reindexed": 0, "skipped": 0, "failed": 0}


__all__ = [
    "HookRecord",
    "MdrmAdapter",
    "MdrmCapabilities",
]
