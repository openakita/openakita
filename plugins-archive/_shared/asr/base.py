"""Common types for the contrib.asr provider library."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


class ASRError(Exception):
    """Standard ASR failure surface."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        provider: str = "",
        kind: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.provider = provider
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ASRChunk:
    """Sentence-ish granularity transcript segment.

    Field names intentionally match the ``TranscriptChunk`` shape used
    by highlight-cutter so downstream code can swap implementations
    without renaming attributes.
    """

    start: float  # seconds
    end: float
    text: str
    confidence: float = 1.0


@dataclass(slots=True)
class ASRResult:
    """Full ASR response — chunks plus debug metadata."""

    provider: str
    chunks: list[ASRChunk]
    language: str = ""
    duration_sec: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class BaseASRProvider(ABC):
    provider_id: ClassVar[str] = "abstract"
    display_name: ClassVar[str] = "Abstract ASR Provider"
    requires_api_key: ClassVar[bool] = False
    requires_internet: ClassVar[bool] = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    @property
    def api_key(self) -> str | None:
        key = self.config.get("api_key")
        return str(key) if key else None

    def update_api_key(self, api_key: str | None) -> None:
        self.config["api_key"] = api_key or ""

    def is_available(self) -> bool:
        if self.config.get("disabled"):
            return False
        if self.requires_api_key and not self.api_key:
            return False
        return True

    @abstractmethod
    async def transcribe(
        self,
        source: Path,
        *,
        language: str = "auto",
        **kwargs: Any,
    ) -> ASRResult:
        """Transcribe ``source`` (audio or video) to an ``ASRResult``."""
