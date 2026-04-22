"""Base classes and common types for the contrib.tts provider library."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """Standard TTS failure surface.

    ``retryable`` lets callers (e.g. ``BaseVendorClient``-aware plugins)
    decide whether to attempt a different provider or escalate to
    ``ErrorCoach``.
    """

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


@dataclass(slots=True)
class TTSResult:
    """Common return shape for every provider.

    ``audio_path`` is the on-disk artefact (mp3/wav). ``duration_sec`` is a
    *best-effort* estimate; callers that need exact duration should ffprobe
    the file.  ``raw`` carries vendor-specific debug info — never rely on
    its keys for control flow.
    """

    provider: str
    audio_path: Path
    duration_sec: float
    voice: str
    raw: dict[str, Any] = field(default_factory=dict)


def estimate_duration_sec(text: str, *, chars_per_sec: float = 4.0) -> float:
    """Rough talking-speed estimate used as a fallback.

    The default of 4 chars/sec is an empirical mid-point across CN/EN
    natural speech rates. Callers should prefer ffprobe when accuracy
    matters (e.g. for video sync).
    """
    if not text:
        return 1.0
    return max(1.0, len(text) / max(chars_per_sec, 0.1))


class BaseTTSProvider(ABC):
    """Provider contract — every TTS backend implements this.

    Subclasses must:
    - declare ``provider_id`` (stable string used by ``select_provider``);
    - implement :meth:`synthesize`;
    - declare ``requires_api_key`` (True for paid vendors);
    - implement :meth:`is_available` if the provider needs a runtime check
      (e.g. an optional pip package); the default returns True.

    Construction takes a ``config: dict`` so callers can plumb
    ``{"api_key": "...", "base_url": "...", ...}`` from plugin
    settings without each provider hand-rolling argument names.
    """

    provider_id: ClassVar[str] = "abstract"
    display_name: ClassVar[str] = "Abstract TTS Provider"
    requires_api_key: ClassVar[bool] = False
    requires_internet: ClassVar[bool] = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    @property
    def api_key(self) -> str | None:
        key = self.config.get("api_key")
        return str(key) if key else None

    def update_api_key(self, api_key: str | None) -> None:
        """Hot-swap the API key without rebuilding the provider.

        Mirrors the ``update_api_key`` convention from
        ``BaseVendorClient`` so plugins can wire their ``POST /settings``
        route without special-casing TTS.
        """
        self.config["api_key"] = api_key or ""

    def is_available(self) -> bool:
        """Default availability check: present iff API key is supplied
        when required, and the provider hasn't been disabled in config."""
        if self.config.get("disabled"):
            return False
        if self.requires_api_key and not self.api_key:
            return False
        return True

    @abstractmethod
    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        output_dir: Path,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        **kwargs: Any,
    ) -> TTSResult:
        """Render ``text`` to an audio file under ``output_dir``."""

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        """Most TTS calls are synchronous on the vendor side; default no-op."""
        return False
