"""
推理-行动引擎 (ReAct Pattern)

从 agent.py 的 _chat_with_tools_and_context 重构为显式的
Reason -> Act -> Observe 三阶段循环。

核心职责:
- 显式推理循环管理（Reason / Act / Observe）
- LLM 响应解析与 Decision 分类
- 工具调用编排（委托给 ToolExecutor）
- 上下文压缩触发（委托给 ContextManager）
- 循环检测（签名重复、自检间隔、安全阈值）
- 模型切换逻辑
- 任务完成度验证（委托给 ResponseHandler）
"""

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import settings
from ..tracing.tracer import get_tracer
from .agent_state import AgentState, TaskState, TaskStatus
from .context_manager import ContextManager
from .response_handler import ResponseHandler, clean_llm_response, strip_thinking_tags
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class DecisionType(Enum):
    """LLM 决策类型"""
    FINAL_ANSWER = "final_answer"  # 纯文本响应
    TOOL_CALLS = "tool_calls"  # 需要工具调用


@dataclass
class Decision:
    """LLM 推理决策"""
    type: DecisionType
    text_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking_content: str = ""
    raw_response: Any = None
    stop_reason: str = ""
    # 完整的 assistant_content（保留 thinking 块等）
    assistant_content: list[dict] = field(default_factory=list)


@dataclass
class Checkpoint:
    """
    决策检查点，用于多路径探索和回滚。

    在关键决策点保存消息历史和任务状态的快照，
    当检测到循环、连续失败等问题时可回滚到之前的检查点，
    附加失败经验提示后重新推理。
    """

    id: str
    messages_snapshot: list[dict]  # 深拷贝消息历史
    state_snapshot: dict  # 序列化的 TaskState 关键字段
    decision_summary: str  # 做出的决策摘要
    iteration: int  # 保存时的迭代次数
    timestamp: float = field(default_factory=time.time)
    tool_names: list[str] = field(default_factory=list)  # 该决策调用的工具


class ReasoningEngine:
    """
    显式推理-行动引擎。

    替代 agent.py 中的 _chat_with_tools_and_context()，
    将隐式循环重构为清晰的 Reason -> Act -> Observe 三阶段。
    支持 Checkpoint + Rollback 多路径探索。
    """

    # 检查点配置
    MAX_CHECKPOINTS = 5  # 保留最近 N 个检查点
    CONSECUTIVE_FAIL_THRESHOLD = 3  # 同一工具连续失败 N 次触发回滚

    def __init__(
        self,
        brain: Any,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        response_handler: ResponseHandler,
        agent_state: AgentState,
    ) -> None:
        self._brain = brain
        self._tool_executor = tool_executor
        self._context_manager = context_manager
        self._response_handler = response_handler
        self._state = agent_state

        # Checkpoint 管理
        self._checkpoints: list[Checkpoint] = []
        self._tool_failure_counter: dict[str, int] = {}  # tool_name -> consecutive_failures

        # 思维链: 暂存最近一次推理的 react_trace，供 agent_handler 读取
        self._last_react_trace: list[dict] = []

        # 浏览器"读页面状态"工具
        self._browser_page_read_tools = frozenset({
            "browser_get_content", "browser_screenshot",
        })

    # ==================== ask_user 等待用户回复 ====================

    async def _wait_for_user_reply(
        self,
        question: str,
        state: TaskState,
        *,
        timeout_seconds: int = 60,
        max_reminders: int = 1,
        poll_interval: float = 2.0,
    ) -> str | None:
        """
        等待用户回复 ask_user 的问题（仅 IM 模式生效）。

        利用 Gateway 的中断队列机制：IM 用户在 Agent 处理中发送的消息
        会被 Gateway 放入 interrupt_queue，本方法轮询该队列获取回复。

        流程:
        1. 通过 Gateway 发送问题给用户
        2. 轮询 interrupt_queue 等待回复（timeout_seconds 超时）
        3. 第一次超时 → 发送提醒，再等一轮
        4. 第二次超时 → 返回 None，由调用方注入系统消息让 LLM 自行决策

        Args:
            question: 要发送给用户的问题文本
            state: 当前任务状态（用于取消检查）
            timeout_seconds: 每轮等待超时（秒）
            max_reminders: 最大追问提醒次数
            poll_interval: 轮询间隔（秒）

        Returns:
            用户回复文本，或 None（超时/无 gateway/被取消）
        """
        # 获取 gateway 和 session 引用
        session = self._state.current_session
        if not session:
            return None

        gateway = session.get_metadata("_gateway") if hasattr(session, "get_metadata") else None
        session_key = session.get_metadata("_session_key") if gateway else None

        if not gateway or not session_key:
            # CLI 模式或无 gateway，不做等待
            return None

        # 发送问题到用户
        try:
            await gateway.send_to_session(session, question, role="assistant")
            logger.info(f"[ask_user] Question sent to user, waiting for reply (timeout={timeout_seconds}s)")
        except Exception as e:
            logger.warning(f"[ask_user] Failed to send question via gateway: {e}")
            return None

        reminders_sent = 0

        while reminders_sent <= max_reminders:
            # 轮询等待用户回复
            elapsed = 0.0

            while elapsed < timeout_seconds:
                # 检查任务是否被取消
                if state.cancelled:
                    logger.info("[ask_user] Task cancelled while waiting for reply")
                    return None

                # 检查中断队列
                try:
                    reply_msg = await gateway.check_interrupt(session_key)
                except Exception as e:
                    logger.warning(f"[ask_user] check_interrupt error: {e}")
                    reply_msg = None

                if reply_msg:
                    # 从 UnifiedMessage 提取文本
                    reply_text = (
                        reply_msg.plain_text.strip()
                        if hasattr(reply_msg, "plain_text") and reply_msg.plain_text
                        else str(reply_msg).strip()
                    )
                    if reply_text:
                        logger.info(f"[ask_user] User replied: {reply_text[:80]}")
                        # 记录到 session 历史
                        try:
                            session.add_message(role="user", content=reply_text, source="ask_user_reply")
                        except Exception:
                            pass
                        return reply_text

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # 本轮超时
            if reminders_sent < max_reminders:
                # 发送追问提醒
                reminders_sent += 1
                reminder = "⏰ 我在等你回复上面的问题哦，看到的话回复一下~"
                try:
                    await gateway.send_to_session(session, reminder, role="assistant")
                    logger.info(f"[ask_user] Timeout #{reminders_sent}, reminder sent")
                except Exception as e:
                    logger.warning(f"[ask_user] Failed to send reminder: {e}")
            else:
                # 追问次数用尽，返回 None
                logger.info(
                    f"[ask_user] Final timeout after {reminders_sent} reminder(s), "
                    f"total wait ~{timeout_seconds * (max_reminders + 1)}s"
                )
                return None

        return None

    # ==================== Checkpoint / Rollback ====================

    def _save_checkpoint(
        self,
        messages: list[dict],
        state: TaskState,
        decision: Decision,
        iteration: int,
    ) -> None:
        """
        在关键决策点保存检查点。

        仅在工具调用决策时保存（纯文本响应不需要回滚）。
        保留最近 MAX_CHECKPOINTS 个检查点以控制内存。
        """
        tool_names = [tc.get("name", "") for tc in decision.tool_calls]
        summary = f"iteration={iteration}, tools=[{', '.join(tool_names)}]"

        cp = Checkpoint(
            id=str(uuid.uuid4())[:8],
            messages_snapshot=copy.deepcopy(messages),
            state_snapshot={
                "iteration": state.iteration,
                "status": state.status.value,
                "executed_tools": list(state.tools_executed),
            },
            decision_summary=summary,
            iteration=iteration,
            tool_names=tool_names,
        )
        self._checkpoints.append(cp)

        # 保留最近 N 个
        if len(self._checkpoints) > self.MAX_CHECKPOINTS:
            self._checkpoints = self._checkpoints[-self.MAX_CHECKPOINTS:]

        logger.debug(f"[Checkpoint] Saved: {cp.id} at iteration {iteration}")

    def _record_tool_result(self, tool_name: str, success: bool) -> None:
        """记录工具执行结果，用于连续失败检测。"""
        if success:
            self._tool_failure_counter[tool_name] = 0
        else:
            self._tool_failure_counter[tool_name] = (
                self._tool_failure_counter.get(tool_name, 0) + 1
            )

    def _should_rollback(self, tool_results: list[dict]) -> tuple[bool, str]:
        """
        检查是否应该触发回滚。

        触发条件:
        1. 同一工具连续失败 >= CONSECUTIVE_FAIL_THRESHOLD 次
        2. 整批工具全部失败

        Returns:
            (should_rollback, reason)
        """
        if not self._checkpoints:
            return False, ""

        # 检查本批次工具执行结果
        batch_failures = []
        for result in tool_results:
            content = ""
            if isinstance(result, dict):
                content = str(result.get("content", ""))
            elif isinstance(result, str):
                content = result

            has_error = any(marker in content for marker in [
                "❌", "⚠️ 工具执行错误", "错误类型:", "ToolError",
            ])
            has_success = any(marker in content for marker in [
                "✅", '"status": "delivered"', '"ok": true',
            ])

            # 部分成功（如 deliver_artifacts 2张图发了1张）不算失败，
            # 避免回滚已经发出的不可撤回内容
            is_failed = has_error and not has_success
            batch_failures.append(is_failed)

        # 整批全部失败
        if batch_failures and all(batch_failures):
            return True, "本轮所有工具调用均失败"

        # 单工具连续失败
        for tool_name, count in self._tool_failure_counter.items():
            if count >= self.CONSECUTIVE_FAIL_THRESHOLD:
                return True, f"工具 '{tool_name}' 连续失败 {count} 次"

        return False, ""

    def _rollback(self, reason: str) -> tuple[list[dict], int] | None:
        """
        执行回滚: 恢复到上一个检查点。

        在恢复的消息历史末尾附加失败经验提示，
        帮助 LLM 避免重蹈覆辙。

        Returns:
            (restored_messages, checkpoint_iteration) or None if no checkpoints
        """
        if not self._checkpoints:
            return None

        # 弹出最近的检查点（避免回滚到同一个点）
        cp = self._checkpoints.pop()
        restored_messages = copy.deepcopy(cp.messages_snapshot)

        # 附加失败经验
        failure_hint = (
            f"[系统提示] 之前的方案失败了（原因: {reason}）。"
            f"失败的决策: {cp.decision_summary}。"
            f"请尝试完全不同的方法来完成任务。"
            f"避免使用与之前相同的工具参数组合。"
        )
        restored_messages.append({
            "role": "user",
            "content": failure_hint,
        })

        # 重置失败计数器
        self._tool_failure_counter.clear()

        logger.info(
            f"[Rollback] Rolled back to checkpoint {cp.id} "
            f"(iteration {cp.iteration}). Reason: {reason}"
        )

        return restored_messages, cp.iteration

    async def run(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system_prompt: str = "",
        base_system_prompt: str = "",
        task_description: str = "",
        task_monitor: Any = None,
        session_type: str = "cli",
        interrupt_check_fn: Any = None,
        conversation_id: str | None = None,
    ) -> str:
        """
        主推理循环: Reason -> Act -> Observe。

        Args:
            messages: 初始消息列表
            tools: 工具定义列表
            system_prompt: 系统提示词
            base_system_prompt: 基础系统提示词（不含动态 Plan）
            task_description: 任务描述
            task_monitor: 任务监控器
            session_type: 会话类型
            interrupt_check_fn: 中断检查函数
            conversation_id: 对话 ID

        Returns:
            最终响应文本
        """
        state = self._state.current_task
        if not state or not state.is_active:
            # 无任务 或 上一个任务已结束（COMPLETED/FAILED/CANCELLED/IDLE），需新建
            state = self._state.begin_task()

        # 启动 Trace（非上下文管理器，因为 run() 有多个 return 路径）
        tracer = get_tracer()
        tracer.begin_trace(session_id=state.session_id, metadata={
            "task_description": task_description[:200] if task_description else "",
            "session_type": session_type,
            "model": self._brain.model,
        })

        max_iterations = settings.max_iterations

        # 保存原始用户消息（用于模型切换时重置上下文）
        state.original_user_messages = [
            msg for msg in messages if self._is_human_user_message(msg)
        ]

        working_messages = list(messages)
        current_model = self._brain.model

        # ForceToolCall 配置
        if session_type == "im":
            base_force_retries = 0
        else:
            base_force_retries = max(0, int(getattr(settings, "force_tool_call_max_retries", 1)))

        max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)
        max_verify_retries = 3
        max_confirmation_text_retries = 1

        # 追踪变量
        executed_tool_names: list[str] = []
        delivery_receipts: list[dict] = []
        _last_browser_url = ""

        # 循环计数器
        consecutive_tool_rounds = 0
        no_tool_call_count = 0
        verify_incomplete_count = 0
        no_confirmation_text_count = 0
        tools_executed_in_task = False

        # 循环检测
        recent_tool_signatures: list[str] = []
        tool_pattern_window = 8
        llm_self_check_interval = 10
        extreme_safety_threshold = 50

        def _build_effective_system_prompt() -> str:
            """动态追加活跃 Plan"""
            try:
                from ..tools.handlers.plan import get_active_plan_prompt
                _cid = conversation_id
                prompt = base_system_prompt or system_prompt
                if _cid:
                    plan_section = get_active_plan_prompt(_cid)
                    if plan_section:
                        prompt += f"\n\n{plan_section}\n"
                return prompt
            except Exception:
                return base_system_prompt or system_prompt

        def _make_tool_signature(tc: dict) -> str:
            """生成工具签名"""
            nonlocal _last_browser_url
            name = tc.get("name", "")
            inp = tc.get("input", {})

            if name == "browser_navigate":
                _last_browser_url = inp.get("url", "")

            try:
                param_str = json.dumps(inp, sort_keys=True, ensure_ascii=False)
            except Exception:
                param_str = str(inp)

            if name in self._browser_page_read_tools and len(param_str) <= 20 and _last_browser_url:
                param_str = f"{param_str}|url={_last_browser_url}"

            param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
            return f"{name}({param_hash})"

        # ==================== 主循环 ====================
        logger.info(f"[ReAct] === Loop started (max_iterations={max_iterations}, model={current_model}) ===")

        react_trace: list[dict] = []
        _trace_started_at = datetime.now().isoformat()

        for iteration in range(max_iterations):
            state.iteration = iteration

            # 检查取消
            if state.cancelled:
                logger.info(f"[ReAct] Task cancelled at iteration start: {state.cancel_reason}")
                self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration})
                return "✅ 任务已停止。"

            # 任务监控
            if task_monitor:
                task_monitor.begin_iteration(iteration + 1, current_model)
                # 模型切换检查
                switch_result = self._check_model_switch(
                    task_monitor, state, working_messages, current_model
                )
                if switch_result:
                    current_model, working_messages = switch_result
                    no_tool_call_count = 0
                    tools_executed_in_task = False
                    verify_incomplete_count = 0
                    executed_tool_names = []
                    consecutive_tool_rounds = 0
                    recent_tool_signatures = []
                    no_confirmation_text_count = 0

            # 上下文压缩
            _ctx_compressed_info: dict | None = None
            if iteration > 0:
                _before_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                working_messages = await self._context_manager.compress_if_needed(
                    working_messages,
                    system_prompt=_build_effective_system_prompt(),
                    tools=tools,
                )
                _after_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                if _after_tokens < _before_tokens:
                    _ctx_compressed_info = {
                        "before_tokens": _before_tokens,
                        "after_tokens": _after_tokens,
                    }
                    logger.info(
                        f"[ReAct] Context compressed: {_before_tokens} → {_after_tokens} tokens"
                    )

            # ==================== REASON 阶段 ====================
            logger.info(f"[ReAct] Iter {iteration+1}/{max_iterations} — REASON (model={current_model})")
            if state.status != TaskStatus.REASONING:
                state.transition(TaskStatus.REASONING)

            _thinking_t0 = time.time()  # 思维链: 记录 thinking 开始时间
            try:
                decision = await self._reason(
                    working_messages,
                    system_prompt=_build_effective_system_prompt(),
                    tools=tools,
                    current_model=current_model,
                    conversation_id=conversation_id,
                )

                if task_monitor:
                    task_monitor.reset_retry_count()

            except Exception as e:
                logger.error(f"[LLM] Brain call failed: {e}")
                retry_result = self._handle_llm_error(
                    e, task_monitor, state, working_messages, current_model
                )
                if retry_result == "retry":
                    await asyncio.sleep(2)
                    continue
                elif isinstance(retry_result, tuple):
                    current_model, working_messages = retry_result
                    no_tool_call_count = 0
                    tools_executed_in_task = False
                    verify_incomplete_count = 0
                    executed_tool_names = []
                    consecutive_tool_rounds = 0
                    recent_tool_signatures = []
                    no_confirmation_text_count = 0
                    continue
                else:
                    raise

            _thinking_duration_ms = int((time.time() - _thinking_t0) * 1000)  # 思维链: 计算 thinking 耗时

            if task_monitor:
                task_monitor.end_iteration(decision.text_content or "")

            # -- 收集 ReAct trace 数据 --
            # token 信息从 raw_response.usage 提取（Decision 本身不携带 token）
            _raw = decision.raw_response
            _usage = getattr(_raw, "usage", None) if _raw else None
            _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
            _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0
            _iter_trace: dict = {
                "iteration": iteration + 1,
                "timestamp": datetime.now().isoformat(),
                "decision_type": decision.type.value if hasattr(decision.type, "value") else str(decision.type),
                "model": current_model,
                "thinking": (decision.thinking_content or "")[:500] if decision.thinking_content else None,
                "thinking_duration_ms": _thinking_duration_ms,
                "text": (decision.text_content or "")[:2000] if decision.text_content else None,
                "tool_calls": [
                    {
                        "name": tc.get("name"),
                        "id": tc.get("id"),
                        "input_preview": str(tc.get("input", {}))[:500],
                    }
                    for tc in (decision.tool_calls or [])
                ],
                "tool_results": [],  # 将在工具执行后填充
                "tokens": {
                    "input": _in_tokens,
                    "output": _out_tokens,
                },
                "context_compressed": _ctx_compressed_info,
            }
            tool_names_for_log = [tc.get("name", "?") for tc in (decision.tool_calls or [])]
            logger.info(
                f"[ReAct] Iter {iteration+1} — decision={_iter_trace['decision_type']}, "
                f"tools={tool_names_for_log}, "
                f"tokens_in={_in_tokens}, tokens_out={_out_tokens}"
            )

            # ==================== 决策分支 ====================

            if decision.type == DecisionType.FINAL_ANSWER:
                # 纯文本响应 - 处理完成度验证
                answer_preview = (decision.text_content or "")[:80].replace("\n", " ")
                logger.info(f"[ReAct] Iter {iteration+1} — FINAL_ANSWER: \"{answer_preview}...\"")
                consecutive_tool_rounds = 0

                result = await self._handle_final_answer(
                    decision=decision,
                    working_messages=working_messages,
                    original_messages=messages,
                    tools_executed_in_task=tools_executed_in_task,
                    executed_tool_names=executed_tool_names,
                    delivery_receipts=delivery_receipts,
                    no_tool_call_count=no_tool_call_count,
                    verify_incomplete_count=verify_incomplete_count,
                    no_confirmation_text_count=no_confirmation_text_count,
                    max_no_tool_retries=max_no_tool_retries,
                    max_verify_retries=max_verify_retries,
                    max_confirmation_text_retries=max_confirmation_text_retries,
                    base_force_retries=base_force_retries,
                    conversation_id=conversation_id,
                )

                if isinstance(result, str):
                    # 最终答案
                    react_trace.append(_iter_trace)
                    logger.info(
                        f"[ReAct] === COMPLETED after {iteration+1} iterations, "
                        f"tools: {list(set(executed_tool_names))} ==="
                    )
                    self._save_react_trace(react_trace, conversation_id, session_type, "completed", _trace_started_at)
                    state.transition(TaskStatus.COMPLETED)
                    tracer.end_trace(metadata={
                        "result": "completed",
                        "iterations": iteration + 1,
                        "tools_used": list(set(executed_tool_names)),
                    })
                    return result
                else:
                    # 需要继续循环（验证不通过）
                    logger.info(f"[ReAct] Iter {iteration+1} — VERIFY: incomplete, continuing loop")
                    react_trace.append(_iter_trace)
                    state.transition(TaskStatus.VERIFYING)
                    (
                        working_messages,
                        no_tool_call_count,
                        verify_incomplete_count,
                        no_confirmation_text_count,
                        max_no_tool_retries,
                    ) = result
                    continue

            elif decision.type == DecisionType.TOOL_CALLS:
                # ==================== ACT 阶段 ====================
                tool_names = [tc.get("name", "?") for tc in decision.tool_calls]
                logger.info(f"[ReAct] Iter {iteration+1} — ACT: {tool_names}")
                state.transition(TaskStatus.ACTING)

                # ---- ask_user 拦截 ----
                # 如果 LLM 调用了 ask_user，立即中断循环，将问题返回给用户
                ask_user_calls = [tc for tc in decision.tool_calls if tc.get("name") == "ask_user"]
                other_calls = [tc for tc in decision.tool_calls if tc.get("name") != "ask_user"]

                if ask_user_calls:
                    logger.info(
                        f"[ReAct] Iter {iteration+1} — ask_user intercepted, "
                        f"pausing for user input (other_tools={[tc.get('name') for tc in other_calls]})"
                    )

                    # 添加 assistant 消息（保留完整的 tool_use 内容用于上下文连贯）
                    working_messages.append({
                        "role": "assistant",
                        "content": decision.assistant_content,
                    })

                    # 如果同时还有其他工具调用，先执行它们
                    # 收集其他工具的 tool_result（Claude API 要求每个 tool_use 都有对应 tool_result）
                    other_tool_results: list[dict] = []
                    if other_calls:
                        other_results, other_executed, other_receipts = (
                            await self._tool_executor.execute_batch(
                                other_calls,
                                state=state,
                                task_monitor=task_monitor,
                                allow_interrupt_checks=self._state.interrupt_enabled,
                                capture_delivery_receipts=True,
                            )
                        )
                        if other_executed:
                            tools_executed_in_task = True
                            executed_tool_names.extend(other_executed)
                            state.record_tool_execution(other_executed)
                        # 保留其他工具的 tool_result 内容
                        other_tool_results = other_results if other_results else []

                    # 提取 ask_user 的问题文本
                    question = ask_user_calls[0].get("input", {}).get("question", "")
                    ask_tool_id = ask_user_calls[0].get("id", "ask_user_0")

                    # 合并 LLM 的文本回复 + 问题
                    text_part = strip_thinking_tags(decision.text_content or "").strip()
                    if text_part and question:
                        final_text = f"{text_part}\n\n{question}"
                    elif question:
                        final_text = question
                    else:
                        final_text = text_part or "（等待用户回复）"

                    state.transition(TaskStatus.WAITING_USER)

                    # ---- IM 模式：等待用户回复（超时 + 追问） ----
                    user_reply = await self._wait_for_user_reply(
                        final_text, state, timeout_seconds=60, max_reminders=1,
                    )

                    # 构建 tool_result 消息（其他工具结果 + ask_user 结果必须在同一条 user 消息中）
                    def _build_ask_user_tool_results(
                        ask_user_content: str,
                        _other_results: list[dict] = other_tool_results,
                        _ask_id: str = ask_tool_id,
                    ) -> list[dict]:
                        """构建包含所有 tool_result 的 user 消息 content"""
                        results = list(_other_results)  # 其他工具的 tool_result
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": _ask_id,
                            "content": ask_user_content,
                        })
                        return results

                    if user_reply:
                        # 用户在超时内回复了 → 注入回复，继续 ReAct 循环
                        logger.info(
                            f"[ReAct] Iter {iteration+1} — ask_user: user replied, resuming loop"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append({
                            "role": "user",
                            "content": _build_ask_user_tool_results(f"用户回复：{user_reply}"),
                        })
                        state.transition(TaskStatus.REASONING)
                        continue  # 继续 ReAct 循环

                    elif user_reply is None and self._state.current_session and (
                        self._state.current_session.get_metadata("_gateway")
                        if hasattr(self._state.current_session, "get_metadata")
                        else None
                    ):
                        # IM 模式，用户超时未回复 → 注入系统提示让 LLM 自行决策
                        logger.info(
                            f"[ReAct] Iter {iteration+1} — ask_user: user timeout, "
                            f"injecting auto-decide prompt"
                        )
                        react_trace.append(_iter_trace)
                        working_messages.append({
                            "role": "user",
                            "content": _build_ask_user_tool_results(
                                "[系统] 用户 2 分钟内未回复你的提问。"
                                "请自行决策：如果能合理推断用户意图，继续执行任务；"
                                "否则终止当前任务并告知用户你需要什么信息。"
                            ),
                        })
                        state.transition(TaskStatus.REASONING)
                        continue  # 继续 ReAct 循环，让 LLM 自行决策

                    else:
                        # CLI 模式或无 gateway → 直接返回问题文本
                        tracer.end_trace(metadata={
                            "result": "waiting_user",
                            "iterations": iteration + 1,
                            "tools_used": list(set(executed_tool_names)),
                        })
                        react_trace.append(_iter_trace)
                        self._save_react_trace(react_trace, conversation_id, session_type, "waiting_user", _trace_started_at)
                        logger.info(
                            f"[ReAct] === WAITING_USER (CLI) after {iteration+1} iterations ==="
                        )
                        return final_text

                # 保存检查点（在工具执行前）
                self._save_checkpoint(working_messages, state, decision, iteration)

                # 添加 assistant 消息
                working_messages.append({
                    "role": "assistant",
                    "content": decision.assistant_content,
                })

                # 检查取消
                if state.cancelled:
                    react_trace.append(_iter_trace)
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return "✅ 任务已停止。"

                # 执行工具
                tool_results, executed, receipts = await self._tool_executor.execute_batch(
                    decision.tool_calls,
                    state=state,
                    task_monitor=task_monitor,
                    allow_interrupt_checks=self._state.interrupt_enabled,
                    capture_delivery_receipts=True,
                )

                if executed:
                    tools_executed_in_task = True
                    executed_tool_names.extend(executed)
                    state.record_tool_execution(executed)

                    # 记录工具成功/失败状态
                    for i, tool_name in enumerate(executed):
                        result_content = ""
                        if i < len(tool_results):
                            r = tool_results[i]
                            result_content = str(r.get("content", "")) if isinstance(r, dict) else str(r)
                        is_error = any(m in result_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:"])
                        self._record_tool_result(tool_name, success=not is_error)

                if receipts:
                    delivery_receipts = receipts

                # ==================== OBSERVE 阶段 ====================
                logger.info(
                    f"[ReAct] Iter {iteration+1} — OBSERVE: "
                    f"{len(tool_results)} results from {executed or []}"
                )
                state.transition(TaskStatus.OBSERVING)

                # 收集工具结果到 trace
                _iter_trace["tool_results"] = [
                    {
                        "tool_use_id": tr.get("tool_use_id", ""),
                        "result_preview": str(tr.get("content", ""))[:1000],
                    }
                    for tr in tool_results
                    if isinstance(tr, dict)
                ]
                for tr in tool_results:
                    if isinstance(tr, dict):
                        t_id = tr.get("tool_use_id", "")
                        r_len = len(str(tr.get("content", "")))
                        logger.info(f"[ReAct] Iter {iteration+1} — tool_result id={t_id} len={r_len}")
                react_trace.append(_iter_trace)

                # 检查是否应该回滚
                should_rb, rb_reason = self._should_rollback(tool_results)
                if should_rb:
                    rollback_result = self._rollback(rb_reason)
                    if rollback_result:
                        working_messages, _ = rollback_result
                        logger.info("[Rollback] 回滚成功，将用不同方法重新推理")
                        continue

                if state.cancelled:
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    tracer.end_trace(metadata={"result": "cancelled", "iterations": iteration + 1})
                    return "✅ 任务已停止。"

                # 添加工具结果
                working_messages.append({
                    "role": "user",
                    "content": tool_results,
                })

                # 循环检测
                consecutive_tool_rounds += 1

                # stop_reason 检查
                if decision.stop_reason == "end_turn":
                    cleaned_text = strip_thinking_tags(decision.text_content)
                    if cleaned_text and cleaned_text.strip():
                        logger.info(f"[LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds")
                        self._save_react_trace(react_trace, conversation_id, session_type, "completed_end_turn", _trace_started_at)
                        state.transition(TaskStatus.COMPLETED)
                        tracer.end_trace(metadata={
                            "result": "completed_end_turn",
                            "iterations": iteration + 1,
                            "tools_used": list(set(executed_tool_names)),
                        })
                        return cleaned_text

                # 工具签名循环检测
                round_signatures = [_make_tool_signature(tc) for tc in decision.tool_calls]
                round_sig_str = "+".join(sorted(round_signatures))
                recent_tool_signatures.append(round_sig_str)
                if len(recent_tool_signatures) > tool_pattern_window:
                    recent_tool_signatures = recent_tool_signatures[-tool_pattern_window:]

                loop_result = self._detect_loops(
                    recent_tool_signatures,
                    consecutive_tool_rounds,
                    working_messages,
                    decision.text_content,
                    llm_self_check_interval,
                    extreme_safety_threshold,
                    conversation_id,
                )
                if loop_result == "terminate":
                    cleaned = strip_thinking_tags(decision.text_content)
                    self._save_react_trace(react_trace, conversation_id, session_type, "loop_terminated", _trace_started_at)
                    state.transition(TaskStatus.FAILED)
                    tracer.end_trace(metadata={"result": "loop_terminated", "iterations": iteration + 1})
                    return cleaned or "⚠️ 检测到工具调用陷入死循环，任务已自动终止。请重新描述您的需求。"
                if loop_result == "disable_force":
                    max_no_tool_retries = 0

        self._save_react_trace(react_trace, conversation_id, session_type, "max_iterations", _trace_started_at)
        state.transition(TaskStatus.FAILED)
        tracer.end_trace(metadata={"result": "max_iterations", "iterations": max_iterations})
        return "已达到最大工具调用次数，请重新描述您的需求。"

    # ==================== 流式输出 (SSE) ====================

    async def reason_stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        base_system_prompt: str = "",
        task_description: str = "",
        task_monitor: Any = None,
        session_type: str = "desktop",
        plan_mode: bool = False,
        endpoint_override: str | None = None,
        conversation_id: str | None = None,
    ):
        """
        流式推理循环，为 HTTP API (SSE) 设计。

        与 run() 保持特性对齐：TaskMonitor、循环检测、模型切换、
        LLM 错误重试、任务完成度验证、Rollback 等。

        调用方（如 Agent.chat_with_session_stream）需传入 tools 和 system_prompt，
        新增参数均 optional，向后兼容老的调用方式。

        Yields dict events:
        - {"type": "iteration_start", "iteration": N}
        - {"type": "context_compressed", "before_tokens": N, "after_tokens": M}
        - {"type": "thinking_start"} / {"type": "thinking_delta"} / {"type": "thinking_end"}
        - {"type": "text_delta", "content": "..."}
        - {"type": "tool_call_start"} / {"type": "tool_call_end"}
        - {"type": "plan_created"} / {"type": "plan_step_updated"}
        - {"type": "ask_user", "question": "..."}
        - {"type": "error", "message": "..."}
        - {"type": "done"}
        """
        tools = tools or []

        # 在 try 外初始化，避免 except/finally 块中 UnboundLocalError
        react_trace: list[dict] = []
        _trace_started_at = datetime.now().isoformat()
        _endpoint_switched = False

        # Task state
        state = self._state.current_task
        if not state or not state.is_active:
            state = self._state.begin_task()

        try:
            # === 动态 System Prompt（追加活跃 Plan） ===
            _base_sp = base_system_prompt or system_prompt

            def _build_effective_prompt() -> str:
                try:
                    from ..tools.handlers.plan import get_active_plan_prompt
                    prompt = _base_sp
                    if conversation_id:
                        plan_section = get_active_plan_prompt(conversation_id)
                        if plan_section:
                            prompt += f"\n\n{plan_section}\n"
                    return prompt
                except Exception:
                    return _base_sp

            effective_prompt = _build_effective_prompt()
            if plan_mode:
                effective_prompt += (
                    "\n\n[PLAN MODE] 用户请求 Plan 模式。"
                    "请先制定详细计划（使用 create_plan 工具），然后按计划执行。"
                )

            # === 端点覆盖 ===
            _endpoint_switched = False
            if endpoint_override:
                if not conversation_id:
                    conversation_id = f"_stream_{uuid.uuid4().hex[:12]}"
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client and hasattr(llm_client, "switch_model"):
                    ok, msg = llm_client.switch_model(
                        endpoint_name=endpoint_override,
                        hours=0.05,
                        reason=f"chat endpoint override: {endpoint_override}",
                        conversation_id=conversation_id,
                    )
                    if not ok:
                        yield {"type": "error", "message": f"端点切换失败: {msg}"}
                        yield {"type": "done"}
                        return
                    _endpoint_switched = True

            current_model = self._brain.model
            if _endpoint_switched and endpoint_override:
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client:
                    _provider = llm_client._providers.get(endpoint_override)
                    if _provider:
                        current_model = _provider.model

            # === 与 run() 一致的循环控制变量 ===
            max_iterations = settings.max_iterations
            working_messages = list(messages)

            # ForceToolCall 配置
            if session_type == "im":
                base_force_retries = 0
            else:
                base_force_retries = max(0, int(getattr(settings, "force_tool_call_max_retries", 1)))

            max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)
            max_verify_retries = 3
            max_confirmation_text_retries = 1

            executed_tool_names: list[str] = []
            delivery_receipts: list[dict] = []
            _last_browser_url = ""
            consecutive_tool_rounds = 0
            no_tool_call_count = 0
            verify_incomplete_count = 0
            no_confirmation_text_count = 0
            tools_executed_in_task = False

            # 循环检测
            recent_tool_signatures: list[str] = []
            tool_pattern_window = 8
            llm_self_check_interval = 10
            extreme_safety_threshold = 50

            def _make_tool_sig(tc: dict) -> str:
                nonlocal _last_browser_url
                name = tc.get("name", "")
                inp = tc.get("input", {})
                if name == "browser_navigate":
                    _last_browser_url = inp.get("url", "")
                try:
                    param_str = json.dumps(inp, sort_keys=True, ensure_ascii=False)
                except Exception:
                    param_str = str(inp)
                if name in self._browser_page_read_tools and len(param_str) <= 20 and _last_browser_url:
                    param_str = f"{param_str}|url={_last_browser_url}"
                param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
                return f"{name}({param_hash})"

            # ==================== 主循环 ====================
            logger.info(
                f"[ReAct-Stream] === Loop started (max_iterations={max_iterations}, model={current_model}) ==="
            )

            for _iteration in range(max_iterations):
                state.iteration = _iteration

                # --- 取消检查 ---
                if state.cancelled:
                    logger.info(f"[ReAct-Stream] Task cancelled at iteration start: {state.cancel_reason}")
                    self._save_react_trace(react_trace, conversation_id, session_type, "cancelled", _trace_started_at)
                    yield {"type": "text_delta", "content": "✅ 任务已停止。"}
                    yield {"type": "done"}
                    return

                # --- TaskMonitor: 迭代开始 + 模型切换检查 ---
                if task_monitor:
                    task_monitor.begin_iteration(_iteration + 1, current_model)
                    switch_result = self._check_model_switch(
                        task_monitor, state, working_messages, current_model
                    )
                    if switch_result:
                        current_model, working_messages = switch_result
                        no_tool_call_count = 0
                        tools_executed_in_task = False
                        verify_incomplete_count = 0
                        executed_tool_names = []
                        consecutive_tool_rounds = 0
                        recent_tool_signatures = []
                        no_confirmation_text_count = 0

                logger.info(
                    f"[ReAct-Stream] Iter {_iteration+1}/{max_iterations} — REASON (model={current_model})"
                )

                # --- 状态转换: REASONING（与 run() 一致） ---
                if state.status != TaskStatus.REASONING:
                    state.transition(TaskStatus.REASONING)

                # --- 上下文压缩（从第 2 轮迭代开始） ---
                _ctx_compressed_info: dict | None = None
                if _iteration > 0:
                    effective_prompt = _build_effective_prompt()  # 每轮刷新 Plan
                    _before_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                    working_messages = await self._context_manager.compress_if_needed(
                        working_messages,
                        system_prompt=effective_prompt,
                        tools=tools,
                    )
                    _after_tokens = self._context_manager.estimate_messages_tokens(working_messages)
                    if _after_tokens < _before_tokens:
                        _ctx_compressed_info = {
                            "before_tokens": _before_tokens,
                            "after_tokens": _after_tokens,
                        }
                        logger.info(
                            f"[ReAct-Stream] Context compressed: {_before_tokens} → {_after_tokens} tokens"
                        )
                        yield {
                            "type": "context_compressed",
                            "before_tokens": _before_tokens,
                            "after_tokens": _after_tokens,
                        }

                # --- 思维链: 迭代开始事件 ---
                yield {"type": "iteration_start", "iteration": _iteration + 1}

                # --- Reason phase ---
                _thinking_t0 = time.time()
                yield {"type": "thinking_start"}

                try:
                    decision = None
                    async for hb_event in self._reason_with_heartbeat(
                        working_messages,
                        system_prompt=effective_prompt,
                        tools=tools,
                        current_model=current_model,
                        conversation_id=conversation_id,
                    ):
                        if hb_event["type"] == "heartbeat":
                            yield {"type": "heartbeat"}
                        elif hb_event["type"] == "decision":
                            decision = hb_event["decision"]
                    if decision is None:
                        raise RuntimeError("_reason returned no decision")

                    if task_monitor:
                        task_monitor.reset_retry_count()

                except Exception as e:
                    # --- LLM Error Handling（与 run() 一致） ---
                    retry_result = self._handle_llm_error(
                        e, task_monitor, state, working_messages, current_model
                    )
                    _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                    yield {"type": "thinking_end", "duration_ms": _thinking_duration}

                    if retry_result == "retry":
                        await asyncio.sleep(2)
                        continue
                    elif isinstance(retry_result, tuple):
                        current_model, working_messages = retry_result
                        no_tool_call_count = 0
                        tools_executed_in_task = False
                        verify_incomplete_count = 0
                        executed_tool_names = []
                        consecutive_tool_rounds = 0
                        recent_tool_signatures = []
                        no_confirmation_text_count = 0
                        continue
                    else:
                        self._save_react_trace(
                            react_trace, conversation_id, session_type,
                            f"reason_error: {str(e)[:100]}", _trace_started_at,
                        )
                        yield {"type": "error", "message": f"推理失败: {str(e)[:300]}"}
                        yield {"type": "done"}
                        return

                # Emit thinking content
                _thinking_duration = int((time.time() - _thinking_t0) * 1000)
                _has_thinking = bool(decision.thinking_content)
                if _has_thinking:
                    yield {"type": "thinking_delta", "content": decision.thinking_content}
                yield {
                    "type": "thinking_end",
                    "duration_ms": _thinking_duration,
                    "has_thinking": _has_thinking,
                }

                if task_monitor:
                    task_monitor.end_iteration(decision.text_content or "")

                # -- 收集 ReAct trace --
                _raw = decision.raw_response
                _usage = getattr(_raw, "usage", None) if _raw else None
                _in_tokens = getattr(_usage, "input_tokens", 0) if _usage else 0
                _out_tokens = getattr(_usage, "output_tokens", 0) if _usage else 0
                _iter_trace: dict = {
                    "iteration": _iteration + 1,
                    "timestamp": datetime.now().isoformat(),
                    "decision_type": decision.type.value if hasattr(decision.type, "value") else str(decision.type),
                    "model": current_model,
                    "thinking": (decision.thinking_content or "")[:500] if decision.thinking_content else None,
                    "thinking_duration_ms": _thinking_duration,
                    "text": (decision.text_content or "")[:2000] if decision.text_content else None,
                    "tool_calls": [
                        {
                            "name": tc.get("name"),
                            "id": tc.get("id"),
                            "input_preview": str(tc.get("input", {}))[:500],
                        }
                        for tc in (decision.tool_calls or [])
                    ],
                    "tool_results": [],
                    "tokens": {"input": _in_tokens, "output": _out_tokens},
                    "context_compressed": _ctx_compressed_info,
                }
                tool_names_log = [tc.get("name", "?") for tc in (decision.tool_calls or [])]
                logger.info(
                    f"[ReAct-Stream] Iter {_iteration+1} — decision={_iter_trace['decision_type']}, "
                    f"tools={tool_names_log}, tokens_in={_in_tokens}, tokens_out={_out_tokens}"
                )

                # ==================== FINAL_ANSWER ====================
                if decision.type == DecisionType.FINAL_ANSWER:
                    consecutive_tool_rounds = 0

                    # 任务完成度验证（与 run() 一致）
                    result = await self._handle_final_answer(
                        decision=decision,
                        working_messages=working_messages,
                        original_messages=messages,
                        tools_executed_in_task=tools_executed_in_task,
                        executed_tool_names=executed_tool_names,
                        delivery_receipts=delivery_receipts,
                        no_tool_call_count=no_tool_call_count,
                        verify_incomplete_count=verify_incomplete_count,
                        no_confirmation_text_count=no_confirmation_text_count,
                        max_no_tool_retries=max_no_tool_retries,
                        max_verify_retries=max_verify_retries,
                        max_confirmation_text_retries=max_confirmation_text_retries,
                        base_force_retries=base_force_retries,
                        conversation_id=conversation_id,
                    )

                    if isinstance(result, str):
                        # 最终答案 → stream 给前端
                        react_trace.append(_iter_trace)
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "completed", _trace_started_at
                        )
                        try:
                            state.transition(TaskStatus.COMPLETED)
                        except ValueError:
                            state.status = TaskStatus.COMPLETED
                        logger.info(
                            f"[ReAct-Stream] === COMPLETED after {_iteration+1} iterations ==="
                        )
                        chunk_size = 20
                        for i in range(0, len(result), chunk_size):
                            yield {"type": "text_delta", "content": result[i:i + chunk_size]}
                            await asyncio.sleep(0.01)
                        yield {"type": "done"}
                        return
                    else:
                        # 验证不通过 → 继续循环
                        logger.info(
                            f"[ReAct-Stream] Iter {_iteration+1} — VERIFY: incomplete, continuing loop"
                        )
                        react_trace.append(_iter_trace)
                        try:
                            state.transition(TaskStatus.VERIFYING)
                        except ValueError:
                            state.status = TaskStatus.VERIFYING
                        (
                            working_messages,
                            no_tool_call_count,
                            verify_incomplete_count,
                            no_confirmation_text_count,
                            max_no_tool_retries,
                        ) = result
                        continue

                # ==================== TOOL_CALLS ====================
                elif decision.type == DecisionType.TOOL_CALLS and decision.tool_calls:
                    try:
                        state.transition(TaskStatus.ACTING)
                    except ValueError:
                        state.status = TaskStatus.ACTING

                    working_messages.append({
                        "role": "assistant",
                        "content": decision.assistant_content or [{"type": "text", "text": ""}],
                    })

                    # ---- ask_user 拦截 ----
                    ask_user_calls = [tc for tc in decision.tool_calls if tc.get("name") == "ask_user"]
                    other_tool_calls = [tc for tc in decision.tool_calls if tc.get("name") != "ask_user"]

                    if ask_user_calls:
                        # 先执行非 ask_user 工具
                        tool_results_for_msg: list[dict] = []
                        for tc in other_tool_calls:
                            t_name = tc.get("name", "unknown")
                            t_args = tc.get("input", tc.get("arguments", {}))
                            t_id = tc.get("id", str(uuid.uuid4()))
                            yield {"type": "tool_call_start", "tool": t_name, "args": t_args, "id": t_id}
                            try:
                                r = await self._tool_executor.execute_tool(
                                    tool_name=t_name,
                                    tool_input=t_args if isinstance(t_args, dict) else {},
                                    session_id=conversation_id,
                                )
                                r = str(r) if r else ""
                            except Exception as exc:
                                r = f"Tool error: {exc}"
                            yield {"type": "tool_call_end", "tool": t_name, "result": r[:2000], "id": t_id}
                            tool_results_for_msg.append({
                                "type": "tool_result", "tool_use_id": t_id, "content": r[:4000],
                            })

                        # ask_user 事件
                        ask_input = ask_user_calls[0].get("input", {})
                        ask_q = ask_input.get("question", "")
                        ask_options = ask_input.get("options")
                        ask_allow_multiple = ask_input.get("allow_multiple", False)
                        ask_questions = ask_input.get("questions")
                        text_part = decision.text_content or ""
                        question_text = f"{text_part}\n\n{ask_q}".strip() if text_part else ask_q
                        event: dict = {
                            "type": "ask_user",
                            "question": question_text,
                            "conversation_id": conversation_id,
                        }
                        if ask_options and isinstance(ask_options, list):
                            event["options"] = [
                                {"id": str(o.get("id", "")), "label": str(o.get("label", ""))}
                                for o in ask_options
                                if isinstance(o, dict) and o.get("id") and o.get("label")
                            ]
                        if ask_allow_multiple:
                            event["allow_multiple"] = True
                        if ask_questions and isinstance(ask_questions, list):
                            parsed_questions = []
                            for q in ask_questions:
                                if not isinstance(q, dict) or not q.get("id") or not q.get("prompt"):
                                    continue
                                pq: dict = {"id": str(q["id"]), "prompt": str(q["prompt"])}
                                q_options = q.get("options")
                                if q_options and isinstance(q_options, list):
                                    pq["options"] = [
                                        {"id": str(o.get("id", "")), "label": str(o.get("label", ""))}
                                        for o in q_options
                                        if isinstance(o, dict) and o.get("id") and o.get("label")
                                    ]
                                if q.get("allow_multiple"):
                                    pq["allow_multiple"] = True
                                parsed_questions.append(pq)
                            if parsed_questions:
                                event["questions"] = parsed_questions
                        yield event
                        react_trace.append(_iter_trace)
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "ask_user", _trace_started_at
                        )
                        yield {"type": "done"}
                        return

                    # ---- 正常工具执行 ----
                    tool_results_for_msg: list[dict] = []
                    for tc in decision.tool_calls:
                        tool_name = tc.get("name", "unknown")
                        tool_args = tc.get("input", tc.get("arguments", {}))
                        tool_id = tc.get("id", str(uuid.uuid4()))

                        yield {"type": "tool_call_start", "tool": tool_name, "args": tool_args, "id": tool_id}

                        try:
                            result_text = await self._tool_executor.execute_tool(
                                tool_name=tool_name,
                                tool_input=tool_args if isinstance(tool_args, dict) else {},
                                session_id=conversation_id,
                            )
                            result_text = str(result_text) if result_text else ""
                        except Exception as exc:
                            result_text = f"Tool error: {exc}"

                        yield {"type": "tool_call_end", "tool": tool_name, "result": result_text[:2000], "id": tool_id}

                        # deliver_artifacts 回执收集（与 run() 一致）
                        if tool_name == "deliver_artifacts" and result_text:
                            try:
                                _receipts_data = json.loads(result_text)
                                if isinstance(_receipts_data, dict) and "receipts" in _receipts_data:
                                    delivery_receipts = _receipts_data["receipts"]
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Plan 事件
                        if tool_name == "create_plan" and isinstance(tool_args, dict):
                            yield {"type": "plan_created", "plan": {
                                "id": str(uuid.uuid4()),
                                "taskSummary": tool_args.get("task_summary", ""),
                                "steps": [{"description": s, "status": "pending"} for s in tool_args.get("steps", [])],
                                "status": "in_progress",
                            }}
                        elif tool_name == "update_plan_step" and isinstance(tool_args, dict):
                            yield {"type": "plan_step_updated", "stepIdx": tool_args.get("step_index", 0), "status": tool_args.get("status", "completed")}

                        tool_results_for_msg.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result_text[:4000],
                        })

                    if decision.tool_calls:
                        tools_executed_in_task = True
                        _executed = [tc.get("name", "") for tc in decision.tool_calls]
                        executed_tool_names.extend(_executed)
                        state.record_tool_execution(_executed)

                        # 记录工具成功/失败状态（与 run() 一致）
                        for i, t_name in enumerate(_executed):
                            r_content = ""
                            if i < len(tool_results_for_msg):
                                r_content = str(tool_results_for_msg[i].get("content", ""))
                            is_error = any(m in r_content for m in ["❌", "⚠️ 工具执行错误", "错误类型:"])
                            self._record_tool_result(t_name, success=not is_error)

                    # 收集工具结果到 trace
                    _iter_trace["tool_results"] = [
                        {
                            "tool_use_id": tr.get("tool_use_id", ""),
                            "result_preview": str(tr.get("content", ""))[:1000],
                        }
                        for tr in tool_results_for_msg
                    ]
                    react_trace.append(_iter_trace)

                    try:
                        state.transition(TaskStatus.OBSERVING)
                    except ValueError:
                        state.status = TaskStatus.OBSERVING

                    # --- Rollback 检查（与 run() 一致） ---
                    should_rb, rb_reason = self._should_rollback(tool_results_for_msg)
                    if should_rb:
                        rollback_result = self._rollback(rb_reason)
                        if rollback_result:
                            working_messages, _ = rollback_result
                            logger.info("[ReAct-Stream][Rollback] 回滚成功，将用不同方法重新推理")
                            continue

                    # 取消检查
                    if state.cancelled:
                        self._save_react_trace(
                            react_trace, conversation_id, session_type, "cancelled", _trace_started_at
                        )
                        yield {"type": "text_delta", "content": "✅ 任务已停止。"}
                        yield {"type": "done"}
                        return

                    working_messages.append({
                        "role": "user",
                        "content": tool_results_for_msg,
                    })

                    # --- 循环检测（与 run() 一致） ---
                    consecutive_tool_rounds += 1

                    # stop_reason 检查
                    if decision.stop_reason == "end_turn":
                        cleaned_text = strip_thinking_tags(decision.text_content)
                        if cleaned_text and cleaned_text.strip():
                            logger.info(
                                f"[ReAct-Stream][LoopGuard] stop_reason=end_turn after {consecutive_tool_rounds} rounds"
                            )
                            self._save_react_trace(
                                react_trace, conversation_id, session_type,
                                "completed_end_turn", _trace_started_at,
                            )
                            chunk_size = 20
                            for i in range(0, len(cleaned_text), chunk_size):
                                yield {"type": "text_delta", "content": cleaned_text[i:i + chunk_size]}
                                await asyncio.sleep(0.01)
                            yield {"type": "done"}
                            return

                    # 工具签名循环检测
                    round_signatures = [_make_tool_sig(tc) for tc in decision.tool_calls]
                    round_sig_str = "+".join(sorted(round_signatures))
                    recent_tool_signatures.append(round_sig_str)
                    if len(recent_tool_signatures) > tool_pattern_window:
                        recent_tool_signatures = recent_tool_signatures[-tool_pattern_window:]

                    loop_result = self._detect_loops(
                        recent_tool_signatures,
                        consecutive_tool_rounds,
                        working_messages,
                        decision.text_content,
                        llm_self_check_interval,
                        extreme_safety_threshold,
                        conversation_id,
                    )
                    if loop_result == "terminate":
                        cleaned = strip_thinking_tags(decision.text_content)
                        self._save_react_trace(
                            react_trace, conversation_id, session_type,
                            "loop_terminated", _trace_started_at,
                        )
                        try:
                            state.transition(TaskStatus.FAILED)
                        except ValueError:
                            state.status = TaskStatus.FAILED
                        msg = cleaned or "⚠️ 检测到工具调用陷入死循环，任务已自动终止。请重新描述您的需求。"
                        yield {"type": "text_delta", "content": msg}
                        yield {"type": "done"}
                        return
                    if loop_result == "disable_force":
                        max_no_tool_retries = 0

                    continue  # Next iteration

            # max_iterations
            self._save_react_trace(
                react_trace, conversation_id, session_type, "max_iterations", _trace_started_at
            )
            try:
                state.transition(TaskStatus.FAILED)
            except ValueError:
                state.status = TaskStatus.FAILED
            logger.info(f"[ReAct-Stream] === MAX_ITERATIONS reached ({max_iterations}) ===")
            yield {"type": "text_delta", "content": "\n\n（已达到最大迭代次数）"}
            yield {"type": "done"}

        except Exception as e:
            logger.error(f"reason_stream error: {e}", exc_info=True)
            self._save_react_trace(
                react_trace, conversation_id, session_type,
                f"error: {str(e)[:100]}", _trace_started_at,
            )
            yield {"type": "error", "message": str(e)[:500]}
            yield {"type": "done"}

        finally:
            # 清理 per-conversation endpoint override
            if _endpoint_switched and conversation_id:
                llm_client = getattr(self._brain, "_llm_client", None)
                if llm_client and hasattr(llm_client, "restore_default"):
                    try:
                        llm_client.restore_default(conversation_id=conversation_id)
                    except Exception:
                        pass

    # ==================== ReAct 推理链保存 ====================

    def _save_react_trace(
        self,
        react_trace: list[dict],
        conversation_id: str | None,
        session_type: str,
        result: str,
        started_at: str,
    ) -> None:
        """
        保存完整的 ReAct 推理链到文件。

        同时暂存到 self._last_react_trace 供 agent_handler 读取（思维链功能）。

        路径: data/react_traces/{date}/trace_{conversation_id}_{timestamp}.json
        """
        # 思维链: 暂存 trace 供外部读取（即使为空也更新，清除旧数据）
        self._last_react_trace = react_trace or []

        if not react_trace:
            return

        try:
            date_str = datetime.now().strftime("%Y%m%d")
            trace_dir = Path("data/react_traces") / date_str
            trace_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%H%M%S")
            cid_part = (conversation_id or "unknown")[:16]
            trace_file = trace_dir / f"trace_{cid_part}_{timestamp}.json"

            # 汇总统计
            total_in = sum(it.get("tokens", {}).get("input", 0) for it in react_trace)
            total_out = sum(it.get("tokens", {}).get("output", 0) for it in react_trace)
            all_tools = []
            for it in react_trace:
                for tc in it.get("tool_calls", []):
                    name = tc.get("name")
                    if name and name not in all_tools:
                        all_tools.append(name)

            trace_data = {
                "conversation_id": conversation_id or "",
                "session_type": session_type,
                "model": react_trace[0].get("model", "") if react_trace else "",
                "started_at": started_at,
                "ended_at": datetime.now().isoformat(),
                "total_iterations": len(react_trace),
                "total_tokens": {"input": total_in, "output": total_out},
                "tools_used": all_tools,
                "result": result,
                "iterations": react_trace,
            }

            with open(trace_file, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(
                f"[ReAct] Trace saved: {trace_file} "
                f"(iterations={len(react_trace)}, tools={all_tools}, "
                f"tokens_in={total_in}, tokens_out={total_out})"
            )

            # 清理超过 7 天的旧 trace 文件
            self._cleanup_old_traces(Path("data/react_traces"), max_age_days=7)

        except Exception as e:
            logger.warning(f"[ReAct] Failed to save trace: {e}")

    def _cleanup_old_traces(self, base_dir: Path, max_age_days: int = 7) -> None:
        """清理超过指定天数的旧 trace 日期目录"""
        try:
            if not base_dir.exists():
                return
            cutoff = time.time() - max_age_days * 86400
            for date_dir in base_dir.iterdir():
                if date_dir.is_dir() and date_dir.stat().st_mtime < cutoff:
                    import shutil
                    shutil.rmtree(date_dir, ignore_errors=True)
        except Exception:
            pass

    # ==================== 心跳保活 ====================

    _HEARTBEAT_INTERVAL = 15  # 秒：LLM 等待期间心跳间隔

    async def _reason_with_heartbeat(
        self,
        messages: list[dict],
        *,
        system_prompt: str,
        tools: list[dict],
        current_model: str,
        conversation_id: str | None = None,
    ):
        """
        包装 _reason()，在等待 LLM 响应期间每隔 HEARTBEAT_INTERVAL 秒
        产出 heartbeat 事件，防止前端 SSE idle timeout。

        Yields:
            {"type": "heartbeat"} 或 {"type": "decision", "decision": Decision}
        """
        queue: asyncio.Queue = asyncio.Queue()

        async def _do_reason():
            try:
                decision = await self._reason(
                    messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    current_model=current_model,
                    conversation_id=conversation_id,
                )
                await queue.put(("result", decision))
            except Exception as exc:
                await queue.put(("error", exc))

        async def _heartbeat_loop():
            try:
                while True:
                    await asyncio.sleep(self._HEARTBEAT_INTERVAL)
                    await queue.put(("heartbeat", None))
            except asyncio.CancelledError:
                pass

        reason_task = asyncio.create_task(_do_reason())
        hb_task = asyncio.create_task(_heartbeat_loop())

        try:
            while True:
                typ, data = await queue.get()
                if typ == "heartbeat":
                    yield {"type": "heartbeat"}
                elif typ == "error":
                    raise data  # 传播 _reason 的异常
                else:
                    yield {"type": "decision", "decision": data}
                    break
        finally:
            hb_task.cancel()
            if not reason_task.done():
                reason_task.cancel()
                try:
                    await reason_task
                except (asyncio.CancelledError, Exception):
                    pass

    # ==================== 推理阶段 ====================

    async def _reason(
        self,
        messages: list[dict],
        *,
        system_prompt: str,
        tools: list[dict],
        current_model: str,
        conversation_id: str | None = None,
    ) -> Decision:
        """
        推理阶段: 调用 LLM，返回结构化 Decision。
        """
        tracer = get_tracer()
        with tracer.llm_span(model=current_model) as span:
            response = await asyncio.to_thread(
                self._brain.messages_create,
                model=current_model,
                max_tokens=self._brain.max_tokens,
                system=system_prompt,
                tools=tools,
                messages=messages,
                conversation_id=conversation_id,
            )

            # 记录 token 使用
            if hasattr(response, "usage"):
                span.set_attribute("input_tokens", getattr(response.usage, "input_tokens", 0))
                span.set_attribute("output_tokens", getattr(response.usage, "output_tokens", 0))

            decision = self._parse_decision(response)
            span.set_attribute("decision_type", decision.type.value)
            span.set_attribute("tool_count", len(decision.tool_calls))
            return decision

    def _parse_decision(self, response: Any) -> Decision:
        """解析 LLM 响应为 Decision"""
        tool_calls = []
        text_content = ""
        thinking_content = ""
        assistant_content = []

        for block in response.content:
            if block.type == "thinking":
                thinking_text = block.thinking if hasattr(block, "thinking") else str(block)
                thinking_content += thinking_text if isinstance(thinking_text, str) else str(thinking_text)
                assistant_content.append({
                    "type": "thinking",
                    "thinking": thinking_text,
                })
            elif block.type == "text":
                text_content += block.text
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        decision_type = DecisionType.TOOL_CALLS if tool_calls else DecisionType.FINAL_ANSWER

        return Decision(
            type=decision_type,
            text_content=text_content,
            tool_calls=tool_calls,
            thinking_content=thinking_content,
            raw_response=response,
            stop_reason=getattr(response, "stop_reason", ""),
            assistant_content=assistant_content,
        )

    # ==================== 最终答案处理 ====================

    async def _handle_final_answer(
        self,
        *,
        decision: Decision,
        working_messages: list[dict],
        original_messages: list[dict],
        tools_executed_in_task: bool,
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
        no_tool_call_count: int,
        verify_incomplete_count: int,
        no_confirmation_text_count: int,
        max_no_tool_retries: int,
        max_verify_retries: int,
        max_confirmation_text_retries: int,
        base_force_retries: int,
        conversation_id: str | None,
    ) -> str | tuple:
        """
        处理纯文本响应（无工具调用）。

        Returns:
            str: 最终答案
            tuple: (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries) - 需要继续循环
        """
        if tools_executed_in_task:
            cleaned_text = strip_thinking_tags(decision.text_content)
            if cleaned_text and len(cleaned_text.strip()) > 0:
                # 任务完成度验证
                is_completed = await self._response_handler.verify_task_completion(
                    user_request=ResponseHandler.get_last_user_request(original_messages),
                    assistant_response=cleaned_text,
                    executed_tools=executed_tool_names,
                    delivery_receipts=delivery_receipts,
                    conversation_id=conversation_id,
                )

                if is_completed:
                    return cleaned_text

                verify_incomplete_count += 1

                # 检查活跃 Plan
                has_plan_pending = self._has_active_plan_pending(conversation_id)
                effective_max = max_verify_retries * 2 if has_plan_pending else max_verify_retries

                if verify_incomplete_count >= effective_max:
                    return cleaned_text

                # 继续循环
                working_messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": decision.text_content}],
                })

                if has_plan_pending:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 当前 Plan 仍有未完成的步骤。"
                            "请立即继续执行下一个 pending 步骤。"
                        ),
                    })
                else:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 根据复核判断，用户请求可能还有未完成的部分。"
                            "如果你认为已经完成，请直接给用户一个总结回复；"
                            "如果确实还有剩余步骤，请继续执行。"
                        ),
                    })
                return (working_messages, no_tool_call_count, verify_incomplete_count,
                        no_confirmation_text_count, max_no_tool_retries)
            else:
                # 无可见文本
                no_confirmation_text_count += 1
                if no_confirmation_text_count <= max_confirmation_text_retries:
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统] 你已执行过工具，但你刚才没有输出任何用户可见的文字确认。"
                            "请基于已产生的 tool_result 证据，给出最终答复。"
                        ),
                    })
                    return (working_messages, no_tool_call_count, verify_incomplete_count,
                            no_confirmation_text_count, max_no_tool_retries)

                return (
                    "⚠️ 大模型返回异常：工具已执行，但多次未返回任何可见文本确认，任务已中断。"
                    "请重试、或切换到更稳定的端点/模型后再继续。"
                )

        # 未执行过工具
        max_no_tool_retries = self._effective_force_retries(base_force_retries, conversation_id)
        no_tool_call_count += 1

        if no_tool_call_count <= max_no_tool_retries:
            if decision.text_content:
                working_messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": decision.text_content}],
                })
            working_messages.append({
                "role": "user",
                "content": "[系统] 若确实需要工具，请调用相应工具；若不需要工具，请直接回答。",
            })
            return (working_messages, no_tool_call_count, verify_incomplete_count,
                    no_confirmation_text_count, max_no_tool_retries)

        # 追问次数用尽
        cleaned_text = clean_llm_response(decision.text_content)
        return cleaned_text or (
            "⚠️ 大模型返回异常：未产生可用输出。任务已中断。"
            "请重试、或更换端点/模型后再执行。"
        )

    # ==================== 循环检测 ====================

    def _detect_loops(
        self,
        recent_signatures: list[str],
        consecutive_rounds: int,
        working_messages: list[dict],
        text_content: str,
        self_check_interval: int,
        extreme_threshold: int,
        conversation_id: str | None,
    ) -> str | None:
        """
        循环检测。

        Returns:
            "terminate" - 终止循环
            "disable_force" - 禁用 ForceToolCall
            None - 继续
        """
        # 签名重复检测
        if len(recent_signatures) >= 3:
            from collections import Counter
            sig_counts = Counter(recent_signatures)
            most_common_sig, most_common_count = sig_counts.most_common(1)[0]

            if most_common_count >= 3:
                logger.warning(
                    f"[LoopGuard] True loop: '{most_common_sig}' repeated {most_common_count} times"
                )
                working_messages.append({
                    "role": "user",
                    "content": (
                        "[系统提示] 你在最近几轮中用完全相同的参数重复调用了同一个工具。"
                        "请评估：1. 任务已完成则停止调用。2. 遇到困难则换方法。"
                    ),
                })

                if most_common_count >= 5:
                    logger.error(f"[LoopGuard] Dead loop ({most_common_count} repeats). Terminating.")
                    return "terminate"

        # 定期 LLM 自检
        if consecutive_rounds > 0 and consecutive_rounds % self_check_interval == 0:
            has_plan = self._has_active_plan_pending(conversation_id)
            if has_plan:
                working_messages.append({
                    "role": "user",
                    "content": (
                        f"[系统提示] 已连续执行 {consecutive_rounds} 轮，Plan 仍有未完成步骤。"
                        "如果遇到困难，请换一种方法继续推进。"
                    ),
                })
            else:
                working_messages.append({
                    "role": "user",
                    "content": (
                        f"[系统提示] 你已连续执行了 {consecutive_rounds} 轮工具调用。请自我评估：\n"
                        "1. 当前任务进度如何？\n"
                        "2. 是否陷入了循环？\n"
                        "3. 如果任务已完成，请停止工具调用，直接回复用户。"
                    ),
                })

        # 极端安全阈值
        if consecutive_rounds == extreme_threshold:
            logger.warning(f"[LoopGuard] Extreme safety threshold ({extreme_threshold})")
            working_messages.append({
                "role": "user",
                "content": (
                    f"[系统提示] 当前任务已连续执行了 {extreme_threshold} 轮。"
                    "请向用户汇报进度并询问是否继续。"
                ),
            })
            return "disable_force"

        return None

    # ==================== 模型切换 ====================

    def _check_model_switch(
        self,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> tuple[str, list[dict]] | None:
        """检查是否需要模型切换。返回 (new_model, new_messages) 或 None"""
        if not task_monitor or not task_monitor.should_switch_model:
            return None

        new_model = task_monitor.fallback_model
        self._switch_llm_endpoint(new_model, reason="task_monitor timeout fallback")
        task_monitor.switch_model(
            new_model,
            "任务超时后切换",
            reset_context=True,
        )

        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            current = llm_client.get_current_model() if llm_client else None
            new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append({
            "role": "user",
            "content": (
                "[系统提示] 发生模型切换：之前的 tool_use/tool_result 历史已清除。"
                "请从头开始处理用户请求。"
            ),
        })

        # 注意：_check_model_switch 不做状态转换，因为它不使用 continue，
        # 执行后自然走到主循环的 REASONING 转换逻辑。
        state.reset_for_model_switch()
        return new_model, new_messages

    def _handle_llm_error(
        self,
        error: Exception,
        task_monitor: Any,
        state: TaskState,
        working_messages: list[dict],
        current_model: str,
    ) -> str | tuple | None:
        """
        处理 LLM 调用错误。

        Returns:
            "retry" - 重试
            (new_model, new_messages) - 切换模型
            None - 重新抛出
        """
        if not task_monitor:
            return None

        should_retry = task_monitor.record_error(str(error))

        if should_retry:
            logger.info(f"[LLM] Will retry (attempt {task_monitor.retry_count})")
            return "retry"

        # 切换模型
        new_model = task_monitor.fallback_model
        self._switch_llm_endpoint(new_model, reason=f"LLM error fallback: {error}")
        task_monitor.switch_model(new_model, "LLM 调用失败后切换", reset_context=True)

        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            current = llm_client.get_current_model() if llm_client else None
            new_model = current.model if current else new_model
        except Exception:
            pass

        new_messages = list(state.original_user_messages)
        new_messages.append({
            "role": "user",
            "content": (
                "[系统提示] 发生模型切换：之前的历史已清除。"
                "请从头开始处理用户请求。"
            ),
        })

        state.transition(TaskStatus.MODEL_SWITCHING)
        state.reset_for_model_switch()
        return new_model, new_messages

    def _switch_llm_endpoint(self, model_or_endpoint: str, reason: str = "") -> bool:
        """执行模型切换"""
        llm_client = getattr(self._brain, "_llm_client", None)
        if not llm_client:
            return False

        endpoint_name = self._resolve_endpoint_name(model_or_endpoint)
        if not endpoint_name:
            return False

        ok, msg = llm_client.switch_model(
            endpoint_name=endpoint_name,
            hours=0.05,
            reason=reason,
        )
        if not ok:
            return False

        try:
            current = llm_client.get_current_model()
            if current and current.model:
                self._brain.model = current.model
        except Exception:
            pass

        logger.info(f"[ModelSwitch] {msg}")
        return True

    def _resolve_endpoint_name(self, model_or_endpoint: str) -> str | None:
        """解析 endpoint 名称"""
        try:
            llm_client = getattr(self._brain, "_llm_client", None)
            if not llm_client:
                return None
            available = [m.name for m in llm_client.list_available_models()]
            if model_or_endpoint in available:
                return model_or_endpoint
            for m in llm_client.list_available_models():
                if m.model == model_or_endpoint:
                    return m.name
            return None
        except Exception:
            return None

    # ==================== 辅助方法 ====================

    @staticmethod
    def _is_human_user_message(msg: dict) -> bool:
        """判断是否为人类用户消息（排除 tool_result）"""
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            part_types = {
                part.get("type")
                for part in content
                if isinstance(part, dict) and part.get("type")
            }
            return "tool_result" not in part_types
        return False

    @staticmethod
    def _effective_force_retries(base_retries: int, conversation_id: str | None) -> int:
        """计算有效 ForceToolCall 重试次数"""
        retries = base_retries
        try:
            from ..tools.handlers.plan import has_active_plan, is_plan_required
            if conversation_id and (has_active_plan(conversation_id) or is_plan_required(conversation_id)):
                retries = max(retries, 1)
        except Exception:
            pass
        return max(0, int(retries))

    @staticmethod
    def _has_active_plan_pending(conversation_id: str | None) -> bool:
        """检查是否有活跃 Plan 且有未完成步骤"""
        try:
            from ..tools.handlers.plan import get_plan_handler_for_session, has_active_plan
            if conversation_id and has_active_plan(conversation_id):
                handler = get_plan_handler_for_session(conversation_id)
                if handler and handler.current_plan:
                    steps = handler.current_plan.get("steps", [])
                    pending = [s for s in steps if s.get("status") in ("pending", "in_progress")]
                    return bool(pending)
        except Exception:
            pass
        return False
