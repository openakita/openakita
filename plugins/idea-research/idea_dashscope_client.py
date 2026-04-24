"""DashScope vendor client + ASR/VLM/text business helpers (§7.4).

Thin façade over ``idea_research_inline.vendor_client.VendorClient`` —
talks to the public DashScope HTTP endpoints and maps every failure
mode into the right ``VendorError`` subclass (which already carries a
canonical ``error_kind`` for §15 hint rendering).

Three business surfaces:

* ``transcribe_audio`` — Faster-Whisper (local subprocess) or DashScope
  Paraformer (cloud async); auto-pick via :func:`select_asr_backend`.
* ``describe_image`` — Qwen-VL-Max single-image describe with the
  exact prompt from §7.2 step 5c.
* ``chat_completion`` — text LLM (Qwen-Max / Qwen-Plus) used by
  ``structure_analyze`` / ``comment_summary`` / ``persona_takeaways`` /
  ``script_remix``. Output is run through the three-tier JSON parser
  before the caller sees it (when ``response_json=True``).

Heavy external deps (``faster_whisper``, ``ffmpeg``) are imported
lazily so the plugin loads on hosts without them; the resulting
``error_kind='dependency'`` carries the install hint.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from idea_research_inline.llm_json_parser import LlmJsonParseError, parse_llm_json
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorClient,
    VendorError,
    VendorFormatError,
    VendorNetworkError,
    VendorQuotaError,
    VendorRateLimitError,
    VendorTimeoutError,
)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"

# DashScope publishes per-model paths; all three under /services/aigc.
TEXT_PATH = "/services/aigc/text-generation/generation"
VLM_PATH = "/services/aigc/multimodal-generation/generation"
PARAFORMER_TASKS_PATH = "/services/audio/asr/transcription"


# --------------------------------------------------------------------------- #
# Backend selection helpers (§7.4)                                             #
# --------------------------------------------------------------------------- #


def faster_whisper_available() -> bool:
    try:
        import importlib

        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_duration(audio_path: Path) -> float:
    """Run ``ffprobe`` to get audio length in seconds.

    Raises ``VendorError(error_kind='dependency')`` if ``ffprobe`` is
    missing on PATH; ``error_kind='format'`` if the file can't be
    decoded.
    """

    if not shutil.which("ffprobe"):
        err = VendorError("ffprobe not found on PATH; please install FFmpeg")
        err.error_kind = "dependency"
        raise err
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        err = VendorError(f"ffprobe failed: {proc.stderr[-160:]!r}")
        err.error_kind = "format"
        raise err
    try:
        return float((proc.stdout or "0").strip())
    except (TypeError, ValueError) as exc:
        err = VendorError(f"ffprobe returned non-numeric: {proc.stdout!r}")
        err.error_kind = "format"
        raise err from exc


def select_asr_backend(
    audio_path: Path | None,
    user_pref: Literal["auto", "local", "cloud"] = "auto",
    *,
    duration_s: float | None = None,
) -> Literal["local", "cloud"]:
    """Pick local Faster-Whisper or cloud Paraformer per §7.4."""

    if user_pref in ("local", "cloud"):
        return user_pref  # type: ignore[return-value]
    if not faster_whisper_available():
        return "cloud"
    if duration_s is None and audio_path is not None and audio_path.exists():
        try:
            duration_s = ffprobe_duration(audio_path)
        except VendorError:
            duration_s = 0.0
    if (duration_s or 0.0) > 600.0:
        return "cloud"
    return "local"


# --------------------------------------------------------------------------- #
# Result dataclasses                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    backend: Literal["local", "cloud"]
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    cost_cny: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FrameDescription:
    desc: str
    has_text: bool = False
    text_extracted: str = ""
    brand_visible: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    content: str
    model: str
    parsed_json: Any | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# DashScope client                                                             #
# --------------------------------------------------------------------------- #


class DashScopeClient(VendorClient):
    """Thin DashScope wrapper layered on the SDK's ``VendorClient``.

    The HTTP transport is provided by an externally-injected
    ``httpx.AsyncClient`` so tests can swap in a ``MockTransport`` —
    the parent ``VendorClient`` only stores config metadata.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
        base_url: str = DASHSCOPE_BASE,
        default_timeout_s: float = 60.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_timeout_s=default_timeout_s,
        )
        self._http = client

    # ---- low-level helpers ------------------------------------------------

    def _headers(self, *, async_task: bool = False) -> dict[str, str]:
        if not self.api_key:
            raise VendorAuthError("DashScope API key not configured")
        h: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if async_task:
            h["X-DashScope-Async"] = "enable"
        return h

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        async_task: bool = False,
    ) -> dict[str, Any]:
        url = self.base_url + path
        try:
            r = await self._http.post(
                url,
                headers=self._headers(async_task=async_task),
                json=payload,
                timeout=timeout or self.default_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(f"timeout posting {url}") from exc
        except httpx.HTTPError as exc:
            raise VendorNetworkError(f"http error posting {url}: {exc}") from exc
        return self._parse_response(r)

    async def _get_json(self, path_or_url: str, *, timeout: float | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else (self.base_url + path_or_url)
        try:
            r = await self._http.get(
                url,
                headers=self._headers(),
                timeout=timeout or self.default_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(f"timeout fetching {url}") from exc
        except httpx.HTTPError as exc:
            raise VendorNetworkError(f"http error fetching {url}: {exc}") from exc
        return self._parse_response(r)

    def _parse_response(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code == 401:
            raise VendorAuthError(
                f"dashscope auth failed (401): {r.text[:160]!r}",
                status_code=r.status_code,
            )
        if r.status_code == 403:
            raise VendorQuotaError(
                f"dashscope forbidden (403): {r.text[:160]!r}",
                status_code=r.status_code,
            )
        if r.status_code == 429:
            raise VendorRateLimitError(
                f"dashscope rate limited (429): {r.text[:160]!r}",
                status_code=r.status_code,
            )
        if r.status_code >= 500:
            raise VendorNetworkError(
                f"dashscope upstream {r.status_code}: {r.text[:160]!r}",
                status_code=r.status_code,
            )
        if r.status_code != 200:
            raise VendorNetworkError(
                f"dashscope unexpected {r.status_code}: {r.text[:160]!r}",
                status_code=r.status_code,
            )
        try:
            payload = r.json()
        except json.JSONDecodeError as exc:
            raise VendorFormatError(f"dashscope non-json response: {r.text[:160]!r}") from exc
        if isinstance(payload, dict) and payload.get("code"):
            code = str(payload.get("code") or "")
            msg = payload.get("message") or ""
            if "Throttling" in code or "RateLimit" in code:
                raise VendorRateLimitError(f"{code}: {msg}")
            if "Auth" in code or "InvalidApiKey" in code:
                raise VendorAuthError(f"{code}: {msg}")
            if "Quota" in code or "Balance" in code:
                raise VendorQuotaError(f"{code}: {msg}")
            if code:
                raise VendorError(f"dashscope error {code}: {msg}")
        return payload

    # ---- chat / text ------------------------------------------------------

    async def chat_completion(
        self,
        *,
        system: str,
        user: str,
        model: str = "qwen-max",
        response_json: bool = False,
        expected_keys: list[str] | None = None,
        temperature: float = 0.5,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "input": {
                "messages": [
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user},
                ]
            },
            "parameters": {
                "result_format": "message",
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["parameters"]["max_tokens"] = int(max_tokens)
        if response_json:
            payload["parameters"]["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(max(1, retries + 1)):
            try:
                resp = await self._post_json(TEXT_PATH, payload)
            except (VendorTimeoutError, VendorRateLimitError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            content = self._extract_message_content(resp)
            usage = resp.get("usage") or {}
            parsed: Any = None
            if response_json:
                try:
                    parsed = parse_llm_json(content, expected_keys=expected_keys)
                except LlmJsonParseError as exc:
                    if attempt < retries:
                        # tighten prompt then retry once
                        payload["input"]["messages"][0]["content"] = (
                            system or ""
                        ) + "\n\n你的上一个输出无法被 json.loads 解析，请只输出严格 JSON。"
                        last_exc = exc
                        continue
                    raise VendorFormatError(str(exc)) from exc
            return ChatResult(
                content=content,
                model=model,
                parsed_json=parsed,
                usage=usage,
                raw=resp,
            )
        if last_exc is not None:
            raise last_exc
        raise VendorError("chat_completion exhausted retries with no error captured")

    def _extract_message_content(self, payload: dict[str, Any]) -> str:
        out = payload.get("output") or {}
        choices = out.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            return str(msg.get("content") or "")
        # legacy shape
        return str(out.get("text") or "")

    # ---- VLM / image describe --------------------------------------------

    DEFAULT_FRAME_PROMPT = (
        "描述画面中的人物姿态、文字、品牌、环境，30 字以内，"
        '输出 JSON {"desc":"","has_text":false,"text_extracted":"",'
        '"brand_visible":""}'
    )

    async def describe_image(
        self,
        image_path: Path | str,
        *,
        prompt: str | None = None,
        model: str = "qwen-vl-max",
        timeout: float = 30.0,
    ) -> FrameDescription:
        path = Path(image_path)
        if not path.exists():
            err = VendorError(f"frame missing: {path}")
            err.error_kind = "format"
            raise err
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": f"data:image/jpeg;base64,{b64}"},
                            {"text": prompt or self.DEFAULT_FRAME_PROMPT},
                        ],
                    }
                ]
            },
            "parameters": {"result_format": "message"},
        }
        resp = await self._post_json(VLM_PATH, payload, timeout=timeout)
        text = self._extract_message_content(resp)
        try:
            parsed = parse_llm_json(text)
        except LlmJsonParseError:
            return FrameDescription(desc=text, raw=resp)
        return FrameDescription(
            desc=str(parsed.get("desc") or ""),
            has_text=bool(parsed.get("has_text")),
            text_extracted=str(parsed.get("text_extracted") or ""),
            brand_visible=str(parsed.get("brand_visible") or ""),
            raw=resp,
        )

    # ---- ASR --------------------------------------------------------------

    async def transcribe_audio(
        self,
        audio_path: Path,
        *,
        backend: Literal["auto", "local", "cloud"] = "auto",
        language: str = "zh",
        local_model_size: str = "base",
        poll_interval_s: float = 4.0,
        poll_timeout_s: float = 600.0,
    ) -> TranscriptResult:
        chosen = select_asr_backend(audio_path, backend)
        if chosen == "local":
            return await asyncio.to_thread(
                self._transcribe_local_sync,
                audio_path,
                language,
                local_model_size,
            )
        return await self._transcribe_cloud(
            audio_path,
            language=language,
            poll_interval_s=poll_interval_s,
            poll_timeout_s=poll_timeout_s,
        )

    def _transcribe_local_sync(
        self,
        audio_path: Path,
        language: str,
        model_size: str,
    ) -> TranscriptResult:  # pragma: no cover — needs faster-whisper
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            err = VendorError("faster-whisper 未安装；执行 `pip install faster-whisper`")
            err.error_kind = "dependency"
            raise err from exc
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(audio_path), language=language)
        seg_list: list[TranscriptSegment] = []
        full_text: list[str] = []
        for seg in segments:
            seg_list.append(TranscriptSegment(start=seg.start, end=seg.end, text=seg.text))
            full_text.append(seg.text)
        return TranscriptResult(
            backend="local",
            text=" ".join(full_text).strip(),
            segments=seg_list,
            language=getattr(info, "language", language),
            cost_cny=0.0,
        )

    async def _transcribe_cloud(
        self,
        audio_path: Path,
        *,
        language: str,
        poll_interval_s: float,
        poll_timeout_s: float,
    ) -> TranscriptResult:
        # DashScope Paraformer accepts a public URL for the audio. In
        # practice the host plugin is responsible for uploading the
        # asset and passing back the URL — the protocol is the same.
        # We expose the lower-level path so tests can drive it via a
        # mocked transport.
        audio_url = (
            audio_path
            if isinstance(audio_path, str) and audio_path.startswith("http")
            else f"file://{audio_path}"
        )
        submit = await self._post_json(
            PARAFORMER_TASKS_PATH,
            {
                "model": "paraformer-v2",
                "input": {"file_urls": [str(audio_url)]},
                "parameters": {"language_hints": [language]},
            },
            async_task=True,
        )
        task_id = ((submit.get("output") or {}).get("task_id")) or submit.get("task_id")
        if not task_id:
            raise VendorFormatError(f"paraformer submit missing task_id: {submit}")
        deadline = asyncio.get_event_loop().time() + poll_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            poll = await self._get_json(
                f"{self.base_url}/tasks/{task_id}",
            )
            status = ((poll.get("output") or {}).get("task_status")) or ""
            if status == "SUCCEEDED":
                results = (poll.get("output") or {}).get("results") or []
                segs: list[TranscriptSegment] = []
                texts: list[str] = []
                for r in results:
                    for s in r.get("sentences", []) or []:
                        seg = TranscriptSegment(
                            start=float(s.get("begin_time", 0)) / 1000.0,
                            end=float(s.get("end_time", 0)) / 1000.0,
                            text=str(s.get("text") or ""),
                        )
                        segs.append(seg)
                        texts.append(seg.text)
                return TranscriptResult(
                    backend="cloud",
                    text=" ".join(texts).strip(),
                    segments=segs,
                    language=language,
                    cost_cny=float((poll.get("usage") or {}).get("cost_cny", 0.0)),
                    raw=poll,
                )
            if status == "FAILED":
                msg = (poll.get("output") or {}).get("message") or "unknown failure"
                raise VendorError(f"paraformer task failed: {msg}")
            await asyncio.sleep(poll_interval_s)
        raise VendorTimeoutError(f"paraformer task {task_id} did not finish in {poll_timeout_s}s")


__all__ = [
    "ChatResult",
    "DashScopeClient",
    "FrameDescription",
    "TranscriptResult",
    "TranscriptSegment",
    "faster_whisper_available",
    "ffmpeg_available",
    "ffprobe_duration",
    "select_asr_backend",
]
