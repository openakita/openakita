"""
Brain 模块 - LLM 交互层

Brain 是 LLMClient 的薄包装，提供向后兼容的接口。
所有实际的 LLM 调用、能力分流、故障切换都由 LLMClient 处理。
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, ToolParam
from anthropic.types import TextBlock as AnthropicTextBlock
from anthropic.types import ToolUseBlock as AnthropicToolUseBlock
from anthropic.types import Usage as AnthropicUsage

from ..config import settings
from ..llm.client import LLMClient
from ..llm.config import get_default_config_path, load_endpoints_config
from ..llm.types import (
    ImageBlock,
    ImageContent,
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
    VideoContent,
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
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
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

        # Prompt Compiler 专用 LLMClient（独立于主模型，使用快速小模型）
        self._compiler_client: LLMClient | None = None
        self._init_compiler_client()

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
                logger.info("  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  可用端点: {', '.join(healthy_eps):<30}║")
                logger.info("  ╚══════════════════════════════════════════╝")

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

    def _init_compiler_client(self) -> None:
        """从配置加载 Prompt Compiler 专属 LLMClient"""
        try:
            _, compiler_eps, _ = load_endpoints_config()
            if compiler_eps:
                self._compiler_client = LLMClient(endpoints=compiler_eps)
                names = [ep.name for ep in compiler_eps]
                logger.info(f"Compiler LLMClient initialized with endpoints: {names}")
            else:
                logger.info("No compiler endpoints configured, will fall back to main model")
        except Exception as e:
            logger.warning(f"Failed to init compiler client: {e}")

    async def compiler_think(self, prompt: str, system: str = "") -> Response:
        """
        Prompt Compiler 专用 LLM 调用。

        调用策略：
        1. 优先用 compiler_client（快速模型，强制禁用思考模式）
        2. compiler_client 全部端点失败时，回退到主模型（同样禁用思考）

        Args:
            prompt: 用户消息
            system: 系统提示词

        Returns:
            Response 对象
        """
        messages = [Message(role="user", content=[TextBlock(text=prompt)])]

        # 尝试 compiler 专用端点
        if self._compiler_client:
            try:
                response = await self._compiler_client.chat(
                    messages=messages,
                    system=system,
                    enable_thinking=False,
                    max_tokens=2048,
                )
                return self._llm_response_to_response(response)
            except Exception as e:
                logger.warning(f"Compiler LLM failed, falling back to main model: {e}")

        # 回退到主模型（同样禁用思考，以节省时间）
        response = await self._llm_client.chat(
            messages=messages,
            system=system,
            enable_thinking=False,
            max_tokens=2048,
        )
        return self._llm_response_to_response(response)

    def _llm_response_to_response(self, llm_response: LLMResponse) -> Response:
        """将 LLMResponse 转换为向后兼容的 Response"""
        text_parts = []
        tool_calls = []
        for block in llm_response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return Response(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=llm_response.stop_reason or "",
            usage={
                "input_tokens": llm_response.usage.input_tokens if llm_response.usage else 0,
                "output_tokens": llm_response.usage.output_tokens if llm_response.usage else 0,
            },
        )

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

        # 调试输出：保存完整请求到文件
        self._dump_llm_request(system, llm_messages, llm_tools, caller="messages_create")

        conversation_id = kwargs.get("conversation_id")

        # 调用 LLMClient
        try:
            response = asyncio.get_event_loop().run_until_complete(
                self._llm_client.chat(
                    messages=llm_messages,
                    system=system,
                    tools=llm_tools,
                    max_tokens=max_tokens,
                    enable_thinking=use_thinking,
                    conversation_id=conversation_id,
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
                    conversation_id=conversation_id,
                )
            )

        # 转换响应: LLMClient -> Anthropic Message
        return self._convert_response_to_anthropic(response)

    # ========================================================================
    # 格式转换方法
    # ========================================================================

    def _convert_messages_to_llm(self, messages: list[MessageParam]) -> list[Message]:
        """将 Anthropic MessageParam 转换为 LLMClient Message

        支持 MiniMax M2.1 的 Interleaved Thinking：
        - 解析并保留 thinking 块
        - 确保多轮工具调用时思维链的连续性

        支持 Kimi reasoning_content：
        - 从消息字典中提取 reasoning_content
        - 传递给 Message 对象以支持模型切换
        """
        result = []

        for msg in messages:
            role = msg.get("role", "user") if isinstance(msg, dict) else msg["role"]
            content = msg.get("content", "") if isinstance(msg, dict) else msg["content"]
            # 提取 reasoning_content（用于 Kimi 等支持思考的模型）
            reasoning_content = msg.get("reasoning_content") if isinstance(msg, dict) else None

            if isinstance(content, str):
                result.append(
                    Message(role=role, content=content, reasoning_content=reasoning_content)
                )
            elif isinstance(content, list):
                # 复杂内容（多模态、工具调用等）
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")

                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))

                        elif part_type == "thinking":
                            # MiniMax M2.1 Interleaved Thinking 支持
                            # 必须完整保留 thinking 块以保持思维链连续性
                            blocks.append(ThinkingBlock(thinking=part.get("thinking", "")))

                        elif part_type == "tool_use":
                            blocks.append(
                                ToolUseBlock(
                                    id=part.get("id", ""),
                                    name=part.get("name", ""),
                                    input=part.get("input", {}),
                                )
                            )

                        elif part_type == "tool_result":
                            tool_content = part.get("content", "")
                            if isinstance(tool_content, list):
                                # 提取文本
                                texts = [
                                    p.get("text", "")
                                    for p in tool_content
                                    if p.get("type") == "text"
                                ]
                                tool_content = "\n".join(texts)
                            blocks.append(
                                ToolResultBlock(
                                    tool_use_id=part.get("tool_use_id", ""),
                                    content=str(tool_content),
                                    is_error=part.get("is_error", False),
                                )
                            )

                        elif part_type == "image":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    ImageBlock(
                                        image=ImageContent(
                                            media_type=source.get("media_type", "image/jpeg"),
                                            data=source.get("data", ""),
                                        )
                                    )
                                )

                        elif part_type == "video":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    VideoBlock(
                                        video=VideoContent(
                                            media_type=source.get("media_type", "video/mp4"),
                                            data=source.get("data", ""),
                                        )
                                    )
                                )

                    elif isinstance(part, str):
                        blocks.append(TextBlock(text=part))

                if blocks:
                    result.append(
                        Message(role=role, content=blocks, reasoning_content=reasoning_content)
                    )
                else:
                    result.append(
                        Message(role=role, content="", reasoning_content=reasoning_content)
                    )
            else:
                result.append(
                    Message(role=role, content=str(content), reasoning_content=reasoning_content)
                )

        return result

    def _convert_tools_to_llm(self, tools: list[ToolParam] | None) -> list[Tool] | None:
        """将 Anthropic ToolParam 转换为 LLMClient Tool

        支持渐进式披露：
        - description: 简短清单描述
        - detail: 详细使用说明（传给 LLM API）
        """
        if not tools:
            return None

        return [
            Tool(
                name=tool.get("name", ""),
                # 优先使用 detail 字段（详细说明），否则 fallback 到 description
                description=tool.get("detail") or tool.get("description", ""),
                input_schema=tool.get("input_schema", {}),
            )
            for tool in tools
        ]

    def _convert_response_to_anthropic(self, response: LLMResponse) -> AnthropicMessage:
        """将 LLMClient Response 转换为 Anthropic Message

        支持 MiniMax M2.1 的 Interleaved Thinking：
        - thinking 块转换为带 <thinking> 标签的文本
        - Agent 层会保留完整内容用于消息历史回传
        """
        # 转换内容块
        content_blocks = []
        thinking_texts = []

        for block in response.content:
            if isinstance(block, ThinkingBlock):
                # MiniMax M2.1 Interleaved Thinking 支持
                # 转换为 <thinking> 标签包裹的文本，保持与其他模型一致的处理方式
                # 在发送消息历史给 MiniMax 时会转换回 thinking 块格式
                thinking_texts.append(f"<thinking>{block.thinking}</thinking>")
            elif isinstance(block, TextBlock):
                content_blocks.append(AnthropicTextBlock(type="text", text=block.text))
            elif isinstance(block, ToolUseBlock):
                content_blocks.append(
                    AnthropicToolUseBlock(
                        type="tool_use",
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )

        # 如果有 thinking 内容，添加到文本块前面
        if thinking_texts:
            thinking_content = "\n".join(thinking_texts)
            if content_blocks and hasattr(content_blocks[0], "text"):
                # 合并到第一个文本块
                content_blocks[0] = AnthropicTextBlock(
                    type="text", text=thinking_content + "\n" + content_blocks[0].text
                )
            else:
                # 插入新的文本块
                content_blocks.insert(0, AnthropicTextBlock(type="text", text=thinking_content))

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
        context: Context | None = None,
        system: str | None = None,
        tools: list[ToolParam] | None = None,
        max_tokens: int | None = None,
    ) -> Response:
        """
        发送思考请求到 LLM（通过 LLMClient）

        Args:
            prompt: 用户输入
            context: 对话上下文
            system: 系统提示词
            tools: 可用工具列表
            max_tokens: 最大输出 token（不传则使用 self.max_tokens）

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
        logger.info(
            f"[LLM REQUEST] messages={len(llm_messages)}, tools={len(tool_list) if tool_list else 0}"
        )

        # 调试输出：保存完整请求到文件
        self._dump_llm_request(sys_prompt, llm_messages, llm_tools, caller="_chat_with_llm_client")

        # 调用 LLMClient
        response = await self._llm_client.chat(
            messages=llm_messages,
            system=sys_prompt,
            tools=llm_tools,
            max_tokens=max_tokens or self.max_tokens,
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

    def _dump_llm_request(
        self, system: str, messages: list, tools: list, caller: str = "unknown"
    ) -> None:
        """
        保存 LLM 请求到调试文件

        用于诊断上下文问题，将完整的 system prompt 和 messages 保存到文件

        Args:
            system: 系统提示词
            messages: 消息列表（可能是 Message 对象或字典）
            tools: 工具列表
            caller: 调用方标识
        """
        try:
            debug_dir = Path("data/llm_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            request_id = uuid.uuid4().hex[:8]
            debug_file = debug_dir / f"llm_request_{timestamp}_{request_id}.json"

            # 转换 messages 为可序列化格式
            serializable_messages = []
            for msg in messages:
                if hasattr(msg, "to_dict"):
                    serializable_messages.append(msg.to_dict())
                elif hasattr(msg, "__dict__"):
                    serializable_messages.append(self._serialize_message(msg))
                elif isinstance(msg, dict):
                    serializable_messages.append(msg)
                else:
                    serializable_messages.append(str(msg))

            # 提取工具名称
            tool_names = []
            for t in tools or []:
                if hasattr(t, "name"):
                    tool_names.append(t.name)
                elif isinstance(t, dict):
                    tool_names.append(t.get("name", str(t)))
                else:
                    tool_names.append(str(t))

            # 估算 token 数量（中文约 1.5 字符/token，英文约 4 字符/token，取平均 2 字符/token）
            system_length = len(system) if system else 0
            estimated_system_tokens = int(system_length / 2)

            # 估算 messages 的 token
            messages_text = json.dumps(serializable_messages, ensure_ascii=False)
            estimated_messages_tokens = int(len(messages_text) / 2)

            total_estimated_tokens = estimated_system_tokens + estimated_messages_tokens

            debug_data = {
                "timestamp": datetime.now().isoformat(),
                "caller": caller,
                "system_prompt": system,
                "system_prompt_length": system_length,
                "system_prompt_estimated_tokens": estimated_system_tokens,
                "messages": serializable_messages,
                "messages_count": len(messages),
                "messages_estimated_tokens": estimated_messages_tokens,
                "total_estimated_tokens": total_estimated_tokens,
                "tools": tool_names,
                "tools_count": len(tools) if tools else 0,
            }

            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2, default=str)

            # 记录日志并在 token 数量过大时发出警告
            if total_estimated_tokens > 50000:
                logger.warning(
                    f"[LLM DEBUG] ⚠️ Very large context! Estimated {total_estimated_tokens} tokens (system: {estimated_system_tokens}, messages: {estimated_messages_tokens})"
                )
            elif total_estimated_tokens > 30000:
                logger.warning(
                    f"[LLM DEBUG] Large context: {total_estimated_tokens} tokens (system: {estimated_system_tokens}, messages: {estimated_messages_tokens})"
                )
            else:
                logger.info(
                    f"[LLM DEBUG] Request saved: {total_estimated_tokens} tokens (system: {estimated_system_tokens}, messages: {estimated_messages_tokens})"
                )

            # 清理超过 3 天的旧调试文件
            self._cleanup_old_debug_files(debug_dir, max_age_days=3)

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to save debug file: {e}")

    def _cleanup_old_debug_files(self, debug_dir: Path, max_age_days: int = 3) -> None:
        """
        清理超过指定天数的旧调试文件

        Args:
            debug_dir: 调试文件目录
            max_age_days: 最大保留天数，默认 3 天
        """
        try:
            import os
            from datetime import timedelta

            cutoff_time = datetime.now() - timedelta(days=max_age_days)
            deleted_count = 0

            for file in debug_dir.glob("llm_request_*.json"):
                try:
                    # 获取文件修改时间
                    mtime = datetime.fromtimestamp(os.path.getmtime(file))
                    if mtime < cutoff_time:
                        file.unlink()
                        deleted_count += 1
                except Exception:
                    pass  # 忽略单个文件删除失败

            if deleted_count > 0:
                logger.debug(
                    f"[LLM DEBUG] Cleaned up {deleted_count} old debug files (older than {max_age_days} days)"
                )

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to cleanup old files: {e}")

    def _serialize_message(self, msg) -> dict:
        """将 Message 对象序列化为字典"""
        result = {"role": getattr(msg, "role", "unknown")}

        content = getattr(msg, "content", None)
        if isinstance(content, str):
            result["content"] = content
        elif isinstance(content, list):
            result["content"] = []
            for block in content:
                if hasattr(block, "__dict__"):
                    block_dict = {"type": getattr(block, "type", "unknown")}
                    # 处理常见的 block 属性
                    if hasattr(block, "text"):
                        block_dict["text"] = block.text
                    if hasattr(block, "id"):
                        block_dict["id"] = block.id
                    if hasattr(block, "name"):
                        block_dict["name"] = block.name
                    if hasattr(block, "input"):
                        block_dict["input"] = block.input
                    if hasattr(block, "content"):
                        # tool_result 的 content
                        block_dict["content"] = (
                            block.content[:500] + "..."
                            if len(str(block.content)) > 500
                            else block.content
                        )
                    if hasattr(block, "thinking"):
                        block_dict["thinking"] = (
                            block.thinking[:200] + "..."
                            if len(str(block.thinking)) > 200
                            else block.thinking
                        )
                    result["content"].append(block_dict)
                elif isinstance(block, dict):
                    # 字典格式的 block，处理大内容
                    block_copy = dict(block)
                    if "content" in block_copy and len(str(block_copy["content"])) > 500:
                        block_copy["content"] = str(block_copy["content"])[:500] + "...[truncated]"
                    result["content"].append(block_copy)
                else:
                    result["content"].append(str(block))
        else:
            result["content"] = str(content) if content else None

        # 添加 reasoning_content（如果有）
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result["reasoning_content"] = (
                msg.reasoning_content[:200] + "..."
                if len(str(msg.reasoning_content)) > 200
                else msg.reasoning_content
            )

        return result

    async def health_check(self) -> dict[str, bool]:
        """检查所有端点健康状态"""
        return await self._llm_client.health_check()

    # ========================================================================
    # 动态模型切换
    # ========================================================================

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = 12,
        reason: str = "",
        conversation_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        临时切换到指定模型

        Args:
            endpoint_name: 端点名称
            hours: 有效时间（小时），默认 12 小时
            reason: 切换原因

        Returns:
            (成功, 消息)
        """
        return self._llm_client.switch_model(
            endpoint_name, hours, reason, conversation_id=conversation_id
        )

    def get_fallback_model(self, conversation_id: str | None = None) -> str:
        """
        获取下一优先级的备用模型端点名称

        按端点配置的 priority 排序，返回当前端点之后的下一个健康端点。
        用于 TaskMonitor 的动态 fallback 模型选择，替代硬编码。

        Args:
            conversation_id: 可选的会话 ID

        Returns:
            下一个端点名称，无可用备选时返回空字符串
        """
        next_ep = self._llm_client.get_next_endpoint(conversation_id)
        return next_ep or ""

    def restore_default_model(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """
        恢复默认模型（清除临时覆盖）

        Returns:
            (成功, 消息)
        """
        return self._llm_client.restore_default(conversation_id=conversation_id)

    def get_current_model_info(self) -> dict:
        """
        获取当前使用的模型信息

        Returns:
            模型信息字典
        """
        model = self._llm_client.get_current_model()
        if not model:
            return {"error": "无可用模型"}

        return {
            "name": model.name,
            "model": model.model,
            "provider": model.provider,
            "is_healthy": model.is_healthy,
            "is_override": model.is_override,
            "capabilities": model.capabilities,
            "note": model.note,
        }

    def list_available_models(self) -> list[dict]:
        """
        列出所有可用模型

        Returns:
            模型信息列表
        """
        models = self._llm_client.list_available_models()
        return [
            {
                "name": m.name,
                "model": m.model,
                "provider": m.provider,
                "priority": m.priority,
                "is_healthy": m.is_healthy,
                "is_current": m.is_current,
                "is_override": m.is_override,
                "capabilities": m.capabilities,
                "note": m.note,
            }
            for m in models
        ]

    def get_override_status(self) -> dict | None:
        """
        获取当前覆盖状态

        Returns:
            覆盖状态信息，无覆盖时返回 None
        """
        return self._llm_client.get_override_status()

    def update_model_priority(self, priority_order: list[str]) -> tuple[bool, str]:
        """
        更新模型优先级顺序（永久生效）

        Args:
            priority_order: 模型名称列表，按优先级从高到低排序

        Returns:
            (成功, 消息)
        """
        return self._llm_client.update_priority(priority_order)

    async def plan(self, task: str, context: Context | None = None) -> str:
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
        context: Context | None = None,
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
        context: str | None = None,
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
