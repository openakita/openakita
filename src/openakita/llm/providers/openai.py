"""
OpenAI Provider

支持 OpenAI API 格式的调用，包括：
- OpenAI 官方 API
- DashScope（通义千问）
- Kimi（Moonshot AI）
- OpenRouter
- 硅基流动
- 云雾 API
- 其他 OpenAI 兼容 API
"""

import os
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from .base import LLMProvider
from ..types import (
    LLMRequest,
    LLMResponse,
    EndpointConfig,
    TextBlock,
    ToolUseBlock,
    Usage,
    StopReason,
    AuthenticationError,
    RateLimitError,
    LLMError,
)
from ..converters.messages import convert_messages_to_openai
from ..converters.tools import (
    convert_tools_to_openai, 
    convert_tool_calls_from_openai,
    parse_text_tool_calls,
    has_text_tool_calls,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 API Provider"""
    
    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop_id: Optional[int] = None  # 记录创建客户端时的事件循环 ID
    
    @property
    def api_key(self) -> str:
        """获取 API Key"""
        return os.environ.get(self.config.api_key_env, "")
    
    @property
    def base_url(self) -> str:
        """获取 base URL"""
        return self.config.base_url.rstrip("/")
    
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
            self._client is None or 
            self._client.is_closed or
            self._client_loop_id != current_loop_id
        )
        
        if need_recreate:
            # 安全关闭旧客户端
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass  # 忽略关闭错误
            
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
            )
            self._client_loop_id = current_loop_id
        
        return self._client
    
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """发送聊天请求"""
        client = await self._get_client()
        
        # 构建请求体
        body = self._build_request_body(request)
        
        logger.debug(f"OpenAI request to {self.base_url}: model={body.get('model')}")
        
        # 发送请求
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
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
            self.mark_unhealthy(f"Timeout: {e}")
            raise LLMError(f"Request timeout: {e}")
        except httpx.RequestError as e:
            self.mark_unhealthy(f"Request error: {e}")
            raise LLMError(f"Request failed: {e}")
    
    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """流式聊天请求"""
        client = await self._get_client()
        
        body = self._build_request_body(request)
        body["stream"] = True
        
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    raise LLMError(f"API error ({response.status_code}): {error_body.decode()}")
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() and data != "[DONE]":
                            try:
                                event = json.loads(data)
                                yield self._convert_stream_event(event)
                            except json.JSONDecodeError:
                                continue
                
                self.mark_healthy()
                
        except httpx.TimeoutException as e:
            self.mark_unhealthy(f"Timeout: {e}")
            raise LLMError(f"Stream timeout: {e}")
    
    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        # OpenRouter 需要额外的头
        if "openrouter" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://github.com/openakita"
            headers["X-Title"] = "OpenAkita"
        
        return headers
    
    def _build_request_body(self, request: LLMRequest) -> dict:
        """构建请求体"""
        # 转换消息格式（传递 provider 以正确处理视频等多媒体内容）
        messages = convert_messages_to_openai(
            request.messages, 
            request.system,
            provider=self.config.provider
        )
        
        body = {
            "model": self.config.model,
            "max_tokens": request.max_tokens or self.config.max_tokens,
            "messages": messages,
        }
        
        # 工具
        if request.tools:
            body["tools"] = convert_tools_to_openai(request.tools)
            body["tool_choice"] = "auto"
        
        # 温度
        if request.temperature != 1.0:
            body["temperature"] = request.temperature
        
        # 停止序列
        if request.stop_sequences:
            body["stop"] = request.stop_sequences
        
        # 额外参数（服务商特定）
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)
        
        # DashScope 思考模式
        if request.enable_thinking and self.config.provider == "dashscope":
            body["enable_thinking"] = True
        
        return body
    
    def _parse_response(self, data: dict) -> LLMResponse:
        """解析响应"""
        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(
                id=data.get("id", ""),
                content=[],
                stop_reason=StopReason.END_TURN,
                usage=Usage(),
                model=data.get("model", self.config.model),
            )
        
        choice = choices[0]
        message = choice.get("message", {})
        content_blocks = []
        has_tool_calls = False
        
        # 文本内容
        text_content = message.get("content") or ""
        
        # 原生工具调用
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            content_blocks.extend(convert_tool_calls_from_openai(tool_calls))
            has_tool_calls = True
            logger.info(f"[TOOL_CALLS] Received {len(tool_calls)} native tool calls from {self.name}")
        
        # 文本格式工具调用解析（降级方案）
        # 当模型不支持原生工具调用时，解析文本中的 <function_calls> 格式
        if not has_tool_calls and text_content and has_text_tool_calls(text_content):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(text_content)
            
            if text_tool_calls:
                # 更新文本内容（移除工具调用部分）
                text_content = clean_text
                content_blocks.extend(text_tool_calls)
                has_tool_calls = True
                logger.info(f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls from text")
        
        # 添加文本内容
        if text_content:
            content_blocks.insert(0, TextBlock(text=text_content))
        
        # 保存思考内容（Kimi/DashScope 等模型返回）
        reasoning_content = message.get("reasoning_content")
        
        # 解析停止原因
        finish_reason = choice.get("finish_reason", "stop")
        if has_tool_calls:
            stop_reason = StopReason.TOOL_USE
        else:
            stop_reason_map = {
                "stop": StopReason.END_TURN,
                "length": StopReason.MAX_TOKENS,
                "tool_calls": StopReason.TOOL_USE,
                "function_call": StopReason.TOOL_USE,
            }
            stop_reason = stop_reason_map.get(finish_reason, StopReason.END_TURN)
        
        # 解析使用统计
        usage_data = data.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )
        
        return LLMResponse(
            id=data.get("id", ""),
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=data.get("model", self.config.model),
            reasoning_content=reasoning_content,
        )
    
    def _convert_stream_event(self, event: dict) -> dict:
        """转换流式事件为统一格式"""
        choices = event.get("choices", [])
        if not choices:
            return {"type": "ping"}
        
        choice = choices[0]
        delta = choice.get("delta", {})
        
        result = {"type": "content_block_delta"}
        
        if "content" in delta:
            result["delta"] = {"type": "text", "text": delta["content"]}
        elif "tool_calls" in delta:
            tool_calls = delta["tool_calls"]
            if tool_calls:
                tc = tool_calls[0]
                result["delta"] = {
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "arguments": tc.get("function", {}).get("arguments"),
                }
        
        if choice.get("finish_reason"):
            result["type"] = "message_stop"
            result["stop_reason"] = choice["finish_reason"]
        
        return result
    
    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
