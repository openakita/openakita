"""
Anthropic Provider

支持 Claude 系列模型的 API 调用。
增强: SSE 规范解析、Prompt Cache 支持、流式 Usage 完整性。
"""

import logging
from collections.abc import AsyncIterator

import httpx

from ..cache import (
    add_message_cache_breakpoints,
    add_tools_cache_control,
    build_cached_system_blocks,
    sort_tools_for_cache_stability,
)
from ..converters.tools import (
    convert_tools_to_anthropic,
    has_text_tool_calls,
    parse_text_tool_calls,
)
from ..model_registry import get_model_capabilities, get_thinking_budget
from ..sse import parse_sse_stream
from ..types import (
    AuthenticationError,
    EndpointConfig,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimitError,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)
from .base import LLMProvider
from .proxy_utils import build_httpx_timeout, get_httpx_transport, get_proxy_config

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic API Provider"""

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None  # 记录创建客户端时的事件循环 ID

    @property
    def api_key(self) -> str:
        """获取 API Key"""
        return self.config.get_api_key() or ""

    @property
    def base_url(self) -> str:
        """获取 base URL"""
        return self.config.base_url.rstrip("/")

    def _messages_url(self) -> str:
        """构建 messages API URL，避免 /v1 重复拼接。"""
        b = self.base_url
        return f"{b}/messages" if b.endswith("/v1") else f"{b}/v1/messages"

    def _is_local_endpoint(self) -> bool:
        """检查是否为本地端点"""
        url = self.base_url.lower()
        return any(host in url for host in (
            "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
        ))

    def _get_validated_api_key(self) -> str:
        """获取并验证 API Key，空 key 时提前抛出有意义的错误而非让 API 返回模糊 401。"""
        api_key = (self.api_key or "").strip()
        if not api_key:
            if self._is_local_endpoint():
                return "local"
            hint = ""
            if self.config.api_key_env:
                hint = f" (env var {self.config.api_key_env} is not set)"
            raise AuthenticationError(
                f"Missing API key for endpoint '{self.name}'{hint}. "
                "Set the environment variable or configure api_key/api_key_env."
            )
        return api_key

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端

        注意：httpx.AsyncClient 绑定到创建时的事件循环。
        如果事件循环变化（如定时任务创建新循环），需要重新创建客户端。
        """
        import asyncio

        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop_id = None

        # 检查是否需要重新创建客户端
        need_recreate = (
            self._client is None
            or self._client.is_closed
            or self._client_loop_id != current_loop_id
        )

        if need_recreate:
            # 安全关闭旧客户端
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass  # 忽略关闭错误

            # 获取代理和网络配置
            proxy = get_proxy_config()
            transport = get_httpx_transport()  # IPv4-only 支持

            is_local = self._is_local_endpoint()

            # httpx strips Authorization on cross-origin redirects for security.
            # Some Anthropic-compatible gateways (MiniMax, Volcengine Coding Plan, etc.)
            # may redirect across hosts. Re-attach credentials via event hook.
            api_key_for_hook = (self.api_key or "").strip()
            if not api_key_for_hook and is_local:
                api_key_for_hook = "local"

            async def _ensure_auth_on_redirect(request: httpx.Request):
                if api_key_for_hook and "Authorization" not in request.headers:
                    request.headers["Authorization"] = f"Bearer {api_key_for_hook}"
                    request.headers["x-api-key"] = api_key_for_hook

            # trust_env=False: 代理由 get_proxy_config() 显式管理（含可达性验证）。
            # 避免 macOS/Windows 残留系统代理（Clash/V2Ray 等）导致请求被路由到
            # 不存在的代理端口而失败。
            client_kwargs = {
                "timeout": build_httpx_timeout(self.config.timeout, default=60.0),
                "follow_redirects": True,
                "trust_env": False,
                "event_hooks": {"request": [_ensure_auth_on_redirect]},
            }

            if proxy and not is_local:
                client_kwargs["proxy"] = proxy
                logger.debug(f"[Anthropic] Using proxy: {proxy}")

            if transport:
                client_kwargs["transport"] = transport

            self._client = httpx.AsyncClient(**client_kwargs)
            self._client_loop_id = current_loop_id

        return self._client

    async def chat(self, request: LLMRequest) -> LLMResponse:
        """发送聊天请求"""
        self._get_validated_api_key()
        await self.acquire_rate_limit()
        client = await self._get_client()

        # 构建请求体
        body = self._build_request_body(request)

        # 发送请求
        try:
            response = await client.post(
                self._messages_url(),
                headers=self._build_headers(),
                json=body,
            )

            if response.status_code >= 400:
                body = (response.text or "")[:500]
                if response.status_code == 401:
                    raise AuthenticationError(f"Authentication failed: {body}", status_code=401)
                if response.status_code == 429:
                    raise RateLimitError(f"Rate limit exceeded: {body}", status_code=429)
                raise LLMError(f"API error ({response.status_code}): {body}", status_code=response.status_code)

            data = response.json()
            self.mark_healthy()
            return self._parse_response(data)

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}")
            raise LLMError(f"Request timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Request error: {detail}")
            raise LLMError(f"Request failed: {detail}")

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """流式聊天请求。

        增强: 使用符合 SSE 规范的解析器，带 finally 资源清理。
        """
        self._get_validated_api_key()
        await self.acquire_rate_limit()
        client = await self._get_client()

        body = self._build_request_body(request)
        body["stream"] = True

        response = None
        try:
            response = await client.send(
                client.build_request(
                    "POST",
                    self._messages_url(),
                    headers=self._build_headers(),
                    json=body,
                ),
                stream=True,
            )

            if response.status_code >= 400:
                error_body = await response.aread()
                error_text = error_body.decode(errors="replace")[:500]
                if response.status_code == 401:
                    raise AuthenticationError(f"Authentication failed: {error_text}", status_code=401)
                if response.status_code == 429:
                    raise RateLimitError(f"Rate limit exceeded: {error_text}", status_code=429)
                raise LLMError(f"API error ({response.status_code}): {error_text}", status_code=response.status_code)

            # 使用符合 SSE 规范的解析器
            async for event in parse_sse_stream(response):
                yield event

            self.mark_healthy()

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}")
            raise LLMError(f"Stream timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Stream request error: {detail}")
            raise LLMError(f"Stream request failed: {detail}")
        finally:
            if response is not None:
                await response.aclose()

    def _build_headers(self) -> dict:
        """构建请求头"""
        key = (self.api_key or "").strip()
        return {
            "Content-Type": "application/json",
            "x-api-key": key,
            # 部分 Anthropic 兼容网关仅识别 Bearer，保留 x-api-key 以兼容官方 Anthropic。
            "Authorization": f"Bearer {key}",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

    @staticmethod
    def _build_system_blocks(system: str) -> list[dict]:
        """Split system prompt into static + dynamic blocks for Anthropic prompt caching.

        Uses the '## Developer' section boundary as the split point.
        The static part (System section) gets cache_control to enable
        cross-turn prompt caching, reducing token costs significantly.
        """
        _BOUNDARY = "\n\n---\n\n## Developer"
        idx = system.find(_BOUNDARY)
        if idx == -1:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        static_part = system[:idx]
        dynamic_part = system[idx:]
        blocks = [
            {"type": "text", "text": static_part, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_part},
        ]
        return blocks

    def _build_request_body(self, request: LLMRequest) -> dict:
        """构建请求体。

        增强: 使用模型注册表查询能力，支持 Prompt Cache。
        """
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")
        messages = self._serialize_messages(request.messages, thinking_enabled)

        # 使用模型注册表查询 max_tokens，替代硬编码
        caps = get_model_capabilities(self.config.model)
        max_tokens = request.max_tokens or self.config.max_tokens or caps.default_output_tokens

        body: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # 系统提示: 分段缓存 (静态部分标记 cache_control)
        if request.system:
            if caps.supports_cache:
                body["system"] = self._build_system_blocks(request.system)
            else:
                body["system"] = request.system

        # 工具 schema: 排序 + 缓存标记
        if request.tools:
            tools = convert_tools_to_anthropic(request.tools)
            tools = sort_tools_for_cache_stability(tools)
            if caps.supports_cache:
                tools = add_tools_cache_control(tools)
            body["tools"] = tools

        if request.temperature != 1.0:
            body["temperature"] = request.temperature

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        # 额外参数
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)

        # 消息缓存断点: 最后 1-2 条消息添加 cache_control
        if caps.supports_cache and messages:
            body["messages"] = add_message_cache_breakpoints(messages, max_breakpoints=2)

        # Anthropic 扩展思考 (Extended Thinking)
        if thinking_enabled:
            budget = get_thinking_budget(self.config.model, request.thinking_depth)
            if budget <= 0:
                budget = 8192
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            body.pop("temperature", None)
            current_max = body.get("max_tokens", 4096)
            if current_max < budget + 1024:
                body["max_tokens"] = budget + 4096

        return body

    @staticmethod
    def _serialize_messages(messages: list, thinking_enabled: bool) -> list[dict]:
        """序列化消息列表，确保 thinking 模式下格式合规。

        当 thinking 启用时，某些 Anthropic 兼容代理（如云雾 AI 转发 Kimi/Qwen 等）
        要求所有含 tool_use 的 assistant 消息都包含 thinking 块，否则返回 400:
        "thinking is enabled but reasoning_content is missing in assistant tool call message"

        对话历史中可能存在没有 thinking 块的 assistant 消息（例如 failover 前由
        非 thinking 端点生成，或 thinking 是中途开启的），这里为它们补一个占位
        thinking 块以满足 API 校验。
        """
        result = []
        for msg in messages:
            msg_dict = msg.to_dict() if hasattr(msg, "to_dict") else dict(msg)
            if not thinking_enabled or msg_dict.get("role") != "assistant":
                result.append(msg_dict)
                continue

            content = msg_dict.get("content")
            if not isinstance(content, list):
                result.append(msg_dict)
                continue

            has_tool_use = any(b.get("type") == "tool_use" for b in content)
            has_thinking = any(b.get("type") == "thinking" for b in content)

            if has_tool_use and not has_thinking:
                content.insert(0, {"type": "thinking", "thinking": "..."})
                msg_dict["content"] = content

            result.append(msg_dict)
        return result

    def _parse_response(self, data: dict) -> LLMResponse:
        """解析响应

        支持 MiniMax M2.1 的 Interleaved Thinking：
        - 解析 thinking 块并保留在 content 中
        - 确保多轮工具调用时思维链的连续性

        支持文本格式工具调用（MiniMax 兼容）：
        - 检测并解析 <minimax:tool_call> 格式
        - 转换为标准的 ToolUseBlock
        """
        content_blocks = []
        has_tool_calls = False
        text_content = ""  # 收集文本内容，用于检测文本格式工具调用
        thinking_content = ""  # 收集 thinking 内容，检测嵌入的工具调用

        for block in data.get("content", []):
            block_type = block.get("type")

            if block_type == "thinking":
                # MiniMax M2.1 Interleaved Thinking 支持
                # 必须完整保留 thinking 块以保持思维链连续性
                raw_thinking = block.get("thinking", "")
                thinking_content += raw_thinking
                content_blocks.append(ThinkingBlock(thinking=raw_thinking))
            elif block_type == "text":
                text = block.get("text", "")
                text_content += text
                content_blocks.append(TextBlock(text=text))
            elif block_type == "tool_use":
                content_blocks.append(
                    ToolUseBlock(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input", {}),
                    )
                )
                has_tool_calls = True

        # === 文本格式工具调用解析（MiniMax 兼容） ===
        # 当模型返回文本格式的工具调用（如 <minimax:tool_call>）时，解析并转换
        # 同时检查 thinking 块内是否嵌入了工具调用（MiniMax-M2.5 已知行为）
        combined_text_for_tool_check = text_content
        if not has_tool_calls and not text_content and thinking_content:
            if has_text_tool_calls(thinking_content):
                combined_text_for_tool_check = thinking_content
                logger.info(
                    f"[TEXT_TOOL_PARSE] Detected tool calls embedded inside thinking block from {self.name}"
                )

        if not has_tool_calls and combined_text_for_tool_check and has_text_tool_calls(combined_text_for_tool_check):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(combined_text_for_tool_check)

            if text_tool_calls:
                # 移除包含工具调用的文本块，替换为清理后的文本
                content_blocks = [
                    b
                    for b in content_blocks
                    if not (isinstance(b, TextBlock) and has_text_tool_calls(b.text))
                ]

                # 添加清理后的文本（如果有）
                if clean_text.strip():
                    content_blocks.append(TextBlock(text=clean_text.strip()))

                # 添加解析出的工具调用
                content_blocks.extend(text_tool_calls)
                has_tool_calls = True
                logger.info(
                    f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls "
                    f"from {'thinking block' if combined_text_for_tool_check != text_content else 'text'}"
                )

        # 解析停止原因
        stop_reason_str = data.get("stop_reason", "end_turn")
        if has_tool_calls:
            stop_reason = StopReason.TOOL_USE
        else:
            stop_reason_map = {
                "end_turn": StopReason.END_TURN,
                "max_tokens": StopReason.MAX_TOKENS,
                "tool_use": StopReason.TOOL_USE,
                "stop_sequence": StopReason.STOP_SEQUENCE,
            }
            stop_reason = stop_reason_map.get(stop_reason_str, StopReason.END_TURN)

        # 解析使用统计
        usage_data = data.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        )

        return LLMResponse(
            id=data.get("id", ""),
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=data.get("model", self.config.model),
        )

    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
