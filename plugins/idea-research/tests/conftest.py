"""Test fixtures for idea-research.

Builds on top of the SDK's ``MockPluginAPI`` to add fake ``brain`` /
``memory_manager`` / ``vector_store`` services so the MDRM adapter can
be exercised end-to-end without a live host runtime.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Make the plugin importable as a top-level package even though its
# directory is named ``idea-research`` (kebab-case is illegal in module
# names; we only need the parent on sys.path so tests can do
# ``from idea_research_inline...``).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

# When the SDK lives next to OpenAkita as a sibling checkout, expose it.
_SDK_SRC = _PLUGIN_ROOT.parents[1] / "openakita-plugin-sdk" / "src"
if _SDK_SRC.is_dir() and str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))

from openakita_plugin_sdk.testing import MockPluginAPI  # noqa: E402

# ---------------------------------------------------------------------------
# Fake host services for the MDRM adapter
# ---------------------------------------------------------------------------


@dataclass
class _FakeBrainResponse:
    content: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class FakeBrain:
    """Records every ``think`` call and returns a canned response."""

    def __init__(self, canned: str = "fake-brain-response") -> None:
        self.canned = canned
        self.calls: list[dict[str, Any]] = []

    async def think(self, prompt: str, *, system: str = "", **kwargs: Any) -> _FakeBrainResponse:
        self.calls.append({"prompt": prompt, "system": system, **kwargs})
        return _FakeBrainResponse(content=self.canned)


class FakeVectorStore:
    """Mimics the slim subset of ``VectorStore`` the adapter needs."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.documents: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

    def is_ready(self) -> bool:
        return self.ready

    async def add_documents(self, docs: list[dict[str, Any]], **kwargs: Any) -> None:
        self.documents.extend(docs)

    async def search(self, query: str, *, limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append({"query": query, "limit": limit, **kwargs})
        return [
            {"id": doc.get("id"), "score": 0.9, "metadata": doc} for doc in self.documents[:limit]
        ]


class FakeMemoryManager:
    """In-memory stand-in for the host MemoryManager."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []

    async def write_memory(self, record: dict[str, Any]) -> str:
        rec_id = f"mem-{len(self.records) + 1}"
        self.records.append({"id": rec_id, **record})
        return rec_id

    async def search_memories(
        self, query: str, *, limit: int = 5, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.queries.append({"query": query, "limit": limit, **kwargs})
        return self.records[:limit]


# ---------------------------------------------------------------------------
# FakePluginAPI = MockPluginAPI + plugged-in host services
# ---------------------------------------------------------------------------


class FakePluginAPI(MockPluginAPI):
    """MockPluginAPI extended with brain / memory / vector fakes.

    Pass ``granted_permissions`` to selectively disable a service (the
    matching ``get_*`` returns ``None``); pass ``None`` for "everything
    granted" which is what most tests want.
    """

    def __init__(
        self,
        plugin_id: str = "idea-research",
        *,
        granted_permissions: list[str] | None = None,
        brain: FakeBrain | None = None,
        memory: FakeMemoryManager | None = None,
        vector: FakeVectorStore | None = None,
    ) -> None:
        super().__init__(plugin_id=plugin_id, granted_permissions=granted_permissions)
        self.fake_brain = brain or FakeBrain()
        self.fake_memory = memory or FakeMemoryManager()
        self.fake_vector = vector or FakeVectorStore()

    def _allowed(self, perm: str) -> bool:
        if self._granted_permissions is None:
            return True
        return perm in self._granted_permissions

    def get_brain(self) -> Any:
        return self.fake_brain if self._allowed("brain.access") else None

    def get_memory_manager(self) -> Any:
        return (
            self.fake_memory
            if self._allowed("memory.read") or self._allowed("memory.write")
            else None
        )

    def get_vector_store(self) -> Any:
        return self.fake_vector if self._allowed("vector.access") else None

    def get_settings(self) -> Any:
        return {}


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_api(tmp_path: Path) -> FakePluginAPI:
    """All-permissions FakePluginAPI rooted in ``tmp_path``."""

    api = FakePluginAPI()
    api._data_dir = tmp_path  # type: ignore[attr-defined]
    return api


@pytest.fixture()
def fake_api_no_mdrm(tmp_path: Path) -> FakePluginAPI:
    """FakePluginAPI with brain/vector/memory permissions revoked."""

    api = FakePluginAPI(
        granted_permissions=[
            "tools.register",
            "routes.register",
            "hooks.basic",
            "config.read",
            "config.write",
            "data.own",
        ]
    )
    api._data_dir = tmp_path  # type: ignore[attr-defined]
    return api


@pytest.fixture()
def freeze_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[float]:
    """Freeze ``time.time`` at a deterministic instant for tests."""

    frozen = 1_730_000_000.0
    monkeypatch.setattr(time, "time", lambda: frozen)
    yield frozen


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Provide a per-session event loop (pytest-asyncio 0.23+ default)."""

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
