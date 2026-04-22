"""WhisperLocalProvider — wraps whisper.cpp / whisper-cli for local ASR.

This is a re-implementation of the ``whisper_cpp_transcribe`` helper that
previously lived inside ``plugins/highlight-cutter/highlight_engine.py``
so other plugins (subtitle-maker, transcribe-archive, dub-it) can share
the same codepath.

The provider is "available" iff the configured whisper binary is on PATH
or supplied explicitly via ``config["binary"]``. No API key is needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import ASRChunk, ASRError, ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


class WhisperLocalProvider(BaseASRProvider):
    provider_id = "whisper_local"
    display_name = "whisper.cpp (local fallback)"
    requires_api_key = False
    requires_internet = False

    DEFAULT_BINARY = "whisper-cli"
    DEFAULT_MODEL = "base"

    @property
    def binary(self) -> str:
        return str(self.config.get("binary") or self.DEFAULT_BINARY)

    @property
    def model(self) -> str:
        return str(self.config.get("model") or self.DEFAULT_MODEL)

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout", 600.0))

    def _resolve_binary(self) -> str | None:
        return shutil.which(self.binary)

    def is_available(self) -> bool:
        if self.config.get("disabled"):
            return False
        return self._resolve_binary() is not None

    async def transcribe(
        self,
        source: Path,
        *,
        language: str = "auto",
        **kwargs: Any,  # noqa: ARG002
    ) -> ASRResult:
        bin_path = self._resolve_binary()
        if not bin_path:
            raise ASRError(
                f"whisper binary {self.binary!r} not found on PATH",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            )
        out_json = source.with_suffix(".whisper.json")
        cmd = [
            bin_path,
            "-m", self.model,
            "-l", language,
            "--output-json",
            "--output-file", str(out_json.with_suffix("")),
            str(source),
        ]

        def _run() -> tuple[list[ASRChunk], dict[str, Any]]:
            try:
                subprocess.run(
                    cmd, timeout=self.timeout, check=True, capture_output=True,
                )
            except subprocess.SubprocessError as exc:
                raise ASRError(
                    f"whisper.cpp failed: {exc}",
                    retryable=False,
                    provider=self.provider_id,
                    kind="vendor_error",
                ) from exc
            try:
                payload = json.loads(out_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ASRError(
                    f"cannot read whisper json output: {exc}",
                    retryable=False,
                    provider=self.provider_id,
                    kind="vendor_error",
                ) from exc
            chunks: list[ASRChunk] = []
            for seg in payload.get("transcription", []):
                try:
                    offsets = seg.get("offsets") or {}
                    chunks.append(
                        ASRChunk(
                            start=float(offsets.get("from", 0)) / 1000.0,
                            end=float(offsets.get("to", 0)) / 1000.0,
                            text=str(seg.get("text", "")).strip(),
                            confidence=1.0,
                        )
                    )
                except (TypeError, ValueError):
                    continue
            return chunks, payload

        chunks, payload = await asyncio.to_thread(_run)
        return ASRResult(
            provider=self.provider_id,
            chunks=chunks,
            language=language,
            duration_sec=chunks[-1].end if chunks else 0.0,
            raw={"binary": bin_path, "model": self.model, "payload_keys": list(payload.keys())},
        )
