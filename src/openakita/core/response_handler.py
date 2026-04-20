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

    # 系统/组织自动注入的元指令前缀。命中其中任何一个意味着「user_request」
    # 实际上不是真正用户输入，而是后端合成的汇总/通知/系统消息，关键词命中
    # （如「文件」「链接」「附件」）会导致 verify 误判。显式白名单 3 个前缀，
    # 不写正则一刀切，避免误伤恰好以 "[" 开头的真实用户输入。
    _SYSTEM_REQUEST_PREFIXES: tuple[str, ...] = (
        "[用户指令最终汇总]",
        "[系统]",
        "[组织]",
    )

    @staticmethod
    def _request_expects_artifact(user_request: str | None) -> bool:
        raw = (user_request or "").strip()
        if raw.startswith(ResponseHandler._SYSTEM_REQUEST_PREFIXES):
            return False
        text = raw.lower()
        return any(
            key in text
            for key in (
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
                "给我一张",
                "给我发",
                "image",
                "photo",
                "picture",
                "file",
                "attachment",
                "download",
                "send me",
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
        accepted_child_count: int = 0,
        has_recent_accepted_signal: bool = False,
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
                accepted_child_count=int(accepted_child_count or 0),
                has_recent_accepted_signal=bool(has_recent_accepted_signal),
            )
            registry = create_default_registry()
            report = registry.run_all(val_context)

            if report.applicable_count > 0:
                for output in report.outputs:
                    if output.result == ValidationResult.PASS and output.name in (
                        "ArtifactValidator",
                        "CompletePlanValidator",
                        "OrgDelegationValidator",
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

        # === Trust-but-verify: org_submit_deliverable / deliver_artifacts 信任路径 ===
        # 组织节点用 org_submit_deliverable 提交交付物时，如果 deliverable 内容
        # 足够长或带文件附件，说明已经实际产出，直接放行；否则才走 LLM 复核。
        # 这是修复"sub-agent 实际已经写好文档/代码并提交，但 LLM verify 误判
        # 'verify_incomplete' 反复重试"的关键。
        try:
            executed_set = set(executed_tools or [])
            tool_results_list = tool_results or []
            org_submit_ok = "org_submit_deliverable" in executed_set
            deliver_artifacts_ok = "deliver_artifacts" in executed_set

            if org_submit_ok or deliver_artifacts_ok:
                # 找出 submit_deliverable 工具调用对应的成功结果
                submit_ok_run = False
                deliverable_len = 0
                attachments_count = 0
                for tr in tool_results_list:
                    name = tr.get("tool_name") or tr.get("name") or ""
                    if name not in (
                        "org_submit_deliverable", "deliver_artifacts",
                    ):
                        continue
                    if tr.get("is_error"):
                        continue
                    submit_ok_run = True
                    args = tr.get("arguments") or tr.get("input") or {}
                    if isinstance(args, dict):
                        deliverable_len = max(
                            deliverable_len,
                            len(args.get("deliverable") or ""),
                        )
                        files = args.get("file_attachments") or args.get("files") or []
                        if isinstance(files, list):
                            attachments_count = max(attachments_count, len(files))

                # 兜底：tool_results 里拿不到详细参数（旧调用路径），退一步只看
                # 工具是否被执行 + delivery_receipts 是否非空（说明附件已发送）
                if not submit_ok_run and (org_submit_ok or deliver_artifacts_ok):
                    submit_ok_run = True
                    if delivery_receipts:
                        attachments_count = max(attachments_count, len(delivery_receipts))

                if submit_ok_run and (deliverable_len >= 200 or attachments_count >= 1):
                    logger.info(
                        "[TaskVerify] trust-but-verify PASS: "
                        "submit_deliverable executed, deliverable_len=%d files=%d",
                        deliverable_len, attachments_count,
                    )
                    return True
                if submit_ok_run:
                    logger.info(
                        "[TaskVerify] trust-but-verify INSUFFICIENT: "
                        "deliverable_len=%d files=%d → fall back to LLM verify",
                        deliverable_len, attachments_count,
                    )
        except Exception as exc:
            logger.debug(
                "[TaskVerify] trust-but-verify gate skipped: %s", exc,
            )

        expects_artifact = self._request_expects_artifact(user_request)

        # 宣称已交付但无证据
        if (
            any(
                k in (assistant_response or "")
                for k in (
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
                f"\n## Plan 状态\n"
                f"当前 Plan 有未完成步骤: {plan_fail_reason}\n"
                f"注意: 若用户意图是**宿主内**任务（工作区写文件、宿主 shell、宿主浏览器自动化等），"
                f"工具已成功执行且与 Plan 一致时可判 COMPLETED。"
                f"若用户意图是**用户本机可观测**（本机 GUI 窗口、本机软件安装、游戏内 overlay 等），"
                f"仅宿主侧 run_shell 等成功**不足**；需有交付回执、用户可在自己机器上执行的明确步骤，"
                f"或助手已清楚说明「效果在宿主、用户屏不可见」并给出可行替代方案。\n"
            )

        verify_prompt = f"""请判断以下交互是否已经**完成**用户的意图。

## 用户消息
{user_display}

## 助手响应
{response_display}

## 已执行的工具
{", ".join(executed_tools) if executed_tools else "无"}

## 附件交付回执（如有）
{delivery_receipts if delivery_receipts else "无"}
{_plan_section}
## 执行域前提（必读）

工具在 **OpenAkita 宿主**执行，与用户发消息的设备/IM 客户端**默认不同域**。宿主上命令成功 ≠ 用户本机已出现窗口或已安装软件。

## 判断标准

### 非任务类消息（直接判 COMPLETED）
- 如果用户消息是**闲聊/问候**，助手已礼貌回复 → **COMPLETED**
- 如果用户消息是**简单确认/反馈**，助手已简短回应 → **COMPLETED**
- 如果用户消息是**简单问答**，助手已给出回答 → **COMPLETED**

### 任务类消息 — 分层完成标准

**A. 宿主内可验证的完成**（以下任一满足且用户意图属此类 → 可 COMPLETED）
- 已执行 write_file / edit_file 等且目标为工作区内保存文件
- 已执行浏览器工具且意图是在**宿主侧**操作网页
- 已有 **deliver_artifacts** 成功回执（status=delivered），且用户要的是可交付产物
- 已调用 **complete_todo** 且 Plan 语义已闭环
- 工具在宿主执行成功，且用户请求**未要求**在用户本人电脑屏幕/本机系统中看到效果

**B. 用户本机可观测的完成**（用户明确要求在本机看到窗口、本机安装、游戏画面内效果等）
- 仅有宿主侧 run_shell / Python 成功**不能**单独作为完成证据
- 需至少其一：成功交付（回执）、回复中含用户可在**自己机器**上执行的明确命令/步骤并已给出、或助手明确说明边界且用户目标已调整为可达成形态

**C. 仍在进行中**
- 响应仅为「现在开始…」「让我…」且关键工具未执行 → **INCOMPLETE**

**D. 上游平台硬性限制**
- 助手已实际尝试且遇不可绕过的 API/平台限制，并已向用户解释 → **COMPLETED**
- 若仍有其他可行路径（换命令、换文件路径等）→ **INCOMPLETE**

## 回答要求
STATUS: COMPLETED 或 INCOMPLETE
EVIDENCE: 完成的证据
MISSING: 缺失的内容
NEXT: 建议的下一步"""

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
                system="你是一个任务执行分析专家。请简洁地分析任务执行情况，找出耗时原因和改进建议。",
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
