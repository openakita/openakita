"""
Unified LLM type definitions

Uses Anthropic format as the internal standard:
- Clearer structure (system separated, content blocks design)
- Tool call parameters are JSON objects (not strings, more secure)
"""

from dataclasses import dataclass, field
from enum import StrEnum

_OPENAI_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/models",
    "/responses",
)


def normalize_base_url(url: str, *, extra_suffixes: tuple[str, ...] = ()) -> str:
    """Strip OpenAI-compatible endpoint path suffixes mistakenly pasted by user and return clean base URL.

    Many service providers (e.g. GitCode AI, Volcano Engine) provide complete endpoint URLs
    (e.g. ``https://xxx/v1/chat/completions``). When users paste and concatenate directly,
    double paths result in 404 errors.
    """
    url = url.rstrip("/")
    for suffix in (*_OPENAI_ENDPOINT_SUFFIXES, *extra_suffixes):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


class StopReason(StrEnum):
    """Stop reason"""

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    STOP_SEQUENCE = "stop_sequence"


class ContentType(StrEnum):
    """Content type"""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class MessageRole(StrEnum):
    """Message role"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Usage:
    """Token usage statistics"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ImageContent:
    """Image content"""

    media_type: str  # "image/jpeg", "image/png", "image/gif", "image/webp"
    data: str  # base64 encoded

    @classmethod
    def from_base64(cls, data: str, media_type: str = "image/jpeg") -> "ImageContent":
        return cls(media_type=media_type, data=data)

    @classmethod
    def from_url(cls, url: str) -> "ImageContent":
        """Create from URL (download and convert to base64 needed)"""
        # Store only the URL; actual download handled in converter
        return cls(media_type="url", data=url)

    def to_data_url(self) -> str:
        """Convert to data URL format"""
        if self.media_type == "url":
            return self.data
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class VideoContent:
    """Video content"""

    media_type: str  # "video/mp4", "video/webm"
    data: str  # base64 encoded

    @classmethod
    def from_base64(cls, data: str, media_type: str = "video/mp4") -> "VideoContent":
        return cls(media_type=media_type, data=data)

    @classmethod
    def from_url(cls, url: str) -> "VideoContent":
        """Create from URL (URL stored, handled by downstream converter)"""
        return cls(media_type="url", data=url)

    def to_data_url(self) -> str:
        """Convert to data URL format"""
        if self.media_type == "url":
            return self.data
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class AudioContent:
    """Audio content"""

    media_type: str  # "audio/wav", "audio/mp3", "audio/ogg", etc.
    data: str  # base64 encoded
    format: str = "wav"  # Audio format: "wav", "mp3", "pcm16", etc.

    @classmethod
    def from_base64(
        cls, data: str, media_type: str = "audio/wav", fmt: str = "wav"
    ) -> "AudioContent":
        return cls(media_type=media_type, data=data, format=fmt)

    @classmethod
    def from_file(cls, path: str) -> "AudioContent":
        """Create from file"""
        import base64
        from pathlib import Path

        file_path = Path(path)
        suffix = file_path.suffix.lower().lstrip(".")
        mime_map = {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "ogg": "audio/ogg",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
        }
        media_type = mime_map.get(suffix, f"audio/{suffix}")
        data = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return cls(media_type=media_type, data=data, format=suffix)

    def to_data_url(self) -> str:
        """Convert to data URL format"""
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class DocumentContent:
    """Document content (PDF, etc.)"""

    media_type: str  # "application/pdf"
    data: str  # base64 encoded
    filename: str = ""  # Original filename

    @classmethod
    def from_base64(
        cls, data: str, media_type: str = "application/pdf", filename: str = ""
    ) -> "DocumentContent":
        return cls(media_type=media_type, data=data, filename=filename)

    @classmethod
    def from_file(cls, path: str) -> "DocumentContent":
        """Create from file"""
        import base64
        from pathlib import Path

        file_path = Path(path)
        suffix = file_path.suffix.lower().lstrip(".")
        mime_map = {"pdf": "application/pdf"}
        media_type = mime_map.get(suffix, f"application/{suffix}")
        data = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return cls(media_type=media_type, data=data, filename=file_path.name)


@dataclass
class ContentBlock:
    """Content block base class"""

    type: str

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        raise NotImplementedError


@dataclass
class TextBlock(ContentBlock):
    """Text content block"""

    text: str
    type: str = field(default="text", init=False)

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingBlock(ContentBlock):
    """Thinking content block (MiniMax M2.1 Interleaved Thinking)"""

    thinking: str
    type: str = field(default="thinking", init=False)

    def to_dict(self) -> dict:
        return {"type": "thinking", "thinking": self.thinking}


@dataclass
class ToolUseBlock(ContentBlock):
    """Tool use content block"""

    id: str
    name: str
    input: dict  # JSON object, not string
    provider_extra: dict | None = None  # Provider pass-through fields (e.g. Gemini thought_signature)
    type: str = field(default="tool_use", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.input, dict):
            from ..tools.input_normalizer import normalize_tool_input

            self.input = normalize_tool_input(self.name, self.input)

    def to_dict(self) -> dict:
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }


@dataclass
class ToolResultBlock(ContentBlock):
    """Tool result content block

    content can be plain text string or multimodal content list (text + images, etc.).
    List format example::

        [
            {"type": "text", "text": "Screenshot saved to ..."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]
    """

    tool_use_id: str
    content: str | list  # Tool execution result, str or multimodal content list
    is_error: bool = False
    type: str = field(default="tool_result", init=False)

    @property
    def text_content(self) -> str:
        """Extract plain text content (for compression, summarization, etc.)."""
        if isinstance(self.content, str):
            return self.content
        texts = []
        for part in self.content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)

    def to_dict(self) -> dict:
        result = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            result["is_error"] = True
        return result


@dataclass
class ImageBlock(ContentBlock):
    """Image content block"""

    image: ImageContent
    type: str = field(default="image", init=False)

    def to_dict(self) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.image.media_type,
                "data": self.image.data,
            },
        }


@dataclass
class VideoBlock(ContentBlock):
    """Video content block"""

    video: VideoContent
    type: str = field(default="video", init=False)

    def to_dict(self) -> dict:
        return {
            "type": "video",
            "source": {
                "type": "base64",
                "media_type": self.video.media_type,
                "data": self.video.data,
            },
        }


@dataclass
class AudioBlock(ContentBlock):
    """Audio content block"""

    audio: AudioContent
    type: str = field(default="audio", init=False)

    def to_dict(self) -> dict:
        return {
            "type": "audio",
            "source": {
                "type": "base64",
                "media_type": self.audio.media_type,
                "data": self.audio.data,
                "format": self.audio.format,
            },
        }


@dataclass
class DocumentBlock(ContentBlock):
    """Document content block (PDF, etc.)"""

    document: DocumentContent
    type: str = field(default="document", init=False)

    def to_dict(self) -> dict:
        result = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": self.document.media_type,
                "data": self.document.data,
            },
        }
        if self.document.filename:
            result["filename"] = self.document.filename
        return result


# Content block union type
ContentBlockType = (
    TextBlock
    | ThinkingBlock
    | ToolUseBlock
    | ToolResultBlock
    | ImageBlock
    | VideoBlock
    | AudioBlock
    | DocumentBlock
)


@dataclass
class Message:
    """Message"""

    role: str  # "user" | "assistant" | "system" | "tool"
    content: str | list[ContentBlockType]
    reasoning_content: str | None = None  # Kimi-specific: thinking content

    def to_dict(self) -> dict:
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        return {
            "role": self.role,
            "content": [
                block.to_dict() if hasattr(block, "to_dict") else block for block in self.content
            ],
        }


@dataclass
class Tool:
    """Tool definition"""

    name: str
    description: str
    input_schema: dict  # JSON Schema

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class LLMRequest:
    """Unified request format"""

    messages: list[Message]
    system: str = ""
    tools: list[Tool] | None = None
    max_tokens: int = 0  # 0=unlimited (OpenAI doesn't send this param; Anthropic uses endpoint config or default 16384)
    temperature: float = 1.0
    enable_thinking: bool = False
    thinking_depth: str | None = None  # Thinking depth: 'low'/'medium'/'high'
    stop_sequences: list[str] | None = None
    extra_params: dict | None = None  # Extra parameters (e.g. enable_thinking)

    def to_dict(self) -> dict:
        result = {
            "messages": [msg.to_dict() for msg in self.messages],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.system:
            result["system"] = self.system
        if self.tools:
            result["tools"] = [tool.to_dict() for tool in self.tools]
        if self.stop_sequences:
            result["stop_sequences"] = self.stop_sequences
        return result


@dataclass
class LLMResponse:
    """Unified response format"""

    id: str
    content: list[ContentBlockType]
    stop_reason: StopReason
    usage: Usage
    model: str
    reasoning_content: str | None = None  # Kimi-specific: thinking content
    endpoint_name: str = ""  # Actual endpoint name handling this request (populated by LLMClient)

    @property
    def text(self) -> str:
        """Get plain text content"""
        texts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
        return "".join(texts)

    @property
    def tool_calls(self) -> list[ToolUseBlock]:
        """Get all tool calls"""
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    @property
    def has_tool_calls(self) -> bool:
        """Whether there are tool calls"""
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": [
                block.to_dict() if hasattr(block, "to_dict") else block for block in self.content
            ],
            "stop_reason": self.stop_reason.value,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "model": self.model,
        }


@dataclass
class EndpointConfig:
    """Endpoint configuration"""

    name: str  # Endpoint name
    provider: str  # Provider identifier (anthropic, dashscope, openrouter, ...)
    api_type: str  # API type ("openai" | "openai_responses" | "anthropic")
    base_url: str  # API address
    api_key_env: str | None = None  # API Key environment variable name
    api_key: str | None = None  # Directly stored API Key (not recommended, but supported)
    model: str = ""  # Model name
    priority: int = 1  # Priority (smaller = higher priority)
    max_tokens: int = 0  # Max output tokens (0=unlimited, use model default)
    context_window: int = 200000  # Context window size (total input+output token limit), fallback when config missing
    timeout: int = 180  # Timeout in seconds
    capabilities: list[str] | None = None  # Capability list
    extra_params: dict | None = None  # Extra parameters
    note: str | None = None  # Note
    rpm_limit: int = 0  # Requests per minute limit (0=no rate limit)
    pricing_tiers: list[dict] | None = (
        None  # Tiered pricing [{"max_input": 128000, "input_price": 1.2, "output_price": 7.2}, ...]
    )
    price_currency: str = "CNY"  # Price currency unit
    enabled: bool = True  # Whether enabled (false=disabled, not called but config retained)
    stream_only: bool = False  # Stream-only mode (some relay/middleware require stream=true)

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["text"]

    def has_capability(self, capability: str) -> bool:
        """Check if certain capability exists

        Priority:
        1. Explicitly configured capabilities list (declared by user in JSON, highest priority)
        2. Compatibility inference (based on extra_params / model name clues, as fallback)
        """
        cap = (capability or "").lower().strip()
        caps = {c.lower() for c in (self.capabilities or [])}
        if cap in caps:
            return True

        # === Compatibility/inference ability ===
        # Legacy configs or manually-edited JSON may lack capabilities annotation,
        # but extra_params/model name already reflects ability. Only infer when explicit list doesn't include it.
        model = (self.model or "").lower()

        if cap == "thinking":
            if "thinking" in model:
                return True
            extra = self.extra_params or {}
            if extra.get("enable_thinking") is True:
                return True

        # Only infer model name fallback when capabilities still at default ["text"]
        # (don't override user intent when they explicitly configured capabilities)
        if caps == {"text"} and model:
            from .capabilities import get_provider_slug_from_base_url, infer_capabilities

            provider_slug = (
                get_provider_slug_from_base_url(self.base_url) if self.base_url else None
            )
            inferred = infer_capabilities(model, provider_slug=provider_slug)
            if inferred.get(cap, False):
                return True

        return False

    def get_api_key(self) -> str | None:
        """Get API Key (prefer directly stored key, then from environment variable)"""
        import os

        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float:
        """Calculate request cost using tiered pricing (unit: price_currency).

        pricing_tiers format: [{"max_input": N, "input_price": P, "output_price": P}, ...]
        price is per million tokens. max_input=-1 means unlimited.
        Match by ascending max_input, use first tier where input_tokens <= max_input.
        """
        tiers = self.pricing_tiers
        if not tiers:
            return 0.0
        sorted_tiers = sorted(
            tiers,
            key=lambda t: (
                (t.get("max_input") or 0) if t.get("max_input", -1) != -1 else float("inf")
            ),
        )
        matched = sorted_tiers[-1]
        for tier in sorted_tiers:
            cap = tier.get("max_input", -1)
            if cap == -1:
                continue
            if input_tokens <= cap:
                matched = tier
                break
        ip = matched.get("input_price", 0)
        op = matched.get("output_price", 0)
        crp = matched.get("cache_read_price", ip * 0.1) if cache_read_tokens else 0
        cost = (input_tokens * ip + output_tokens * op + cache_read_tokens * crp) / 1_000_000
        return round(cost, 8)

    @classmethod
    def from_dict(cls, data: dict) -> "EndpointConfig":
        return cls(
            name=data["name"],
            provider=data["provider"],
            api_type=data["api_type"],
            base_url=data["base_url"],
            api_key_env=data.get("api_key_env"),
            api_key=data.get("api_key"),
            model=data.get("model", ""),
            priority=data.get("priority", 1),
            max_tokens=data.get("max_tokens", 0),
            context_window=data.get("context_window", 200000),
            timeout=data.get("timeout", 180),
            capabilities=data.get("capabilities"),
            extra_params=data.get("extra_params"),
            note=data.get("note"),
            rpm_limit=int(data.get("rpm_limit") or 0),
            pricing_tiers=data.get("pricing_tiers"),
            price_currency=data.get("price_currency", "CNY"),
            enabled=data.get("enabled", True),
            stream_only=data.get("stream_only", False),
        )

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "provider": self.provider,
            "api_type": self.api_type,
            "base_url": self.base_url,
            "model": self.model,
            "priority": self.priority,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
            "timeout": self.timeout,
        }
        # API Key: prefer environment variable name, don't save plaintext key to config
        if self.api_key_env:
            result["api_key_env"] = self.api_key_env
        elif self.api_key:
            result["api_key"] = self.api_key
        if self.capabilities:
            result["capabilities"] = self.capabilities
        if self.extra_params:
            result["extra_params"] = self.extra_params
        if self.note:
            result["note"] = self.note
        if self.rpm_limit and self.rpm_limit > 0:
            result["rpm_limit"] = self.rpm_limit
        if self.pricing_tiers:
            result["pricing_tiers"] = self.pricing_tiers
        if self.price_currency and self.price_currency != "CNY":
            result["price_currency"] = self.price_currency
        if not self.enabled:
            result["enabled"] = False
        if self.stream_only:
            result["stream_only"] = True
        return result


# Exception classes
class LLMError(Exception):
    """LLM-related error base class"""

    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class UnsupportedMediaError(LLMError):
    """Unsupported media type error"""

    pass


class AllEndpointsFailedError(LLMError):
    """All endpoints failed"""

    def __init__(self, message: str, *, is_structural: bool = False):
        super().__init__(message)
        self.is_structural = is_structural


class ConfigurationError(LLMError):
    """Configuration error"""

    pass


class AuthenticationError(LLMError):
    """Authentication error (should not retry)"""

    pass


class RateLimitError(LLMError):
    """Rate limit error (can retry)"""

    pass
