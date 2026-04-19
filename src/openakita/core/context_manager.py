"""
Context manager

Context compression/management logic extracted from agent.py. Responsibilities:
- Estimate token counts
- Group messages (preserve tool_calls/tool_result pairing)
- LLM chunked summary compression
- Recursive compression
- Hard-truncation safety net
- Dynamic context window calculation
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
CHARS_PER_TOKEN = 2  # After JSON serialization approximately 2 chars = 1 token
CHUNK_MAX_TOKENS = 30000  # Upper bound per chunk sent to LLM for compression
CONTEXT_BOUNDARY_MARKER = "[Context boundary]"  # Topic-switch boundary marker


class _CancelledError(Exception):
    """Internal cancellation signal used by ContextManager; propagated up and converted to UserCancelledError at the Agent layer."""

    pass


class ContextManager:
    """
    Context compression and management.

    When conversation context approaches the LLM's context window limit,
    uses LLM chunked summaries to compress earlier conversation while preserving
    the integrity of recent tool interactions.
    """

    def __init__(self, brain: Any, cancel_event: asyncio.Event | None = None) -> None:
        """
        Args:
            brain: Brain instance used for LLM calls
            cancel_event: Optional cancel event; when set, interrupts the compression LLM call
        """
        self._brain = brain
        self._cancel_event = cancel_event
        self._token_cache: dict[int, int] = {}
        self._tools_tokens_cache: int | None = None
        self._previous_summary: str = ""

    def set_cancel_event(self, event: asyncio.Event | None) -> None:
        """Update cancel_event (set by the Agent at the start of each task)."""
        self._cancel_event = event

    async def _cancellable_llm(self, **kwargs):
        """LLM call that can be interrupted by cancel_event (direct await, no thread)."""
        logger.debug("[ContextManager] _cancellable_llm issuing LLM call")
        coro = self._brain.messages_create_async(**kwargs)
        if not self._cancel_event:
            return await coro
        task = asyncio.create_task(coro)
        cancel_waiter = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {task, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if task in done:
            logger.debug("[ContextManager] _cancellable_llm LLM call completed")
            return task.result()
        logger.info("[ContextManager] _cancellable_llm cancelled by user")
        raise _CancelledError("Context compression cancelled by user")

    def get_max_context_tokens(self, conversation_id: str | None = None) -> int:
        """Dynamically get the available context token count for the current model.

        Fallback chain (precise to broad):
        1. Exact match by endpoint name -> read context_window and compute usable budget
        2. If name match fails, use the context_window of the highest-priority endpoint
        3. If all the above fail, return DEFAULT_MAX_CONTEXT_TOKENS (160K)

        Formula: (context_window - output_reserve) * 0.95
        - context_window < 8192 is considered invalid; uses fallback value 200000
        - output_reserve = min(max_tokens or 4096, context_window / 3)

        Args:
            conversation_id: Conversation ID (used to identify per-conversation endpoint overrides)
        """
        return _shared_get_max_context_tokens(self._brain, conversation_id=conversation_id)

    @staticmethod
    def _calc_context_budget(ep, fallback_window: int) -> int:
        """Compute the available context budget from endpoint configuration."""
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
        """Estimate the token count of text (CJK-aware)."""
        return _shared_estimate_tokens(text)

    @staticmethod
    def static_estimate_tokens(text: str) -> int:
        """Static version of estimate_tokens for callers without an instance."""
        return _shared_estimate_tokens(text)

    _IMAGE_TOKEN_ESTIMATE = 1600
    _VIDEO_TOKEN_ESTIMATE = 4800

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """
        Estimate the token count of a message list (with content-hash caching).

        For each message's content, uses the same CJK-aware algorithm as estimate_tokens,
        and adds a fixed structural overhead per message (role / tool_use_id etc., ~10 tokens).
        Multimedia blocks (images/videos) use fixed estimates to avoid running text-token math on base64 data.
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
                cache_key = hash(
                    json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
                )
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
        tokens += 10  # Per-message structural overhead

        if cache_key is not None and len(self._token_cache) < 10000:
            self._token_cache[cache_key] = tokens
        return tokens

    @staticmethod
    def group_messages(messages: list[dict]) -> list[list[dict]]:
        """
        Group messages into "tool interaction groups" so that tool_calls/tool pairs are not split.

        Grouping rules:
        - An assistant message containing tool_use -> grouped with the following tool_result messages
        - Other messages each form their own group
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
                    isinstance(item, dict) and item.get("type") == "tool_use" for item in content
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
        """Lightweight pre-request cleanup (microcompact).

        Zero LLM call cost: clear expired tool results, preview large results, remove old thinking.
        Called before compress_if_needed.
        """
        from .microcompact import microcompact

        return microcompact(messages)

    def snip_old_segments(self, messages: list[dict]) -> tuple[list[dict], int]:
        """Drop the earliest conversation segment directly (History Snip).

        Zero LLM call cost; suitable for very long conversations.
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
        """Emergency compression after the API returns 413/prompt-too-long.

        More aggressive than compress_if_needed: snip first, then compress, to ensure it fits the context window.
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
        Compress the context if it is near the limit (autocompact).

        Three-layer compression strategy:
        - Layer 0 (microcompact): caller manually invokes pre_request_cleanup() before the request
        - Layer 1 (autocompact): this method — threshold-triggered LLM summary compression
        - Layer 2 (reactive): reactive_compact() invoked when the API returns 413

        Strategy:
        0. Pre-compression: quick rule-based extraction + notify MemoryManager
        1. Independently LLM-compress individual oversized tool_result items first
        2. Group by tool interaction groups
        3. Keep recent groups; LLM-summarize the early groups
        4. Recursive compression / hard-truncation safety net

        Args:
            messages: Message list
            system_prompt: System prompt (used to estimate token usage)
            tools: Tool definition list (used to estimate token usage)
            max_tokens: Maximum token count
            memory_manager: MemoryManager instance (v2: extract memory before compression)
            conversation_id: Conversation ID (used to identify per-conversation endpoint overrides)

        Returns:
            The compressed message list.
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
                _overhead_bytes += len(
                    json.dumps(tools, ensure_ascii=False, default=str).encode("utf-8")
                )
            except Exception:
                _overhead_bytes += len(tools) * 800

        current_tokens = self.estimate_messages_tokens(messages)

        logger.info(
            f"[Compress] Budget: max_ctx={max_tokens}, system={system_tokens}, "
            f"tools={tools_tokens}({len(tools) if tools else 0} items), "
            f"hard={hard_limit}, soft={soft_limit}, msgs={current_tokens}({len(messages)} msgs)"
        )

        if current_tokens <= soft_limit:
            return messages

        # v2: pre-compression memory extraction — ensures messages about to be compressed are saved to memory first
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
            """End ctx_span, fix tool pairing, and return the result."""
            result_msgs = self._sanitize_tool_pairs(result_msgs)
            result_tokens = self.estimate_messages_tokens(result_msgs)
            ctx_span.set_attribute("tokens_after", result_tokens)
            ctx_span.set_attribute("compression_ratio", result_tokens / max(current_tokens, 1))
            tracer.end_span(ctx_span)
            return result_msgs

        # Step 1: independently compress individual oversized tool_result items
        if _settings.context_enable_tool_compression:
            messages = await self._compress_large_tool_results(messages)
            current_tokens = self.estimate_messages_tokens(messages)
            if current_tokens <= soft_limit:
                logger.info(f"After tool_result compression: {current_tokens} tokens, within limit")
                return _end_ctx_span(messages)

        # Step 1.5: context-boundary awareness — if a boundary marker exists, apply more aggressive compression to the old topic
        messages = await self._compress_across_boundary(messages, soft_limit, memory_manager)
        current_tokens = self.estimate_messages_tokens(messages)
        if current_tokens <= soft_limit:
            logger.info(f"After boundary compression: {current_tokens} tokens, within limit")
            return _end_ctx_span(messages)

        # Step 2: group by tool interaction groups
        groups = self.group_messages(messages)

        # Trailing Q&A protection: if the last 2 groups are [assistant text, user short text],
        # merge them so the AI's question isn't compressed while the user's short answer is left orphaned
        if (
            len(groups) >= 2
            and len(groups[-1]) == 1
            and groups[-1][0].get("role") == "user"
            and len(groups[-2]) == 1
            and groups[-2][0].get("role") == "assistant"
            and self.estimate_messages_tokens(groups[-1]) < 200
        ):
            merged = groups[-2] + groups[-1]
            groups = groups[:-2] + [merged]
            logger.debug(
                "[Compress] Merged trailing assistant-question + user-answer into one group"
            )

        recent_group_count = min(_settings.context_min_recent_turns, len(groups))

        if len(groups) <= recent_group_count:
            messages = await self._compress_large_tool_results(messages, threshold=2000)
            return _end_ctx_span(
                self._hard_truncate_if_needed(
                    messages,
                    hard_limit,
                    memory_manager,
                    overhead_bytes=_overhead_bytes,
                )
            )

        early_groups = groups[:-recent_group_count]
        recent_groups = groups[-recent_group_count:]

        early_messages = [msg for group in early_groups for msg in group]
        recent_messages = [msg for group in recent_groups for msg in group]

        logger.info(
            f"Split into {len(early_groups)} early groups and {len(recent_groups)} recent groups"
        )

        # Step 3: LLM chunked summarization of early conversation (supports iterative updates)
        early_tokens = self.estimate_messages_tokens(early_messages)
        target_summary_tokens = max(int(early_tokens * _settings.context_compression_ratio), 200)
        summary = await self._summarize_messages_chunked(
            early_messages, target_summary_tokens, previous_summary=self._previous_summary,
        )
        if summary:
            self._previous_summary = summary

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

        # Step 4: recursive compression
        logger.warning(f"Context still large ({compressed_tokens} tokens), compressing further...")
        compressed = await self._compress_further(compressed, soft_limit)

        # Step 5: hard safety net
        return _end_ctx_span(
            self._hard_truncate_if_needed(
                compressed,
                hard_limit,
                memory_manager,
                overhead_bytes=_overhead_bytes,
            )
        )

    @staticmethod
    def _find_last_boundary_index(messages: list[dict]) -> int:
        """Find the position of the last context boundary marker in the message list; returns -1 if not found."""
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
        """Context-boundary-aware compression: apply a more aggressive strategy to the old topic before the boundary.

        If the messages contain a [Context boundary] marker, compress messages before the boundary into an extreme summary (5%),
        keeping only key information that may still be useful for the current topic.
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
        summary = await self._summarize_messages_chunked_for_boundary(pre_boundary, target_tokens)

        result = []
        if summary:
            result.append(
                {
                    "role": "user",
                    "content": f"[Old topic summary]\n{summary}",
                }
            )

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
        """Use a more aggressive summary strategy for old-topic messages before the context boundary.

        Unlike the normal summary, this emphasizes "keep only key information that may still be useful for the new topic".
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
            combined = combined[:max_chars] + "\n...(earlier content omitted)..."

        target_chars = target_tokens * CHARS_PER_TOKEN

        _tt = set_tracking_context(
            TokenTrackingContext(
                operation_type="context_compress",
                operation_detail="boundary_old_topic",
            )
        )
        try:
            response = await self._cancellable_llm(
                model=self._brain.model,
                max_tokens=target_tokens,
                system=(
                    "You are a conversation compression assistant. The user has switched to a new topic; "
                    "compress the following old-topic conversation into a structured summary.\n"
                    "Must preserve:\n"
                    "1. User identity and preference settings\n"
                    "2. Important configuration/environment info (paths, versions, parameters, etc.)\n"
                    "3. Key conclusions and final decisions (including specific numbers and names)\n"
                    "4. User-stated requirements and constraints\n"
                    "5. Completed actions and their results (one sentence per item)\n"
                    "6. User-specified behavior rules (e.g. 'always do X first', 'do not Y', 'must first Z'), preserved verbatim\n"
                    "May omit: intermediate debugging, raw tool-call output, repeated trial-and-error steps."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": f"Please compress the following old-topic conversation to within {target_chars} characters:\n\n{combined}",
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
                        summary = (
                            block.thinking
                            if isinstance(block.thinking, str)
                            else str(block.thinking)
                        )

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
        """Compress oversized individual tool_result contents in parallel via LLM."""
        if threshold is None:
            from ..config import settings as _settings

            threshold = _settings.context_large_tool_threshold

        # Phase 1: Collect all large items that need compression
        compress_jobs: list[
            tuple[int, int, str, str, int]
        ] = []  # (msg_idx, item_idx, text, type, target)
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
                        compress_jobs.append(
                            (msg_idx, item_idx, result_text, "tool_result", target_tokens)
                        )
                elif isinstance(item, dict) and item.get("type") == "tool_use":
                    input_text = json.dumps(item.get("input", {}), ensure_ascii=False)
                    input_tokens = self.estimate_tokens(input_text)
                    if input_tokens > threshold:
                        from ..config import settings as _s

                        target_tokens = max(int(input_tokens * _s.context_compression_ratio), 100)
                        compress_jobs.append(
                            (msg_idx, item_idx, input_text, "tool_input", target_tokens)
                        )

        if not compress_jobs:
            return messages

        # Phase 2: Parallel compression
        async def _compress_one(text: str, ctx_type: str, target: int) -> str:
            return await self._llm_compress_text(text, target, context_type=ctx_type)

        tasks = [
            _compress_one(text, ctx_type, target) for _, _, text, ctx_type, target in compress_jobs
        ]
        compressed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Phase 3: Apply compressed results back
        result = [dict(msg) for msg in messages]
        for job, compressed in zip(compress_jobs, compressed_results, strict=False):
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
        """Use the LLM to compress a piece of text to a target token count."""
        max_input = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
        if len(text) > max_input:
            head_size = int(max_input * 0.6)
            tail_size = int(max_input * 0.3)
            text = text[:head_size] + "\n...(middle content too long, omitted)...\n" + text[-tail_size:]

        target_chars = target_tokens * CHARS_PER_TOKEN

        if context_type == "tool_result":
            system_prompt = (
                "You are an information compression assistant. Compress the following tool execution result into a concise summary, "
                "preserving key data, status codes, error messages, and important output; remove redundant details."
            )
        elif context_type == "tool_input":
            system_prompt = (
                "You are an information compression assistant. Compress the following tool call arguments into a concise summary, "
                "preserving key parameter names and values; remove redundant content."
            )
        else:
            system_prompt = (
                "You are a conversation compression assistant. Compress the following conversation into a structured summary. "
                "Must preserve: user's original goal, completed steps and their results, current task progress, "
                "pending questions (AI questions and user answers), all specific numeric and configuration values "
                "(ports, paths, secrets, etc. — do not replace concrete values with vague descriptions), next-step plan, "
                "and user-specified behavior rules (e.g. 'always do X first', 'do not Y', 'must first Z' — preserved verbatim)."
            )

        _tt = set_tracking_context(
            TokenTrackingContext(
                operation_type="context_compress",
                operation_detail=context_type,
            )
        )
        try:
            response = await self._cancellable_llm(
                model=self._brain.model,
                max_tokens=target_tokens,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": f"Please compress the following content to within {target_chars} characters:\n\n{text}",
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
                        summary = (
                            block.thinking
                            if isinstance(block.thinking, str)
                            else str(block.thinking)
                        )

            if not summary.strip():
                logger.warning(
                    "[Compress] LLM returned empty summary, falling back to hard truncation"
                )
                if len(text) > target_chars:
                    head = int(target_chars * 0.7)
                    tail = int(target_chars * 0.2)
                    return text[:head] + "\n...(compression failed, truncated)...\n" + text[-tail:]
                return text

            return summary.strip()

        except _CancelledError:
            raise
        except Exception as e:
            logger.warning(f"LLM compression failed: {e}")
            if len(text) > target_chars:
                head = int(target_chars * 0.7)
                tail = int(target_chars * 0.2)
                return text[:head] + "\n...(compression failed, truncated)...\n" + text[-tail:]
            return text
        finally:
            reset_tracking_context(_tt)

    def _extract_message_text(self, msg: dict) -> str:
        """Extract text content from a message (including tool_use/tool_result structured info)."""
        role = "User" if msg["role"] == "user" else "Assistant"
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
                        input_summary, _ = _st(
                            input_summary, 3000, save_full=False, label="compress_input"
                        )
                        texts.append(f"[Tool call: {name}, args: {input_summary}]")
                    elif item.get("type") == "tool_result":
                        from .tool_executor import smart_truncate as _st

                        result_text = str(item.get("content", ""))
                        result_text, _ = _st(
                            result_text, 10000, save_full=False, label="compress_result"
                        )
                        is_error = item.get("is_error", False)
                        status = "error" if is_error else "success"
                        texts.append(f"[Tool result ({status}): {result_text}]")
            if texts:
                return f"{role}: {' '.join(texts)}\n"

        return ""

    async def _summarize_messages_chunked(
        self,
        messages: list[dict],
        target_tokens: int,
        previous_summary: str = "",
    ) -> str:
        """Chunked LLM summarization of the message list. Supports iterative updates: when previous_summary
        exists, take the 'update summary' path rather than summarizing from scratch, to avoid gradual
        dilution of early information over multiple compression rounds."""
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
            _tt2 = set_tracking_context(
                TokenTrackingContext(
                    operation_type="context_compress",
                    operation_detail=f"chunk_{i}",
                )
            )
            try:
                from ..prompt.compact import format_compact_summary, get_compact_prompt

                if previous_summary and i == 0:
                    _system = get_compact_prompt(
                        custom_instructions=(
                            "This is an iterative summary update. Below is the previous compressed summary and new conversation. "
                            "Preserve all information from the previous summary that is still relevant, and integrate new progress. "
                            "Move completed work from 'pending' to 'done'. "
                            "Move answered questions to 'resolved questions'. "
                            "Delete only information that is clearly obsolete."
                        ),
                    )
                    _content = (
                        f"Previous summary:\n{previous_summary}\n\n"
                        f"New conversation (chunk {i + 1}/{len(chunks)}, "
                        f"~{chunk_tokens} tokens):\n\n{chunk}\n\n"
                        f"Update the summary, compressing to within {chunk_target * CHARS_PER_TOKEN} characters."
                    )
                else:
                    _system = get_compact_prompt()
                    _content = (
                        f"Please compress the following conversation chunk "
                        f"(chunk {i + 1}/{len(chunks)}, ~{chunk_tokens} tokens) to within "
                        f"{chunk_target * CHARS_PER_TOKEN} characters:\n\n{chunk}"
                    )

                response = await self._cancellable_llm(
                    model=self._brain.model,
                    max_tokens=chunk_target,
                    system=_system,
                    messages=[{"role": "user", "content": _content}],
                    use_thinking=False,
                )

                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                    elif block.type == "thinking" and hasattr(block, "thinking"):
                        if not summary:
                            summary = (
                                block.thinking
                                if isinstance(block.thinking, str)
                                else str(block.thinking)
                            )

                if not summary.strip():
                    logger.warning(f"[Compress] Chunk {i + 1} returned empty summary")
                    max_chars = chunk_target * CHARS_PER_TOKEN
                    return (
                        chunk[: max_chars // 2] + "\n...(summary failed, truncated)...\n"
                        if len(chunk) > max_chars
                        else chunk
                    )
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
                return (
                    chunk[: max_chars // 2] + "\n...(summary failed, truncated)...\n"
                    if len(chunk) > max_chars
                    else chunk
                )
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
                fallback = (
                    chunks[i][: max_chars // 2] + "\n...(summary error)...\n"
                    if len(chunks[i]) > max_chars
                    else chunks[i]
                )
                chunk_summaries.append(fallback)
            else:
                chunk_summaries.append(result)

        combined = "\n---\n".join(chunk_summaries)
        combined_tokens = self.estimate_tokens(combined)

        if combined_tokens > target_tokens * 2 and len(chunks) > 1:
            logger.info(
                f"Combined summary still large ({combined_tokens} tokens), consolidating..."
            )
            combined = await self._llm_compress_text(
                combined, target_tokens, context_type="conversation"
            )

        return combined

    async def _compress_further(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """Recursive compression: reduce the number of retained recent groups."""
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
    def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
        """Fix orphaned tool_use/tool_result pairings that may appear after compression/truncation.

        The Anthropic API requires every tool_use to have a corresponding tool_result.
        Compression or truncation can break this correspondence, causing API 400 errors.

        Handles two cases:
        1. Orphan tool_result (referenced tool_use was removed) -> remove
        2. Orphan tool_use (corresponding tool_result was removed) -> insert a stub
        """
        if not messages:
            return messages

        # Collect all tool_use IDs from assistant messages
        tool_use_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = block.get("id", "")
                    if tid:
                        tool_use_ids.add(tid)

        # Collect all tool_result IDs from user messages
        tool_result_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid:
                        tool_result_ids.add(tid)

        orphan_results = tool_result_ids - tool_use_ids
        missing_results = tool_use_ids - tool_result_ids

        if not orphan_results and not missing_results:
            return messages

        result: list[dict] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            # Remove orphaned tool_results from user messages
            if role == "user" and isinstance(content, list) and orphan_results:
                filtered = [
                    block for block in content
                    if not (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id", "") in orphan_results
                    )
                ]
                if not filtered:
                    continue
                if len(filtered) != len(content):
                    msg = {**msg, "content": filtered}

            result.append(msg)

            # Insert stub tool_results after assistant messages with orphaned tool_uses
            if role == "assistant" and isinstance(content, list) and missing_results:
                stubs = []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("id", "") in missing_results
                    ):
                        stubs.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": "[Result from earlier conversation, compressed — see summary above]",
                        })
                if stubs:
                    result.append({"role": "user", "content": stubs})

        if orphan_results or missing_results:
            logger.info(
                f"[Sanitize] Removed {len(orphan_results)} orphan result(s), "
                f"added {len(missing_results)} stub result(s)"
            )

        return result

    @staticmethod
    def _inject_summary_into_recent(summary: str, recent_messages: list[dict]) -> list[dict]:
        """Inject a summary into recent_messages without inserting a fake assistant reply.

        Strategy: find the first user message in recent_messages and inject the summary as a prefix.
        If the first message isn't a user message, prepend a user summary message.
        """
        if not summary:
            return list(recent_messages)

        summary_prefix = (
            "[Context compression — for reference only]\n"
            "The following is a structured summary of the earlier conversation, serving as a handoff record.\n"
            "Do not re-answer or re-process questions already addressed in the summary — they've already been handled. "
            "Only respond to the latest user message that appears after the summary.\n"
            "The current session state may already reflect the work described in the summary; avoid redoing it.\n\n"
            f"{summary}\n\n---\n"
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
        Post-compression prompt rewriting (Agent Harness: Context Rewriting).

        After compression, inject a structured orientation prompt to prevent the Agent from "losing memory".
        Re-injects key information via deterministic rules (no LLM).

        Args:
            messages: Compressed message list
            plan_section: Current plan status text (from PlanHandler.get_plan_prompt_section)
            scratchpad_summary: Working memory summary (from Scratchpad)
            completed_tools: List of tools already executed
            task_description: Original task description
        """
        if not messages:
            return messages

        rewrite_parts: list[str] = []

        rewrite_parts.append("[Conversation summary]")

        if task_description:
            preview = task_description[:300]
            if len(task_description) > 300:
                preview += "..."
            rewrite_parts.append(f"Original task: {preview}")

        if plan_section:
            # Truncation safeguard: when the plan status is too long, keep only the first 2000 characters
            # to avoid being dropped by a subsequent compression pass.
            _ps = (
                plan_section
                if len(plan_section) <= 2000
                else plan_section[:2000] + "\n... (plan status truncated)"
            )
            rewrite_parts.append(f"\nCurrent plan status:\n{_ps}")

        if completed_tools:
            unique_tools = list(dict.fromkeys(completed_tools))
            tools_summary = ", ".join(unique_tools[-10:])
            rewrite_parts.append(f"Tools used: {tools_summary}")

        if scratchpad_summary:
            rewrite_parts.append(f"\nWorking memory:\n{scratchpad_summary}")

        rewrite_parts.append("\nPlease continue processing normally, maintaining consistent reply quality and level of detail.")

        rewrite_text = "\n".join(rewrite_parts)

        # Find the last user message in the compressed messages and append the rewrite prompt after it,
        # or append at the end of the message list.
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

    MAX_PAYLOAD_BYTES = 1_800_000  # 1.8MB — most APIs cap at 2MB

    def _hard_truncate_if_needed(
        self,
        messages: list[dict],
        hard_limit: int,
        memory_manager: object | None = None,
        overhead_bytes: int = 0,
    ) -> list[dict]:
        """Hard safety net: when LLM compression still exceeds hard_limit, apply hard truncation.

        Uses prefix-sum + binary search for O(n log n) instead of O(n^2).
        """
        current_tokens = self.estimate_messages_tokens(messages)
        need_token_truncation = current_tokens > hard_limit

        if not need_token_truncation:
            # Within token budget, still need to check payload size (base64 images may exceed payload limits)
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
                            + "\n\n...[content too long, hard-truncated]...\n\n"
                            + content[-keep_tail:]
                        ),
                    }
                elif isinstance(content, list):
                    new_content = self._hard_truncate_content_blocks(content, max_chars_per_msg)
                    truncated[i] = {**msg, "content": new_content}

        truncated.insert(
            0,
            {
                "role": "user",
                "content": (
                    "[context_note: earlier conversation automatically compacted] Please reply normally, keeping the same level of detail and output quality."
                ),
            },
        )

        final_tokens = self.estimate_messages_tokens(truncated)
        logger.warning(
            f"[HardTruncate] Final: {final_tokens} tokens "
            f"(hard_limit={hard_limit}, messages={len(truncated)})"
        )
        return self._strip_oversized_payload(truncated, overhead_bytes=overhead_bytes)

    def _strip_oversized_payload(
        self,
        messages: list[dict],
        *,
        overhead_bytes: int = 0,
    ) -> list[dict]:
        """Check serialized payload size; remove media content when it exceeds the API limit.

        Args:
            overhead_bytes: byte size of non-message parts (system prompt + tools, etc.),
                           deducted from the MAX_PAYLOAD_BYTES budget.
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

    _MEDIA_BLOCK_TYPES = frozenset(
        {
            "image",
            "image_url",
            "video",
            "video_url",
            "audio",
            "input_audio",
        }
    )

    @classmethod
    def _hard_truncate_content_blocks(
        cls,
        content: list,
        max_chars: int,
    ) -> list:
        """Truncate large items (images/videos/large text etc.) in a content block list."""
        new_content: list = []
        for item in content:
            if not isinstance(item, dict):
                new_content.append(item)
                continue

            item_type = item.get("type", "")

            if item_type in cls._MEDIA_BLOCK_TYPES:
                label = {
                    "image": "image",
                    "image_url": "image",
                    "video": "video",
                    "video_url": "video",
                    "audio": "audio",
                    "input_audio": "audio",
                }.get(item_type, "media")
                new_content.append(
                    {
                        "type": "text",
                        "text": f"[{label} content removed to save context space]",
                    }
                )
                logger.warning(f"[HardTruncate] Stripped {item_type} block to free context")
                continue

            truncated_item = dict(item)
            for key in ("text", "content"):
                val = truncated_item.get(key, "")
                if isinstance(val, str) and len(val) > max_chars:
                    keep_h = int(max_chars * 0.7)
                    keep_t = int(max_chars * 0.2)
                    truncated_item[key] = val[:keep_h] + "\n...[hard-truncated]...\n" + val[-keep_t:]

            item_size = len(json.dumps(truncated_item, ensure_ascii=False, default=str))
            if item_size > max_chars:
                new_content.append(
                    {
                        "type": "text",
                        "text": f"[{item_type or 'content'} data too large, removed "
                        f"(original {item_size} chars)]",
                    }
                )
                logger.warning(
                    f"[HardTruncate] Replaced oversized {item_type} block "
                    f"({item_size} chars > {max_chars} limit)"
                )
                continue

            new_content.append(truncated_item)
        return new_content

    @staticmethod
    def _enqueue_dropped_for_extraction(dropped: list[dict], memory_manager: object) -> None:
        """Enqueue hard-truncated dropped messages into the extraction queue."""
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
                logger.info(
                    f"[HardTruncate] Enqueued {enqueued} dropped messages for memory extraction"
                )
        except Exception as e:
            logger.warning(f"[HardTruncate] Failed to enqueue dropped messages: {e}")
