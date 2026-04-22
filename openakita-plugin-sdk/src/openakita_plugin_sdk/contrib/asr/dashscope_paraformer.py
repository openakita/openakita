"""DashScope Paraformer (Bailian ASR) provider.

Uses Bailian's async ASR API (``paraformer-v2``):

1. POST ``services/audio/asr/transcription`` with file URL → ``task_id``.
2. Poll ``tasks/<task_id>`` until ``SUCCEEDED`` / ``FAILED`` / ``CANCELED``.
3. Fetch the final ``transcription_url`` JSON, flatten into ASRChunk list.

The provider expects ``source`` to be either a public URL (string in
``kwargs["source_url"]``) or a local file (in which case the caller is
responsible for uploading first — typical pattern is to expose the file
through the host's static-files endpoint via ``add_upload_preview_route``
and pass that URL through ``source_url``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import ASRChunk, ASRError, ASRResult, BaseASRProvider

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


class DashScopeParaformerProvider(BaseASRProvider):
    provider_id = "dashscope_paraformer"
    display_name = "通义千问 Paraformer (paraformer-v2)"
    requires_api_key = True
    requires_internet = True

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")

    @property
    def model(self) -> str:
        return str(self.config.get("model") or "paraformer-v2")

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout", 120.0))

    @property
    def poll_interval(self) -> float:
        return float(self.config.get("poll_interval", 3.0))

    @property
    def poll_max_seconds(self) -> float:
        return float(self.config.get("poll_max_seconds", 1200.0))

    def _headers(self, *, async_mode: bool = True) -> dict[str, str]:
        if not self.api_key:
            raise ASRError(
                "DashScope API key not configured.",
                retryable=False,
                provider=self.provider_id,
                kind="auth",
            )
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return h

    async def transcribe(
        self,
        source: Path,
        *,
        language: str = "auto",
        source_url: str | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> ASRResult:
        if not source_url:
            raise ASRError(
                "DashScope Paraformer requires a publicly reachable file URL "
                "(pass via kwargs['source_url']). Local-only files must be "
                "uploaded first.",
                retryable=False,
                provider=self.provider_id,
                kind="config",
            )

        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ASRError(
                "httpx is required for DashScope ASR.",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            ) from exc

        body: dict[str, Any] = {
            "model": self.model,
            "input": {"file_urls": [source_url]},
            "parameters": {
                "language_hints": [language] if language and language != "auto" else [],
                "diarization_enabled": bool(self.config.get("diarization", False)),
            },
        }
        submit_url = self.base_url + "/api/v1/services/audio/asr/transcription"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(submit_url, headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                raise ASRError(
                    f"paraformer submit network error: {exc}",
                    retryable=True,
                    provider=self.provider_id,
                    kind="network",
                ) from exc
            if resp.status_code >= 400:
                raise ASRError(
                    f"paraformer submit HTTP {resp.status_code}: {resp.text[:200]}",
                    retryable=resp.status_code in (429, 500, 502, 503, 504),
                    provider=self.provider_id,
                    kind="vendor_error",
                )
            task_id = ((resp.json().get("output") or {}).get("task_id")) or ""
            if not task_id:
                raise ASRError(
                    "paraformer submit did not return a task_id",
                    retryable=False,
                    provider=self.provider_id,
                    kind="vendor_error",
                )

            # Poll
            poll_url = self.base_url + f"/api/v1/tasks/{task_id}"
            poll_headers = {"Authorization": f"Bearer {self.api_key}"}
            elapsed = 0.0
            data: dict[str, Any] = {}
            while elapsed < self.poll_max_seconds:
                await asyncio.sleep(self.poll_interval)
                elapsed += self.poll_interval
                try:
                    pr = await client.get(poll_url, headers=poll_headers)
                except httpx.HTTPError:
                    continue
                if pr.status_code >= 400:
                    continue
                data = pr.json()
                status = (data.get("output") or {}).get("task_status", "")
                if status in _TERMINAL_STATES:
                    if status != "SUCCEEDED":
                        raise ASRError(
                            f"paraformer task ended with status {status!r}",
                            retryable=False,
                            provider=self.provider_id,
                            kind="vendor_error",
                        )
                    break
            else:
                raise ASRError(
                    f"paraformer task {task_id} timed out after "
                    f"{self.poll_max_seconds}s",
                    retryable=True,
                    provider=self.provider_id,
                    kind="timeout",
                )

            # Fetch transcript json (URL inside results[0])
            results = (data.get("output") or {}).get("results") or []
            if not results:
                raise ASRError(
                    "paraformer succeeded but returned no results",
                    retryable=False,
                    provider=self.provider_id,
                    kind="vendor_error",
                )
            transcript_url = results[0].get("transcription_url")
            if not transcript_url:
                raise ASRError(
                    "paraformer result missing transcription_url",
                    retryable=False,
                    provider=self.provider_id,
                    kind="vendor_error",
                )
            try:
                tr_resp = await client.get(transcript_url)
                tr_resp.raise_for_status()
                transcript = tr_resp.json()
            except httpx.HTTPError as exc:
                raise ASRError(
                    f"paraformer transcript download error: {exc}",
                    retryable=True,
                    provider=self.provider_id,
                    kind="network",
                ) from exc

        chunks = _flatten_paraformer_transcript(transcript)
        return ASRResult(
            provider=self.provider_id,
            chunks=chunks,
            language=language,
            duration_sec=chunks[-1].end if chunks else 0.0,
            raw={"task_id": task_id, "url": transcript_url},
        )


def _flatten_paraformer_transcript(payload: dict[str, Any]) -> list[ASRChunk]:
    """Convert the Paraformer JSON response into a flat ASRChunk list.

    Paraformer returns per-channel ``transcripts`` with nested
    ``sentences`` (each with begin/end ms + text). We pick the first
    channel as the canonical track.
    """
    transcripts = payload.get("transcripts") or []
    if not transcripts:
        return []
    sentences = transcripts[0].get("sentences") or []
    out: list[ASRChunk] = []
    for sent in sentences:
        try:
            out.append(
                ASRChunk(
                    start=float(sent.get("begin_time", 0)) / 1000.0,
                    end=float(sent.get("end_time", 0)) / 1000.0,
                    text=str(sent.get("text", "")).strip(),
                    confidence=float(sent.get("confidence", 1.0) or 1.0),
                )
            )
        except (TypeError, ValueError):
            continue
    return out
