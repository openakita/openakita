"""
Brain 模块 - LLM 交互层

Brain 是 LLMClient 的薄包装，提供向后兼容的接口。
所有实际的 LLM 调用、能力分流、故障切换都由 LLMClient 处理。
"""

import logging
import asyncio
from typing import Any, Optional
from dataclasses import dataclass, field

from anthropic.types import Message as AnthropicMessage, MessageParam, ToolParam
from anthropic.types import TextBlock as AnthropicTextBlock, ToolUseBlock as AnthropicToolUseBlock, Usage as AnthropicUsage

from ..config import settings
from ..llm.client import LLMClient, get_default_client
from ..llm.config import load_endpoints_config, get_default_config_path
from ..llm.types import (
    Message, Tool, LLMResponse, 
    TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock, VideoBlock,
    ImageContent, VideoContent, StopReason,
)

logger = logging.getLogger(__name__)


@dataclass
class Response:
    """LLM 响应（向后兼容）"""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class Context:
    """对话上下文"""
    messages: list[MessageParam] = field(default_factory=list)
    system: str = ""
    tools: list[ToolParam] = field(default_factory=list)


class Brain:
    """
    Agent 大脑 - LLM 交互层
    
    Brain 是 LLMClient 的薄包装：
    - 配置从 llm_endpoints.json 加载
    - 能力分流、故障切换由 LLMClient 处理
    - 提供向后兼容的 Anthropic Message 格式接口
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ):
        self.max_tokens = max_tokens or settings.max_tokens
        
        # 创建 LLMClient（统一入口）
        config_path = get_default_config_path()
        if config_path.exists():
            self._llm_client = LLMClient(config_path=config_path)
            logger.info(f"Brain using LLMClient with config from {config_path}")
        else:
            # 如果没有配置文件，创建空客户端
            self._llm_client = LLMClient()
            logger.warning("No llm_endpoints.json found, LLMClient may not work")
        
        # 公开属性（从 LLMClient 获取）
        self._update_public_attrs()
        
        # Thinking 模式状态
        self._thinking_enabled = True
        
        # 启动信息
        endpoints = self._llm_client.endpoints
        logger.info(f"Brain initialized with {len(endpoints)} endpoints via LLMClient")
        for ep in endpoints:
            logger.info(f"  - {ep.name}: {ep.model} (capabilities: {ep.capabilities})")
        
        # 显示当前端点
        if endpoints:
            # 获取健康的端点
            healthy_eps = [p.name for p in self._llm_client.providers.values() if p.is_healthy]
            if healthy_eps:
                logger.info(f"  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  可用端点: {', '.join(healthy_eps):<30}║")
                logger.info(f"  ╚══════════════════════════════════════════╝")
    
    def _update_public_attrs(self) -> None:
        """更新公开属性（向后兼容）"""
        endpoints = self._llm_client.endpoints
        if endpoints:
            ep = endpoints[0]  # 使用第一个端点的信息
            self.model = ep.model
            self.base_url = ep.base_url
            # API key 不再暴露
        else:
            self.model = settings.default_model
            self.base_url = ""
    
    def set_thinking_mode(self, enabled: bool) -> None:
        """设置 thinking 模式"""
        self._thinking_enabled = enabled
        logger.info(f"Thinking mode {'enabled' if enabled else 'disabled'}")
    
    def is_thinking_enabled(self) -> bool:
        """检查 thinking 模式是否启用"""
        thinking_mode = settings.thinking_mode
        if thinking_mode == "always":
            return True
        if thinking_mode == "never":
            return False
        return self._thinking_enabled
    
    def get_current_endpoint_info(self) -> dict:
        """获取当前端点信息"""
        providers = self._llm_client.providers
        for name, provider in providers.items():
            if provider.is_healthy:
                return {
                    "name": name,
                    "model": provider.model,
                    "healthy": True,
                }
        # 没有健康的端点
        endpoints = self._llm_client.endpoints
        if endpoints:
            return {
                "name": endpoints[0].name,
                "model": endpoints[0].model,
                "healthy": False,
            }
        return {"name": "none", "model": "none", "healthy": False}
    
    # ========================================================================
    # 核心方法：messages_create
    # ========================================================================
    
    def messages_create(self, use_thinking: bool = None, **kwargs) -> AnthropicMessage:
        """
        调用 LLM API（通过 LLMClient）
        
        这是主要的 LLM 调用入口，自动处理：
        - 能力分流（图片/视频自动选择支持的端点）
        - 故障切换
        - 格式转换
        
        Args:
            use_thinking: 是否使用 thinking 模式
            **kwargs: Anthropic 格式参数 (messages, system, tools, max_tokens)
        
        Returns:
            Anthropic Message 格式响应
        """
        if use_thinking is None:
            use_thinking = self.is_thinking_enabled()
        
        # 转换消息格式: Anthropic -> LLMClient
        llm_messages = self._convert_messages_to_llm(kwargs.get("messages", []))
        system = kwargs.get("system", "")
        llm_tools = self._convert_tools_to_llm(kwargs.get("tools", []))
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        
        # 调用 LLMClient
        try:
            response = asyncio.get_event_loop().run_until_complete(
                self._llm_client.chat(
                    messages=llm_messages,
                    system=system,
                    tools=llm_tools,
                    max_tokens=max_tokens,
                    enable_thinking=use_thinking,
                )
            )
        except RuntimeError:
            # 没有事件循环，创建新的
            response = asyncio.run(
                self._llm_client.chat(
                    messages=llm_messages,
                    system=system,
                    tools=llm_tools,
                    max_tokens=max_tokens,
                    enable_thinking=use_thinking,
                )
            )
        
        # 转换响应: LLMClient -> Anthropic Message
        return self._convert_response_to_anthropic(response)
    
    # ========================================================================
    # 格式转换方法
    # ========================================================================
    
    def _convert_messages_to_llm(self, messages: list[MessageParam]) -> list[Message]:
        """将 Anthropic MessageParam 转换为 LLMClient Message"""
        result = []
        
        for msg in messages:
            role = msg.get("role", "user") if isinstance(msg, dict) else msg["role"]
            content = msg.get("content", "") if isinstance(msg, dict) else msg["content"]
            
            if isinstance(content, str):
                result.append(Message(role=role, content=content))
            elif isinstance(content, list):
                # 复杂内容（多模态、工具调用等）
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        
                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))
                        
                        elif part_type == "tool_use":
                            blocks.append(ToolUseBlock(
                                id=part.get("id", ""),
                                name=part.get("name", ""),
                                input=part.get("input", {}),
                            ))
                        
                        elif part_type == "tool_result":
                            tool_content = part.get("content", "")
                            if isinstance(tool_content, list):
                                # 提取文本
                                texts = [p.get("text", "") for p in tool_content if p.get("type") == "text"]
                                tool_content = "\n".join(texts)
                            blocks.append(ToolResultBlock(
                                tool_use_id=part.get("tool_use_id", ""),
                                content=str(tool_content),
                                is_error=part.get("is_error", False),
                            ))
                        
                        elif part_type == "image":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(ImageBlock(
                                    image=ImageContent(
                                        media_type=source.get("media_type", "image/jpeg"),
                                        data=source.get("data", ""),
                                    )
                                ))
                        
                        elif part_type == "video":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(VideoBlock(
                                    video=VideoContent(
                                        media_type=source.get("media_type", "video/mp4"),
                                        data=source.get("data", ""),
                                    )
                                ))
                    
                    elif isinstance(part, str):
                        blocks.append(TextBlock(text=part))
                
                if blocks:
                    result.append(Message(role=role, content=blocks))
                else:
                    result.append(Message(role=role, content=""))
            else:
                result.append(Message(role=role, content=str(content)))
        
        return result
    
    def _convert_tools_to_llm(self, tools: Optional[list[ToolParam]]) -> Optional[list[Tool]]:
        """将 Anthropic ToolParam 转换为 LLMClient Tool"""
        if not tools:
            return None
        
        return [
            Tool(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema", {}),
            )
            for tool in tools
        ]
    
    def _convert_response_to_anthropic(self, response: LLMResponse) -> AnthropicMessage:
        """将 LLMClient Response 转换为 Anthropic Message"""
        # 转换内容块
        content_blocks = []
        
        for block in response.content:
            if isinstance(block, TextBlock):
                content_blocks.append(AnthropicTextBlock(type="text", text=block.text))
            elif isinstance(block, ToolUseBlock):
                content_blocks.append(AnthropicToolUseBlock(
                    type="tool_use",
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
        
        # 如果没有内容，添加空文本块
        if not content_blocks:
            content_blocks.append(AnthropicTextBlock(type="text", text=""))
        
        # 转换 stop_reason
        stop_reason_map = {
            StopReason.END_TURN: "end_turn",
            StopReason.MAX_TOKENS: "max_tokens",
            StopReason.TOOL_USE: "tool_use",
            StopReason.STOP_SEQUENCE: "stop_sequence",
        }
        stop_reason = stop_reason_map.get(response.stop_reason, "end_turn")
        
        return AnthropicMessage(
            id=response.id,
            type="message",
            role="assistant",
            content=content_blocks,
            model=response.model,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=AnthropicUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )
    
    # ========================================================================
    # 高级方法：think（向后兼容）
    # ========================================================================
    
    async def think(
        self,
        prompt: str,
        context: Optional[Context] = None,
        system: Optional[str] = None,
        tools: Optional[list[ToolParam]] = None,
    ) -> Response:
        """
        发送思考请求到 LLM（通过 LLMClient）
        
        Args:
            prompt: 用户输入
            context: 对话上下文
            system: 系统提示词
            tools: 可用工具列表
        
        Returns:
            Response 对象
        """
        # 构建消息列表
        messages: list[MessageParam] = []
        if context and context.messages:
            messages.extend(context.messages)
        messages.append({"role": "user", "content": prompt})
        
        # 确定系统提示词和工具
        sys_prompt = system or (context.system if context else "")
        tool_list = tools or (context.tools if context else [])
        
        # 转换为 LLMClient 格式
        llm_messages = self._convert_messages_to_llm(messages)
        llm_tools = self._convert_tools_to_llm(tool_list) if tool_list else None
        
        # 日志
        logger.info(f"[LLM REQUEST] messages={len(llm_messages)}, tools={len(tool_list) if tool_list else 0}")
        
        # 调用 LLMClient
        response = await self._llm_client.chat(
            messages=llm_messages,
            system=sys_prompt,
            tools=llm_tools,
            max_tokens=self.max_tokens,
            enable_thinking=self.is_thinking_enabled(),
        )
        
        # 转换响应
        content = response.text
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in response.tool_calls
        ]
        
        # 日志
        logger.info(f"[LLM RESPONSE] content_len={len(content)}, tool_calls={len(tool_calls)}")
        
        return Response(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason.value,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
    
    # ========================================================================
    # 辅助方法
    # ========================================================================
    
    async def health_check(self) -> dict[str, bool]:
        """检查所有端点健康状态"""
        return await self._llm_client.health_check()
    
    async def plan(self, task: str, context: Optional[Context] = None) -> str:
        """为任务生成执行计划"""
        prompt = f"""请为以下任务制定详细的执行计划:

任务: {task}

要求:
1. 分解为具体的步骤
2. 识别需要的工具和技能
3. 考虑可能的失败情况和备选方案
4. 估计每个步骤的复杂度

请以 Markdown 格式输出计划。"""

        response = await self.think(prompt, context)
        return response.content
    
    async def generate_code(
        self,
        description: str,
        language: str = "python",
        context: Optional[Context] = None,
    ) -> str:
        """生成代码"""
        prompt = f"""请生成以下功能的 {language} 代码:

{description}

要求:
1. 代码应该完整、可运行
2. 包含必要的导入语句
3. 添加适当的注释和 docstring
4. 遵循 {language} 的最佳实践
5. 如果是类，包含类型提示

只输出代码，不要解释。"""

        response = await self.think(prompt, context)
        
        # 提取代码块
        code = response.content
        if f"```{language}" in code:
            start = code.find(f"```{language}") + len(f"```{language}")
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        
        return code
    
    async def analyze_error(
        self,
        error: str,
        context: Optional[str] = None,
    ) -> dict[str, Any]:
        """分析错误并提供解决方案"""
        prompt = f"""请分析以下错误并提供解决方案:

错误信息:
{error}

{"上下文:" + context if context else ""}

请提供:
1. 错误原因分析
2. 可能的解决方案（按优先级排序）
3. 如何避免类似错误

以 JSON 格式输出:
{{
    "cause": "错误原因",
    "solutions": ["解决方案1", "解决方案2"],
    "prevention": "预防措施"
}}"""

        response = await self.think(prompt)
        
        import json
        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()
            
            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "cause": "Unable to parse error analysis",
                "solutions": [response.content],
                "prevention": "",
            }
