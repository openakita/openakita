"""DashScope client wrappers for subtitle-craft.

Three vendor surfaces, each as its own method on ``SubtitleAsrClient``:

- ``transcribe()`` — Paraformer-v2 word-level ASR (P0-1, P0-2, P0-3, P0-4,
  P0-5, P0-15 + word_normalize).
- ``translate_batch()`` — Qwen-MT chunked translation (P0-6, P0-7, P1-5,
  P1-6).
- ``identify_characters()`` — Qwen-VL speaker → character name mapping
  (P1-12 fallback rules; CutClaw prompt translated to Chinese).

Architectural rules baked in (red-line guardrails):

- Subclasses ``BaseVendorClient`` from ``subtitle_craft_inline.vendor_client``;
  inherits retry / cancel / moderation contract.
- All errors raised as ``AsrError`` carrying one of the **9 canonical
  error_kind** strings (mapped via ``map_vendor_kind_to_error_kind``);
  pipeline writes them straight to ``tasks.error_kind``.
- Paraformer task **query** uses ``POST /api/v1/tasks/{task_id}`` — see
  ``VALIDATION.md §2`` (P0-5 ruling). **No GET fallback branch.**
- Word-level fields normalized via ``_normalize_word()`` to the canonical
  shape ``{text, start_ms, end_ms, punctuation}``; pipeline never sees raw
  Paraformer field names (P0-15).
- ``language_hints`` accepts ISO codes (`zh`, `en`, ...) and an empty list
  for `auto`; passed through to Paraformer parameters dict only when
  non-empty.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from subtitle_craft_inline.vendor_client import BaseVendorClient, VendorError
from subtitle_models import language_name, map_vendor_kind_to_error_kind

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"

PARAFORMER_SUBMIT_PATH = "/api/v1/services/audio/asr/transcription"
PARAFORMER_TASK_PATH_TPL = "/api/v1/tasks/{task_id}"
QWEN_OPENAI_PATH = "/compatible-mode/v1/chat/completions"

PARAFORMER_MODEL = "paraformer-v2"
QWEN_MT_DEFAULT_MODEL = "qwen-mt-flash"
QWEN_VL_DEFAULT_MODEL = "qwen-vl-max"
QWEN_PLUS_DEFAULT_MODEL = "qwen-plus"
# Models that reliably honor OpenAI-compatible JSON mode on DashScope (v1.1).
# Lesser models (qwen-mt, qwen-vl) silently ignore response_format.
QWEN_PLUS_JSON_MODE_WHITELIST: frozenset[str] = frozenset(
    {"qwen-plus", "qwen-plus-2025-09-11", "qwen-max"}
)

_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"})

# Per VALIDATION.md §3: 8192 doc cap; 6000 token safe headroom; ~0.7 token/char
# in mixed CJK + ASCII; 8500-char chunk respects both budgets.
QWEN_MT_CHAR_CHUNK = 8500

# Defensive prose-stripper (VALIDATION.md §3 + P1 Qwen-MT contract).
_PROSE_PREAMBLE_PREFIXES: tuple[str, ...] = (
    "sure,",
    "sure!",
    "here is the translation",
    "here are the translations",
    "here is",
    "here are",
    "below is",
    "below are",
    "translation:",
    "translations:",
    "以下是",
    "下面是",
    "翻译如下",
    "译文：",
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsrWord:
    """One word from Paraformer-v2 (canonical shape).

    Times in **integer milliseconds** (P0-2, VALIDATION.md §1).
    """

    text: str
    start_ms: int
    end_ms: int
    punctuation: str = ""
    speaker_id: str | None = None


@dataclass(frozen=True)
class AsrSentence:
    """One sentence from Paraformer-v2 with word-level breakdown."""

    start_ms: int
    end_ms: int
    text: str
    words: tuple[AsrWord, ...] = ()
    speaker_id: str | None = None


@dataclass
class AsrResult:
    """Result of a Paraformer-v2 transcription job."""

    sentences: list[AsrSentence] = field(default_factory=list)
    full_text: str = ""
    language: str = ""
    duration_sec: float = 0.0
    speaker_count: int = 0
    channel_count: int = 1
    api_task_id: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def all_words(self) -> list[AsrWord]:
        out: list[AsrWord] = []
        for s in self.sentences:
            out.extend(s.words)
        return out


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AsrError(Exception):
    """Failure from any of the three vendor methods.

    ``kind`` is always one of the 9 canonical kinds (mapped from
    ``vendor_client.ERROR_KIND_*`` via ``map_vendor_kind_to_error_kind``);
    pipeline writes it straight to ``tasks.error_kind`` without further
    re-mapping.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        retryable: bool = False,
        status: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status
        self.body = body


def _from_vendor_error(exc: VendorError, fallback_msg: str) -> AsrError:
    canonical = map_vendor_kind_to_error_kind(exc.kind or "unknown")
    return AsrError(
        f"{fallback_msg}: {exc}",
        kind=canonical,
        retryable=exc.retryable,
        status=exc.status,
        body=exc.body,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SubtitleAsrClient(BaseVendorClient):
    """DashScope ASR + translation + character-id client for subtitle-craft.

    Inherits retry / moderation / timeout from ``BaseVendorClient``. Subclass
    only overrides ``auth_headers`` and ``cancel_task``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DASHSCOPE_BASE_URL,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
        poll_max_seconds: float = 900.0,
    ) -> None:
        super().__init__(base_url=base_url, timeout=timeout)
        self._api_key = (api_key or "").strip()
        self.poll_interval = max(1.0, float(poll_interval))
        self.poll_max_seconds = max(30.0, float(poll_max_seconds))

    # -- subclass contract ------------------------------------------------

    def auth_headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AsrError(
                "DashScope API key is not configured",
                kind="auth",
                retryable=False,
            )
        return {"Authorization": f"Bearer {self._api_key}"}

    async def cancel_task(self, task_id: str) -> bool:
        """Best-effort Paraformer task cancel.

        Sends ``DELETE /api/v1/tasks/{task_id}``. DashScope returns 200 on
        success, 404 if the task already finished, and 4xx for not-cancelable
        states. Any non-2xx is treated as "no cancel happened" and returned
        as ``False`` so the caller can still mark the local task as canceled.
        """
        if not task_id:
            return False
        try:
            await self.request("DELETE", PARAFORMER_TASK_PATH_TPL.format(task_id=task_id))
            return True
        except VendorError as exc:
            logger.info("Paraformer cancel returned non-2xx for %s: %s", task_id, exc)
            return False

    def update_api_key(self, key: str) -> None:
        self._api_key = (key or "").strip()

    # -- 1. Paraformer transcription (P0-1~P0-7, P0-15) -------------------

    async def transcribe(
        self,
        audio_url: str,
        *,
        language_hints: list[str] | None = None,
        diarization_enabled: bool = False,
        channel_id: list[int] | None = None,
        cancel_check: callable | None = None,  # type: ignore[type-arg]
    ) -> AsrResult:
        """Submit Paraformer-v2 ASR job, poll, download, normalize.

        Args:
            audio_url: Publicly reachable audio URL (P0-1).
            language_hints: ISO codes like ``["zh"]`` / ``["en"]``; empty/None
                lets Paraformer auto-detect (P1-5).
            diarization_enabled: Enable speaker diarization (P2-2).
            channel_id: Channel indices to transcribe (default ``[0]``;
                multi-channel returns multiple ``transcripts[]``).
            cancel_check: Optional 0-arg callable returning True when the
                local task was canceled — checked on each poll iteration.

        Raises:
            AsrError: Always with one of the 9 canonical ``kind`` values.
        """
        if not audio_url:
            raise AsrError("audio_url is required (must be public URL)", kind="format")

        body: dict[str, Any] = {
            "model": PARAFORMER_MODEL,
            "input": {"file_urls": [audio_url]},
            "parameters": {
                "channel_id": channel_id or [0],
                # P0-3 + P0-15: word-level timestamps mandatory.
                "timestamp_alignment_enabled": True,
            },
        }
        if language_hints:
            body["parameters"]["language_hints"] = list(language_hints)
        if diarization_enabled:
            body["parameters"]["diarization_enabled"] = True

        try:
            submit_payload = await self.request(
                "POST",
                PARAFORMER_SUBMIT_PATH,
                json_body=body,
                extra_headers={"X-DashScope-Async": "enable"},
            )
        except VendorError as exc:
            raise _from_vendor_error(exc, "Paraformer submit failed") from exc

        task_id = ((submit_payload.get("output") or {}).get("task_id")) or ""
        if not task_id:
            raise AsrError(
                f"Paraformer submit returned no task_id: {submit_payload!r}",
                kind="unknown",
            )

        final_payload = await self._poll_paraformer_task(task_id, cancel_check=cancel_check)

        # P0-4: every results[i].subtask_status must be SUCCEEDED.
        results = (final_payload.get("output") or {}).get("results") or []
        if not results:
            raise AsrError(
                "Paraformer succeeded but returned no results[]",
                kind="unknown",
                body=final_payload,
            )
        for idx, r in enumerate(results):
            sub_status = r.get("subtask_status") or "SUCCEEDED"
            if sub_status != "SUCCEEDED":
                code = r.get("code") or ""
                msg = r.get("message") or ""
                raise AsrError(
                    f"Paraformer subtask[{idx}] not SUCCEEDED: {sub_status} code={code} msg={msg}",
                    kind="format" if code else "unknown",
                    body=r,
                )

        # P1-3: Pull transcript JSON immediately (URL valid 24h max,
        # OSS-signed; pipeline caches into transcripts table).
        sentences_per_channel: list[list[AsrSentence]] = []
        speaker_ids: set[str] = set()
        languages: set[str] = set()
        max_end_ms = 0
        raw_transcripts: list[dict[str, Any]] = []

        for r in results:
            transcription_url = r.get("transcription_url")
            if not transcription_url:
                raise AsrError(
                    f"Paraformer result missing transcription_url: {r!r}",
                    kind="unknown",
                )
            try:
                payload = await self.request("GET", transcription_url)
            except VendorError as exc:
                raise _from_vendor_error(exc, "Paraformer transcript download failed") from exc
            if not isinstance(payload, dict):
                raise AsrError(
                    "Paraformer transcript JSON is not an object",
                    kind="format",
                    body=str(payload)[:200],
                )
            raw_transcripts.append(payload)
            for tr in payload.get("transcripts") or []:
                lang = tr.get("language") or ""
                if lang:
                    languages.add(lang)
                ch_sentences: list[AsrSentence] = []
                for sent in tr.get("sentences") or []:
                    sentence = _normalize_sentence(sent)
                    if sentence is None:
                        continue
                    if sentence.speaker_id:
                        speaker_ids.add(sentence.speaker_id)
                    if sentence.end_ms > max_end_ms:
                        max_end_ms = sentence.end_ms
                    ch_sentences.append(sentence)
                if ch_sentences:
                    sentences_per_channel.append(ch_sentences)

        # Flatten all channels into a single timeline (caller can later
        # split via transcripts[i].channel_id if multi-channel UI is added).
        flat: list[AsrSentence] = []
        for ch in sentences_per_channel:
            flat.extend(ch)
        flat.sort(key=lambda s: s.start_ms)

        full_text = " ".join(s.text for s in flat).strip()
        primary_language = (
            next(iter(languages))
            if languages
            else ((language_hints or [""])[0] if language_hints else "")
        )

        return AsrResult(
            sentences=flat,
            full_text=full_text,
            language=primary_language,
            duration_sec=round(max_end_ms / 1000.0, 3),
            speaker_count=len(speaker_ids),
            channel_count=max(1, len(sentences_per_channel)),
            api_task_id=task_id,
            raw_payload={"submit": submit_payload, "final": final_payload},
        )

    async def _poll_paraformer_task(
        self,
        task_id: str,
        *,
        cancel_check: callable | None = None,  # type: ignore[type-arg]
    ) -> dict[str, Any]:
        """Poll the task with **POST** (P0-5 ruling per VALIDATION.md §2).

        Note: P1-10 — non-200 from query is treated as a transient error and
        retried up to ``max_retries`` times via ``BaseVendorClient.request``;
        if a 4xx surfaces past retry, ``VendorError`` is raised and we
        re-raise as ``AsrError(kind="auth"|"format")``.
        """
        path = PARAFORMER_TASK_PATH_TPL.format(task_id=task_id)
        elapsed = 0.0
        while elapsed < self.poll_max_seconds:
            if cancel_check and cancel_check():
                # Best-effort vendor-side cancel; we still raise so caller
                # treats the local task as canceled.
                await self.cancel_task(task_id)
                raise AsrError(
                    f"Paraformer task {task_id} canceled by user",
                    kind="unknown",
                )
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
            try:
                payload = await self.request("POST", path, json_body={})
            except VendorError as exc:
                # 5xx / 429 are retried by request() already; if we're here,
                # treat 4xx as terminal.
                raise _from_vendor_error(
                    exc, f"Paraformer task query failed for {task_id}"
                ) from exc
            status = (payload.get("output") or {}).get("task_status", "")
            if status in _TERMINAL_STATES:
                if status == "SUCCEEDED":
                    return payload
                err_code = ((payload.get("output") or {}).get("code")) or ""
                err_msg = ((payload.get("output") or {}).get("message")) or ""
                kind = "format" if err_code else "unknown"
                raise AsrError(
                    f"Paraformer task {task_id} ended {status} code={err_code} msg={err_msg}",
                    kind=kind,
                    body=payload,
                )
        raise AsrError(
            f"Paraformer task {task_id} timed out after {self.poll_max_seconds}s",
            kind="timeout",
            retryable=True,
        )

    # -- 2. Qwen-MT translation (P0-6, P0-7, P1-5) -----------------------

    async def translate_batch(
        self,
        text_chunks: list[str],
        *,
        source_lang: str,
        target_lang: str,
        model: str = QWEN_MT_DEFAULT_MODEL,
    ) -> list[str]:
        """Translate a list of text chunks; returns one output per input.

        Each chunk is sent as a single user message (P0-6: no system role).
        Chunks larger than ``QWEN_MT_CHAR_CHUNK`` are split internally and
        re-stitched preserving order.

        Returns ``list[str]`` aligned 1:1 with ``text_chunks``. If a single
        sub-call fails after retries, the corresponding output is the empty
        string (caller decides whether to abort or fill with original).

        Args:
            source_lang: ISO 639 code or English name (e.g. ``"zh"`` or
                ``"Chinese"``); auto-mapped via ``language_name()`` (P1-5).
            target_lang: Same as above.
            model: One of ``qwen-mt-flash`` / ``qwen-mt-plus`` /
                ``qwen-mt-lite``.
        """
        if not text_chunks:
            return []
        src = language_name(source_lang)
        tgt = language_name(target_lang)
        if not src or not tgt:
            raise AsrError(
                f"source_lang ({source_lang!r}) and target_lang ({target_lang!r}) required",
                kind="format",
            )

        out: list[str] = []
        for chunk in text_chunks:
            if not chunk.strip():
                out.append("")
                continue
            sub_pieces = _split_long_chunk(chunk)
            translated_pieces: list[str] = []
            for piece in sub_pieces:
                translated_pieces.append(
                    await self._translate_one(piece, src=src, tgt=tgt, model=model)
                )
            out.append("\n".join(p for p in translated_pieces if p))
        return out

    async def _translate_one(self, text: str, *, src: str, tgt: str, model: str) -> str:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "translation_options": {
                "source_lang": src,
                "target_lang": tgt,
            },
        }
        try:
            payload = await self.request("POST", QWEN_OPENAI_PATH, json_body=body, timeout=60.0)
        except VendorError as exc:
            # Surface as AsrError so caller can decide single-piece fallback.
            raise _from_vendor_error(exc, f"Qwen-MT {model} call failed") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise AsrError(
                f"Qwen-MT {model} returned no choices[]",
                kind="unknown",
                body=payload,
            )
        content = ((choices[0].get("message") or {}).get("content")) or ""
        return _strip_prose_preamble(content).strip()

    # -- 3. Qwen-VL character identification (P1-12) ---------------------

    async def identify_characters(
        self,
        speaker_samples: dict[str, str],
        *,
        model: str = QWEN_VL_DEFAULT_MODEL,
        context_hint: str = "",
    ) -> dict[str, str]:
        """Map ``SPEAKER_xx`` IDs to display names via Qwen-VL text inference.

        ``speaker_samples`` maps each speaker_id to a sample-text excerpt
        (one or two sentences they spoke). Any failure returns ``{}`` so
        the caller keeps the original SPEAKER_xx labels intact (P1-12).

        Note: despite the model name, this implementation uses **text-only**
        input — visual frames are not yet wired into v1.0 (V1.1 issue).
        """
        if not speaker_samples:
            return {}

        sample_block = "\n".join(
            f"- {sid}: {sample[:200]}" for sid, sample in speaker_samples.items()
        )
        prompt = (
            "你是一名视频后期编辑助手。下面是一段视频中不同说话人的样例发言。"
            "请基于发言内容、语气、可能的角色（例如主持人/嘉宾/采访者/被采访者），"
            "为每个 SPEAKER_xx 标签建议一个简短的中文角色名（不超过 6 个汉字）。\n"
        )
        if context_hint:
            prompt += f"视频背景提示：{context_hint}\n"
        prompt += (
            f"\n说话人样例：\n{sample_block}\n\n"
            "只输出 JSON 对象，不要其他说明文字。格式：\n"
            '{"SPEAKER_00": "主持人", "SPEAKER_01": "嘉宾A", ...}\n'
            "若信息不足以判断，返回空对象 {}。"
        )

        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        try:
            payload = await self.request("POST", QWEN_OPENAI_PATH, json_body=body, timeout=60.0)
        except VendorError as exc:
            logger.warning("identify_characters: vendor failed (%s); returning {}", exc)
            return {}

        choices = payload.get("choices") or []
        if not choices:
            return {}
        content = ((choices[0].get("message") or {}).get("content")) or ""

        from subtitle_craft_inline.llm_json_parser import parse_llm_json

        result = parse_llm_json(content, fallback={}, expect=dict)
        if not isinstance(result, dict):
            return {}

        # Sanitize: keep only mappings whose key matches an input speaker_id
        # and whose value is a non-empty short string.
        cleaned: dict[str, str] = {}
        for k, v in result.items():
            if k in speaker_samples and isinstance(v, str) and v.strip():
                cleaned[k] = v.strip()[:24]
        return cleaned

    # -- 4. Generic Qwen chat-completion (hook_picker mode v1.1) ---------

    async def call_qwen_plus(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = QWEN_PLUS_DEFAULT_MODEL,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: float = 120.0,
        response_format_json: bool = True,
    ) -> str | None:
        """Generic Qwen chat-completion call (used by ``hook_picker``).

        Reuses ``BaseVendorClient.request`` so we keep the inherited retry /
        cancel / moderation contract — **no new openai SDK dependency**
        (red line #1).  Returns the raw assistant content string, or
        ``None`` if the response carried no choices.

        Args:
            messages: OpenAI-style ``[{role, content}, ...]`` chat history.
            model: Qwen model id; only models in
                ``QWEN_PLUS_JSON_MODE_WHITELIST`` enable strict JSON mode.
            temperature: Sampling temperature (CutClaw uses ``0.3``).
            max_tokens: Max output tokens; hook prompts return ~150 tokens
                so 2000 is generous.
            timeout: Per-attempt HTTP timeout (the Qwen-Plus 99th-percentile
                latency is ~6s; default is 120s for safety).
            response_format_json: Enable OpenAI-compatible JSON mode for
                whitelisted models.  Has no effect on others.

        Raises:
            AsrError: One of the 9 canonical kinds (mapped from
                ``VendorError``) so the pipeline can write directly to
                ``tasks.error_kind`` without remapping.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format_json and model in QWEN_PLUS_JSON_MODE_WHITELIST:
            body["response_format"] = {"type": "json_object"}
        try:
            payload = await self.request("POST", QWEN_OPENAI_PATH, json_body=body, timeout=timeout)
        except VendorError as exc:
            raise _from_vendor_error(exc, f"Qwen-Plus {model} call failed") from exc

        choices = payload.get("choices") or []
        if not choices:
            return None
        content = ((choices[0].get("message") or {}).get("content")) or None
        return content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_word(raw: dict[str, Any]) -> AsrWord | None:
    """Convert a raw Paraformer word dict into the canonical ``AsrWord``.

    Per VALIDATION.md §1: raw fields are
    ``begin_time``/``end_time``/``text``/``punctuation`` (ms ints).
    P0-15: this is the **only** place the raw field names appear.
    """
    try:
        start_ms = int(raw.get("begin_time", 0))
        end_ms = int(raw.get("end_time", 0))
    except (TypeError, ValueError):
        return None
    text = str(raw.get("text") or "")
    if not text:
        return None
    punctuation = str(raw.get("punctuation") or "")
    speaker_id = raw.get("speaker_id")
    if speaker_id is not None:
        speaker_id = str(speaker_id)
    return AsrWord(
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        punctuation=punctuation,
        speaker_id=speaker_id,
    )


def _normalize_sentence(raw: dict[str, Any]) -> AsrSentence | None:
    try:
        start_ms = int(raw.get("begin_time", 0))
        end_ms = int(raw.get("end_time", 0))
    except (TypeError, ValueError):
        return None
    text = str(raw.get("text") or "").strip()
    speaker_id = raw.get("speaker_id")
    if speaker_id is not None:
        speaker_id = str(speaker_id)
    words_raw = raw.get("words") or []
    words: list[AsrWord] = []
    for w in words_raw:
        normalized = _normalize_word(w)
        if normalized is not None:
            # Inherit sentence-level speaker_id if the word didn't carry one.
            if normalized.speaker_id is None and speaker_id is not None:
                normalized = AsrWord(
                    text=normalized.text,
                    start_ms=normalized.start_ms,
                    end_ms=normalized.end_ms,
                    punctuation=normalized.punctuation,
                    speaker_id=speaker_id,
                )
            words.append(normalized)
    if not text and not words:
        return None
    if not text:
        text = "".join(w.text for w in words).strip()
    return AsrSentence(
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        words=tuple(words),
        speaker_id=speaker_id,
    )


def _split_long_chunk(text: str, *, max_chars: int = QWEN_MT_CHAR_CHUNK) -> list[str]:
    """Split a chunk into pieces respecting Qwen-MT 8192-token cap (P0-7).

    Splits on newline first; if a single line still exceeds ``max_chars``,
    splits on the rough sentence delimiters (``。！？.?!``) before falling
    back to hard char-count split.
    """
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= max_chars:
            current += line
            continue
        if current:
            pieces.append(current)
        if len(line) <= max_chars:
            current = line
            continue
        # Single line too long — split on sentence delimiters.
        buf = ""
        for ch in line:
            buf += ch
            if ch in "。！？.?!\n" and len(buf) >= max_chars * 0.6:
                pieces.append(buf)
                buf = ""
        if buf:
            # Final hard char-count slice (rare).
            for i in range(0, len(buf), max_chars):
                pieces.append(buf[i : i + max_chars])
        current = ""
    if current:
        pieces.append(current)
    return pieces


def _strip_prose_preamble(text: str) -> str:
    """Remove leading prose like 'Sure, here is the translation:' (val §3)."""
    if not text:
        return text
    lines = text.splitlines()
    while lines:
        head = lines[0].strip()
        head_lower = head.lower()
        if any(head_lower.startswith(p) for p in _PROSE_PREAMBLE_PREFIXES):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()
