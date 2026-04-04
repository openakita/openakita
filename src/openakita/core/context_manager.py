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

from ..tracing.tracer import get_tracer
from .context_utils import DEFAULT_MAX_CONTEXT_TOKENS
from .context_utils import estimate_tokens as _shared_estimate_tokens
from .context_utils import get_max_context_tokens as _shared_get_max_context_tokens
from .token_tracking import TokenTrackingContext, reset_tracking_context, set_tracking_context
from .tool_executor import OVERFLOW_MARKER

logger = logging.getLogger(__name__)
CHARS_PER_TOKEN = 2  # JSON 序列化后约 2 字符 = 1 token
CHUNK_MAX_TOKENS = 30000  # 每次发给 LLM 压缩的单块上限
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
        self._token_cache: dict[int, int] = {}
        self._tools_tokens_cache: int | None = None

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

    def get_max_context_tokens(self, conversation_id: str | None = None) -> int:
        """动态获取当前模型的可用上下文 token 数。

        Fallback 链（从精确到宽泛）：
        1. 按端点名精确匹配 → 读取 context_window 并计算可用预算
        2. 名称匹配失败时，取最高优先级端点的 context_window 计算
        3. 以上均失败时返回 DEFAULT_MAX_CONTEXT_TOKENS (160K)

        计算公式：(context_window - output_reserve) * 0.95
        - context_window < 8192 视为无效，使用兜底值 200000
        - output_reserve = min(max_tokens or 4096, context_window / 3)

        Args:
            conversation_id: 对话 ID（用于识别 per-conversation 端点覆盖）
        """
        return _shared_get_max_context_tokens(self._brain, conversation_id=conversation_id)

    @staticmethod
    def _calc_context_budget(ep, fallback_window: int) -> int:
        """从端点配置计算可用上下文预算。"""
        ctx = getattr(ep, "context_window", 0) or 0
        if ctx < 8192:
            ctx = fallback_window
        output_reserve = ep.max_tokens or 4096
        output_reserve = min(output_reserve, ctx // 3)
        result = int((ctx - output_reserve) * 0.95)
        if result < 4096:
            return DEFAULT_MAX_CONTEXT_TOKENS
        return result

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量（中英文感知）。"""
        return _shared_estimate_tokens(text)

    @staticmethod
    def static_estimate_tokens(text: str) -> int:
        """静态版 estimate_tokens，供外部模块无需实例即可调用。"""
        return _shared_estimate_tokens(text)

    _IMAGE_TOKEN_ESTIMATE = 1600
    _VIDEO_TOKEN_ESTIMATE = 4800

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """
        估算消息列表的 token 数量（with content-hash caching）。

        对每条消息的 content 使用与 estimate_tokens 相同的中英文感知算法，
        并为每条消息加固定结构开销（role / tool_use_id 等约 10 tokens）。
        多媒体块（图片/视频）使用固定估算值，避免对 base64 数据做文本 token 计算。
        """
        total = 0
        for msg in messages:
            total += self._estimate_single_message_tokens(msg)
        return max(total, 1)

    def _estimate_single_message_tokens(self, msg: dict) -> int:
        """Estimate tokens for a single message with caching by content hash."""
        content = msg.get("content", "")
        if isinstance(content, str):
            cache_key = hash(content)
        elif isinstance(content, list):
            try:
                cache_key = hash(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str))
            except (TypeError, ValueError):
                cache_key = None
        else:
            cache_key = None
        if cache_key is not None:
            cached = self._token_cache.get(cache_key)
            if cached is not None:
                return cached

        tokens = 0
        if isinstance(content, str):
            tokens = self.estimate_tokens(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    block_type = item.get("type", "")
                    if block_type in ("image", "image_url"):
                        tokens += self._IMAGE_TOKEN_ESTIMATE
                    elif block_type in ("video", "video_url"):
                        tokens += self._VIDEO_TOKEN_ESTIMATE
                    else:
                        text = item.get("text", "") or item.get("content", "")
                        if isinstance(text, str) and text:
                            tokens += self.estimate_tokens(text)
                        else:
                            tokens += self.estimate_tokens(
                                json.dumps(item, ensure_ascii=False, default=str)
                            )
                elif isinstance(item, str):
                    tokens += self.estimate_tokens(item)
        tokens += 10  # 每条消息的结构开销

        if cache_key is not None and len(self._token_cache) < 10000:
            self._token_cache[cache_key] = tokens
        return tokens

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

    def pre_request_cleanup(self, messages: list[dict]) -> list[dict]:
        """请求前轻量清理 (microcompact)。

        零 LLM 调用成本: 过期工具结果清空、大结果预览、旧 thinking 移除。
        在 compress_if_needed 之前调用。
        """
        from .microcompact import microcompact
        return microcompact(messages)

    def snip_old_segments(self, messages: list[dict]) -> tuple[list[dict], int]:
        """直接丢弃最早的对话段 (History Snip)。

        零 LLM 调用成本，适用于超长对话。
        """
        from .microcompact import snip_old_segments
        return snip_old_segments(messages)

    async def reactive_compact(
        self,
        messages: list[dict],
        *,
        system_prompt: str = "",
        tools: list | None = None,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """API 返回 413/prompt-too-long 后的紧急压缩。

        比 compress_if_needed 更激进: 先 snip 再压缩，确保能放进上下文窗口。
        """
        logger.warning("[ReactiveCompact] 413/overflow triggered, performing emergency compaction")

        # Step 1: History snip (zero cost)
        messages, snipped = self.snip_old_segments(messages)
        if snipped > 0:
            logger.info(f"[ReactiveCompact] Snipped {snipped} messages")

        # Step 2: Microcompact
        messages = self.pre_request_cleanup(messages)

        # Step 3: If still too large, run full compress with tighter budget
        max_tokens = self.get_max_context_tokens(conversation_id=conversation_id)
        tighter_budget = int(max_tokens * 0.7)  # 30% more aggressive
        return await self.compress_if_needed(
            messages,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=tighter_budget,
            conversation_id=conversation_id,
        )

    async def compress_if_needed(
        self,
        messages: list[dict],
        *,
        system_prompt: str = "",
        tools: list | None = None,
        max_tokens: int | None = None,
        memory_manager: object | None = None,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """
        如果上下文接近限制，执行压缩 (autocompact)。

        三层压缩策略:
        - Layer 0 (microcompact): 调用方在请求前手动调用 pre_request_cleanup()
        - Layer 1 (autocompact): 本方法 — 阈值触发的 LLM 摘要压缩
        - Layer 2 (reactive): API 返回 413 时调用 reactive_compact()

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
            conversation_id: 对话 ID（用于识别 per-conversation 端点覆盖）

        Returns:
            压缩后的消息列表
        """
        max_tokens = max_tokens or self.get_max_context_tokens(conversation_id=conversation_id)

        system_tokens = self.estimate_tokens(system_prompt)

        tools_tokens = 0
        if tools:
            try:
                tools_text = json.dumps(tools, ensure_ascii=False, default=str)
                tools_tokens = self.estimate_tokens(tools_text)
            except Exception:
                tools_tokens = len(tools) * 200

        hard_limit = max_tokens - system_tokens - tools_tokens - 500
        min_hard_limit = max(min(1024, int(max_tokens * 0.3)), 256)
        if hard_limit < min_hard_limit:
            logger.warning(
                f"[Compress] hard_limit too small ({hard_limit}), "
                f"max={max_tokens}, system={system_tokens}, tools={tools_tokens}. "
                f"Falling back to {min_hard_limit}."
            )
            hard_limit = min_hard_limit
        from ..config import settings as _settings
        _threshold = _settings.context_compression_threshold
        soft_limit = int(hard_limit * _threshold)

        _overhead_bytes = len(system_prompt.encode("utf-8")) if system_prompt else 0
        if tools:
            try:
                _overhead_bytes += len(json.dumps(tools, ensure_ascii=False, default=str).encode("utf-8"))
            except Exception:
                _overhead_bytes += len(tools) * 800

        current_tokens = self.estimate_messages_tokens(messages)

        logger.info(
            f"[Compress] Budget: max_ctx={max_tokens}, system={system_tokens}, "
            f"tools={tools_tokens}({len(tools) if tools else 0}个), "
            f"hard={hard_limit}, soft={soft_limit}, msgs={current_tokens}({len(messages)}条)"
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
        if _settings.context_enable_tool_compression:
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

        recent_group_count = min(_settings.context_min_recent_turns, len(groups))

        if len(groups) <= recent_group_count:
            messages = await self._compress_large_tool_results(messages, threshold=2000)
            return _end_ctx_span(self._hard_truncate_if_needed(
                messages, hard_limit, memory_manager, overhead_bytes=_overhead_bytes,
            ))

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
        target_summary_tokens = max(int(early_tokens * _settings.context_compression_ratio), 200)
        summary = await self._summarize_messages_chunked(early_messages, target_summary_tokens)

        if summary and memory_manager is not None:
            try:
                hook = getattr(memory_manager, "on_summary_generated", None)
                if hook:
                    await hook(summary)
            except Exception as e:
                logger.warning(f"[Compress] Relational backfill from summary failed: {e}")

        compressed = self._inject_summary_into_recent(summary, recent_messages)

        compressed_tokens = self.estimate_messages_tokens(compressed)
        if compressed_tokens <= soft_limit:
            logger.info(f"Compressed context from {current_tokens} to {compressed_tokens} tokens")
            return _end_ctx_span(compressed)

        # Step 4: 递归压缩
        logger.warning(f"Context still large ({compressed_tokens} tokens), compressing further...")
        compressed = await self._compress_further(compressed, soft_limit)

        # Step 5: 硬保底
        return _end_ctx_span(self._hard_truncate_if_needed(
            compressed, hard_limit, memory_manager, overhead_bytes=_overhead_bytes,
        ))

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

        from ..config import settings as _settings
        target_tokens = max(int(pre_tokens * _settings.context_boundary_compression_ratio), 100)
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
                    "6. 用户设定的行为规则（如「每次先做X」「不要Y」「必须先Z」等），必须原文保留\n"
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
        self, messages: list[dict], threshold: int | None = None
    ) -> list[dict]:
        """对单条过大的 tool_result 内容并行 LLM 压缩"""
        if threshold is None:
            from ..config import settings as _settings
            threshold = _settings.context_large_tool_threshold

        # Phase 1: Collect all large items that need compression
        compress_jobs: list[tuple[int, int, str, str, int]] = []  # (msg_idx, item_idx, text, type, target)
        for msg_idx, msg in enumerate(messages):
            content = msg.get("content", "")
            if not isinstance(content, list):
                continue
            for item_idx, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    result_text = str(item.get("content", ""))
                    if OVERFLOW_MARKER in result_text:
                        continue
                    result_tokens = self.estimate_tokens(result_text)
                    if result_tokens > threshold:
                        from ..config import settings as _s
                        target_tokens = max(int(result_tokens * _s.context_compression_ratio), 100)
                        compress_jobs.append((msg_idx, item_idx, result_text, "tool_result", target_tokens))
                elif isinstance(item, dict) and item.get("type") == "tool_use":
                    input_text = json.dumps(item.get("input", {}), ensure_ascii=False)
                    input_tokens = self.estimate_tokens(input_text)
                    if input_tokens > threshold:
                        from ..config import settings as _s
                        target_tokens = max(int(input_tokens * _s.context_compression_ratio), 100)
                        compress_jobs.append((msg_idx, item_idx, input_text, "tool_input", target_tokens))

        if not compress_jobs:
            return messages

        # Phase 2: Parallel compression
        async def _compress_one(text: str, ctx_type: str, target: int) -> str:
            return await self._llm_compress_text(text, target, context_type=ctx_type)

        tasks = [_compress_one(text, ctx_type, target) for _, _, text, ctx_type, target in compress_jobs]
        compressed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Phase 3: Apply compressed results back
        result = [dict(msg) for msg in messages]
        for job, compressed in zip(compress_jobs, compressed_results):
            msg_idx, item_idx, original_text, ctx_type, _ = job
            if isinstance(compressed, Exception):
                logger.warning(f"Tool result compression failed: {compressed}")
                continue

            msg = result[msg_idx]
            content = list(msg.get("content", []))
            item = dict(content[item_idx])
            original_tokens = self.estimate_tokens(original_text)

            if ctx_type == "tool_result":
                item["content"] = compressed
                logger.info(
                    f"Compressed tool_result from {original_tokens} to "
                    f"~{self.estimate_tokens(compressed)} tokens"
                )
            elif ctx_type == "tool_input":
                item["input"] = {"compressed_summary": compressed}

            content[item_idx] = item
            result[msg_idx] = {**msg, "content": content}

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
                "（端口号、路径、密钥等，不要用模糊描述代替具体值）、下一步计划、"
                "用户设定的行为规则（如「每次先做X」「不要Y」「必须先Z」等，必须原文保留）。"
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
                        from .tool_executor import smart_truncate as _st
                        name = item.get("name", "unknown")
                        input_data = item.get("input", {})
                        input_summary = json.dumps(input_data, ensure_ascii=False)
                        input_summary, _ = _st(input_summary, 3000, save_full=False, label="compress_input")
                        texts.append(f"[调用工具: {name}, 参数: {input_summary}]")
                    elif item.get("type") == "tool_result":
                        from .tool_executor import smart_truncate as _st
                        result_text = str(item.get("content", ""))
                        result_text, _ = _st(result_text, 10000, save_full=False, label="compress_result")
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

        chunk_target = max(int(target_tokens / len(chunks)), 100)

        async def _summarize_one_chunk(i: int, chunk: str) -> str:
            chunk_tokens = self.estimate_tokens(chunk)
            _tt2 = set_tracking_context(TokenTrackingContext(
                operation_type="context_compress",
                operation_detail=f"chunk_{i}",
            ))
            try:
                from ..prompt.compact import format_compact_summary, get_compact_prompt
                response = await self._cancellable_llm(
                    model=self._brain.model,
                    max_tokens=chunk_target,
                    system=get_compact_prompt(),
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
                    return chunk[: max_chars // 2] + "\n...(摘要失败，已截断)...\n" if len(chunk) > max_chars else chunk
                summary = format_compact_summary(summary)
                logger.info(
                    f"Chunk {i + 1}/{len(chunks)}: {chunk_tokens} -> "
                    f"~{self.estimate_tokens(summary)} tokens"
                )
                return summary.strip()

            except _CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to summarize chunk {i + 1}: {e}")
                max_chars = chunk_target * CHARS_PER_TOKEN
                return chunk[: max_chars // 2] + "\n...(摘要失败，已截断)...\n" if len(chunk) > max_chars else chunk
            finally:
                reset_tracking_context(_tt2)

        # Parallel summarization
        tasks = [_summarize_one_chunk(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        chunk_summaries = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Chunk {i + 1} summarization raised: {result}")
                max_chars = chunk_target * CHARS_PER_TOKEN
                fallback = chunks[i][:max_chars // 2] + "\n...(摘要异常)...\n" if len(chunks[i]) > max_chars else chunks[i]
                chunk_summaries.append(fallback)
            else:
                chunk_summaries.append(result)

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
        from ..config import settings as _settings
        target = max(int(early_tokens * _settings.context_compression_ratio), 100)
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

        summary_prefix = (
            f"[之前的对话摘要]\n{summary}\n\n"
            "请直接从中断处继续，不要确认摘要、不要回顾之前的工作。\n\n---\n"
        )
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

    @staticmethod
    def rewrite_after_compression(
        messages: list[dict],
        *,
        plan_section: str = "",
        scratchpad_summary: str = "",
        completed_tools: list[str] | None = None,
        task_description: str = "",
    ) -> list[dict]:
        """
        上下文压缩后的 Prompt 重写 (Agent Harness: Context Rewriting)。

        在压缩完成后注入结构化方向提示，防止 Agent 在压缩后"失忆"。
        通过确定性规则（不用 LLM）重新注入关键信息。

        Args:
            messages: 压缩后的消息列表
            plan_section: 当前 Plan 状态文本（来自 PlanHandler.get_plan_prompt_section）
            scratchpad_summary: 工作记忆摘要（来自 Scratchpad）
            completed_tools: 已执行的工具列表
            task_description: 原始任务描述
        """
        if not messages:
            return messages

        rewrite_parts: list[str] = []

        rewrite_parts.append("[对话摘要]")

        if task_description:
            preview = task_description[:300]
            if len(task_description) > 300:
                preview += "..."
            rewrite_parts.append(f"原始任务: {preview}")

        if plan_section:
            # 截断保护：Plan 状态过长时只保留前 2000 字符，避免二次压缩时被丢弃
            _ps = plan_section if len(plan_section) <= 2000 else plan_section[:2000] + "\n... (计划状态已截断)"
            rewrite_parts.append(f"\n当前计划状态:\n{_ps}")

        if completed_tools:
            unique_tools = list(dict.fromkeys(completed_tools))
            tools_summary = ", ".join(unique_tools[-10:])
            rewrite_parts.append(f"已使用工具: {tools_summary}")

        if scratchpad_summary:
            rewrite_parts.append(f"\n工作记忆:\n{scratchpad_summary}")

        rewrite_parts.append(
            "\n请继续正常处理，保持一贯的回复质量和详细程度。"
        )

        rewrite_text = "\n".join(rewrite_parts)

        # 找到压缩后消息中最后一条 user 消息，在其后追加重写提示
        # 或者在消息列表末尾追加
        result = list(messages)
        last_user_idx = -1
        for i in range(len(result) - 1, -1, -1):
            if result[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx >= 0:
            content = result[last_user_idx].get("content", "")
            if isinstance(content, str):
                result[last_user_idx] = {
                    **result[last_user_idx],
                    "content": content + f"\n\n{rewrite_text}",
                }
            else:
                result.append({"role": "user", "content": rewrite_text})
        else:
            result.append({"role": "user", "content": rewrite_text})

        logger.info("[ContextRewriter] Injected post-compression orientation prompt")
        return result

    MAX_PAYLOAD_BYTES = 1_800_000  # 1.8MB — 大多数 API 限制在 2MB

    def _hard_truncate_if_needed(
        self, messages: list[dict], hard_limit: int, memory_manager: object | None = None,
        overhead_bytes: int = 0,
    ) -> list[dict]:
        """硬保底：当 LLM 压缩后仍超过 hard_limit，直接硬截断。

        Uses prefix-sum + binary search for O(n log n) instead of O(n^2).
        """
        current_tokens = self.estimate_messages_tokens(messages)
        need_token_truncation = current_tokens > hard_limit

        if not need_token_truncation:
            # token 预算内，仍需检查 payload 大小（base64 图片可能导致 payload 超限）
            return self._strip_oversized_payload(messages, overhead_bytes=overhead_bytes)

        logger.error(
            f"[HardTruncate] Still {current_tokens} tokens > hard_limit {hard_limit}. "
            f"Applying hard truncation."
        )

        # Build per-message token array and suffix sum
        n = len(messages)
        msg_tokens = [self._estimate_single_message_tokens(msg) for msg in messages]

        # Binary search: find smallest k such that sum(msg_tokens[k:]) <= hard_limit
        # Suffix sum: suffix[i] = sum(msg_tokens[i:])
        suffix = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            suffix[i] = suffix[i + 1] + msg_tokens[i]

        # Find the smallest start index where suffix fits budget (keep at least 2 messages)
        drop_until = 0
        max_drop = max(0, n - 2)
        lo, hi = 0, max_drop
        while lo <= hi:
            mid = (lo + hi) // 2
            if suffix[mid] <= hard_limit:
                hi = mid - 1
            else:
                lo = mid + 1
        drop_until = lo

        truncated = list(messages[drop_until:])
        dropped_messages = list(messages[:drop_until])
        if dropped_messages:
            logger.warning(f"[HardTruncate] Dropped {len(dropped_messages)} earliest messages")

        truncated = self._sanitize_tool_pairs(truncated, dropped_messages)

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
                    new_content = self._hard_truncate_content_blocks(
                        content, max_chars_per_msg
                    )
                    truncated[i] = {**msg, "content": new_content}

        truncated.insert(0, {
            "role": "user",
            "content": (
                "[context_note: 早期对话已自动整理] "
                "请正常回复，保持详细程度和输出质量不变。"
            ),
        })

        final_tokens = self.estimate_messages_tokens(truncated)
        logger.warning(
            f"[HardTruncate] Final: {final_tokens} tokens "
            f"(hard_limit={hard_limit}, messages={len(truncated)})"
        )
        return self._strip_oversized_payload(truncated, overhead_bytes=overhead_bytes)

    @staticmethod
    def _sanitize_tool_pairs(
        messages: list[dict], dropped: list[dict] | None = None,
    ) -> list[dict]:
        """Remove unpaired tool_calls / tool-result messages to prevent API 400.

        After hard truncation drops messages from the front, orphaned ``tool``
        role messages (whose ``assistant(tool_calls)`` was removed) or orphaned
        ``assistant(tool_calls)`` messages (whose ``tool`` results are missing)
        will cause the LLM API to reject the request.
        """
        if not messages:
            return messages

        answered_ids: set[str] = set()
        declared_ids: set[str] = set()

        for msg in messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                answered_ids.add(msg["tool_call_id"])
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if tc.get("id"):
                        declared_ids.add(tc["id"])

        result: list[dict] = []
        skip_ids: set[str] = set()

        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant" and msg.get("tool_calls"):
                tc_ids = [
                    tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")
                ]
                missing = [tid for tid in tc_ids if tid not in answered_ids]
                if missing:
                    skip_ids.update(tc_ids)
                    if dropped is not None:
                        dropped.append(msg)
                    logger.warning(
                        f"[HardTruncate] Stripped assistant(tool_calls) with "
                        f"{len(missing)} missing results"
                    )
                    continue
                result.append(msg)
            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in skip_ids:
                    if dropped is not None:
                        dropped.append(msg)
                    continue
                if tc_id and tc_id not in declared_ids:
                    if dropped is not None:
                        dropped.append(msg)
                    logger.warning(
                        "[HardTruncate] Dropped orphaned tool message "
                        f"(no assistant declares tool_call_id={tc_id[:20]})"
                    )
                    continue
                result.append(msg)
            else:
                result.append(msg)

        return result or [{"role": "user", "content": "（对话上下文不可用）"}]

    def _strip_oversized_payload(
        self, messages: list[dict], *, overhead_bytes: int = 0,
    ) -> list[dict]:
        """检查序列化 payload 大小，超过 API 限制时移除媒体内容。

        Args:
            overhead_bytes: system prompt + tools 等非 message 部分的 byte 大小,
                           从 MAX_PAYLOAD_BYTES 预算中扣除。
        """
        effective_limit = self.MAX_PAYLOAD_BYTES - overhead_bytes
        if effective_limit < 200_000:
            effective_limit = 200_000

        payload_size = sum(
            len(json.dumps(msg, ensure_ascii=False, default=str).encode("utf-8"))
            for msg in messages
        )
        if payload_size <= effective_limit:
            return messages

        logger.warning(
            f"[PayloadGuard] Serialized payload ~{payload_size} bytes "
            f"> {effective_limit} limit (overhead={overhead_bytes}). "
            f"Stripping media from history."
        )
        result = list(messages)
        budget_per_msg = effective_limit // max(len(result), 1)
        for i, msg in enumerate(result):
            content = msg.get("content", "")
            if isinstance(content, list):
                result[i] = {
                    **msg,
                    "content": self._hard_truncate_content_blocks(content, budget_per_msg),
                }
        return result

    _MEDIA_BLOCK_TYPES = frozenset({
        "image", "image_url", "video", "video_url", "audio", "input_audio",
    })

    @classmethod
    def _hard_truncate_content_blocks(
        cls, content: list, max_chars: int,
    ) -> list:
        """截断 content block 列表中的大型内容（图片/视频/大文本等）。"""
        new_content: list = []
        for item in content:
            if not isinstance(item, dict):
                new_content.append(item)
                continue

            item_type = item.get("type", "")

            if item_type in cls._MEDIA_BLOCK_TYPES:
                label = {"image": "图片", "image_url": "图片", "video": "视频",
                         "video_url": "视频", "audio": "音频", "input_audio": "音频"
                         }.get(item_type, "媒体")
                new_content.append({
                    "type": "text",
                    "text": f"[{label}内容已移除以节省上下文空间]",
                })
                logger.warning(f"[HardTruncate] Stripped {item_type} block to free context")
                continue

            truncated_item = dict(item)
            for key in ("text", "content"):
                val = truncated_item.get(key, "")
                if isinstance(val, str) and len(val) > max_chars:
                    keep_h = int(max_chars * 0.7)
                    keep_t = int(max_chars * 0.2)
                    truncated_item[key] = (
                        val[:keep_h] + "\n...[硬截断]...\n" + val[-keep_t:]
                    )

            item_size = len(json.dumps(truncated_item, ensure_ascii=False, default=str))
            if item_size > max_chars:
                new_content.append({
                    "type": "text",
                    "text": f"[{item_type or 'content'} 数据过大已移除 "
                            f"(原始 {item_size} 字符)]",
                })
                logger.warning(
                    f"[HardTruncate] Replaced oversized {item_type} block "
                    f"({item_size} chars > {max_chars} limit)"
                )
                continue

            new_content.append(truncated_item)
        return new_content

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
