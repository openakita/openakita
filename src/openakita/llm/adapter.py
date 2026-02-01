"""
LLM 适配层

为现有的 Brain 类提供向后兼容的接口，同时使用新的 LLMClient。
"""

import logging
from typing import Any, Optional
from dataclasses import dataclass, field

from .types import (
    LLMRequest,
    LLMResponse,
    Message,
    Tool,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    ImageContent,
)
from .client import LLMClient, get_default_client

logger = logging.getLogger(__name__)


@dataclass
class LegacyResponse:
    """兼容旧版 Brain.Response 的响应格式"""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class LegacyContext:
    """兼容旧版 Brain.Context 的上下文格式"""
    messages: list[dict] = field(default_factory=list)
    system: str = ""
    tools: list[dict] = field(default_factory=list)


class LLMAdapter:
    """
    LLM 适配器
    
    提供与旧版 Brain 类兼容的接口，内部使用 LLMClient。
    
    Example:
        adapter = LLMAdapter()
        response = await adapter.think(
            prompt="Hello",
            system="You are helpful",
            tools=[{"name": "search", ...}]
        )
    """
    
    def __init__(self, client: Optional[LLMClient] = None):
        """
        初始化适配器
        
        Args:
            client: LLMClient 实例，默认使用全局单例
        """
        self._client = client or get_default_client()
    
    async def think(
        self,
        prompt: str,
        context: Optional[LegacyContext] = None,
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        enable_thinking: bool = False,
        max_tokens: int = 4096,
    ) -> LegacyResponse:
        """
        兼容旧版 Brain.think 的接口
        
        Args:
            prompt: 用户输入
            context: 对话上下文（旧格式）
            system: 系统提示词
            tools: 可用工具列表（旧格式）
            enable_thinking: 是否启用思考模式
            max_tokens: 最大输出 tokens
            
        Returns:
            兼容旧版的响应对象
        """
        # 转换消息格式
        messages = self._convert_legacy_messages(context)
        messages.append(Message(role="user", content=prompt))
        
        # 确定系统提示词
        sys_prompt = system or (context.system if context else "")
        
        # 转换工具格式
        converted_tools = None
        if tools:
            converted_tools = self._convert_legacy_tools(tools)
        elif context and context.tools:
            converted_tools = self._convert_legacy_tools(context.tools)
        
        # 调用新的 LLMClient
        try:
            response = await self._client.chat(
                messages=messages,
                system=sys_prompt,
                tools=converted_tools,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
            
            return self._convert_to_legacy_response(response)
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
    
    def _convert_legacy_messages(self, context: Optional[LegacyContext]) -> list[Message]:
        """将旧格式消息转换为新格式"""
        if not context or not context.messages:
            return []
        
        messages = []
        for msg in context.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if isinstance(content, str):
                messages.append(Message(role=role, content=content))
            elif isinstance(content, list):
                # 处理多模态内容
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))
                        elif part_type == "image":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(ImageBlock(
                                    image=ImageContent(
                                        media_type=source.get("media_type", "image/jpeg"),
                                        data=source.get("data", ""),
                                    )
                                ))
                        elif part_type == "tool_use":
                            blocks.append(ToolUseBlock(
                                id=part.get("id", ""),
                                name=part.get("name", ""),
                                input=part.get("input", {}),
                            ))
                        elif part_type == "tool_result":
                            blocks.append(ToolResultBlock(
                                tool_use_id=part.get("tool_use_id", ""),
                                content=part.get("content", ""),
                                is_error=part.get("is_error", False),
                            ))
                    elif isinstance(part, str):
                        blocks.append(TextBlock(text=part))
                
                if blocks:
                    messages.append(Message(role=role, content=blocks))
        
        return messages
    
    def _convert_legacy_tools(self, tools: list[dict]) -> list[Tool]:
        """将旧格式工具转换为新格式"""
        converted = []
        for tool in tools:
            converted.append(Tool(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema", {}),
            ))
        return converted
    
    def _convert_to_legacy_response(self, response: LLMResponse) -> LegacyResponse:
        """将新格式响应转换为旧格式"""
        # 提取文本内容
        content = response.text
        
        # 提取工具调用
        tool_calls = []
        for tc in response.tool_calls:
            tool_calls.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        
        return LegacyResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason.value,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
    
    @property
    def client(self) -> LLMClient:
        """获取底层的 LLMClient"""
        return self._client


# 便捷函数
async def think(
    prompt: str,
    context: Optional[LegacyContext] = None,
    system: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    **kwargs,
) -> LegacyResponse:
    """
    便捷函数：使用默认适配器思考
    
    这是对旧版 Brain.think 的直接替代。
    """
    adapter = LLMAdapter()
    return await adapter.think(prompt, context, system, tools, **kwargs)
