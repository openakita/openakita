"""
在线语音识别 (STT) 客户端

支持两种 API 协议:
1. OpenAI /audio/transcriptions (Whisper API):
   - OpenAI Whisper API (gpt-4o-transcribe, whisper-1)
   - Groq Whisper 等兼容服务
2. DashScope /chat/completions (Qwen-ASR):
   - qwen3-asr-flash（文件识别，≤5分钟）
   - 通过 chat/completions 端点，音频以 base64 编码传入

特性:
- 多端点 failover（按 priority 排序）
- 自动检测 provider 并选择合适的 API 协议
- 重试和超时
- 支持格式: mp3, mp4, wav, webm, m4a, ogg, flac
"""

import asyncio
import base64
import logging
from pathlib import Path

from .types import EndpointConfig

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}

_DASHSCOPE_ASR_PROVIDERS = {"dashscope", "dashscope-intl"}

_AUDIO_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


def _is_dashscope_asr(endpoint: EndpointConfig) -> bool:
    """判断端点是否需要走 DashScope chat/completions ASR 协议"""
    provider = (endpoint.provider or "").lower()
    model = (endpoint.model or "").lower()
    if provider in _DASHSCOPE_ASR_PROVIDERS and "asr" in model:
        return True
    return False


class STTClient:
    """在线语音识别客户端"""

    def __init__(self, endpoints: list[EndpointConfig] | None = None):
        self._endpoints = sorted(endpoints or [], key=lambda x: x.priority)
        if self._endpoints:
            names = [ep.name for ep in self._endpoints]
            logger.info(f"[STT] Initialized with {len(self._endpoints)} endpoints: {names}")

    @property
    def is_available(self) -> bool:
        """检查是否有可用的 STT 端点"""
        return bool(self._endpoints)

    @property
    def endpoints(self) -> list[EndpointConfig]:
        return self._endpoints

    def reload(self, endpoints: list[EndpointConfig] | None = None) -> None:
        """重载端点配置"""
        self._endpoints = sorted(endpoints or [], key=lambda x: x.priority)
        if self._endpoints:
            names = [ep.name for ep in self._endpoints]
            logger.info(f"[STT] Reloaded with {len(self._endpoints)} endpoints: {names}")

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        timeout: int = 60,
    ) -> str | None:
        """语音转文字

        Args:
            audio_path: 音频文件路径
            language: 语言代码（如 "zh", "en"），可选
            timeout: 请求超时时间（秒）

        Returns:
            转写文本，失败返回 None
        """
        if not self._endpoints:
            logger.warning("[STT] No STT endpoints configured")
            return None

        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"[STT] Audio file not found: {audio_path}")
            return None

        last_error = None
        for endpoint in self._endpoints:
            try:
                result = await self._call_endpoint(endpoint, audio_file, language, timeout)
                if result:
                    logger.info(
                        f"[STT] Transcription successful via {endpoint.name}: "
                        f"{result[:50]}{'...' if len(result) > 50 else ''}"
                    )
                    return result
            except Exception as e:
                last_error = e
                logger.warning(f"[STT] Endpoint {endpoint.name} failed: {e}")
                continue

        logger.error(f"[STT] All {len(self._endpoints)} endpoints failed. Last error: {last_error}")
        return None

    async def _call_endpoint(
        self,
        endpoint: EndpointConfig,
        audio_file: Path,
        language: str | None,
        timeout: int,
    ) -> str | None:
        """调用单个 STT 端点，自动选择协议"""
        if _is_dashscope_asr(endpoint):
            return await self._call_dashscope_asr(endpoint, audio_file, language, timeout)
        return await self._call_openai_transcriptions(endpoint, audio_file, language, timeout)

    async def _call_openai_transcriptions(
        self,
        endpoint: EndpointConfig,
        audio_file: Path,
        language: str | None,
        timeout: int,
    ) -> str | None:
        """OpenAI /audio/transcriptions 协议"""
        import httpx

        api_key = endpoint.get_api_key()
        if not api_key:
            logger.warning(f"[STT] No API key for endpoint {endpoint.name}")
            return None

        base_url = endpoint.base_url.rstrip("/")
        url = f"{base_url}/audio/transcriptions"
        model = endpoint.model or "whisper-1"

        headers = {"Authorization": f"Bearer {api_key}"}

        files = {"file": (audio_file.name, audio_file.read_bytes(), "application/octet-stream")}
        data: dict = {"model": model}
        if language:
            data["language"] = language

        loop = asyncio.get_event_loop()

        def _do_request():
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, files=files, data=data)
                resp.raise_for_status()
                result = resp.json()
                return result.get("text", "")

        return await loop.run_in_executor(None, _do_request)

    async def _call_dashscope_asr(
        self,
        endpoint: EndpointConfig,
        audio_file: Path,
        language: str | None,
        timeout: int,
    ) -> str | None:
        """DashScope Qwen-ASR 协议 (/chat/completions + base64 音频)"""
        import httpx

        api_key = endpoint.get_api_key()
        if not api_key:
            logger.warning(f"[STT] No API key for endpoint {endpoint.name}")
            return None

        base_url = endpoint.base_url.rstrip("/")
        url = f"{base_url}/chat/completions"

        model = endpoint.model or "qwen3-asr-flash"
        if model.endswith("-realtime"):
            model = model.replace("-realtime", "")
            logger.info(f"[STT] DashScope ASR: stripped '-realtime' suffix, using model={model}")

        suffix = audio_file.suffix.lower()
        mime_type = _AUDIO_MIME_MAP.get(suffix, "application/octet-stream")

        audio_bytes = audio_file.read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode()
        data_uri = f"data:{mime_type};base64,{audio_b64}"

        payload: dict = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"enable_itn": True},
        }
        if language:
            payload["asr_options"]["language"] = language

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        loop = asyncio.get_event_loop()

        def _do_request():
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                result = resp.json()
                return result["choices"][0]["message"]["content"]

        return await loop.run_in_executor(None, _do_request)
