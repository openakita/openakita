"""
Anthropic Provider

支持 Claude 系列模型的 API 调用。
"""

import logging
from collections.abc import AsyncIterator

import httpx

from ..converters.tools import has_text_tool_calls, parse_text_tool_calls
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

            client_kwargs = {
                "timeout": build_httpx_timeout(self.config.timeout, default=60.0),
                "follow_redirects": True,
            }

            if proxy:
                client_kwargs["proxy"] = proxy
                logger.debug(f"[Anthropic] Using proxy: {proxy}")

            if transport:
                client_kwargs["transport"] = transport

            self._client = httpx.AsyncClient(**client_kwargs)
            self._client_loop_id = current_loop_id

        return self._client

    async def chat(self, request: LLMRequest) -> LLMResponse:
        """发送聊天请求"""
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

            if response.status_code == 401:
                raise AuthenticationError(f"Authentication failed: {response.text}")
            if response.status_code == 429:
                raise RateLimitError(f"Rate limit exceeded: {response.text}")
            if response.status_code >= 400:
                raise LLMError(f"API error ({response.status_code}): {response.text}")

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
        """流式聊天请求"""
        await self.acquire_rate_limit()
        client = await self._get_client()

        body = self._build_request_body(request)
        body["stream"] = True

        try:
            async with client.stream(
                "POST",
                self._messages_url(),
                headers=self._build_headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    raise LLMError(f"API error ({response.status_code}): {error_body.decode()}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip():
                            import json

                            try:
                                event = json.loads(data)
                                yield event
                            except json.JSONDecodeError:
                                continue

                self.mark_healthy()

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}")
            raise LLMError(f"Stream timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Stream request error: {detail}")
            raise LLMError(f"Stream request failed: {detail}")

    def _build_headers(self) -> dict:
        """构建请求头"""
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            # 部分 Anthropic 兼容网关仅识别 Bearer，保留 x-api-key 以兼容官方 Anthropic。
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

    def _build_request_body(self, request: LLMRequest) -> dict:
        """构建请求体"""
        # Anthropic API 强制要求 max_tokens（不传会 400 报错），
        # 与 OpenAI 兼容 API 不同（可选参数，不传则用模型默认上限）。
        # 因此这里必须传一个值。使用端点配置的 max_tokens 或请求指定的值。
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")
        messages = self._serialize_messages(request.messages, thinking_enabled)
        body = {
            "model": self.config.model,
            "max_tokens": request.max_tokens or self.config.max_tokens or 16384,
            "messages": messages,
        }

        if request.system:
            body["system"] = request.system

        if request.tools:
            body["tools"] = [tool.to_dict() for tool in request.tools]

        if request.temperature != 1.0:
            body["temperature"] = request.temperature

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        # 额外参数
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)

        # Anthropic 扩展思考 (Extended Thinking)
        # 仅在端点声明了 thinking 能力时才添加，避免对不支持的模型发送无效参数
        if thinking_enabled:
            depth_budget_map = {
                "low": 2048,
                "medium": 8192,
                "high": 32768,
            }
            budget = depth_budget_map.get(request.thinking_depth or "medium", 8192)
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            # Anthropic 扩展思考要求 temperature=1，移除自定义温度
            body.pop("temperature", None)
            # Anthropic 要求 max_tokens >= budget_tokens，确保不会冲突
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
            msg_dict = msg.to_dict()
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

        for block in data.get("content", []):
            block_type = block.get("type")

            if block_type == "thinking":
                # MiniMax M2.1 Interleaved Thinking 支持
                # 必须完整保留 thinking 块以保持思维链连续性
                content_blocks.append(ThinkingBlock(thinking=block.get("thinking", "")))
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
        if not has_tool_calls and text_content and has_text_tool_calls(text_content):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(text_content)

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
                    f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls from text"
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
