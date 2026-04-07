"""Plugin protocols — abstract interfaces for pluggable subsystems."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryBackendProtocol(Protocol):
    """Full memory backend — plugin can replace built-in memory."""

    async def store(self, memory: dict) -> str: ...
    async def search(self, query: str, limit: int = 10) -> list[dict]: ...
    async def delete(self, memory_id: str) -> bool: ...
    async def get_injection_context(self, query: str, max_tokens: int) -> str: ...
    async def start_session(self, session_id: str) -> None: ...
    async def end_session(self) -> None: ...
    async def record_turn(self, role: str, content: str) -> None: ...


@runtime_checkable
class RetrievalSource(Protocol):
    """External retrieval source — Obsidian / Notion / local files / etc."""

    source_name: str

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]: ...


@runtime_checkable
class SearchBackend(Protocol):
    """Search backend protocol (mirrors existing memory.search_backends)."""

    def search(
        self, query: str, limit: int = 10, filter_type: str | None = None
    ) -> list[tuple[str, float]]: ...

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool: ...

    def delete(self, memory_id: str) -> bool: ...

    def batch_add(self, items: list[dict]) -> int: ...

    @property
    def available(self) -> bool: ...

    @property
    def backend_type(self) -> str: ...
