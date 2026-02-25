"""
上下文管理器

从 agent.py 提取的上下文压缩/管理逻辑，负责:
- 估算 token 数量
- 消息分组（保证 tool_calls/tool_result 配对完整）
- LLM 分块摘要压缩
- 递归压缩
- 硬截断保底
- 动态上下文窗口计算
"""

import asyncio
import json
import logging
from typing import Any

from .tool_executor import OVERFLOW_MARKER
from .token_tracking import TokenTrackingContext, set_tracking_context, reset_tracking_context
from ..tracing.tracer import get_tracer

logger = logging.getLogger(__name__)

# 上下文管理常量
DEFAULT_MAX_CONTEXT_TOKENS = 160000
CHARS_PER_TOKEN = 2  # JSON 序列化后约 2 字符 = 1 token
MIN_RECENT_TURNS = 8  # 至少保留最近 8 组对话（工具密集型对话需要更多上下文）
COMPRESSION_RATIO = 0.15  # 目标压缩到原上下文的 15%
BOUNDARY_COMPRESSION_RATIO = 0.18  # 上下文边界前的旧话题压缩到 18%
CHUNK_MAX_TOKENS = 30000  # 每次发给 LLM 压缩的单块上限
LARGE_TOOL_RESULT_THRESHOLD = 5000  # 单条 tool_result 超过此 token 数时独立压缩
CONTEXT_BOUNDARY_MARKER = "[上下文边界]"  # 话题切换边界标记


class _CancelledError(Exception):
    """ContextManager 内部使用的取消信号，向上传播后由 Agent 层转换为 UserCancelledError。"""
    pass


class ContextManager:
    """
    上下文压缩和管理器。

    负责在对话上下文接近 LLM 上下文窗口限制时，
    使用 LLM 分块摘要压缩早期对话，保留最近的工具交互完整性。
    """

    def __init__(self, brain: Any, cancel_event: asyncio.Event | None = None) -> None:
        """
        Args:
            brain: Brain 实例，用于 LLM 调用
            cancel_event: 可选的取消事件，set 时中断压缩 LLM 调用
        """
        self._brain = brain
        self._cancel_event = cancel_event

    def set_cancel_event(self, event: asyncio.Event | None) -> None:
        """更新 cancel_event（每次任务开始时由 Agent 设置）"""
        self._cancel_event = event

    async def _cancellable_llm(self, **kwargs):
        """可被 cancel_event 中断的 LLM 调用（直接 await，不创建线程）"""
        logger.debug("[ContextManager] _cancellable_llm 发起 LLM 调用")
        coro = self._brain.messages_create_async(**kwargs)
        if not self._cancel_event:
            return await coro
        task = asyncio.create_task(coro)
        cancel_waiter = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if task in done:
            logger.debug("[ContextManager] _cancellable_llm LLM 调用完成")
            return task.result()
        logger.info("[ContextManager] _cancellable_llm 被用户取消")
        raise _CancelledError("Context compression cancelled by user")

    def get_max_context_tokens(self) -> int:
        """
        动态获取当前模型的上下文窗口大小。

        优先级：
        1. 端点配置的 context_window 字段
        2. 兜底值 200000
        3. 减去 max_tokens（输出预留）和 10% buffer
        """
        FALLBACK_CONTEXT_WINDOW = 200000

        try:
            info = self._brain.get_current_model_info()
            ep_name = info.get("name", "")
            endpoints = self._brain._llm_client.endpoints
            for ep in endpoints:
                if ep.name == ep_name:
                    ctx = getattr(ep, "context_window", 0) or 0
                    if ctx < 8192:
                        ctx = FALLBACK_CONTEXT_WINDOW
                    output_reserve = ep.max_tokens or 4096
                    output_reserve = min(output_reserve, ctx // 2)
                    result = int((ctx - output_reserve) * 0.90)
                    if result < 4096:
                        return DEFAULT_MAX_CONTEXT_TOKENS
                    return result
            return DEFAULT_MAX_CONTEXT_TOKENS
        except Exception:
            return DEFAULT_MAX_CONTEXT_TOKENS

    def estimate_tokens(self, text: str) -> int:
        """
        估算文本的 token 数量。

        使用中英文感知算法：中文约 1.5 字符/token，英文约 4 字符/token。
        """
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        total_chars = len(text)
        english_chars = total_chars - chinese_chars
        chinese_tokens = chinese_chars / 1.5
        english_tokens = english_chars / 4
        return max(int(chinese_tokens + english_tokens), 1)

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """
        估算消息列表的 token 数量。

        对每条消息的 content 使用与 estimate_tokens 相同的中英文感知算法，
        并为每条消息加固定结构开销（role / tool_use_id 等约 10 tokens）。
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.estimate_tokens(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text", "") or item.get("content", "")
                        if isinstance(text, str) and text:
                            total += self.estimate_tokens(text)
                        else:
                            total += self.estimate_tokens(
                                json.dumps(item, ensure_ascii=False, default=str)
                            )
                    elif isinstance(item, str):
                        total += self.estimate_tokens(item)
            total += 10  # 每条消息的结构开销
        return max(total, 1)

    @staticmethod
    def group_messages(messages: list[dict]) -> list[list[dict]]:
        """
        将消息列表分组为"工具交互组"，保证 tool_calls/tool 配对不被拆散。

        分组规则：
        - assistant 消息含 tool_use → 和后续 tool_result 消息归为同一组
        - 其他消息各自独立成组
        """
        if not messages:
            return []

        groups: list[list[dict]] = []
        i = 0

        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")
            content = msg.get("content", "")

            has_tool_calls = False
            if role == "assistant" and isinstance(content, list):
                has_tool_calls = any(
                    isinstance(item, dict) and item.get("type") == "tool_use"
                    for item in content
                )

            if has_tool_calls:
                group = [msg]
                i += 1
                while i < len(messages):
                    next_msg = messages[i]
                    next_role = next_msg.get("role", "")
                    next_content = next_msg.get("content", "")

                    if next_role == "user" and isinstance(next_content, list):
                        all_tool_results = all(
                            isinstance(item, dict) and item.get("type") == "tool_result"
                            for item in next_content
                            if isinstance(item, dict)
                        )
                        if all_tool_results and next_content:
                            group.append(next_msg)
                            i += 1
                            continue

                    if next_role == "tool":
                        group.append(next_msg)
                        i += 1
                        continue

                    break

                groups.append(group)
            else:
                groups.append([msg])
                i += 1

        return groups

    async def compress_if_needed(
        self,
        messages: list[dict],
        *,
        system_prompt: str = "",
        tools: list | None = None,
        max_tokens: int | None = None,
        memory_manager: object | None = None,
    ) -> list[dict]:
        """
        如果上下文接近限制，执行压缩。

        策略:
        0. 压缩前: 快速规则提取 + 通知 MemoryManager
        1. 先对单条过大的 tool_result 独立 LLM 压缩
        2. 按工具交互组分组
        3. 保留最近组，早期组 LLM 摘要压缩
        4. 递归压缩 / 硬截断保底

        Args:
            messages: 消息列表
            system_prompt: 系统提示词（用于估算 token 占用）
            tools: 工具定义列表（用于估算 token 占用）
            max_tokens: 最大 token 数
            memory_manager: MemoryManager 实例 (v2: 压缩前提取记忆)

        Returns:
            压缩后的消息列表
        """
        max_tokens = max_tokens or self.get_max_context_tokens()

        system_tokens = self.estimate_tokens(system_prompt)

        tools_tokens = 0
        if tools:
            try:
                tools_text = json.dumps(tools, ensure_ascii=False, default=str)
                tools_tokens = int(len(tools_text) / 2)
            except Exception:
                tools_tokens = len(tools) * 300

        hard_limit = max_tokens - system_tokens - tools_tokens - 1000
        if hard_limit < 4096:
            logger.warning(
                f"[Compress] hard_limit too small ({hard_limit}), "
                f"max={max_tokens}, system={system_tokens}, tools={tools_tokens}. "
                f"Falling back to 4096."
            )
            hard_limit = 4096
        soft_limit = int(hard_limit * 0.80)

        current_tokens = self.estimate_messages_tokens(messages)

        logger.debug(
            f"[Compress] Check: {len(messages)} msgs, ~{current_tokens} tokens, "
            f"soft_limit={soft_limit}, hard_limit={hard_limit}"
        )

        if current_tokens <= soft_limit:
            return messages

        # v2: 压缩前记忆提取 — 确保即将被压缩的消息先保存到记忆
        if memory_manager is not None:
            try:
                on_compressing = getattr(memory_manager, "on_context_compressing", None)
                if on_compressing:
                    await on_compressing(messages)
            except Exception as e:
                logger.warning(f"[Compress] Memory extraction before compression failed: {e}")

        tracer = get_tracer()
        from ..tracing.tracer import SpanType
        ctx_span = tracer.start_span("context_compression", SpanType.CONTEXT)
        ctx_span.set_attribute("tokens_before", current_tokens)
        ctx_span.set_attribute("soft_limit", soft_limit)
        ctx_span.set_attribute("hard_limit", hard_limit)

        logger.info(
            f"Context approaching limit ({current_tokens} tokens, soft={soft_limit}, "
            f"hard={hard_limit}), compressing with LLM..."
        )

        def _end_ctx_span(result_msgs: list[dict]) -> list[dict]:
            """结束 ctx_span 并返回结果"""
            result_tokens = self.estimate_messages_tokens(result_msgs)
            ctx_span.set_attribute("tokens_after", result_tokens)
            ctx_span.set_attribute("compression_ratio", result_tokens / max(current_tokens, 1))
            tracer.end_span(ctx_span)
            return result_msgs

        # Step 1: 对单条过大的 tool_result 独立压缩
        messages = await self._compress_large_tool_results(messages)
        current_tokens = self.estimate_messages_tokens(messages)
        if current_tokens <= soft_limit:
            logger.info(f"After tool_result compression: {current_tokens} tokens, within limit")
            return _end_ctx_span(messages)

        # Step 1.5: 上下文边界感知 — 如果存在边界标记，对旧话题使用更激进的压缩
        messages = await self._compress_across_boundary(messages, soft_limit, memory_manager)
        current_tokens = self.estimate_messages_tokens(messages)
        if current_tokens <= soft_limit:
            logger.info(f"After boundary compression: {current_tokens} tokens, within limit")
            return _end_ctx_span(messages)

        # Step 2: 按工具交互组分组
        groups = self.group_messages(messages)

        # 末尾问答对保护：如果最后 2 个 group 是 [assistant text, user short text]，
        # 合并为一组以防止 AI 的提问被压掉而用户的简短回答变成孤立无头信息
        if (len(groups) >= 2
                and len(groups[-1]) == 1 and groups[-1][0].get("role") == "user"
                and len(groups[-2]) == 1 and groups[-2][0].get("role") == "assistant"
                and self.estimate_messages_tokens(groups[-1]) < 200):
            merged = groups[-2] + groups[-1]
            groups = groups[:-2] + [merged]
            logger.debug("[Compress] Merged trailing assistant-question + user-answer into one group")

        recent_group_count = min(MIN_RECENT_TURNS, len(groups))

        if len(groups) <= recent_group_count:
            messages = await self._compress_large_tool_results(messages, threshold=2000)
            return _end_ctx_span(self._hard_truncate_if_needed(messages, hard_limit, memory_manager))

        early_groups = groups[:-recent_group_count]
        recent_groups = groups[-recent_group_count:]

        early_messages = [msg for group in early_groups for msg in group]
        recent_messages = [msg for group in recent_groups for msg in group]

        logger.info(
            f"Split into {len(early_groups)} early groups and "
            f"{len(recent_groups)} recent groups"
        )

        # Step 3: LLM 分块摘要早期对话
        early_tokens = self.estimate_messages_tokens(early_messages)
        target_summary_tokens = max(int(early_tokens * COMPRESSION_RATIO), 200)
        summary = await self._summarize_messages_chunked(early_messages, target_summary_tokens)

        compressed = self._inject_summary_into_recent(summary, recent_messages)

        compressed_tokens = self.estimate_messages_tokens(compressed)
        if compressed_tokens <= soft_limit:
            logger.info(f"Compressed context from {current_tokens} to {compressed_tokens} tokens")
            return _end_ctx_span(compressed)

        # Step 4: 递归压缩
        logger.warning(f"Context still large ({compressed_tokens} tokens), compressing further...")
        compressed = await self._compress_further(compressed, soft_limit)

        # Step 5: 硬保底
        return _end_ctx_span(self._hard_truncate_if_needed(compressed, hard_limit, memory_manager))

    @staticmethod
    def _find_last_boundary_index(messages: list[dict]) -> int:
        """找到消息列表中最后一个上下文边界标记的位置，返回 -1 表示未找到。"""
        for i in range(len(messages) - 1, -1, -1):
            content = messages[i].get("content", "")
            if isinstance(content, str) and CONTEXT_BOUNDARY_MARKER in content:
                return i
        return -1

    async def _compress_across_boundary(
        self,
        messages: list[dict],
        soft_limit: int,
        memory_manager: object | None = None,
    ) -> list[dict]:
        """上下文边界感知压缩：对边界之前的旧话题使用更激进的压缩策略。

        如果消息中包含 [上下文边界] 标记，将边界之前的消息压缩为极简摘要（5%），
        仅保留可能对当前话题有用的关键信息。
        """
        boundary_idx = self._find_last_boundary_index(messages)
        if boundary_idx <= 0:
            return messages

        pre_boundary = messages[:boundary_idx]
        post_boundary = messages[boundary_idx:]  # includes the boundary marker message

        pre_tokens = self.estimate_messages_tokens(pre_boundary)
        if pre_tokens < 200:
            return messages

        logger.info(
            f"[Compress] Found context boundary at index {boundary_idx}, "
            f"compressing {len(pre_boundary)} pre-boundary messages "
            f"(~{pre_tokens} tokens) with aggressive ratio"
        )

        target_tokens = max(int(pre_tokens * BOUNDARY_COMPRESSION_RATIO), 100)
        summary = await self._summarize_messages_chunked_for_boundary(
            pre_boundary, target_tokens
        )

        result = []
        if summary:
            result.append({
                "role": "user",
                "content": (
                    f"[旧话题摘要（已结束）]\n{summary}\n\n"
                    "---\n以上是之前话题的简要背景，当前已切换到新话题。"
                ),
            })

        result.extend(post_boundary)

        compressed_tokens = self.estimate_messages_tokens(result)
        logger.info(
            f"[Compress] Boundary compression: {pre_tokens + self.estimate_messages_tokens(post_boundary)} "
            f"-> {compressed_tokens} tokens"
        )
        return result

    async def _summarize_messages_chunked_for_boundary(
        self, messages: list[dict], target_tokens: int
    ) -> str:
        """针对上下文边界前的旧话题消息，使用更激进的摘要策略。

        与普通摘要不同，这里强调"只保留可能对新话题有用的关键信息"。
        """
        if not messages:
            return ""

        text_parts = []
        for msg in messages:
            text_parts.append(self._extract_message_text(msg))

        combined = "".join(text_parts)
        if not combined.strip():
            return ""

        if self.estimate_tokens(combined) > CHUNK_MAX_TOKENS:
            max_chars = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
            combined = combined[:max_chars] + "\n...(更早的内容已省略)..."

        target_chars = target_tokens * CHARS_PER_TOKEN

        _tt = set_tracking_context(TokenTrackingContext(
            operation_type="context_compress",
            operation_detail="boundary_old_topic",
        ))
        try:
            response = await self._cancellable_llm(
                model=self._brain.model,
                max_tokens=target_tokens,
                system=(
                    "你是一个对话压缩助手。用户已切换到新话题，"
                    "请将以下旧话题对话压缩为结构化摘要。\n"
                    "必须保留：\n"
                    "1. 用户身份信息和偏好设定\n"
                    "2. 重要的配置/环境信息（路径、版本、参数等）\n"
                    "3. 关键结论和最终决策（包括具体数值、名称）\n"
                    "4. 用户明确提到的需求和约束条件\n"
                    "5. 已完成的操作及其结果（一句话概括每项）\n"
                    "可以省略：中间调试过程、工具调用原始输出、重复的试错步骤。"
                ),
                messages=[{
                    "role": "user",
                    "content": f"请将以下旧话题对话压缩到 {target_chars} 字以内:\n\n{combined}",
                }],
                use_thinking=False,
            )

            summary = ""
            for block in response.content:
                if block.type == "text":
                    summary += block.text
                elif block.type == "thinking" and hasattr(block, "thinking"):
                    if not summary:
                        summary = block.thinking if isinstance(block.thinking, str) else str(block.thinking)

            return summary.strip() if summary else ""

        except _CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Compress] Boundary summarization failed: {e}")
            return ""
        finally:
            reset_tracking_context(_tt)

    async def _compress_large_tool_results(
        self, messages: list[dict], threshold: int = LARGE_TOOL_RESULT_THRESHOLD
    ) -> list[dict]:
        """对单条过大的 tool_result 内容独立 LLM 压缩"""
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                new_content = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        result_text = str(item.get("content", ""))
                        # 含 OVERFLOW_MARKER 的为 handler 故意放行的长输出（如 get_skill_info），不压缩以免丢失技能全文
                        if OVERFLOW_MARKER in result_text:
                            new_content.append(item)
                            continue
                        result_tokens = self.estimate_tokens(result_text)
                        if result_tokens > threshold:
                            target_tokens = max(int(result_tokens * COMPRESSION_RATIO), 100)
                            compressed_text = await self._llm_compress_text(
                                result_text, target_tokens, context_type="tool_result"
                            )
                            new_item = dict(item)
                            new_item["content"] = compressed_text
                            new_content.append(new_item)
                            logger.info(
                                f"Compressed tool_result from {result_tokens} to "
                                f"~{self.estimate_tokens(compressed_text)} tokens"
                            )
                        else:
                            new_content.append(item)
                    elif isinstance(item, dict) and item.get("type") == "tool_use":
                        input_text = json.dumps(item.get("input", {}), ensure_ascii=False)
                        input_tokens = self.estimate_tokens(input_text)
                        if input_tokens > threshold:
                            target_tokens = max(int(input_tokens * COMPRESSION_RATIO), 100)
                            compressed_input = await self._llm_compress_text(
                                input_text, target_tokens, context_type="tool_input"
                            )
                            new_item = dict(item)
                            new_item["input"] = {"compressed_summary": compressed_input}
                            new_content.append(new_item)
                        else:
                            new_content.append(item)
                    else:
                        new_content.append(item)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result

    async def _llm_compress_text(
        self, text: str, target_tokens: int, context_type: str = "general"
    ) -> str:
        """使用 LLM 压缩一段文本到目标 token 数"""
        max_input = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
        if len(text) > max_input:
            head_size = int(max_input * 0.6)
            tail_size = int(max_input * 0.3)
            text = text[:head_size] + "\n...(中间内容过长已省略)...\n" + text[-tail_size:]

        target_chars = target_tokens * CHARS_PER_TOKEN

        if context_type == "tool_result":
            system_prompt = (
                "你是一个信息压缩助手。请将以下工具执行结果压缩为简洁摘要，"
                "保留关键数据、状态码、错误信息和重要输出，去掉冗余细节。"
            )
        elif context_type == "tool_input":
            system_prompt = (
                "你是一个信息压缩助手。请将以下工具调用参数压缩为简洁摘要，"
                "保留关键参数名和值，去掉冗余内容。"
            )
        else:
            system_prompt = (
                "你是一个对话压缩助手。请将以下对话内容压缩为结构化摘要，"
                "必须保留：用户原始目标、已完成的步骤及结果、当前任务进度、"
                "待处理的问题（AI 的提问和用户的回答）、所有具体数值和配置信息"
                "（端口号、路径、密钥等，不要用模糊描述代替具体值）、下一步计划。"
            )

        _tt = set_tracking_context(TokenTrackingContext(
            operation_type="context_compress",
            operation_detail=context_type,
        ))
        try:
            response = await self._cancellable_llm(
                model=self._brain.model,
                max_tokens=target_tokens,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": f"请将以下内容压缩到 {target_chars} 字以内:\n\n{text}",
                    }
                ],
                use_thinking=False,
            )

            summary = ""
            for block in response.content:
                if block.type == "text":
                    summary += block.text
                elif block.type == "thinking" and hasattr(block, "thinking"):
                    if not summary:
                        summary = block.thinking if isinstance(block.thinking, str) else str(block.thinking)

            if not summary.strip():
                logger.warning("[Compress] LLM returned empty summary, falling back to hard truncation")
                if len(text) > target_chars:
                    head = int(target_chars * 0.7)
                    tail = int(target_chars * 0.2)
                    return text[:head] + "\n...(压缩失败，已截断)...\n" + text[-tail:]
                return text

            return summary.strip()

        except _CancelledError:
            raise
        except Exception as e:
            logger.warning(f"LLM compression failed: {e}")
            if len(text) > target_chars:
                head = int(target_chars * 0.7)
                tail = int(target_chars * 0.2)
                return text[:head] + "\n...(压缩失败，已截断)...\n" + text[-tail:]
            return text
        finally:
            reset_tracking_context(_tt)

    def _extract_message_text(self, msg: dict) -> str:
        """从消息中提取文本内容（包括 tool_use/tool_result 结构化信息）"""
        role = "用户" if msg["role"] == "user" else "助手"
        content = msg.get("content", "")

        if isinstance(content, str):
            return f"{role}: {content}\n"

        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    elif item.get("type") == "tool_use":
                        name = item.get("name", "unknown")
                        input_data = item.get("input", {})
                        input_summary = json.dumps(input_data, ensure_ascii=False)
                        if len(input_summary) > 2000:
                            input_summary = input_summary[:1500] + "...(省略)..." + input_summary[-400:]
                        texts.append(f"[调用工具: {name}, 参数: {input_summary}]")
                    elif item.get("type") == "tool_result":
                        result_text = str(item.get("content", ""))
                        if len(result_text) > 8000:
                            result_text = result_text[:6000] + "...(省略)..." + result_text[-1500:]
                        is_error = item.get("is_error", False)
                        status = "错误" if is_error else "成功"
                        texts.append(f"[工具结果({status}): {result_text}]")
            if texts:
                return f"{role}: {' '.join(texts)}\n"

        return ""

    async def _summarize_messages_chunked(
        self, messages: list[dict], target_tokens: int
    ) -> str:
        """分块 LLM 摘要消息列表"""
        if not messages:
            return ""

        chunks: list[str] = []
        current_chunk = ""
        current_chunk_tokens = 0

        for msg in messages:
            msg_text = self._extract_message_text(msg)
            msg_tokens = self.estimate_tokens(msg_text)

            if current_chunk_tokens + msg_tokens > CHUNK_MAX_TOKENS and current_chunk:
                chunks.append(current_chunk)
                current_chunk = msg_text
                current_chunk_tokens = msg_tokens
            else:
                current_chunk += msg_text
                current_chunk_tokens += msg_tokens

        if current_chunk:
            chunks.append(current_chunk)

        if not chunks:
            return ""

        logger.info(f"Splitting {len(messages)} messages into {len(chunks)} chunks for compression")

        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            chunk_tokens = self.estimate_tokens(chunk)
            chunk_target = max(int(target_tokens / len(chunks)), 100)

            _tt2 = set_tracking_context(TokenTrackingContext(
                operation_type="context_compress",
                operation_detail=f"chunk_{i}",
            ))
            try:
                response = await self._cancellable_llm(
                    model=self._brain.model,
                    max_tokens=chunk_target,
                    system=(
                        "你是一个对话压缩助手。请将以下对话片段压缩为结构化摘要。\n"
                        "必须包含以下部分（如有相关内容）：\n"
                        "1. **对话背景**: 用户的原始目标\n"
                        "2. **用户需求**: 用户明确提出的要求和指令\n"
                        "3. **已完成的步骤**: 执行了哪些操作，结果如何（成功/失败/错误信息）\n"
                        "4. **当前任务进度**: 进行到哪一步了\n"
                        "5. **待处理的问题**: AI 向用户提出了什么问题，用户是否已回答（保留原文）\n"
                        "6. **关键配置/数值**: 端口号、路径、密钥、版本号等具体值（必须保留原始数值）\n"
                        "7. **下一步计划**: 接下来要做什么\n\n"
                        "重要：保留所有具体数值和配置信息，不要用模糊描述代替具体值。"
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"请将以下对话片段（第 {i + 1}/{len(chunks)} 块，"
                                f"约 {chunk_tokens} tokens）压缩到 "
                                f"{chunk_target * CHARS_PER_TOKEN} 字以内:\n\n{chunk}"
                            ),
                        }
                    ],
                    use_thinking=False,
                )

                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                    elif block.type == "thinking" and hasattr(block, "thinking"):
                        if not summary:
                            summary = block.thinking if isinstance(block.thinking, str) else str(block.thinking)

                if not summary.strip():
                    logger.warning(f"[Compress] Chunk {i + 1} returned empty summary")
                    max_chars = chunk_target * CHARS_PER_TOKEN
                    if len(chunk) > max_chars:
                        chunk_summaries.append(
                            chunk[: max_chars // 2] + "\n...(摘要失败，已截断)...\n"
                        )
                    else:
                        chunk_summaries.append(chunk)
                else:
                    chunk_summaries.append(summary.strip())
                    logger.info(
                        f"Chunk {i + 1}/{len(chunks)}: {chunk_tokens} -> "
                        f"~{self.estimate_tokens(summary)} tokens"
                    )

            except _CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to summarize chunk {i + 1}: {e}")
                max_chars = chunk_target * CHARS_PER_TOKEN
                if len(chunk) > max_chars:
                    chunk_summaries.append(
                        chunk[: max_chars // 2] + "\n...(摘要失败，已截断)...\n"
                    )
                else:
                    chunk_summaries.append(chunk)
            finally:
                reset_tracking_context(_tt2)

        combined = "\n---\n".join(chunk_summaries)
        combined_tokens = self.estimate_tokens(combined)

        if combined_tokens > target_tokens * 2 and len(chunks) > 1:
            logger.info(f"Combined summary still large ({combined_tokens} tokens), consolidating...")
            combined = await self._llm_compress_text(
                combined, target_tokens, context_type="conversation"
            )

        return combined

    async def _compress_further(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """递归压缩：减少保留的最近组数量"""
        current_tokens = self.estimate_messages_tokens(messages)
        if current_tokens <= max_tokens:
            return messages

        groups = self.group_messages(messages)
        recent_group_count = min(4, len(groups))

        if len(groups) <= recent_group_count:
            logger.warning("Cannot compress further, attempting final tool_result compression")
            return await self._compress_large_tool_results(messages, threshold=1000)

        early_groups = groups[:-recent_group_count]
        recent_groups = groups[-recent_group_count:]

        early_messages = [msg for group in early_groups for msg in group]
        recent_messages = [msg for group in recent_groups for msg in group]

        early_tokens = self.estimate_messages_tokens(early_messages)
        target = max(int(early_tokens * COMPRESSION_RATIO), 100)
        summary = await self._summarize_messages_chunked(early_messages, target)

        compressed = self._inject_summary_into_recent(summary, recent_messages)

        compressed_tokens = self.estimate_messages_tokens(compressed)
        logger.info(f"Further compressed from {current_tokens} to {compressed_tokens} tokens")
        return compressed

    @staticmethod
    def _inject_summary_into_recent(summary: str, recent_messages: list[dict]) -> list[dict]:
        """将摘要注入到 recent_messages 中，避免插入假 assistant 回复。

        策略：找到 recent_messages 中第一条 user 消息，将摘要作为前缀注入。
        如果第一条不是 user，则在最前面插入一条 user 摘要消息。
        """
        if not summary:
            return list(recent_messages)

        summary_prefix = f"[之前的对话摘要]\n{summary}\n\n---\n"
        result = list(recent_messages)

        if result and result[0].get("role") == "user":
            first = result[0]
            content = first.get("content", "")
            if isinstance(content, str):
                result[0] = {**first, "content": summary_prefix + content}
            else:
                result.insert(0, {"role": "user", "content": summary_prefix.rstrip()})
        else:
            result.insert(0, {"role": "user", "content": summary_prefix.rstrip()})

        return result

    def _hard_truncate_if_needed(
        self, messages: list[dict], hard_limit: int, memory_manager: object | None = None
    ) -> list[dict]:
        """硬保底：当 LLM 压缩后仍超过 hard_limit，直接硬截断"""
        current_tokens = self.estimate_messages_tokens(messages)
        if current_tokens <= hard_limit:
            return messages

        logger.error(
            f"[HardTruncate] Still {current_tokens} tokens > hard_limit {hard_limit}. "
            f"Applying hard truncation."
        )

        truncated = list(messages)
        dropped_messages: list[dict] = []
        while len(truncated) > 2 and self.estimate_messages_tokens(truncated) > hard_limit:
            removed = truncated.pop(0)
            dropped_messages.append(removed)
            logger.warning(f"[HardTruncate] Dropped earliest message (role={removed.get('role', '?')})")

        # 将被丢弃的消息入队到提取队列，避免永久丢失
        if dropped_messages and memory_manager is not None:
            self._enqueue_dropped_for_extraction(dropped_messages, memory_manager)

        if self.estimate_messages_tokens(truncated) > hard_limit:
            max_chars_per_msg = (hard_limit * CHARS_PER_TOKEN) // max(len(truncated), 1)
            for i, msg in enumerate(truncated):
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > max_chars_per_msg:
                    keep_head = int(max_chars_per_msg * 0.7)
                    keep_tail = int(max_chars_per_msg * 0.2)
                    truncated[i] = {
                        **msg,
                        "content": (
                            content[:keep_head]
                            + "\n\n...[内容过长已硬截断]...\n\n"
                            + content[-keep_tail:]
                        ),
                    }
                elif isinstance(content, list):
                    new_content = []
                    for item in content:
                        if isinstance(item, dict):
                            for key in ("text", "content"):
                                val = item.get(key, "")
                                if isinstance(val, str) and len(val) > max_chars_per_msg:
                                    keep_h = int(max_chars_per_msg * 0.7)
                                    keep_t = int(max_chars_per_msg * 0.2)
                                    item = dict(item)
                                    item[key] = val[:keep_h] + "\n...[硬截断]...\n" + val[-keep_t:]
                        new_content.append(item)
                    truncated[i] = {**msg, "content": new_content}

        truncated.insert(0, {
            "role": "user",
            "content": (
                "[系统提示] 上下文因超出模型限制已被紧急截断，早期对话内容可能丢失。"
                "请基于当前可见的消息继续处理，如信息不足请询问用户。"
            ),
        })

        final_tokens = self.estimate_messages_tokens(truncated)
        logger.warning(
            f"[HardTruncate] Final: {final_tokens} tokens "
            f"(hard_limit={hard_limit}, messages={len(truncated)})"
        )
        return truncated

    @staticmethod
    def _enqueue_dropped_for_extraction(
        dropped: list[dict], memory_manager: object
    ) -> None:
        """将硬截断丢弃的消息入队到提取队列"""
        store = getattr(memory_manager, "store", None)
        if store is None:
            return
        session_id = getattr(memory_manager, "_current_session_id", None) or "hard_truncate"
        try:
            enqueued = 0
            for i, msg in enumerate(dropped):
                content = msg.get("content", "")
                if not content or not isinstance(content, str) or len(content) < 20:
                    continue
                store.enqueue_extraction(
                    session_id=session_id,
                    turn_index=i,
                    content=content,
                    tool_calls=msg.get("tool_calls"),
                    tool_results=msg.get("tool_results"),
                )
                enqueued += 1
            if enqueued:
                logger.info(f"[HardTruncate] Enqueued {enqueued} dropped messages for memory extraction")
        except Exception as e:
            logger.warning(f"[HardTruncate] Failed to enqueue dropped messages: {e}")
