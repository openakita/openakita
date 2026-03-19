"""
响应处理器

从 agent.py 提取的响应处理逻辑，负责:
- LLM 响应文本清理（思考标签、模拟工具调用）
- 任务完成度验证
- 任务复盘分析
- 辅助判断函数
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ==================== 文本清理函数 ====================


def strip_thinking_tags(text: str) -> str:
    """
    移除响应中的内部标签内容。

    需要清理的标签包括：
    - <thinking>...</thinking> - Claude extended thinking
    - <think>...</think> - MiniMax/Qwen thinking 格式
    - <minimax:tool_call>...</minimax:tool_call>
    - <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> - Kimi K2
    - </thinking> - 残留的闭合标签
    """
    if not text:
        return text

    cleaned = text

    cleaned = re.sub(r"<thinking>.*?</thinking>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*?</think>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(
        r"<minimax:tool_call>.*?</minimax:tool_call>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>\s*", "", cleaned, flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<invoke\s+[^>]*>.*?</invoke>\s*", "", cleaned, flags=re.DOTALL | re.IGNORECASE,
    )

    # 移除残留的闭合标签
    cleaned = re.sub(r"</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</think>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</minimax:tool_call>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<<\|tool_calls_section_begin\|>>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\?xml[^>]*\?>\s*", "", cleaned)

    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    移除 LLM 在文本中模拟工具调用的内容。

    当使用不支持原生工具调用的备用模型时，LLM 可能在文本中
    "模拟"工具调用。
    """
    if not text:
        return text

    pattern1 = r"^\.?[a-z_]+\s*\(.*\)\s*$"
    pattern2 = r"^[a-z_]+:\d+[\{\(].*[\}\)]\s*$"
    pattern3 = r'^\{["\']?(tool|function|name)["\']?\s*:'
    # 裸工具名：含下划线的标识符独占一行，如 "browser_open"、"web_search"
    # 部分模型不格式化工具调用，仅输出函数名
    pattern4 = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"

    lines = text.split("\n")
    cleaned_lines = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            continue

        if in_code_block:
            cleaned_lines.append(line)
            continue

        is_tool_sim = (
            re.match(pattern1, stripped, re.IGNORECASE)
            or re.match(pattern2, stripped, re.IGNORECASE)
            or re.match(pattern3, stripped, re.IGNORECASE)
            or re.match(pattern4, stripped)
        )
        if not is_tool_sim:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def clean_llm_response(text: str) -> str:
    """
    清理 LLM 响应文本。

    依次应用:
    1. strip_thinking_tags - 移除思考标签
    2. strip_tool_simulation_text - 移除模拟工具调用
    3. strip_intent_tag - 移除意图声明标记
    """
    if not text:
        return text

    cleaned = strip_thinking_tags(text)
    cleaned = strip_tool_simulation_text(cleaned)
    _, cleaned = parse_intent_tag(cleaned)

    return cleaned.strip()


# ==================== 意图声明解析 ====================

_INTENT_TAG_RE = re.compile(r"^\s*\[(ACTION|REPLY)\]\s*\n?", re.IGNORECASE)


def parse_intent_tag(text: str) -> tuple[str | None, str]:
    """
    解析并剥离响应文本开头的意图声明标记。

    模型在纯文本回复时应在第一行声明 [ACTION] 或 [REPLY]：
    - [ACTION]: 声明需要调用工具（若实际未调用则为幻觉）
    - [REPLY]: 声明纯对话回复，不需要工具

    Returns:
        (intent, stripped_text):
        - intent: "ACTION" / "REPLY" / None（无标记）
        - stripped_text: 移除标记后的文本
    """
    if not text:
        return None, text or ""
    m = _INTENT_TAG_RE.match(text)
    if m:
        return m.group(1).upper(), text[m.end():]
    return None, text


class ResponseHandler:
    """
    响应处理器。

    负责 LLM 响应的后处理，包括任务完成度验证和复盘分析。
    """

    def __init__(self, brain: Any, memory_manager: Any = None) -> None:
        """
        Args:
            brain: Brain 实例，用于 LLM 调用
            memory_manager: MemoryManager 实例（可选，用于保存复盘结果）
        """
        self._brain = brain
        self._memory_manager = memory_manager

    async def verify_task_completion(
        self,
        user_request: str,
        assistant_response: str,
        executed_tools: list[str],
        delivery_receipts: list[dict] | None = None,
        conversation_id: str | None = None,
    ) -> bool:
        """
        任务完成度复核。

        让 LLM 判断当前响应是否真正完成了用户的意图。

        Args:
            user_request: 用户原始请求
            assistant_response: 助手当前响应
            executed_tools: 已执行的工具列表
            delivery_receipts: 交付回执
            conversation_id: 对话 ID（用于 Plan 检查）

        Returns:
            True 如果任务已完成
        """
        delivery_receipts = delivery_receipts or []

        # === Deterministic Validation (Agent Harness) ===
        try:
            from .validators import ValidationContext, ValidationResult, create_default_registry

            val_context = ValidationContext(
                user_request=user_request,
                assistant_response=assistant_response,
                executed_tools=executed_tools or [],
                delivery_receipts=delivery_receipts,
                conversation_id=conversation_id or "",
            )
            registry = create_default_registry()
            report = registry.run_all(val_context)

            if report.applicable_count > 0:
                for output in report.outputs:
                    if output.result == ValidationResult.PASS and output.name in (
                        "ArtifactValidator", "CompletePlanValidator",
                    ):
                        logger.info(f"[TaskVerify] Deterministic PASS: {output.name} — {output.reason}")
                        return True

                for output in report.outputs:
                    if output.result == ValidationResult.FAIL and output.name == "PlanValidator":
                        logger.info(f"[TaskVerify] Deterministic FAIL: {output.name} — {output.reason}")
                        return False

                for output in report.outputs:
                    if output.result == ValidationResult.FAIL and output.name == "ArtifactValidator":
                        logger.warning(
                            f"[TaskVerify] ArtifactValidator FAIL but treating as PASS "
                            f"(delivery failure is infra issue, not agent fault): {output.reason}"
                        )
                        return True
        except Exception as e:
            logger.debug(f"[TaskVerify] Deterministic validation skipped: {e}")

        # 宣称已交付但无证据
        if any(
            k in (assistant_response or "") for k in ("已发送", "已交付", "已发给你", "已发给您")
        ) and not delivery_receipts and "deliver_artifacts" not in (executed_tools or []):
            logger.info("[TaskVerify] delivery claim without receipts, INCOMPLETE")
            return False

        # LLM 判断
        from .tool_executor import smart_truncate
        user_display, _ = smart_truncate(user_request, 3000, save_full=False, label="verify_user")
        response_display, _ = smart_truncate(assistant_response, 8000, save_full=False, label="verify_response")

        verify_prompt = f"""请判断以下交互是否已经**完成**用户的意图。

## 用户消息
{user_display}

## 助手响应
{response_display}

## 已执行的工具
{", ".join(executed_tools) if executed_tools else "无"}

## 附件交付回执（如有）
{delivery_receipts if delivery_receipts else "无"}

## 判断标准

### 非任务类消息（直接判 COMPLETED）
- 如果用户消息是**闲聊/问候**，助手已礼貌回复 → **COMPLETED**
- 如果用户消息是**简单确认/反馈**，助手已简短回应 → **COMPLETED**
- 如果用户消息是**简单问答**，助手已给出回答 → **COMPLETED**

### 任务类消息
- 如果已执行 write_file 工具，说明文件已保存，保存任务完成
- 工具执行成功即表示该操作完成
- 如果响应只是说"现在开始..."且没有工具执行，任务还在进行中
- 如果响应包含明确的操作确认，任务完成

### 上游平台/系统限制（需谨慎区分）
- 如果助手**已实际尝试**执行任务，但遇到**上游平台或 API 本身不支持**的硬性限制（例如：某 IM 平台的 API 根本不提供某功能、目标服务返回明确的"功能未开放"错误），且助手已向用户**解释了原因** → **COMPLETED**（这种情况下重试毫无意义）
- 但如果只是**某一条执行路径失败**（如文件不存在、权限不足、某个命令报错），助手还有其他可尝试的替代方案 → **INCOMPLETE**（应继续尝试）
- 关键判断：问题是**不可绕过的平台级限制**还是**可以换个方式解决的执行问题**？前者完成，后者继续

## 回答要求
STATUS: COMPLETED 或 INCOMPLETE
EVIDENCE: 完成的证据
MISSING: 缺失的内容
NEXT: 建议的下一步"""

        try:
            response = await self._brain.think(
                prompt=verify_prompt,
                system="你是一个任务完成度判断助手。请分析任务是否完成，并说明证据和缺失项。",
            )

            result = response.content.strip().upper() if response.content else ""
            is_completed = "STATUS: COMPLETED" in result or (
                "COMPLETED" in result and "INCOMPLETE" not in result
            )

            logger.info(
                f"[TaskVerify] request={user_request[:50]}... result={'COMPLETED' if is_completed else 'INCOMPLETE'}"
            )

            # Decision Trace: 记录验证决策
            try:
                from ..tracing.tracer import get_tracer
                tracer = get_tracer()
                tracer.record_decision(
                    decision_type="task_verification",
                    reasoning=f"tools={executed_tools}, receipts={len(delivery_receipts)}",
                    outcome="completed" if is_completed else "incomplete",
                )
            except Exception:
                pass

            return is_completed

        except Exception as e:
            logger.warning(f"[TaskVerify] Failed to verify: {e}, assuming INCOMPLETE")
            return False

    async def do_task_retrospect(self, task_monitor: Any) -> str:
        """
        执行任务复盘分析。

        当任务耗时过长时，让 LLM 分析原因。

        Args:
            task_monitor: TaskMonitor 实例

        Returns:
            复盘分析结果
        """
        try:
            from .task_monitor import RETROSPECT_PROMPT

            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)

            response = await self._brain.think(
                prompt=prompt,
                system="你是一个任务执行分析专家。请简洁地分析任务执行情况，找出耗时原因和改进建议。",
            )

            result = strip_thinking_tags(response.content).strip() if response.content else ""

            task_monitor.metrics.retrospect_result = result

            # 如果发现重复错误模式，记录到记忆
            if self._memory_manager and any(kw in result for kw in ("重复", "无效", "弯路")):
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"任务执行复盘发现问题：{result}",
                        source="retrospect",
                        importance_score=0.7,
                    )
                    self._memory_manager.add_memory(memory)
                except Exception as e:
                    logger.warning(f"Failed to save retrospect to memory: {e}")

            return result

        except Exception as e:
            logger.warning(f"Task retrospect failed: {e}")
            return ""

    async def do_task_retrospect_background(
        self, task_monitor: Any, session_id: str
    ) -> None:
        """
        后台执行任务复盘分析（不阻塞主响应）。
        """
        try:
            retrospect_result = await self.do_task_retrospect(task_monitor)

            if not retrospect_result:
                return

            from .task_monitor import RetrospectRecord, get_retrospect_storage

            record = RetrospectRecord(
                task_id=task_monitor.metrics.task_id,
                session_id=session_id,
                description=task_monitor.metrics.description,
                duration_seconds=task_monitor.metrics.total_duration_seconds,
                iterations=task_monitor.metrics.total_iterations,
                model_switched=task_monitor.metrics.model_switched,
                initial_model=task_monitor.metrics.initial_model,
                final_model=task_monitor.metrics.final_model,
                retrospect_result=retrospect_result,
            )

            storage = get_retrospect_storage()
            storage.save(record)

            logger.info(f"[Session:{session_id}] Retrospect saved: {task_monitor.metrics.task_id}")

        except Exception as e:
            logger.error(f"[Session:{session_id}] Background retrospect failed: {e}")

    @staticmethod
    def should_compile_prompt(message: str) -> bool:
        """判断是否需要进行 Prompt 编译"""
        if len(message.strip()) < 20:
            return False
        return True

    @staticmethod
    def get_last_user_request(messages: list[dict]) -> str:
        """获取最后一条用户请求"""
        from .tool_executor import smart_truncate

        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and not content.startswith("[系统]"):
                    result, _ = smart_truncate(content, 3000, save_full=False, label="user_request")
                    return result
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if not text.startswith("[系统]"):
                                result, _ = smart_truncate(text, 3000, save_full=False, label="user_request")
                                return result
        return ""
