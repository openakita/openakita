"""
Anthropic Provider

支持 Claude 系列模型的 API 调用。
"""

import os
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

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic API Provider"""
    
    ANTHROPIC_VERSION = "2023-06-01"
    
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
        
        # 发送请求
        try:
            response = await client.post(
                f"{self.base_url}/v1/messages",
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
                f"{self.base_url}/v1/messages",
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
            self.mark_unhealthy(f"Timeout: {e}")
            raise LLMError(f"Stream timeout: {e}")
    
    def _build_headers(self) -> dict:
        """构建请求头"""
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
    
    def _build_request_body(self, request: LLMRequest) -> dict:
        """构建请求体"""
        body = {
            "model": self.config.model,
            "max_tokens": request.max_tokens or self.config.max_tokens,
            "messages": [msg.to_dict() for msg in request.messages],
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
        
        return body
    
    def _parse_response(self, data: dict) -> LLMResponse:
        """解析响应"""
        content_blocks = []
        
        for block in data.get("content", []):
            block_type = block.get("type")
            
            if block_type == "text":
                content_blocks.append(TextBlock(text=block.get("text", "")))
            elif block_type == "tool_use":
                content_blocks.append(ToolUseBlock(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                ))
        
        # 解析停止原因
        stop_reason_str = data.get("stop_reason", "end_turn")
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
