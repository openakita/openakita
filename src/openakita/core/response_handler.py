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
        r"<minimax:tool_call>.*?</minimax:tool_call>\s*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>\s*",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<invoke\s+[^>]*>.*?</invoke>\s*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 移除残留的闭合标签
    cleaned = re.sub(r"</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</think>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</minimax:tool_call>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<<\|tool_calls_section_begin\|>>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\?xml[^>]*\?>\s*", "", cleaned)

    # 兜底：清理孤立的开标签（无闭合，从标签到字符串末尾）
    cleaned = re.sub(r"<thinking>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    移除 LLM 在文本中模拟工具调用的内容。

    当使用不支持原生工具调用的备用模型时，LLM 可能在文本中
    "模拟"工具调用。支持三种情况：
    1. 整行都是工具调用（直接移除）
    2. 行内嵌入的 .tool_name(args)（从行尾剥离，保留前面的正文）
    3. <tool_call>...</tool_call> XML 块（Ask 模式下 LLM 常泄漏此格式）
    """
    if not text:
        return text

    # 先移除 <tool_call>...</tool_call> 块（可能跨行）
    text = re.sub(
        r"<tool_call>\s*.*?\s*</tool_call>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    pattern1 = r"^\.?[a-z_]+\s*\(.*\)\s*$"
    pattern2 = r"^[a-z_]+:\d+[\{\(].*[\}\)]\s*$"
    pattern3 = r'^\{["\']?(tool|function|name)["\']?\s*:'
    pattern4 = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"

    # 行内 .tool_name(args) 剥离：匹配行尾的 .tool_name(args) 部分
    inline_dot_pattern = re.compile(r"\s*\.[a-z][a-z0-9_]{2,}\s*\(.*\)\s*$", re.IGNORECASE)

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
        if is_tool_sim:
            continue

        # 检查行尾是否嵌入了 .tool_name(args)（如混合文本+工具调用）
        m = inline_dot_pattern.search(stripped)
        if m and m.start() > 0:
            cleaned_lines.append(stripped[: m.start()].rstrip())
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


_LEADING_TIMESTAMP_RE = re.compile(r"^\s*\[\d{1,2}:\d{2}\]\s*")


def clean_llm_response(text: str) -> str:
    """
    清理 LLM 响应文本。

    依次应用:
    1. strip_thinking_tags - 移除思考标签
    2. strip_tool_simulation_text - 移除模拟工具调用
    3. strip_intent_tag - 移除意图声明标记
    4. strip leading [HH:MM] timestamp leaked from historical message formatting
    """
    if not text:
        return text

    cleaned = strip_thinking_tags(text)
    cleaned = strip_tool_simulation_text(cleaned)
    _, cleaned = parse_intent_tag(cleaned)
    cleaned = _LEADING_TIMESTAMP_RE.sub("", cleaned)

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
        return m.group(1).upper(), text[m.end() :]
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

    @staticmethod
    def _request_expects_artifact(user_request: str | None) -> bool:
        text = (user_request or "").lower()
        return any(
            key in text
            for key in (
                "image",
                "photo",
                "picture",
                "file",
                "attachment",
                "download",
                "send me",
                "poster",
                "wallpaper",
                "screenshot",
                "give me a",
                "send to me",
                "图片",
                "照片",
                "图像",
                "海报",
                "壁纸",
                "配图",
                "截图",
                "附件",
                "文件",
                "下载",
                "发我",
                "发给我",
            )
        )

    async def verify_task_completion(
        self,
        user_request: str,
        assistant_response: str,
        executed_tools: list[str],
        delivery_receipts: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        conversation_id: str | None = None,
        bypass: bool = False,
    ) -> bool:
        """
        任务完成度复核。

        让 LLM 判断当前响应是否真正完成了用户的意图。

        Args:
            user_request: 用户原始请求
            assistant_response: 助手当前响应
            executed_tools: 已执行的工具列表
            delivery_receipts: 交付回执
            tool_results: 累积的工具执行结果（含 is_error 标记）
            conversation_id: 对话 ID（用于 Plan 检查）
            bypass: 当 Supervisor 已介入时跳过验证

        Returns:
            True 如果任务已完成
        """
        if bypass:
            logger.info("[TaskVerify] Bypassed (supervisor intervention active)")
            return True

        delivery_receipts = delivery_receipts or []

        # === Deterministic Validation (Agent Harness) ===
        plan_fail_reason = ""
        try:
            from .validators import ValidationContext, ValidationResult, create_default_registry

            val_context = ValidationContext(
                user_request=user_request,
                assistant_response=assistant_response,
                executed_tools=executed_tools or [],
                delivery_receipts=delivery_receipts,
                tool_results=tool_results or [],
                conversation_id=conversation_id or "",
            )
            registry = create_default_registry()
            report = registry.run_all(val_context)

            if report.applicable_count > 0:
                for output in report.outputs:
                    if output.result == ValidationResult.PASS and output.name in (
                        "ArtifactValidator",
                        "CompletePlanValidator",
                    ):
                        logger.info(
                            f"[TaskVerify] Deterministic PASS: {output.name} — {output.reason}"
                        )
                        return True

                for output in report.outputs:
                    if output.result == ValidationResult.FAIL and output.name == "PlanValidator":
                        plan_fail_reason = output.reason
                        logger.info(
                            f"[TaskVerify] PlanValidator FAIL (non-blocking): {output.reason}"
                        )

                for output in report.outputs:
                    if (
                        output.result == ValidationResult.FAIL
                        and output.name == "ArtifactValidator"
                    ):
                        logger.warning(
                            f"[TaskVerify] ArtifactValidator FAIL but treating as PASS "
                            f"(delivery failure is infra issue, not agent fault): {output.reason}"
                        )
                        return True
        except Exception as e:
            logger.debug(f"[TaskVerify] Deterministic validation skipped: {e}")

        expects_artifact = self._request_expects_artifact(user_request)

        # 宣称已交付但无证据
        if (
            any(
                k in (assistant_response or "")
                for k in (
                    "sent",
                    "delivered",
                    "sent to you",
                    "here is the image",
                    "here is the picture",
                    "give you a",
                    "I sent it to you",
                    "I generated an image for you",
                    "the image is as follows",
                    "the attachment is as follows",
                    "已发送",
                    "已交付",
                    "已发给你",
                    "已发给您",
                    "下面是图片",
                    "给你一张",
                    "给您一张",
                    "我给你发",
                    "我给您发",
                    "我为你生成了图片",
                    "我为您生成了图片",
                    "图片如下",
                    "附件如下",
                )
            )
            and not delivery_receipts
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info("[TaskVerify] delivery claim without receipts, INCOMPLETE")
            return False

        if (
            expects_artifact
            and not delivery_receipts
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info(
                "[TaskVerify] artifact requested but no delivery receipts/tools, INCOMPLETE"
            )
            return False

        _delivered_ok = any(r.get("status") == "delivered" for r in delivery_receipts)
        # 宣称用户在本机已看到界面/窗口，但无交付回执等可证实路径（与「空口交付」同构）
        if (
            any(
                k in (assistant_response or "")
                for k in (
                    "you should be able to see",
                    "on your screen",
                    "on your desktop",
                    "at your computer",
                    "when you play the game",
                    "你应该能看到",
                    "你屏幕上",
                    "你桌面上",
                    "你的桌面",
                    "在你电脑上",
                    "你玩游戏时能看到",
                )
            )
            and not _delivered_ok
            and "deliver_artifacts" not in (executed_tools or [])
        ):
            logger.info("[TaskVerify] user-visible UI claim without delivery/evidence, INCOMPLETE")
            return False

        # LLM 判断
        from .tool_executor import smart_truncate

        user_display, _ = smart_truncate(user_request, 3000, save_full=False, label="verify_user")
        response_display, _ = smart_truncate(
            assistant_response, 8000, save_full=False, label="verify_response"
        )

        _plan_section = ""
        if plan_fail_reason:
            _plan_section = (
                f"\n## Plan Status\n"
                f"The current Plan has incomplete steps: {plan_fail_reason}\n"
                f"Note: If the user intent is a **host-internal** task (writing files in workspace, host shell, host browser automation, etc.), "
                f"it can be judged as COMPLETED if tools executed successfully and align with the Plan. "
                f"If the user intent is **user-local observable** (local GUI windows, local software installation, in-game overlays, etc.), "
                f"mere success of host-side run_shell, etc., is **insufficient**. There must be a delivery receipt, explicit steps for the user to execute on their own machine, "
                f"or the assistant has clearly explained that 'effects are on the host and not visible on the user's screen' and provided a viable alternative.\n"
            )
        verify_prompt = f"""Please determine if the following interaction has **successfully fulfilled** the user's intent.

## User Message
{user_display}

## Assistant Response
{response_display}

## Executed Tools
{", ".join(executed_tools) if executed_tools else "None"}

## Artifact Delivery Receipts (if any)
{delivery_receipts if delivery_receipts else "None"}
{_plan_section}
## Execution Domain Context (Read Carefully)

Tools are executed on the **OpenAkita Host**, which is **different by default** from the device/IM client where the user sends messages. Success on the host ≠ a window appearing or software being installed on the user's local machine.

## Criteria

### Non-Task Messages (Rate as COMPLETED)
- If the user message is **Chit-chat/Greeting** and the assistant replied politely → **COMPLETED**
- If the user message is **Simple confirmation/feedback** and the assistant gave a brief response → **COMPLETED**
- If the user message is **Simple Q&A** and the assistant gave an answer → **COMPLETED**

### Task-Oriented Messages — Hierarchical Standards

**A. Host-Domain Verifiable Completion** (rate as COMPLETED if user intent falls here and any condition is met)
- Executed `write_file` / `edit_file` etc., targeting files within the workspace.
- Executed browser tools and the intent was to operate on a webpage on the **host side**.
- Success receipt from **deliver_artifacts** (status=delivered), and the user requested a deliverable artifact.
- Called **complete_todo** and the Plan semantics are closed.
- Tools executed successfully on the host, and the user request **did not require** seeing the effect on their own screen/local system.

**B. User-Local Observable Completion** (user explicitly requested seeing a window, local installation, in-game overlay effects, etc.)
- A successful `run_shell` / Python execution on the host **cannot** alone serve as evidence of completion.
- Requirement (at least one): Successful delivery (receipt), the response includes clear commands/steps for the user to execute on **their own machine**, or the assistant explicitly explained the boundary/limitations and the user's goal was adjusted to an achievable form.

**C. Still In Progress**
- The response is merely "Starting now..." or "Let me..." and key tools haven't been executed → **INCOMPLETE**

**D. Upstream/Platform Hard Constraints**
- The assistant actually attempted the task but encountered unavoidable API/platform limitations and explained them to the user → **COMPLETED**
- If alternative viable paths still exist (different command, different path) → **INCOMPLETE**

## Response Format
STATUS: COMPLETED or INCOMPLETE
EVIDENCE: Evidence of completion
MISSING: What is missing
NEXT: Suggested next step"""

        try:
            response = await self._brain.think_lightweight(
                prompt=verify_prompt,
                system=(
                    "你是任务完成度判断助手。OpenAkita 工具在宿主环境执行，与用户聊天设备通常不是同一台机器；"
                    "必须区分「宿主内已验证完成」与「用户本机可观测完成」，不要仅凭宿主命令退出成功判定后者已完成。"
                ),
                max_tokens=512,
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

            response = await self._brain.think_lightweight(
                prompt=prompt,
                system="You are an expert in analyzing task execution. Please concisely analyze task performance, identifying causes for delays and suggestions for improvement.",
                max_tokens=512,
            )

            result = strip_thinking_tags(response.content).strip() if response.content else ""

            task_monitor.metrics.retrospect_result = result

            # 如果发现重复错误模式，记录到记忆
            if self._memory_manager and any(kw in result for kw in ("重复", "无效", "弯路")):
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryScope, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"任务执行复盘发现问题：{result}",
                        source="retrospect",
                        importance_score=0.7,
                        scope=MemoryScope.AGENT,
                    )
                    self._memory_manager.add_memory(
                        memory, scope=MemoryScope.AGENT
                    )
                except Exception as e:
                    logger.warning(f"Failed to save retrospect to memory: {e}")

            return result

        except Exception as e:
            logger.warning(f"Task retrospect failed: {e}")
            return ""

    async def do_task_retrospect_background(self, task_monitor: Any, session_id: str) -> None:
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

        def _strip_context_prefix(text: str) -> str:
            """移除对话历史前缀，提取真正的用户输入。"""
            _marker = "：]"
            if text.startswith("[以上是之前的对话历史"):
                idx = text.find(_marker)
                if idx != -1:
                    text = text[idx + len(_marker) :].strip()
            return text

        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and not content.startswith("[系统]"):
                    content = _strip_context_prefix(content)
                    result, _ = smart_truncate(content, 3000, save_full=False, label="user_request")
                    return result
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if not text.startswith("[系统]"):
                                text = _strip_context_prefix(text)
                                result, _ = smart_truncate(
                                    text, 3000, save_full=False, label="user_request"
                                )
                                return result
        return ""
