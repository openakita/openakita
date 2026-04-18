"""
Response handler

Response-handling logic extracted from agent.py, responsible for:
- Cleaning LLM response text (thinking tags, simulated tool calls)
- Task completion verification
- Task retrospective analysis
- Helper predicate functions
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ==================== Text cleaning functions ====================


def strip_thinking_tags(text: str) -> str:
    """
    Remove internal-tag content from the response.

    Tags to clean include:
    - <thinking>...</thinking> - Claude extended thinking
    - <think>...</think> - MiniMax/Qwen thinking format
    - <minimax:tool_call>...</minimax:tool_call>
    - <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> - Kimi K2
    - </thinking> - stray closing tags
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

    # Remove any stray closing tags
    cleaned = re.sub(r"</thinking>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</think>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</minimax:tool_call>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<<\|tool_calls_section_begin\|>>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\?xml[^>]*\?>\s*", "", cleaned)

    # Fallback: clean up orphan opening tags (no closing tag, from the tag to the end of the string)
    cleaned = re.sub(r"<thinking>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>\s*.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    Remove text where the LLM is simulating tool calls.

    When using fallback models that do not support native tool calling, the LLM
    may "simulate" tool calls in text. Three cases are handled:
    1. Entire line is a tool call (removed outright)
    2. Inline .tool_name(args) embedded at line end (stripped from the end, keeping preceding prose)
    3. <tool_call>...</tool_call> XML block (commonly leaked by LLMs in Ask mode)
    """
    if not text:
        return text

    # First, remove <tool_call>...</tool_call> blocks (may span multiple lines)
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

    # Inline .tool_name(args) stripping: match the trailing .tool_name(args) part of a line
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

        # Check whether the end of the line embeds .tool_name(args) (e.g., mixed text + tool call)
        m = inline_dot_pattern.search(stripped)
        if m and m.start() > 0:
            cleaned_lines.append(stripped[: m.start()].rstrip())
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


_LEADING_TIMESTAMP_RE = re.compile(r"^\s*\[\d{1,2}:\d{2}\]\s*")


def clean_llm_response(text: str) -> str:
    """
    Clean LLM response text.

    Applies, in order:
    1. strip_thinking_tags - remove thinking tags
    2. strip_tool_simulation_text - remove simulated tool calls
    3. strip_intent_tag - remove intent-declaration markers
    4. strip leading [HH:MM] timestamp leaked from historical message formatting
    """
    if not text:
        return text

    cleaned = strip_thinking_tags(text)
    cleaned = strip_tool_simulation_text(cleaned)
    _, cleaned = parse_intent_tag(cleaned)
    cleaned = _LEADING_TIMESTAMP_RE.sub("", cleaned)

    return cleaned.strip()


# ==================== Intent-declaration parsing ====================

_INTENT_TAG_RE = re.compile(r"^\s*\[(ACTION|REPLY)\]\s*\n?", re.IGNORECASE)


def parse_intent_tag(text: str) -> tuple[str | None, str]:
    """
    Parse and strip the intent-declaration marker at the start of a response.

    In plain-text replies the model should declare [ACTION] or [REPLY] on the first line:
    - [ACTION]: declares that a tool call is needed (a hallucination if none is actually made)
    - [REPLY]: declares a pure conversational reply, no tools needed

    Returns:
        (intent, stripped_text):
        - intent: "ACTION" / "REPLY" / None (no marker)
        - stripped_text: text with the marker removed
    """
    if not text:
        return None, text or ""
    m = _INTENT_TAG_RE.match(text)
    if m:
        return m.group(1).upper(), text[m.end() :]
    return None, text


class ResponseHandler:
    """
    Response handler.

    Handles post-processing of LLM responses, including task completion verification
    and retrospective analysis.
    """

    def __init__(self, brain: Any, memory_manager: Any = None) -> None:
        """
        Args:
            brain: Brain instance, used for LLM calls
            memory_manager: MemoryManager instance (optional, used to save retrospective results)
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
        Task completion re-check.

        Ask the LLM to judge whether the current response truly fulfills the user's intent.

        Args:
            user_request: original user request
            assistant_response: current assistant response
            executed_tools: list of tools that have been executed
            delivery_receipts: delivery receipts
            tool_results: accumulated tool execution results (including is_error flag)
            conversation_id: conversation ID (used for Plan check)
            bypass: skip verification when the Supervisor has already intervened

        Returns:
            True if the task is completed.
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

        # Claims delivery but has no evidence
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
        # Claims the user already sees a UI/window on their own machine but has no delivery receipt or other verifiable path (isomorphic to "empty-promise delivery")
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

        # LLM judgment
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
                    "You are a task-completion judgment assistant. OpenAkita tools execute on the host environment, "
                    "which is typically not the same machine as the user's chat device; "
                    "you must distinguish 'verified completion on the host' from 'user-local observable completion', "
                    "and must not judge the latter as completed solely because a host command exited successfully."
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

            # Decision Trace: record the verification decision
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
        Perform task retrospective analysis.

        When a task takes too long, have the LLM analyze the cause.

        Args:
            task_monitor: TaskMonitor instance

        Returns:
            Retrospective analysis result.
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

            # If a repeated-error pattern is found, record it to memory
            if self._memory_manager and any(
                kw in result.lower() for kw in ("repeat", "redundant", "useless", "detour")
            ):
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryScope, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"Task execution retrospective found issue: {result}",
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
        Perform task retrospective analysis in the background (does not block the main response).
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
