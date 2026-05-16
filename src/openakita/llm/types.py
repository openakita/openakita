"""
LLM 统一类型定义

采用 Anthropic 格式作为内部标准：
- 结构更清晰（system 独立、content blocks 设计）
- 工具调用参数是 JSON 对象（非字符串，更安全）
"""

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse

_OPENAI_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/models",
    "/responses",
)


def normalize_base_url(url: str, *, extra_suffixes: tuple[str, ...] = ()) -> str:
    """剥离用户误粘贴的 OpenAI 兼容端点路径后缀，返回干净的 base URL。

    很多服务商（GitCode AI、火山引擎等）给出的 API 地址是完整端点 URL
    （如 ``https://xxx/v1/chat/completions``），用户直接粘贴后拼接会产生
    双重路径导致 404。
    """
    url = url.rstrip("/")
    for suffix in (*_OPENAI_ENDPOINT_SUFFIXES, *extra_suffixes):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


class StopReason(StrEnum):
    """停止原因"""

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    STOP_SEQUENCE = "stop_sequence"


class ContentType(StrEnum):
    """内容类型"""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class MessageRole(StrEnum):
    """消息角色"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Usage:
    """Token 使用统计"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ImageContent:
    """图片内容"""

    media_type: str  # "image/jpeg", "image/png", "image/gif", "image/webp"
    data: str  # base64 编码

    @classmethod
    def from_base64(cls, data: str, media_type: str = "image/jpeg") -> "ImageContent":
        return cls(media_type=media_type, data=data)

    @classmethod
    def from_url(cls, url: str) -> "ImageContent":
        """从 URL 创建（需要下载并转换为 base64）"""
        # 这里只存储 URL，实际下载在转换器中处理
        return cls(media_type="url", data=url)

    def to_data_url(self) -> str:
        """转换为 data URL 格式"""
        if self.media_type == "url":
            return self.data
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class VideoContent:
    """视频内容"""

    media_type: str  # "video/mp4", "video/webm"
    data: str  # base64 编码

    @classmethod
    def from_base64(cls, data: str, media_type: str = "video/mp4") -> "VideoContent":
        return cls(media_type=media_type, data=data)

    @classmethod
    def from_url(cls, url: str) -> "VideoContent":
        """从 URL 创建（存储 URL，由下游转换器处理）"""
        return cls(media_type="url", data=url)

    def to_data_url(self) -> str:
        """转换为 data URL 格式"""
        if self.media_type == "url":
            return self.data
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class AudioContent:
    """音频内容"""

    media_type: str  # "audio/wav", "audio/mp3", "audio/ogg", etc.
    data: str  # base64 编码
    format: str = "wav"  # 音频格式: "wav", "mp3", "pcm16", etc.

    @classmethod
    def from_base64(
        cls, data: str, media_type: str = "audio/wav", fmt: str = "wav"
    ) -> "AudioContent":
        return cls(media_type=media_type, data=data, format=fmt)

    @classmethod
    def from_file(cls, path: str) -> "AudioContent":
        """从文件创建"""
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
        """转换为 data URL 格式"""
        return f"data:{self.media_type};base64,{self.data}"


@dataclass
class DocumentContent:
    """文档内容（PDF 等）"""

    media_type: str  # "application/pdf"
    data: str  # base64 编码
    filename: str = ""  # 原始文件名

    @classmethod
    def from_base64(
        cls, data: str, media_type: str = "application/pdf", filename: str = ""
    ) -> "DocumentContent":
        return cls(media_type=media_type, data=data, filename=filename)

    @classmethod
    def from_file(cls, path: str) -> "DocumentContent":
        """从文件创建"""
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
    """内容块基类"""

    type: str

    def to_dict(self) -> dict:
        """转换为字典"""
        raise NotImplementedError


@dataclass
class TextBlock(ContentBlock):
    """文本内容块"""

    text: str
    type: str = field(default="text", init=False)

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingBlock(ContentBlock):
    """思考内容块 (MiniMax M2.1 Interleaved Thinking)"""

    thinking: str
    type: str = field(default="thinking", init=False)

    def to_dict(self) -> dict:
        return {"type": "thinking", "thinking": self.thinking}


@dataclass
class ToolUseBlock(ContentBlock):
    """工具调用内容块"""

    id: str
    name: str
    input: dict  # JSON 对象，非字符串
    provider_extra: dict | None = None  # provider 透传字段（如 Gemini thought_signature）
    type: str = field(default="tool_use", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.input, dict):
            from ..tools.input_normalizer import normalize_tool_input

            self.input = normalize_tool_input(self.name, self.input)

    def to_dict(self) -> dict:
        d: dict = {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }
        if self.provider_extra:
            d["provider_extra"] = self.provider_extra
        return d


@dataclass
class ToolResultBlock(ContentBlock):
    """工具结果内容块

    content 可以是纯文本字符串，也可以是多模态内容列表（文本 + 图片等）。
    列表格式示例::

        [
            {"type": "text", "text": "截图已保存到 ..."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]
    """

    tool_use_id: str
    content: str | list  # 工具执行结果，str 或多模态 content list
    is_error: bool = False
    type: str = field(default="tool_result", init=False)

    @property
    def text_content(self) -> str:
        """提取纯文本内容（用于压缩、摘要等场景）。"""
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
    """图片内容块"""

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
    """视频内容块"""

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
    """音频内容块"""

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
    """文档内容块（PDF 等）"""

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


# 内容块联合类型
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
    """消息"""

    role: str  # "user" | "assistant" | "system" | "tool"
    content: str | list[ContentBlockType]
    reasoning_content: str | None = None  # Kimi 专用：思考内容

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
    """工具定义"""

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
    """统一请求格式"""

    messages: list[Message]
    system: str = ""
    tools: list[Tool] | None = None
    max_tokens: int = 0  # 0=不限制（OpenAI 不发送该参数；Anthropic 使用端点配置值或兜底 16384）
    temperature: float = 1.0
    enable_thinking: bool = False
    thinking_depth: str | None = None  # 思考深度: 'low'/'medium'/'high'/'max'
    stop_sequences: list[str] | None = None
    extra_params: dict | None = None  # 额外参数（如 enable_thinking 等）

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
    """统一响应格式"""

    id: str
    content: list[ContentBlockType]
    stop_reason: StopReason
    usage: Usage
    model: str
    reasoning_content: str | None = None  # Kimi 专用：思考内容
    endpoint_name: str = ""  # 实际处理此请求的端点名称（由 LLMClient 填充）
    # PR-C2: 当从非标准字段恢复 content 时记录来源（如 "message.reasoning_content"）。
    # endpoint_manager 见此字段视为"已自愈"，不再触发 30s cooldown。
    recovered_from: str = ""

    @property
    def text(self) -> str:
        """获取纯文本内容"""
        texts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
        return "".join(texts)

    @property
    def tool_calls(self) -> list[ToolUseBlock]:
        """获取所有工具调用"""
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    @property
    def has_tool_calls(self) -> bool:
        """是否有工具调用"""
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


DEFAULT_CONTEXT_WINDOW = 200000
LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW = 4096
_LOCAL_PROVIDER_SLUGS = {"local", "localai", "lmstudio", "ollama"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def is_local_endpoint_config(provider: str = "", base_url: str = "") -> bool:
    """Return True for local OpenAI-compatible runtimes such as LM Studio/Ollama/LocalAI."""
    provider_slug = (provider or "").strip().lower()
    if provider_slug in _LOCAL_PROVIDER_SLUGS:
        return True

    try:
        parsed = urlparse(base_url or "")
        host = (parsed.hostname or "").lower()
    except Exception:
        host = ""
    return host in _LOCAL_HOSTS


def normalize_context_window(
    value: int | str | None,
    *,
    provider: str = "",
    base_url: str = "",
) -> int:
    """Normalize endpoint context windows.

    Hosted providers keep the broad historical default. Local runtimes often
    expose 4K/8K GGUF models; treating a missing value as 200K causes the
    prompt/tool budgeter to overload them before it can recover.
    """
    is_local = is_local_endpoint_config(provider, base_url)
    try:
        ctx = int(value) if value is not None and value != "" else 0
    except (TypeError, ValueError):
        ctx = 0

    if is_local and (ctx <= 0 or ctx == DEFAULT_CONTEXT_WINDOW):
        return LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW
    if ctx <= 0:
        return DEFAULT_CONTEXT_WINDOW
    return ctx


@dataclass
class EndpointConfig:
    """端点配置"""

    name: str  # 端点名称
    provider: str  # 服务商标识 (anthropic, dashscope, openrouter, ...)
    api_type: str  # API 类型 ("openai" | "openai_responses" | "anthropic")
    base_url: str  # API 地址
    api_key_env: str | None = None  # API Key 环境变量名
    api_key: str | None = None  # 直接存储的 API Key (不推荐，但支持)
    model: str = ""  # 模型名称
    priority: int = 1  # 优先级 (越小越优先)
    max_tokens: int = 0  # 最大输出 tokens (0=不限制，使用模型默认上限)
    context_window: int = DEFAULT_CONTEXT_WINDOW  # 上下文窗口大小 (输入+输出总 token 上限)
    timeout: int = 180  # 超时时间 (秒)
    capabilities: list[str] | None = None  # 能力列表
    extra_params: dict | None = None  # 额外参数
    note: str | None = None  # 备注
    rpm_limit: int = 0  # 每分钟请求数限制 (0=不限流)
    pricing_tiers: list[dict] | None = (
        None  # 阶梯定价 [{"max_input": 128000, "input_price": 1.2, "output_price": 7.2}, ...]
    )
    price_currency: str = "CNY"  # 价格货币单位
    enabled: bool = True  # 是否启用 (false=停用，不参与调用但保留配置)
    stream_only: bool = False  # 仅流式模式 (某些中转站/relay 要求 stream=true)
    # ── Relay/Aggregator capability discovery ──────────────────────────
    # When the user points an endpoint at a relay station (oneapi /
    # new-api / yunwu / private gateway), the upstream model catalog
    # often differs from the official provider's. ``supported_models``
    # caches the result of GET /v1/models (or the equivalent for
    # Anthropic / DashScope) so the UI can grey out models the relay
    # does not actually carry, and ``LLMClient`` can skip endpoints
    # whose configured ``model`` is not in their own catalog instead of
    # surfacing an opaque 404 to the user.
    # An empty list means "never probed" — treat as "allow any" so we
    # do not break upgrades from older configs.
    supported_models: list[str] | None = None
    models_synced_at: float | None = None  # epoch seconds of last sync
    models_sync_error: str | None = None  # last sync error (kept for UI)
    # ── Directed fallback chain ────────────────────────────────────────
    # When ``fallback_enabled`` is True and ``fallback_endpoint`` names
    # another endpoint in the same llm_endpoints.json, that endpoint is
    # promoted to be tried immediately after this one (instead of the
    # next priority-sorted entry). Lets the user express "if my yunwu
    # relay fails, prefer official Anthropic" without juggling priorities
    # against the other ten endpoints. Disabled by default so legacy
    # configs are unaffected.
    fallback_endpoint: str | None = None
    fallback_enabled: bool = False

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["text"]
        self.context_window = normalize_context_window(
            self.context_window,
            provider=self.provider,
            base_url=self.base_url,
        )

    def has_capability(self, capability: str) -> bool:
        """检查是否有某种能力

        优先级:
        1. 显式配置的 capabilities 列表（用户在 JSON 中声明的，最高优先级）
        2. 兼容推断（基于 extra_params / model 名等线索，作为兜底）
        """
        cap = (capability or "").lower().strip()
        caps = {c.lower() for c in (self.capabilities or [])}
        if cap in caps:
            return True

        # === 兼容/推断能力 ===
        # 历史配置或手动编辑的 JSON 可能缺少 capabilities 标注，
        # 但 extra_params/model 名已能反映能力。仅在显式列表未包含时才走推断。
        model = (self.model or "").lower()

        if cap == "thinking":
            if "thinking" in model:
                return True
            extra = self.extra_params or {}
            if extra.get("enable_thinking") is True:
                return True

        # 仅在 capabilities 仍为默认值 ["text"] 时才做模型名推断兜底
        # （用户显式配置过 capabilities 的情况下不覆盖其意图）
        if caps == {"text"} and model:
            from .capabilities import get_provider_slug_from_base_url, infer_capabilities

            provider_slug = (
                get_provider_slug_from_base_url(self.base_url) if self.base_url else None
            )
            inferred = infer_capabilities(model, provider_slug=provider_slug)
            if inferred.get(cap, False):
                return True

        return False

    def supports_model(self, model: str) -> bool:
        """Return True when this endpoint's relay/upstream catalog
        contains ``model`` (or when no catalog has ever been probed —
        we then assume the user knows what they typed and let the
        upstream answer).

        Comparison is case-insensitive and tolerant of leading/trailing
        whitespace. We do NOT split on ``/`` because some relays use
        prefixed names like ``anthropic/claude-3.5-sonnet`` verbatim.
        """
        if not model:
            return True
        if not self.supported_models:
            # Never probed — be permissive so existing configs don't
            # silently lose endpoints after an upgrade. ``sync_models``
            # populates the list when the user clicks "Sync".
            return True
        target = model.strip().lower()
        return any((m or "").strip().lower() == target for m in self.supported_models)

    def get_api_key(self) -> str | None:
        """获取 API Key (优先使用直接存储的 key，然后从环境变量获取)"""
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
        """根据阶梯定价计算本次请求的费用（单位: price_currency）。

        pricing_tiers 格式: [{"max_input": N, "input_price": P, "output_price": P}, ...]
        price 为每百万 token 的价格。max_input=-1 表示无上限。
        按 max_input 升序匹配，取第一个 input_tokens <= max_input 的档位。

        Fix-5：当 endpoint 自身没有配置 ``pricing_tiers`` 时，回退到内置
        价格表（按 provider+model 模糊匹配）。**仍然找不到** 时返回 ``0.0``，
        但调用方应优先使用 ``calculate_cost_or_none`` 拿到 ``None``，UI 上
        渲染为 "-" 而不是误导性的 "0"。
        """
        tiers = self.pricing_tiers
        if not tiers:
            from .pricing import lookup_builtin_price

            fallback = lookup_builtin_price(self.provider, self.model)
            if fallback is None:
                return 0.0
            tiers = [fallback]
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

    def calculate_cost_or_none(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float | None:
        """Like ``calculate_cost`` but returns ``None`` (instead of 0.0) when
        no pricing source — neither user-configured nor built-in — is
        available for this endpoint. UIs should render ``None`` as "-" so
        users aren't misled into thinking the model is free.
        """
        if self.pricing_tiers:
            return self.calculate_cost(input_tokens, output_tokens, cache_read_tokens)
        from .pricing import lookup_builtin_price

        if lookup_builtin_price(self.provider, self.model) is None:
            return None
        return self.calculate_cost(input_tokens, output_tokens, cache_read_tokens)

    def get_effective_pricing(self) -> dict | None:
        """Return the price tier currently used for this endpoint (Fix-5).

        Resolution order: user ``pricing_tiers[0]`` → built-in table →
        ``None`` (unknown). Used by ``GET /api/llm/pricing/effective``.
        """
        if self.pricing_tiers:
            tier = dict(self.pricing_tiers[0])
            tier.setdefault("source", "user")
            tier.setdefault("currency", self.price_currency)
            return tier
        from .pricing import lookup_builtin_price

        return lookup_builtin_price(self.provider, self.model)

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
            context_window=normalize_context_window(
                data.get("context_window"),
                provider=data.get("provider", ""),
                base_url=data.get("base_url", ""),
            ),
            timeout=data.get("timeout", 180),
            capabilities=data.get("capabilities"),
            extra_params=data.get("extra_params"),
            note=data.get("note"),
            rpm_limit=int(data.get("rpm_limit") or 0),
            pricing_tiers=data.get("pricing_tiers"),
            price_currency=data.get("price_currency", "CNY"),
            enabled=data.get("enabled", True),
            stream_only=data.get("stream_only", False),
            supported_models=(
                list(data["supported_models"])
                if isinstance(data.get("supported_models"), list)
                else None
            ),
            models_synced_at=data.get("models_synced_at"),
            models_sync_error=data.get("models_sync_error"),
            fallback_endpoint=(
                str(data["fallback_endpoint"]).strip()
                if data.get("fallback_endpoint")
                else None
            ),
            fallback_enabled=bool(data.get("fallback_enabled", False)),
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
        # API Key: 优先使用环境变量名，不保存明文 key 到配置
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
        if self.supported_models:
            result["supported_models"] = list(self.supported_models)
        if self.models_synced_at is not None:
            result["models_synced_at"] = self.models_synced_at
        if self.models_sync_error:
            result["models_sync_error"] = self.models_sync_error
        if self.fallback_endpoint:
            result["fallback_endpoint"] = self.fallback_endpoint
        if self.fallback_enabled:
            result["fallback_enabled"] = True
        return result


# 异常类
class LLMError(Exception):
    """LLM 相关错误基类"""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        raw_body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.raw_body = raw_body


class UnsupportedMediaError(LLMError):
    """不支持的媒体类型错误"""

    pass


class AllEndpointsFailedError(LLMError):
    """所有端点都失败"""

    def __init__(self, message: str, *, is_structural: bool = False):
        super().__init__(message)
        self.is_structural = is_structural


class ConfigurationError(LLMError):
    """配置错误"""

    pass


class AuthenticationError(LLMError):
    """认证错误（不应重试）"""

    pass


class RateLimitError(LLMError):
    """速率限制错误（可重试）"""

    pass
