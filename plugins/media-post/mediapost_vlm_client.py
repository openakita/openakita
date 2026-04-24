"""DashScope Qwen-VL-max + Qwen-Plus async client for media-post.

Three call surfaces, all 1:1 with ``docs/media-post-plan.md`` §6.1:

- :func:`MediaPostVlmClient.call_vlm_batch` — single 8-frame batch.
  Ports CutClaw ``Reviewer.py:545-606``
  (``_detect_protagonist_in_frames_vlm``).
- :func:`MediaPostVlmClient.call_vlm_concurrent` — N batches running
  through an :class:`asyncio.Semaphore`, results flattened back into
  the original frame order with failed slots set to ``None``. Ports
  CutClaw ``Reviewer.py:656-737`` (``get_protagonist_frame_data``).
- :func:`MediaPostVlmClient.qwen_plus_call` — text-only Qwen-Plus call
  used by the SEO generator (Phase 3 ``mediapost_seo_generator``).

All three classify HTTP / transport failures into the canonical 9-key
error taxonomy from ``mediapost_models.ERROR_HINTS`` so the pipeline
can write ``tasks.error_kind`` straight from the raised
:class:`mediapost_models.MediaPostError`. Retries follow an exponential
backoff (``2 ** attempt`` seconds) and only kick in for retryable
status codes (``429`` / ``5xx``) or transport-level errors.

Red-line guards baked in (per ``docs/media-post-plan.md`` §13):

- Lazy ``import httpx`` — module import never pulls the dependency in.
- ``finally: gc.collect()`` after every batch — CutClaw's hard-won
  fix for OOM on long ``multi_aspect`` jobs (one 1080p frame in
  base64 is ~1 MB; 8 of them = 8 MB per batch).
- ``len(detections) == len(frame_indices)`` ordering guard — VLM
  occasionally returns N±1 entries; the guard returns ``None`` so the
  caller treats the batch as a clean failure rather than silently
  mis-pairing frames with scores.
- 429 always maps to ``quota`` (never ``rate_limit``) so the unified
  error catalog stays at 9 keys.
- No ``shell=True`` and no archive imports.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from mediapost_models import (
    DEFAULT_VLM_BATCH_SIZE,
    MediaPostError,
    map_vendor_kind_to_error_kind,
)

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
DASHSCOPE_CHAT_PATH = "/compatible-mode/v1/chat/completions"

DEFAULT_VLM_MODEL = "qwen-vl-max"
DEFAULT_QWEN_PLUS_MODEL = "qwen-plus"

# CutClaw §6.1 P1 — 4 MB limit on a single image inside the request body;
# the recompose / cover_pick frames are 512x288 PNG ~100 KB so 8 frames
# stays well under the limit.
MAX_REQUEST_IMAGE_BYTES = 4 * 1024 * 1024

# Per §6.1 P0 — 8 frames is CutClaw's empirically-verified upper bound;
# higher batch sizes saw mismatch rates >5% in their internal tests.
MAX_VLM_BATCH_FRAMES = DEFAULT_VLM_BATCH_SIZE

# Default retry config: total 3 attempts (initial + 2 retries) with
# 1s / 2s backoff. Kept conservative because Qwen-VL batches are
# expensive (¥0.08/batch) and we'd rather surface the error than
# silently retry past the user-visible cost preview.
DEFAULT_MAX_RETRIES = 2

# Exponential backoff base (seconds) used by the per-attempt sleep.
_BACKOFF_BASE_SEC = 2.0

# Substrings that appear in DashScope error payloads when the request
# triggered the platform's content-moderation pipeline. We map these to
# the canonical ``moderation`` kind so the UI can show the dedicated
# coach card instead of a generic 4xx error.
_MODERATION_TOKENS = (
    "data_inspection_failed",
    "input_data_inspection_failed",
    "response_data_inspection_failed",
    "image risky",
    "sensitive content",
    "moderation",
)


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class MediaPostVlmClient:
    """Async client wrapping DashScope's Qwen-VL-max and Qwen-Plus endpoints.

    The client is created once per pipeline run (Phase 3 ``mediapost_pipeline``)
    and reused across all batches inside a single task so the underlying
    ``httpx.AsyncClient`` pools its TCP connections.

    Args:
        api_key: DashScope API key (``DASHSCOPE_API_KEY`` env var).
        base_url: Optional override for self-hosted / regional gateways.
        timeout: Per-request timeout in seconds.
        max_retries: Number of retries *after* the initial attempt.
            Set to 0 inside unit tests that already inject a mock
            client to keep test runtime predictable.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DASHSCOPE_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._max_retries = int(max_retries)
        self._client: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_api_key(self, key: str) -> None:
        self._api_key = key

    async def _ensure_client(self) -> Any:
        """Create the underlying ``httpx.AsyncClient`` lazily.

        Lazy import keeps ``import mediapost_vlm_client`` cheap so the
        Phase 0 ``test_skeleton`` import-smoke does not have to depend
        on httpx being available at import time.
        """
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> MediaPostVlmClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Single-batch VLM call (CutClaw Reviewer.py:545-606)
    # ------------------------------------------------------------------

    async def call_vlm_batch(
        self,
        frames_b64: list[str],
        frame_indices: list[int],
        prompt_template: str,
        prompt_kwargs: dict[str, Any] | None = None,
        *,
        model: str = DEFAULT_VLM_MODEL,
        system_prompt: str = "You are an expert video frame analyst.",
        timeout: float | None = None,
    ) -> list[dict[str, Any]] | None:
        """Run one VLM call with up to 8 frames and return the parsed JSON list.

        Returns ``None`` when the call succeeds at the HTTP level but the
        response cannot be parsed into a list of length
        ``len(frame_indices)`` — the caller (typically
        :meth:`call_vlm_concurrent`) treats ``None`` as a clean batch
        failure and leaves the corresponding output slots ``None``.

        Raises :class:`MediaPostError` for:

        - ``auth`` (401 / 403)
        - ``quota`` (429 / quota_exceeded payloads)
        - ``moderation`` (DashScope ``data_inspection_failed`` etc.)
        - ``timeout`` (httpx Timeout / ReadTimeout)
        - ``network`` (httpx connect / 5xx)
        - ``format`` (4xx other than the above)
        - ``unknown`` (everything else — never silently swallowed)

        Args:
            frames_b64: Base64-encoded PNG / JPEG frame strings (no
                data URI prefix). Length 1..8.
            frame_indices: Integer ids the prompt template uses to
                tell the model "frame N has property X". Must be the
                same length as ``frames_b64``.
            prompt_template: f-string-style template; the call applies
                ``.format(**prompt_kwargs)`` so callers can pass things
                like ``{frame_count}`` / ``{frame_indices}``.
            prompt_kwargs: Substitutions for the template.
            model: Override the default ``qwen-vl-max``.
            system_prompt: System-role instruction.
            timeout: Per-request timeout override.
        """
        if not self._api_key:
            raise MediaPostError("auth", "DashScope API key not configured")
        if not frames_b64:
            raise MediaPostError("format", "call_vlm_batch: frames_b64 is empty")
        if len(frames_b64) != len(frame_indices):
            raise MediaPostError(
                "format",
                "call_vlm_batch: len(frames_b64) != len(frame_indices)",
            )
        if len(frames_b64) > MAX_VLM_BATCH_FRAMES:
            raise MediaPostError(
                "format",
                f"call_vlm_batch: batch size {len(frames_b64)} exceeds "
                f"limit {MAX_VLM_BATCH_FRAMES} (CutClaw §6.1 P0)",
            )

        kwargs = dict(prompt_kwargs or {})
        try:
            user_text = prompt_template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            raise MediaPostError(
                "format", f"prompt_template format error: {exc}"
            ) from exc

        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for b64 in frames_b64:
            data_uri = f"data:image/png;base64,{b64}"
            user_content.append(
                {"type": "image_url", "image_url": {"url": data_uri}}
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        body = {"model": model, "messages": messages, "max_tokens": 4000}

        try:
            data = await self._post_chat(body, timeout=timeout)
        except MediaPostError:
            raise
        finally:
            user_content.clear()
            messages.clear()
            gc.collect()

        raw = (
            (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            or ""
        )
        return _parse_vlm_json_list(raw, expected_len=len(frame_indices))

    # ------------------------------------------------------------------
    # Concurrent multi-batch VLM call (CutClaw Reviewer.py:656-737)
    # ------------------------------------------------------------------

    async def call_vlm_concurrent(
        self,
        all_frames_b64: list[str],
        all_indices: list[int],
        prompt_template: str,
        prompt_kwargs_factory: Callable[[list[int]], dict[str, Any]],
        *,
        batch_size: int = DEFAULT_VLM_BATCH_SIZE,
        concurrency: int = 4,
        model: str = DEFAULT_VLM_MODEL,
        system_prompt: str = "You are an expert video frame analyst.",
    ) -> list[dict[str, Any] | None]:
        """Run N batches concurrently; preserve order; failed slots are ``None``.

        - The output list always has length ``len(all_indices)``.
        - Slot ``i`` holds the parsed dict for frame ``all_indices[i]``,
          or ``None`` if its batch failed (for any reason: HTTP error,
          JSON parse error, length mismatch, etc).
        - Per-batch :class:`MediaPostError`s are caught and turned into
          ``None`` slots — the caller decides whether the overall task
          should fail (e.g. cover_pick can drop failed candidates;
          smart_recompose interpolates over them).
        - ``batch_size`` is capped at :data:`MAX_VLM_BATCH_FRAMES`.

        Args:
            all_frames_b64: Full list of base64 PNGs to process.
            all_indices: Parallel list of integer ids; length must
                match ``all_frames_b64``.
            prompt_template: Same template applied to every batch.
            prompt_kwargs_factory: Called once per batch with the
                batch's slice of ``all_indices`` so the prompt can
                reference frame ids correctly per batch.
            batch_size: Frames per VLM call (default 8 — see §13 #6).
            concurrency: Maximum concurrent in-flight batches.
        """
        if len(all_frames_b64) != len(all_indices):
            raise MediaPostError(
                "format",
                "call_vlm_concurrent: all_frames_b64 / all_indices length mismatch",
            )
        if batch_size < 1 or batch_size > MAX_VLM_BATCH_FRAMES:
            raise MediaPostError(
                "format",
                f"call_vlm_concurrent: batch_size must be 1..{MAX_VLM_BATCH_FRAMES}",
            )
        if concurrency < 1:
            raise MediaPostError(
                "format", "call_vlm_concurrent: concurrency must be >= 1"
            )

        n = len(all_indices)
        if n == 0:
            return []

        batches: list[tuple[list[str], list[int]]] = []
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            batches.append((all_frames_b64[start:end], all_indices[start:end]))

        sem = asyncio.Semaphore(concurrency)

        async def _one(
            b_frames: list[str], b_indices: list[int]
        ) -> list[dict[str, Any]] | None:
            async with sem:
                try:
                    return await self.call_vlm_batch(
                        b_frames,
                        b_indices,
                        prompt_template,
                        prompt_kwargs_factory(b_indices),
                        model=model,
                        system_prompt=system_prompt,
                    )
                except MediaPostError as exc:
                    logger.warning(
                        "call_vlm_concurrent batch[%s..%s] failed: kind=%s msg=%s",
                        b_indices[0] if b_indices else "?",
                        b_indices[-1] if b_indices else "?",
                        exc.kind,
                        exc.message,
                    )
                    return None

        batch_results = await asyncio.gather(
            *(_one(bf, bi) for bf, bi in batches), return_exceptions=False
        )

        flat: list[dict[str, Any] | None] = [None] * n
        for batch_idx, result in enumerate(batch_results):
            if result is None:
                continue
            offset = batch_idx * batch_size
            for k, det in enumerate(result):
                if offset + k < n:
                    flat[offset + k] = det
        return flat

    # ------------------------------------------------------------------
    # Qwen-Plus text-only call (used by mediapost_seo_generator)
    # ------------------------------------------------------------------

    async def qwen_plus_call(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = DEFAULT_QWEN_PLUS_MODEL,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: float | None = None,
    ) -> str:
        """Run a text-only Qwen-Plus chat completion and return the raw content.

        Caller is responsible for JSON parsing / format validation
        (the SEO generator does both with platform-aware fallbacks).

        Returns the assistant's ``content`` string, or empty string if
        the API returned a 200 with empty content (rare).

        Raises :class:`MediaPostError` on the same 9-key taxonomy as
        :meth:`call_vlm_batch`.
        """
        if not self._api_key:
            raise MediaPostError("auth", "DashScope API key not configured")
        if not messages:
            raise MediaPostError("format", "qwen_plus_call: messages is empty")

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        data = await self._post_chat(body, timeout=timeout)
        content = (
            (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            or ""
        )
        return str(content)

    # ------------------------------------------------------------------
    # Internals — HTTP plumbing with retry + 9-kind classification
    # ------------------------------------------------------------------

    async def _post_chat(
        self, body: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        """POST to the DashScope chat endpoint with retry + classification."""
        import httpx

        client = await self._ensure_client()
        url = self._base_url + DASHSCOPE_CHAT_PATH
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        per_request_timeout = float(timeout) if timeout else self._timeout

        last_exc: MediaPostError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=per_request_timeout,
                )
            except httpx.TimeoutException as exc:
                last_exc = MediaPostError(
                    "timeout", f"DashScope request timeout: {exc}"
                )
            except httpx.HTTPError as exc:
                vendor_kind = _classify_transport_error(exc)
                last_exc = MediaPostError(
                    map_vendor_kind_to_error_kind(vendor_kind),
                    f"DashScope transport error: {exc}",
                )
            else:
                kind, retryable, message = _classify_http_response(resp)
                if kind is None:
                    try:
                        return resp.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        last_exc = MediaPostError(
                            "format", f"DashScope returned non-JSON 200: {exc}"
                        )
                    else:
                        # Unreachable — return inside try satisfies all paths.
                        return {}
                else:
                    last_exc = MediaPostError(kind, message)
                    if not retryable:
                        raise last_exc

            if attempt < self._max_retries:
                await asyncio.sleep(_BACKOFF_BASE_SEC ** attempt)
                continue
            break

        assert last_exc is not None
        raise last_exc


# ---------------------------------------------------------------------------
# Helpers (module-private; exposed for the test suite via __all__)
# ---------------------------------------------------------------------------


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _strip_json_fence(raw: str) -> str:
    """Strip ```json ...``` markdown fences if present, else return ``raw.strip()``."""
    if not raw:
        return ""
    m = _JSON_FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def _parse_vlm_json_list(
    raw: str, *, expected_len: int
) -> list[dict[str, Any]] | None:
    """Parse a VLM ``content`` string into a list[dict] of the expected length.

    Returns ``None`` (not raises) when:

    - the response is empty
    - the JSON parse fails after fence stripping
    - the parsed value is not a ``list``
    - the list length does not match ``expected_len``

    Raising would force the caller into try/except boilerplate; the
    pipeline treats ``None`` as "skip this batch" (CutClaw pattern).
    """
    cleaned = _strip_json_fence(raw)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    if len(parsed) != expected_len:
        logger.warning(
            "VLM length mismatch: got %d, expected %d (treating as failed batch)",
            len(parsed),
            expected_len,
        )
        return None
    out: list[dict[str, Any]] = []
    for item in parsed:
        out.append(item if isinstance(item, dict) else {})
    return out


def _classify_http_response(resp: Any) -> tuple[str | None, bool, str]:
    """Classify an httpx response into (error_kind, retryable, message).

    ``error_kind`` is ``None`` when the response is a clean 2xx — the
    caller then tries to parse the body. Everything else returns one of
    the 9 canonical kinds. ``retryable`` is True for 429 / 5xx so the
    POST loop can back off and retry.
    """
    status = int(getattr(resp, "status_code", 0))
    body_preview = ""
    try:
        body_preview = (resp.text or "")[:400]
    except Exception:  # pragma: no cover  -- defensive against MagicMock
        body_preview = ""

    if 200 <= status < 300:
        return None, False, ""

    body_lower = body_preview.lower()

    if status in (401, 403):
        return "auth", False, f"HTTP {status} {body_preview}"

    if status == 404:
        return "format", False, f"HTTP {status} {body_preview}"

    if status == 429:
        return "quota", True, f"HTTP {status} {body_preview}"

    if any(token in body_lower for token in _MODERATION_TOKENS):
        return "moderation", False, f"HTTP {status} {body_preview}"

    if status == 400 and "quota" in body_lower:
        return "quota", False, f"HTTP {status} {body_preview}"

    if status >= 500:
        return "network", True, f"HTTP {status} {body_preview}"

    if 400 <= status < 500:
        return "format", False, f"HTTP {status} {body_preview}"

    return "unknown", False, f"HTTP {status} {body_preview}"


def _classify_transport_error(exc: BaseException) -> str:
    """Map httpx transport errors into the vendor-kind taxonomy."""
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "Connect" in name or "Network" in name or "Proxy" in name:
        return "network"
    return "unknown"


__all__ = [
    "DASHSCOPE_BASE_URL",
    "DEFAULT_QWEN_PLUS_MODEL",
    "DEFAULT_VLM_MODEL",
    "MAX_VLM_BATCH_FRAMES",
    "MediaPostVlmClient",
]
