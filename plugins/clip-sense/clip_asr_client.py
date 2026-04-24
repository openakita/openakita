"""DashScope Paraformer ASR + Qwen analysis client for clip-sense.

Reference: plugins-archive/_shared/asr/dashscope_paraformer.py (async task model)
Reference: CutClaw prompt.py:522-606 (structure proposal prompts)

P0-1: file_urls must be publicly reachable URLs
P0-2: Paraformer timestamps are in milliseconds (divide by 1000)
P0-3: Sentence-level granularity (archive only parses sentences, not words)
P0-7: Qwen JSON output parsed via llm_json_parser 5-level fallback
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"})

MAX_TRANSCRIPT_CHARS = 20000
MAX_ANALYSIS_RETRIES = 2


@dataclass(frozen=True)
class TranscriptSentence:
    start: float
    end: float
    text: str
    confidence: float = 1.0


@dataclass
class TranscriptResult:
    sentences: list[TranscriptSentence]
    full_text: str
    language: str = ""
    duration_sec: float = 0.0
    api_task_id: str = ""


class AsrError(Exception):
    """ASR / analysis failure."""

    def __init__(
        self, message: str, *, retryable: bool = False, kind: str = "unknown"
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


class ClipAsrClient:
    """DashScope Paraformer + Qwen client for clip-sense."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DASHSCOPE_BASE_URL,
        poll_interval: float = 3.0,
        poll_max_seconds: float = 900.0,
        timeout: float = 120.0,
        qwen_model: str = "qwen-plus",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._poll_max_seconds = poll_max_seconds
        self._timeout = timeout
        self._qwen_model = qwen_model
        self._client: Any = None

    def update_api_key(self, key: str) -> None:
        self._api_key = key

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _auth_headers(self, *, async_mode: bool = False) -> dict[str, str]:
        if not self._api_key:
            raise AsrError("DashScope API key not configured", kind="auth")
        h: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return h

    # ------------------------------------------------------------------
    # Paraformer transcription (P0-1, P0-2, P0-3)
    # ------------------------------------------------------------------

    async def transcribe(
        self, source_url: str, *, language: str = "auto"
    ) -> TranscriptResult:
        """Submit Paraformer-v2 transcription, poll, return sentences.

        Args:
            source_url: Publicly reachable URL of the audio/video file.
            language: Language hint ('auto', 'zh', 'en', etc.).
        """
        if not source_url:
            raise AsrError(
                "source_url is required (must be publicly reachable)",
                kind="config",
            )

        import httpx

        client = await self._ensure_client()

        body: dict[str, Any] = {
            "model": "paraformer-v2",
            "input": {"file_urls": [source_url]},
            "parameters": {
                "language_hints": [language] if language and language != "auto" else [],
            },
        }
        submit_url = f"{self._base_url}/api/v1/services/audio/asr/transcription"

        try:
            resp = await client.post(
                submit_url, headers=self._auth_headers(async_mode=True), json=body
            )
        except httpx.HTTPError as exc:
            raise AsrError(
                f"Paraformer submit network error: {exc}",
                retryable=True, kind="network",
            ) from exc

        if resp.status_code >= 400:
            kind = "auth" if resp.status_code in (401, 403) else "network"
            raise AsrError(
                f"Paraformer submit HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                kind=kind,
            )

        task_id = ((resp.json().get("output") or {}).get("task_id")) or ""
        if not task_id:
            raise AsrError("Paraformer did not return task_id", kind="unknown")

        poll_url = f"{self._base_url}/api/v1/tasks/{task_id}"
        poll_headers = {"Authorization": f"Bearer {self._api_key}"}
        elapsed = 0.0
        data: dict[str, Any] = {}

        while elapsed < self._poll_max_seconds:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
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
                    raise AsrError(
                        f"Paraformer task ended with status {status!r}",
                        kind="unknown",
                    )
                break
        else:
            raise AsrError(
                f"Paraformer task {task_id} timed out after {self._poll_max_seconds}s",
                retryable=True, kind="timeout",
            )

        results = (data.get("output") or {}).get("results") or []
        if not results:
            raise AsrError("Paraformer succeeded but returned no results", kind="unknown")

        transcript_url = results[0].get("transcription_url")
        if not transcript_url:
            raise AsrError("Paraformer result missing transcription_url", kind="unknown")

        try:
            tr_resp = await client.get(transcript_url)
            tr_resp.raise_for_status()
            transcript = tr_resp.json()
        except httpx.HTTPError as exc:
            raise AsrError(
                f"Paraformer transcript download error: {exc}",
                retryable=True, kind="network",
            ) from exc

        sentences = _flatten_sentences(transcript)
        full_text = " ".join(s.text for s in sentences)
        duration = sentences[-1].end if sentences else 0.0

        return TranscriptResult(
            sentences=sentences,
            full_text=full_text,
            language=language,
            duration_sec=duration,
            api_task_id=task_id,
        )

    # ------------------------------------------------------------------
    # Qwen analysis methods
    # ------------------------------------------------------------------

    async def analyze_highlights(
        self,
        transcript_text: str,
        sentences: list[dict[str, Any]],
        *,
        flavor: str = "",
        target_count: int = 5,
        target_duration: int = 30,
        total_duration_sec: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Use Qwen to select highlight segments from transcript."""
        truncated = transcript_text[:MAX_TRANSCRIPT_CHARS]

        prompt = (
            "你是一个专业的视频剪辑师，请从以下转写文本中选出最精彩的片段。\n\n"
            f"要求：选出 {target_count} 个片段，每个片段约 {target_duration} 秒。\n"
        )
        if flavor:
            prompt += f"选段偏好：{flavor}\n"
        if total_duration_sec > 0:
            prompt += f"视频总时长：{total_duration_sec:.0f}秒，请确保选段在视频前、中、后部均有分布。\n"
        prompt += (
            "\n转写文本（每句带时间戳）：\n"
            + _format_sentences_for_prompt(sentences)
            + "\n\n请以 JSON 数组格式返回，每项包含：\n"
            '{"start_sec": 起始秒, "end_sec": 结束秒, "reason": "选段原因", "score": 评分1-10}\n'
            "只返回 JSON 数组，不要其他内容。"
        )

        return await self._qwen_analyze_with_retry(prompt, expect_type=list, fallback=[])

    async def analyze_topics(
        self,
        transcript_text: str,
        sentences: list[dict[str, Any]],
        *,
        target_segment_duration: int = 180,
    ) -> list[dict[str, Any]]:
        """Use Qwen to split transcript into topic segments."""
        prompt = (
            "你是一个专业的视频剪辑师，请将以下视频转写文本按主题/段落分段。\n\n"
            f"要求：每段目标时长约 {target_segment_duration} 秒。\n\n"
            "转写文本（每句带时间戳）：\n"
            + _format_sentences_for_prompt(sentences)
            + "\n\n请以 JSON 数组格式返回，每项包含：\n"
            '{"title": "段落标题", "start_sec": 起始秒, "end_sec": 结束秒, "summary": "内容摘要"}\n'
            "只返回 JSON 数组，不要其他内容。"
        )

        return await self._qwen_analyze_with_retry(prompt, expect_type=list, fallback=[])

    async def analyze_filler(
        self,
        transcript_text: str,
        sentences: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Use Qwen to identify filler words, stutters, and repetitions."""
        prompt = (
            "你是一个专业的视频剪辑师，请从以下口播转写文本中识别需要删除的部分。\n\n"
            "需要识别的类型：\n"
            "- filler: 口头禅/语气词（嗯、啊、那个、就是说）\n"
            "- repeat: 重复表达（连续说了两遍的内容）\n"
            "- stutter: 口误/卡顿\n\n"
            "转写文本（每句带时间戳）：\n"
            + _format_sentences_for_prompt(sentences)
            + "\n\n请以 JSON 数组格式返回，每项包含：\n"
            '{"start_sec": 起始秒, "end_sec": 结束秒, "type": "filler|repeat|stutter", "content": "具体内容"}\n'
            "只返回 JSON 数组，不要其他内容。"
        )

        return await self._qwen_analyze_with_retry(prompt, expect_type=list, fallback=[])

    async def _qwen_analyze_with_retry(
        self,
        prompt: str,
        *,
        expect_type: type = list,
        fallback: Any = None,
        max_retries: int = MAX_ANALYSIS_RETRIES,
    ) -> Any:
        """Call Qwen with retry + feedback on parse failure (CutClaw pattern)."""
        from clip_sense_inline.llm_json_parser import parse_llm_json

        import httpx

        client = await self._ensure_client()
        url = f"{self._base_url}/compatible-mode/v1/chat/completions"
        last_feedback = ""

        for attempt in range(max_retries + 1):
            messages = [{"role": "user", "content": prompt}]
            if last_feedback:
                messages.append({
                    "role": "user",
                    "content": f"**IMPORTANT - PREVIOUS ATTEMPT FAILED:** {last_feedback}\n请只返回合法的 JSON。",
                })

            body = {
                "model": self._qwen_model,
                "messages": messages,
                "temperature": 0.3,
            }

            try:
                resp = await client.post(
                    url, headers=self._auth_headers(), json=body
                )
            except httpx.HTTPError as exc:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise AsrError(
                    f"Qwen network error: {exc}", retryable=True, kind="network"
                ) from exc

            if resp.status_code >= 400:
                if attempt < max_retries and resp.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt)
                    continue
                kind = "auth" if resp.status_code in (401, 403) else "network"
                raise AsrError(
                    f"Qwen HTTP {resp.status_code}: {resp.text[:200]}",
                    kind=kind,
                )

            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            errors: list[str] = []
            result = parse_llm_json(
                content, fallback=None, expect=expect_type, errors=errors
            )
            if result is not None:
                return result

            last_feedback = "; ".join(errors[:3])
            logger.warning(
                "Qwen JSON parse failed (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, last_feedback,
            )

        logger.error("Qwen analysis exhausted all retries, returning fallback")
        return fallback if fallback is not None else ([] if expect_type is list else {})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _flatten_sentences(payload: dict[str, Any]) -> list[TranscriptSentence]:
    """Convert Paraformer JSON to TranscriptSentence list.

    P0-2: begin_time/end_time are in milliseconds.
    P0-3: Only parses sentences (sentence-level), not words.
    """
    transcripts = payload.get("transcripts") or []
    if not transcripts:
        return []
    sentences_raw = transcripts[0].get("sentences") or []
    out: list[TranscriptSentence] = []
    for sent in sentences_raw:
        try:
            out.append(TranscriptSentence(
                start=float(sent.get("begin_time", 0)) / 1000.0,
                end=float(sent.get("end_time", 0)) / 1000.0,
                text=str(sent.get("text", "")).strip(),
                confidence=float(sent.get("confidence", 1.0) or 1.0),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _format_sentences_for_prompt(
    sentences: list[dict[str, Any]], max_chars: int = MAX_TRANSCRIPT_CHARS
) -> str:
    """Format sentences with timestamps for LLM prompt, with char budget."""
    lines: list[str] = []
    total = 0
    for s in sentences:
        start = s.get("start", 0)
        end = s.get("end", 0)
        text = s.get("text", "")
        line = f"[{start:.1f}s-{end:.1f}s] {text}"
        total += len(line) + 1
        if total > max_chars:
            lines.append("... (文本已截断)")
            break
        lines.append(line)
    return "\n".join(lines)
