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
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"
_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"})

MAX_TRANSCRIPT_CHARS = 20000
MAX_ANALYSIS_RETRIES = 2


def _normalize_paraformer_task_status(raw: object) -> str:
    """DashScope may return mixed casing or alternate success tokens."""
    s = str(raw or "").strip().upper()
    aliases = {
        "SUCCESS": "SUCCEEDED",
        "SUCCEED": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "COMPLETE": "SUCCEEDED",
        "DONE": "SUCCEEDED",
    }
    return aliases.get(s, s)


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
        analysis_provider: str = "host",
        analysis_brain: Any = None,
        analysis_api_key: str = "",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._poll_max_seconds = poll_max_seconds
        self._timeout = timeout
        self._qwen_model = qwen_model
        self._client: Any = None
        self._analysis_provider = self._normalize_analysis_provider(analysis_provider)
        self._analysis_brain = analysis_brain
        self._analysis_api_key = analysis_api_key

    def update_api_key(self, key: str) -> None:
        self._api_key = key

    def configure_analysis(
        self,
        *,
        provider: str = "host",
        brain: Any = None,
        api_key: str = "",
    ) -> None:
        self._analysis_provider = self._normalize_analysis_provider(provider)
        self._analysis_brain = brain
        self._analysis_api_key = api_key

    @staticmethod
    def _normalize_analysis_provider(provider: str) -> str:
        return "dashscope" if str(provider or "").lower() == "dashscope" else "host"

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

    def _analysis_auth_headers(self) -> dict[str, str]:
        key = self._analysis_api_key
        if not key:
            raise AsrError("DashScope analysis API key not configured", kind="auth")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _oss_resolve_headers(source_url: str) -> dict[str, str]:
        """DashScope temporary ``oss://`` URLs require this header on HTTP calls."""
        if (source_url or "").strip().lower().startswith("oss://"):
            return {"X-DashScope-OssResourceResolve": "enable"}
        return {}

    async def upload_local_source(self, path: Path) -> str:
        """Upload a local media file to DashScope temp OSS for Paraformer ``file_urls``."""
        from clip_dashscope_upload import upload_local_file_for_paraformer

        client = await self._ensure_client()
        return await upload_local_file_for_paraformer(
            client,
            self._api_key,
            base_url=self._base_url,
            local_path=path,
        )

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
        submit_headers = {
            **self._auth_headers(async_mode=True),
            **self._oss_resolve_headers(source_url),
        }

        try:
            resp = await client.post(
                submit_url,
                headers=submit_headers,
                json=body,
                timeout=httpx.Timeout(180.0, connect=60.0),
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
        poll_headers = {
            "Authorization": f"Bearer {self._api_key}",
            **self._oss_resolve_headers(source_url),
        }
        elapsed = 0.0
        data: dict[str, Any] = {}

        while elapsed < self._poll_max_seconds:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
            try:
                pr = await client.get(
                    poll_url,
                    headers=poll_headers,
                    timeout=httpx.Timeout(120.0, connect=30.0),
                )
            except httpx.HTTPError:
                continue
            if pr.status_code >= 400:
                continue
            data = pr.json()
            out = data.get("output") or {}
            if not isinstance(out, dict):
                out = {}
            raw_status = out.get("task_status") or data.get("task_status") or ""
            status = _normalize_paraformer_task_status(raw_status)
            if status in _TERMINAL_STATES:
                if status != "SUCCEEDED":
                    msg = (
                        out.get("message")
                        or out.get("msg")
                        or out.get("detail")
                        or ""
                    )
                    code = out.get("code") or data.get("code") or ""
                    if not msg and out:
                        try:
                            msg = json.dumps(out, ensure_ascii=False)[:400]
                        except (TypeError, ValueError):
                            msg = str(out)[:400]
                    parts = [f"Paraformer task ended with status {status!r}"]
                    if str(msg).strip():
                        parts.append(str(msg).strip()[:500])
                    if str(code).strip():
                        parts.append(f"code={code}")
                    parts.append(f"task_id={task_id}")
                    logger.warning(
                        "Paraformer terminal status=%s task_id=%s body=%s",
                        status, task_id, json.dumps(data, ensure_ascii=False)[:1200],
                    )
                    code_s = str(code).strip().upper()
                    msg_s = str(msg).strip().upper()
                    # DashScope returns task_status=FAILED with e.g. SUCCESS_WITH_NO_VALID_FRAGMENT
                    # when the file is reachable but no usable speech is detected in the audio.
                    if "NO_VALID_FRAGMENT" in code_s or "NO_VALID_FRAGMENT" in msg_s:
                        friendly = (
                            "云端未从音轨中识别到可转写的人声片段（Paraformer：无有效语音）。"
                            "请使用带清晰对白/口播的视频；纯音乐、环境声或音量过低会导致此结果。"
                        )
                        raise AsrError(
                            f"{friendly} code={code}; task_id={task_id}",
                            kind="no_speech",
                        )
                    raise AsrError("; ".join(parts), kind="unknown")
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
            tr_headers = self._oss_resolve_headers(source_url)
            tr_resp = await client.get(
                transcript_url,
                headers=tr_headers if tr_headers else None,
                timeout=httpx.Timeout(300.0, connect=60.0),
            )
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
        """Analyze with host LLM by default, or DashScope Qwen when explicitly enabled."""
        from clip_sense_inline.llm_json_parser import parse_llm_json

        if self._analysis_provider != "dashscope":
            return await self._host_analyze_with_retry(
                prompt,
                expect_type=expect_type,
                fallback=fallback,
                max_retries=max_retries,
                parse_llm_json=parse_llm_json,
            )

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
                    url, headers=self._analysis_auth_headers(), json=body
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

    async def _host_analyze_with_retry(
        self,
        prompt: str,
        *,
        expect_type: type,
        fallback: Any,
        max_retries: int,
        parse_llm_json: Any,
    ) -> Any:
        brain = self._analysis_brain
        if brain is None:
            raise AsrError(
                "Host LLM is unavailable. Grant brain.access or enable custom Bailian analysis key.",
                kind="auth",
            )

        last_feedback = ""
        system = (
            "你是 ClipSense 的视频内容分析器。"
            "只返回合法 JSON，不要输出 Markdown 围栏、解释或额外文本。"
        )
        for attempt in range(max_retries + 1):
            user = prompt
            if last_feedback:
                user += (
                    "\n\n上一次输出无法解析，错误："
                    f"{last_feedback}\n请只返回合法 JSON。"
                )
            try:
                content = await _call_host_brain(brain, system=system, prompt=user)
            except Exception as exc:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise AsrError(f"Host LLM analysis error: {exc}", kind="unknown") from exc

            errors: list[str] = []
            result = parse_llm_json(
                content, fallback=None, expect=expect_type, errors=errors
            )
            if result is not None:
                return result
            last_feedback = "; ".join(errors[:3])
            logger.warning(
                "Host LLM JSON parse failed (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, last_feedback,
            )

        logger.error("Host LLM analysis exhausted all retries, returning fallback")
        return fallback if fallback is not None else ([] if expect_type is list else {})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def _call_host_brain(brain: Any, *, system: str, prompt: str) -> str:
    if hasattr(brain, "chat"):
        response = await brain.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            temperature=0.2,
            max_tokens=4000,
        )
    elif hasattr(brain, "think_lightweight"):
        response = await brain.think_lightweight(
            prompt=prompt,
            system=system,
            temperature=0.2,
            max_tokens=4000,
        )
    elif hasattr(brain, "think"):
        response = await brain.think(
            prompt=prompt,
            system=system,
            temperature=0.2,
            max_tokens=4000,
        )
    else:
        raise RuntimeError("Host Brain has no supported LLM call method")
    return _response_to_text(response)


def _response_to_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or getattr(block, "content", "") or ""))
        return "\n".join(p for p in parts if p)
    if content is not None:
        return str(content)
    return str(response or "")


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
