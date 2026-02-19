"""
轻量级 Handoff 多 Agent 编排

在现有 Master-Worker (ZMQ 重量级跨进程) 之外，提供进程内的轻量级
Agent 切换机制。参考 OpenAI Agents SDK 的 Handoff 设计。

核心概念:
- HandoffAgent: 具有特定能力的 Agent 角色
- HandoffTarget: 描述何时以及如何委托给其他 Agent
- HandoffOrchestrator: 管理 Agent 间的切换和消息路由

与现有架构的关系:
- Handoff 作为 Orchestration 的轻量级选项 (进程内，无 ZMQ)
- Master-Worker 保留用于跨进程/跨机器场景
- 通过 config.orchestration_mode 选择: "single" | "handoff" | "master-worker"

Usage:
    # 定义 Agent 角色
    coder = HandoffAgent(
        name="code_writer",
        description="擅长编写和修改代码",
        system_prompt="你是一个代码编写专家...",
        tools=["run_shell", "write_file", "read_file"],
    )
    reviewer = HandoffAgent(
        name="code_reviewer",
        description="擅长代码审查和质量改进",
        system_prompt="你是一个代码审查专家...",
        tools=["read_file", "web_search"],
    )
    coder.add_handoff(reviewer, description="当代码编写完成需要审查时")
    reviewer.add_handoff(coder, description="当审查发现问题需要修改代码时")

    # 编排
    orchestrator = HandoffOrchestrator(agents=[coder, reviewer], entry_agent=coder)
    result = await orchestrator.run("请帮我写一个排序算法并审查")
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.token_tracking import TokenTrackingContext, set_tracking_context, reset_tracking_context

logger = logging.getLogger(__name__)


@dataclass
class HandoffTarget:
    """
    Handoff 目标定义。

    描述当前 Agent 可以委托给哪些其他 Agent，
    以及何时应该触发委托。
    """

    agent_name: str  # 目标 Agent 名称
    tool_name: str  # 生成的工具名称 (如 "transfer_to_code_reviewer")
    description: str  # 何时触发 handoff 的说明
    input_filter: Callable[[list[dict]], list[dict]] | None = None  # 上下文过滤器


@dataclass
class HandoffAgent:
    """
    支持 Handoff 的 Agent 角色。

    每个 HandoffAgent 代表一个具有特定能力的角色，
    通过 handoffs 列表声明可以委托给哪些其他角色。
    """

    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)  # 允许使用的工具名称
    handoffs: list[HandoffTarget] = field(default_factory=list)
    max_iterations: int = 20  # 单个 Agent 的最大迭代数

    def add_handoff(
        self,
        target: "HandoffAgent",
        description: str,
        tool_name: str | None = None,
        input_filter: Callable[[list[dict]], list[dict]] | None = None,
    ) -> None:
        """添加一个 handoff 目标。"""
        if tool_name is None:
            tool_name = f"transfer_to_{target.name}"

        self.handoffs.append(HandoffTarget(
            agent_name=target.name,
            tool_name=tool_name,
            description=description,
            input_filter=input_filter,
        ))

    def get_handoff_tools(self) -> list[dict]:
        """
        生成 handoff 工具定义。

        将每个 HandoffTarget 转化为 LLM 可调用的工具 schema，
        使 LLM 能够通过调用这些工具来触发 Agent 切换。
        """
        tools = []
        for h in self.handoffs:
            tools.append({
                "name": h.tool_name,
                "description": (
                    f"将任务委托给 '{h.agent_name}' Agent。"
                    f"触发条件: {h.description}"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "要传递给目标 Agent 的上下文消息和具体指令",
                        },
                    },
                    "required": ["message"],
                },
            })
        return tools


@dataclass
class HandoffEvent:
    """Handoff 事件记录"""

    from_agent: str
    to_agent: str
    message: str
    timestamp: float = field(default_factory=time.time)


class HandoffOrchestrator:
    """
    Handoff 编排器。

    管理多个 HandoffAgent 之间的切换和消息路由。
    在同一进程内运行，无需 ZMQ 等重量级 IPC。

    工作流程:
    1. 从 entry_agent 开始处理用户消息
    2. 如果当前 Agent 调用了 handoff 工具 → 切换到目标 Agent
    3. 如果当前 Agent 返回最终答案 → 结束
    4. 维护完整的 handoff 历史用于追踪
    """

    MAX_HANDOFFS = 10  # 防止 Agent 之间无限互相委托

    def __init__(
        self,
        agents: list[HandoffAgent],
        entry_agent: HandoffAgent | None = None,
        brain: Any = None,
    ) -> None:
        self._agents: dict[str, HandoffAgent] = {a.name: a for a in agents}
        self._entry_agent = entry_agent or agents[0]
        self._brain = brain
        self._current_agent: HandoffAgent = self._entry_agent
        self._handoff_history: list[HandoffEvent] = []
        self._run_id: str = ""

    @property
    def current_agent(self) -> HandoffAgent:
        return self._current_agent

    @property
    def handoff_history(self) -> list[HandoffEvent]:
        return self._handoff_history

    def set_brain(self, brain: Any) -> None:
        """设置 LLM 客户端（延迟注入）"""
        self._brain = brain

    async def run(
        self,
        message: str,
        *,
        session_id: str = "",
        reasoning_engine: Any = None,
    ) -> str:
        """
        运行 Handoff 编排。

        Args:
            message: 用户消息
            session_id: 会话 ID
            reasoning_engine: ReasoningEngine 实例 (用于 Agent 推理)

        Returns:
            最终响应文本
        """
        self._run_id = str(uuid.uuid4())[:8]
        self._handoff_history = []
        self._current_agent = self._entry_agent
        handoff_count = 0

        # 初始消息
        messages: list[dict] = [{"role": "user", "content": message}]

        logger.info(
            f"[Handoff:{self._run_id}] Starting with agent '{self._current_agent.name}'"
        )

        while handoff_count < self.MAX_HANDOFFS:
            agent = self._current_agent

            # 构建当前 Agent 的系统提示词
            system_prompt = self._build_agent_prompt(agent)

            # 获取当前 Agent 允许的工具 + handoff 工具
            handoff_tools = agent.get_handoff_tools()
            handoff_tool_names = {h.tool_name for h in agent.handoffs}

            # 调用 ReasoningEngine 执行推理循环
            if reasoning_engine is None:
                logger.error("[Handoff] No reasoning_engine provided")
                return "❌ Handoff 编排器未配置推理引擎"

            # 使用 reasoning engine 运行当前 Agent
            # ReasoningEngine.run() 返回最终文本或触发 handoff
            result = await self._run_agent(
                agent=agent,
                messages=messages,
                system_prompt=system_prompt,
                handoff_tools=handoff_tools,
                handoff_tool_names=handoff_tool_names,
                reasoning_engine=reasoning_engine,
                session_id=session_id,
            )

            if isinstance(result, dict) and result.get("type") == "handoff":
                # 触发了 handoff
                target_name = result["target_agent"]
                handoff_message = result["message"]

                if target_name not in self._agents:
                    logger.error(f"[Handoff] Unknown target agent: {target_name}")
                    return f"❌ 未知的目标 Agent: {target_name}"

                # 记录 handoff 事件
                event = HandoffEvent(
                    from_agent=agent.name,
                    to_agent=target_name,
                    message=handoff_message,
                )
                self._handoff_history.append(event)

                # 应用上下文过滤器
                target_agent = self._agents[target_name]
                target_handoff = next(
                    (h for h in agent.handoffs if h.agent_name == target_name), None
                )
                if target_handoff and target_handoff.input_filter:
                    messages = target_handoff.input_filter(messages)

                # 追加 handoff 上下文
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Handoff from '{agent.name}'] {handoff_message}"
                    ),
                })

                self._current_agent = target_agent
                handoff_count += 1

                logger.info(
                    f"[Handoff:{self._run_id}] "
                    f"'{agent.name}' -> '{target_name}' "
                    f"(handoff #{handoff_count})"
                )
                continue

            # 最终答案
            logger.info(
                f"[Handoff:{self._run_id}] Completed by '{agent.name}' "
                f"after {handoff_count} handoffs"
            )
            return result if isinstance(result, str) else str(result)

        # 达到最大 handoff 次数
        logger.warning(
            f"[Handoff:{self._run_id}] Max handoffs ({self.MAX_HANDOFFS}) reached"
        )
        return "⚠️ Agent 之间委托次数过多，任务终止。请简化任务描述后重试。"

    async def _run_agent(
        self,
        agent: HandoffAgent,
        messages: list[dict],
        system_prompt: str,
        handoff_tools: list[dict],
        handoff_tool_names: set[str],
        reasoning_engine: Any,
        session_id: str = "",
    ) -> str | dict:
        """
        运行单个 Agent 的推理循环。

        检测到 handoff 工具调用时提前返回 handoff 指令。

        Returns:
            str: 最终答案文本
            dict: {"type": "handoff", "target_agent": ..., "message": ...}
        """
        # 注意: 这里需要和 ReasoningEngine 集成
        # 简化实现: 使用 brain 直接调用, 由 reasoning_engine 处理主循环
        # 真正的集成需要 reasoning_engine 支持 handoff 工具的特殊处理

        # 临时简化: 使用 brain 单次推理检测 handoff
        import asyncio

        if not self._brain:
            return "❌ 未配置 LLM 客户端"

        # 将 handoff 工具加入工具列表
        all_tools = list(handoff_tools)

        for _iter in range(agent.max_iterations):
            _tt = set_tracking_context(TokenTrackingContext(
                operation_type="handoff",
                operation_detail=agent.name,
            ))
            try:
                response = await asyncio.to_thread(
                    self._brain.messages_create,
                    model=self._brain.model,
                    max_tokens=self._brain.max_tokens,
                    system=system_prompt,
                    tools=all_tools if all_tools else None,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"[Handoff] Agent '{agent.name}' LLM error: {e}")
                return f"❌ Agent '{agent.name}' 推理失败: {e}"
            finally:
                reset_tracking_context(_tt)

            # 解析响应
            stop_reason = getattr(response, "stop_reason", "end_turn")

            # 检查是否有 handoff 工具调用
            content_blocks = getattr(response, "content", [])
            text_parts = []
            tool_use_blocks = []

            for block in content_blocks:
                block_type = getattr(block, "type", "")
                if block_type == "text":
                    text_parts.append(getattr(block, "text", ""))
                elif block_type == "tool_use":
                    tool_use_blocks.append(block)

            # 检查 handoff 工具
            for block in tool_use_blocks:
                tool_name = getattr(block, "name", "")
                if tool_name in handoff_tool_names:
                    tool_input = getattr(block, "input", {})
                    # 找到对应的 handoff 目标
                    target = next(
                        (h for h in agent.handoffs if h.tool_name == tool_name), None
                    )
                    if target:
                        return {
                            "type": "handoff",
                            "target_agent": target.agent_name,
                            "message": tool_input.get("message", ""),
                        }

            # 没有 handoff，如果是 end_turn 则返回最终答案
            if stop_reason == "end_turn":
                return "\n".join(text_parts)

            # 否则如果有普通工具调用，暂时返回文本
            # (完整实现应委托给 tool_executor)
            if tool_use_blocks:
                # 非 handoff 工具调用 - 需要执行
                # TODO: 集成 tool_executor
                messages.append({
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "\n".join(text_parts)} if text_parts else None,
                        *[{
                            "type": "tool_use",
                            "id": getattr(b, "id", ""),
                            "name": getattr(b, "name", ""),
                            "input": getattr(b, "input", {}),
                        } for b in tool_use_blocks]
                    ],
                })
                # 返回 placeholder tool results
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": getattr(b, "id", ""),
                        "content": "工具执行暂未在 Handoff 模式中实现",
                    } for b in tool_use_blocks],
                })
                continue

            return "\n".join(text_parts)

        return f"⚠️ Agent '{agent.name}' 达到最大迭代次数"

    def _build_agent_prompt(self, agent: HandoffAgent) -> str:
        """为当前 Agent 构建系统提示词。"""
        parts = []

        if agent.system_prompt:
            parts.append(agent.system_prompt)
        else:
            parts.append(f"你是 '{agent.name}' Agent。{agent.description}")

        # 添加 handoff 说明
        if agent.handoffs:
            parts.append("\n## 可用的 Agent 委托")
            parts.append("当你认为任务需要其他专业能力时，可以使用以下工具将任务委托给其他 Agent：")
            for h in agent.handoffs:
                parts.append(f"- `{h.tool_name}`: {h.description}")

        # 添加 handoff 历史
        if self._handoff_history:
            parts.append("\n## 委托历史")
            for event in self._handoff_history[-5:]:
                parts.append(
                    f"- {event.from_agent} → {event.to_agent}: {event.message[:100]}"
                )

        return "\n".join(parts)

    def get_summary(self) -> dict[str, Any]:
        """获取编排摘要。"""
        return {
            "run_id": self._run_id,
            "entry_agent": self._entry_agent.name,
            "final_agent": self._current_agent.name,
            "total_handoffs": len(self._handoff_history),
            "handoff_chain": [
                {"from": e.from_agent, "to": e.to_agent}
                for e in self._handoff_history
            ],
        }
