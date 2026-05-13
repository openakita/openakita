"""
流式事件累加器 (Stream Accumulator)

参考 Claude Code (claude.ts) 的 contentBlocks[] 状态机模式，统一处理
Anthropic 原始 SSE 和 OpenAI 归一化流事件，产出高层 SSE 事件并累积构建 Decision。

核心设计：
- tool_use 的 input 作为字符串拼接，仅在 block 结束时 json.loads（避免 O(n²)）
- text_delta / thinking_delta 即时产出供上游 yield 给前端
- 流结束后通过 build_decision() 构建完整 Decision 对象
"""

from __future__ import annotations

import json
import logging
import re

from .response_handler import (
    INTERNAL_TRACE_MARKERS,
    INTERNAL_TRACE_SECTION_TERMINATORS,
)

logger = logging.getLogger(__name__)

_THINK_OPEN_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)
_THINK_OPEN_TAGS = ("<think>", "<thinking>")
_THINK_CLOSE_TAGS = ("</think>", "</thinking>")


class StreamingInternalTraceScrubber:
    """跨 chunk 状态机：从流式正文中实时丢弃内部 trace marker section。

    背景：``build_tool_trace_summary()`` 在历史 metadata 里注入 ``<<TOOL_TRACE>>``
    / ``<<DELEGATION_TRACE>>`` 摘要供 LLM 在下一轮记忆"已执行过的工具"。
    某些模型（已知 MiniMax-M2.7）会在本轮回复中模仿这种格式，把模型自己刚
    调用的工具伪装成"已完成回放"，并把整段 marker 泄露到用户可见正文。

    本 scrubber 在 ``StreamAccumulator`` 的文本输出路径上拦截，将整段
    ``<<TOOL_TRACE>>...`` 在流式过程中即时丢弃，前端不会先看到一段 marker
    再被后置清理"抹掉"。

    设计要点（参考 ``hermes-agent/agent/think_scrubber.py``）：

    - **Boundary gated**：marker 仅在 start-of-stream / 行首 / 段落边界识别，
      避免误删用户在正文里讨论 marker 字面量。
    - **跨 chunk hold-back**：``<<TOOL_TR`` + ``ACE>>`` 被拆在两 chunk 时，
      第一段不能提前发给前端；通过 ``_buf`` 持有"可能是 marker 前缀"的尾部
      字符，等下一次 ``feed()`` 拼接判定。
    - **Section 终止**：marker 没有闭合标签，section 在下一段起始符
      （``\\n\\n[`` / ``\\n\\n<<`` / ``\\n\\n##`` / ``\\n\\n---``）处结束；
      若流结束仍在 section 内，``flush()`` 丢弃 held 内容。
    - **不处理 thinking 通道**：仅作用于普通正文 delta。thinking 通道的
      内部 marker 由最终清理阶段 ``strip_internal_trace_markers()`` 兜底。
    - **不识别 fenced code block**：与现有 ``_route_tagged_text_delta`` 的
      ``<think>`` 路由保持一致；完整文本最终清理函数才做 code block 跳过。

    实例每次新流（含 LLM 重试 / failover）都应新建或调用 ``reset()``。
    OpenAkita 现有 ``StreamAccumulator`` 在每次 ``messages_create_stream``
    都新建实例，scrubber 天然隔离。
    """

    _MARKERS: tuple[str, ...] = INTERNAL_TRACE_MARKERS
    _TERMINATORS: tuple[str, ...] = INTERNAL_TRACE_SECTION_TERMINATORS

    # 预编译"换行边界 + marker"正则，支持任意数量 leading \n + 可选缩进。
    _BOUNDARY_MARKER_RE = re.compile(
        r"\n+[ \t]*(?:"
        + "|".join(re.escape(m) for m in INTERNAL_TRACE_MARKERS)
        + r")"
    )

    def __init__(self) -> None:
        self._in_section: bool = False
        self._buf: str = ""
        # start-of-stream 算作 boundary（与 hermes scrubber 一致）。
        self._last_emit_ended_newline: bool = True

    def reset(self) -> None:
        self._in_section = False
        self._buf = ""
        self._last_emit_ended_newline = True

    def feed(self, text: str) -> str:
        """喂一个 delta 文本，返回该 delta 中应发给前端的可见部分。

        若 delta 完全落在 trace section 中或全部被 hold-back，则返回 ``""``。
        held-back 字符会保留到下一次 ``feed()`` 或 ``flush()`` 决定。
        """
        if not text and not self._buf:
            return ""
        buf = self._buf + (text or "")
        self._buf = ""
        out: list[str] = []

        while buf:
            if self._in_section:
                term_idx = self._find_earliest_terminator(buf)
                if term_idx == -1:
                    # 未出现终止符 —— hold 尾部可能的"部分终止符"前缀，
                    # 其余全部丢弃。
                    held = self._max_partial_terminator_suffix(buf)
                    self._buf = buf[-held:] if held else ""
                    return "".join(out)
                # 找到终止符 —— 保留终止符本体（``\n\n##`` 等是下一段
                # 的合法起始），退出 section 让 loop 继续。
                buf = buf[term_idx:]
                self._in_section = False
                continue

            # 非 section 状态：寻找下一个 boundary marker。
            m_idx, m_len = self._find_marker_at_boundary(buf)
            if m_idx == -1:
                held = self._max_partial_marker_suffix(buf)
                if held:
                    emit_text = buf[:-held]
                    self._buf = buf[-held:]
                else:
                    emit_text = buf
                    self._buf = ""
                if emit_text:
                    out.append(emit_text)
                    self._last_emit_ended_newline = emit_text.endswith("\n")
                return "".join(out)

            preceding = buf[:m_idx]
            if preceding:
                out.append(preceding)
                self._last_emit_ended_newline = preceding.endswith("\n")
            self._in_section = True
            buf = buf[m_idx + m_len :]

        return "".join(out)

    def flush(self) -> str:
        """流结束时调用。

        - 仍处于 section 内 → 丢弃 held 内容（半截 trace 摘要不应泄露）。
        - 否则 → 返回 hold-back 的 partial-marker 尾部字符（事实证明它
          不是 marker，应该回送给前端）。
        """
        if self._in_section:
            self._buf = ""
            self._in_section = False
            return ""
        tail = self._buf
        self._buf = ""
        if tail:
            self._last_emit_ended_newline = tail.endswith("\n")
        return tail

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _find_marker_at_boundary(self, buf: str) -> tuple[int, int]:
        """返回 (match_start_in_buf, consume_length) 或 (-1, 0)。

        match_start_in_buf 是 buf 中应"删除起点"的位置（含 boundary 前导
        ``\\n`` / spaces）；consume_length 是从该位置到 marker 末尾要丢弃
        的字符数。
        """
        earliest = -1
        earliest_len = 0

        # Case A：buf 起点本身就是 logical boundary（start-of-stream 或上次
        # 发出的字符以 ``\n`` 结尾）。
        if self._last_emit_ended_newline:
            for marker in self._MARKERS:
                if buf.startswith(marker):
                    earliest = 0
                    earliest_len = len(marker)
                    break  # 0 是最早可能值

        # Case B：buf 内部出现 ``\n+[ \t]*<<MARKER>>``。
        m = self._BOUNDARY_MARKER_RE.search(buf)
        if m and (earliest == -1 or m.start() < earliest):
            earliest = m.start()
            earliest_len = m.end() - m.start()

        return earliest, earliest_len

    def _find_earliest_terminator(self, buf: str) -> int:
        best = -1
        for term in self._TERMINATORS:
            i = buf.find(term)
            if i != -1 and (best == -1 or i < best):
                best = i
        return best

    def _max_partial_marker_suffix(self, buf: str) -> int:
        """返回应 hold 的尾部字符数。

        只在"尾部能构成 boundary 后 marker 前缀"时 hold；纯 mid-line 出现
        marker 前缀不 hold（intrinsic false negative，不影响最终结果，
        见 docstring）。
        """
        anchor: int | None = None
        if self._last_emit_ended_newline:
            anchor = 0
        last_nl = buf.rfind("\n")
        if last_nl != -1:
            i = last_nl + 1
            while i < len(buf) and buf[i] in (" ", "\t"):
                i += 1
            anchor = i  # 比 start-of-buf 更靠后的 boundary

        if anchor is None:
            return 0

        suffix = buf[anchor:]
        if not suffix:
            return 0

        for marker in self._MARKERS:
            if marker.startswith(suffix):
                return len(suffix)
        return 0

    def _max_partial_terminator_suffix(self, buf: str) -> int:
        max_len = 0
        for term in self._TERMINATORS:
            for k in range(1, min(len(term), len(buf)) + 1):
                if buf.endswith(term[:k]):
                    max_len = max(max_len, k)
        return max_len


class StreamAccumulator:
    """归一化 Provider 流事件 → 高层 SSE 事件 + Decision 数据累积。

    支持两种 Provider 事件格式:
    - Anthropic 原始 SSE: message_start / content_block_start / content_block_delta /
      content_block_stop / message_delta / message_stop
    - OpenAI 归一化格式: content_block_delta (delta.type: text/thinking/tool_use) /
      message_stop / ping
    """

    def __init__(self) -> None:
        self.text_content: str = ""
        self.thinking_content: str = ""
        self.tool_calls: list[dict] = []
        self.assistant_content: list[dict] = []
        self.stop_reason: str = ""
        self.usage: dict | None = None

        # Anthropic: 按 content block index 追踪
        self._blocks: dict[int, dict] = {}
        # OpenAI: 按 tool call id 追踪 JSON 字符串
        self._openai_tool_inputs: dict[str, dict] = {}
        self._openai_current_tool_id: str | None = None
        self._tagged_thinking_active = False
        self._tagged_text_buffer = ""

        # 内部 trace marker 流式 scrubber：拦截 ``<<TOOL_TRACE>>`` 等
        # OpenAkita 自己注入的历史摘要 marker 被模型模仿后泄露到用户
        # 可见正文。仅作用于普通正文 ``text_delta`` 路径；``thinking_delta``
        # 不经过 scrubber（最终阶段由 ``strip_internal_trace_markers``
        # 兜底）。每个 ``StreamAccumulator`` 新实例自带新 scrubber，跨流
        # 状态天然隔离。
        self._trace_scrubber = StreamingInternalTraceScrubber()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def feed(self, event: dict) -> list[dict]:
        """处理一个原始 Provider 事件，返回 0~N 个高层事件供 yield。"""
        evt_type = event.get("type", "")

        if evt_type == "ping":
            return []

        # ── Anthropic 专有事件 ──
        if evt_type == "message_start":
            return self._on_anthropic_message_start(event)
        if evt_type == "content_block_start":
            return self._on_anthropic_block_start(event)
        if evt_type == "content_block_stop":
            return self._on_anthropic_block_stop(event)
        if evt_type == "message_delta":
            return self._on_anthropic_message_delta(event)
        if evt_type == "message_stop":
            raw_reason = event.get("stop_reason", "")
            _reason_map = {
                "stop": "end_turn",
                "length": "max_tokens",
                "tool_calls": "tool_use",
                "function_call": "tool_use",
            }
            self.stop_reason = _reason_map.get(raw_reason, raw_reason) or self.stop_reason
            self._finalize_openai_tools()
            u = event.get("usage")
            if u:
                self.usage = u
            # flush 顺序固定：先 think router buffer（其文本输出仍要过
            # scrubber），再 trace scrubber（其输出直接进 _append_text_delta，
            # 绕过 scrubber 避免循环）。颠倒会让 think router 的尾字符
            # 绕过 scrubber 直发前端，破坏跨 chunk 拼接判定。
            events = self._flush_tagged_text_buffer()
            events.extend(self._flush_trace_scrubber())
            return events

        # ── 共用: content_block_delta（Anthropic 原始 / OpenAI 归一化） ──
        if evt_type == "content_block_delta":
            return self._on_content_block_delta(event)

        # ── 其它/未知 ──
        return []

    def build_decision(self):
        """从累积状态构建 Decision 对象。

        返回 Decision（延迟导入，避免循环依赖）。
        """
        from .reasoning_engine import Decision, DecisionType

        # 防御性 flush：通常 message_stop 已经触发过，这里兜底以防上游
        # 未发 message_stop（异常 / 取消）就直接构建 Decision。flush 顺序
        # 与 message_stop 保持一致。
        self._flush_tagged_text_buffer()
        self._flush_trace_scrubber()
        decision_type = DecisionType.TOOL_CALLS if self.tool_calls else DecisionType.FINAL_ANSWER
        return Decision(
            type=decision_type,
            text_content=self.text_content,
            tool_calls=list(self.tool_calls),
            thinking_content=self.thinking_content,
            raw_response=None,
            stop_reason=self.stop_reason,
            assistant_content=list(self.assistant_content),
        )

    # ------------------------------------------------------------------
    # Anthropic 事件处理
    # ------------------------------------------------------------------

    def _on_anthropic_message_start(self, event: dict) -> list[dict]:
        msg = event.get("message", {})
        u = msg.get("usage")
        if u:
            self.usage = u
        return []

    def _on_anthropic_block_start(self, event: dict) -> list[dict]:
        idx = event.get("index", 0)
        block = event.get("content_block", {})
        block_type = block.get("type", "")

        if block_type == "tool_use":
            self._blocks[idx] = {
                "type": "tool_use",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input_str": "",
            }
        elif block_type == "text":
            self._blocks[idx] = {"type": "text", "text": ""}
        elif block_type == "thinking":
            self._blocks[idx] = {"type": "thinking", "thinking": "", "signature": ""}
        else:
            self._blocks[idx] = {"type": block_type}

        return []

    def _on_anthropic_block_stop(self, event: dict) -> list[dict]:
        idx = event.get("index", 0)

        # **Multi-block 边界保护**：text block 关闭前先 flush trace scrubber。
        # 关键 bug 修复：Anthropic 风格的 ``text → tool_use → text`` 流中，
        # 若 trace section 在第一个 text block 内未出现 ``\n\n##`` 等终止符，
        # scrubber 的 ``_in_section=True`` 会一直延续到下一个 text block，
        # 静默吞掉其全部内容。这里在 block 关闭瞬间 flush：
        # - in_section=True → 清零 section 状态，下一个 text block 干净起步
        # - 持有 partial-marker 尾部 → emit 到当前 block.text + assistant_content
        # think router 的 ``_tagged_text_buffer`` 不在此 flush（保持现有跨
        # block 识别 ``<thi`` + ``nk>`` 的能力），仅针对 trace scrubber 修复。
        peek = self._blocks.get(idx)
        flush_events: list[dict] = []
        if peek and peek.get("type") == "text":
            flush_events = self._flush_trace_scrubber(idx)

        block = self._blocks.pop(idx, None)
        if not block:
            return flush_events

        results: list[dict] = flush_events
        btype = block.get("type", "")

        if btype == "tool_use":
            input_str = block.get("input_str", "")
            try:
                parsed_input = json.loads(input_str) if input_str else {}
            except json.JSONDecodeError:
                parsed_input = {"_raw": input_str}
                logger.warning(
                    f"[StreamAccumulator] Failed to parse tool input JSON for "
                    f"{block.get('name')}: {input_str[:200]}"
                )
            tc = {
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": parsed_input,
            }
            self.tool_calls.append(tc)
            self.assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                }
            )

        elif btype == "text":
            text = block.get("text", "")
            if text:
                self.assistant_content.append({"type": "text", "text": text})

        elif btype == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                entry: dict = {"type": "thinking", "thinking": thinking}
                sig = block.get("signature", "")
                if sig:
                    entry["signature"] = sig
                self.assistant_content.append(entry)

        return results

    def _on_anthropic_message_delta(self, event: dict) -> list[dict]:
        d = event.get("delta", {})
        self.stop_reason = d.get("stop_reason", self.stop_reason)
        u = event.get("usage")
        if u:
            self.usage = u
        return []

    # ------------------------------------------------------------------
    # 文本中内嵌 <think> 标签的流式路由
    # ------------------------------------------------------------------

    @staticmethod
    def _possible_tag_suffix_len(text: str, tags: tuple[str, ...]) -> int:
        lowered = text.lower()
        max_len = 0
        for tag in tags:
            for length in range(1, min(len(tag), len(text) + 1)):
                if lowered.endswith(tag[:length]):
                    max_len = max(max_len, length)
        return max_len

    def _append_text_delta(self, text: str, idx: int | None = None) -> list[dict]:
        if not text:
            return []
        self.text_content += text
        if idx is not None and idx in self._blocks:
            self._blocks[idx]["text"] = self._blocks[idx].get("text", "") + text
        return [{"type": "text_delta", "content": text}]

    def _emit_visible_text(self, text: str, idx: int | None = None) -> list[dict]:
        """将原本要直接送进 ``_append_text_delta`` 的正文先经过 trace scrubber。

        scrubber 会拦截内部 trace marker section；只有它返回的可见部分
        才会进入 ``text_content`` / block.text 与 ``text_delta`` 事件。
        被 scrubber 丢弃（在 section 内）或 hold-back 的字符**不**进入
        累积状态，保证 ``decision.text_content`` 与发出的事件序列一致。

        flush 路径（``scrubber.flush()`` 的输出）必须**直接**调用
        ``_append_text_delta``，不要再经此函数，否则会被 scrubber 重新
        扫描一次形成循环。
        """
        if not text:
            return []
        visible = self._trace_scrubber.feed(text)
        if not visible:
            return []
        return self._append_text_delta(visible, idx)

    def _append_thinking_delta(self, text: str, idx: int | None = None) -> list[dict]:
        if not text:
            return []
        self.thinking_content += text
        if idx is not None and idx in self._blocks:
            self._blocks[idx]["thinking"] = self._blocks[idx].get("thinking", "") + text
        return [{"type": "thinking_delta", "content": text}]

    def _route_tagged_text_delta(self, text: str, idx: int | None = None) -> list[dict]:
        if not text:
            return []

        self._tagged_text_buffer += text
        events: list[dict] = []

        while self._tagged_text_buffer:
            buf = self._tagged_text_buffer

            if self._tagged_thinking_active:
                close_match = _THINK_CLOSE_RE.search(buf)
                if not close_match:
                    keep = self._possible_tag_suffix_len(buf, _THINK_CLOSE_TAGS)
                    emit = buf[:-keep] if keep else buf
                    self._tagged_text_buffer = buf[-keep:] if keep else ""
                    events.extend(self._append_thinking_delta(emit, idx))
                    break

                before = buf[: close_match.start()]
                events.extend(self._append_thinking_delta(before, idx))
                self._tagged_thinking_active = False
                self._tagged_text_buffer = buf[close_match.end() :]
                continue

            open_match = _THINK_OPEN_RE.search(buf)
            if not open_match:
                keep = self._possible_tag_suffix_len(buf, _THINK_OPEN_TAGS)
                emit = buf[:-keep] if keep else buf
                self._tagged_text_buffer = buf[-keep:] if keep else ""
                events.extend(self._emit_visible_text(emit, idx))
                break

            before = buf[: open_match.start()]
            events.extend(self._emit_visible_text(before, idx))
            self._tagged_thinking_active = True
            self._tagged_text_buffer = buf[open_match.end() :]

        return events

    def _flush_tagged_text_buffer(self) -> list[dict]:
        pending = self._tagged_text_buffer
        self._tagged_text_buffer = ""
        if not pending:
            return []
        if self._tagged_thinking_active:
            return self._append_thinking_delta(pending)
        # 普通正文走 scrubber；thinking 内容不走（保持 thinking 流式
        # 展示语义干净，最终阶段再清理 thinking_content）。
        return self._emit_visible_text(pending)

    def _flush_trace_scrubber(self, idx: int | None = None) -> list[dict]:
        """让 trace scrubber 把 hold-back 字符吐出来 + 重置 in_section。

        scrubber.flush() 的输出**直接**走 ``_append_text_delta``，不再经
        ``_emit_visible_text``，避免被 scrubber 重新扫描形成循环（见
        ``_emit_visible_text`` docstring）。

        ``idx`` 传当前 text block 的索引时，hold-back 字符会同步追加到
        ``_blocks[idx]["text"]`` 与 ``self.text_content``，保证流式 block.text
        与最终 ``assistant_content`` 中 text block 同步。在 ``message_stop``
        等没有具体 block 归属的场景，传 ``None``（只更新 ``text_content``）。
        """
        tail = self._trace_scrubber.flush()
        if not tail:
            return []
        return self._append_text_delta(tail, idx)

    # ------------------------------------------------------------------
    # 共用: content_block_delta
    # ------------------------------------------------------------------

    def _on_content_block_delta(self, event: dict) -> list[dict]:
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")
        idx = event.get("index")

        # ── Anthropic: text_delta ──
        if delta_type == "text_delta":
            text = delta.get("text", "")
            return self._route_tagged_text_delta(text, idx)

        # ── Anthropic: thinking_delta ──
        if delta_type == "thinking_delta":
            text = delta.get("thinking", "")
            self.thinking_content += text
            if idx is not None and idx in self._blocks:
                self._blocks[idx]["thinking"] = self._blocks[idx].get("thinking", "") + text
            return [{"type": "thinking_delta", "content": text}] if text else []

        # ── Anthropic: signature_delta ──
        if delta_type == "signature_delta":
            if idx is not None and idx in self._blocks:
                self._blocks[idx]["signature"] = self._blocks[idx].get("signature", "") + delta.get(
                    "signature", ""
                )
            return []

        # ── Anthropic: input_json_delta ──
        if delta_type == "input_json_delta":
            if idx is not None:
                if idx not in self._blocks:
                    logger.warning(
                        f"[StreamAccumulator] input_json_delta for unknown block idx={idx}, "
                        "creating fallback tool_use block"
                    )
                    self._blocks[idx] = {
                        "type": "tool_use",
                        "id": "",
                        "name": "",
                        "input_str": "",
                    }
                self._blocks[idx]["input_str"] = self._blocks[idx].get("input_str", "") + delta.get(
                    "partial_json", ""
                )
            return []

        # ── OpenAI 归一化: text ──
        if delta_type == "text":
            text = delta.get("text", "")
            return self._route_tagged_text_delta(text, idx)

        # ── OpenAI 归一化: thinking / reasoning ──
        # 不同兼容网关会把同一类增量命名为 thinking 或 reasoning。
        if delta_type in ("thinking", "reasoning"):
            text = delta.get("text", "") or delta.get("thinking", "") or delta.get("reasoning", "")
            self.thinking_content += text
            return [{"type": "thinking_delta", "content": text}] if text else []

        # ── OpenAI 归一化: tool_use ──
        if delta_type == "tool_use":
            return self._on_openai_tool_delta(delta)

        return []

    # ------------------------------------------------------------------
    # OpenAI 工具增量
    # ------------------------------------------------------------------

    def _on_openai_tool_delta(self, delta: dict) -> list[dict]:
        call_id = delta.get("id")
        if call_id:
            if call_id not in self._openai_tool_inputs:
                self._openai_tool_inputs[call_id] = {
                    "name": delta.get("name") or "",
                    "arguments": "",
                }
            elif delta.get("name") and not self._openai_tool_inputs[call_id]["name"]:
                self._openai_tool_inputs[call_id]["name"] = delta["name"]
            self._openai_current_tool_id = call_id

        target_id = call_id or self._openai_current_tool_id
        if target_id and target_id in self._openai_tool_inputs:
            self._openai_tool_inputs[target_id]["arguments"] += delta.get("arguments") or ""

        return []

    def _finalize_openai_tools(self) -> None:
        """message_stop 时解析所有累积的 OpenAI 工具 JSON。"""
        for call_id, tc in self._openai_tool_inputs.items():
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["arguments"]}
                logger.warning(
                    f"[StreamAccumulator] Failed to parse OpenAI tool JSON for "
                    f"{tc['name']}: {tc['arguments'][:200]}"
                )
            tool = {"id": call_id, "name": tc["name"], "input": args}
            self.tool_calls.append(tool)
            self.assistant_content.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": tc["name"],
                    "input": args,
                }
            )

        if self._openai_tool_inputs and not self.stop_reason:
            self.stop_reason = "tool_use"
        self._openai_tool_inputs.clear()
        self._openai_current_tool_id = None


def post_process_streamed_decision(decision) -> None:
    """对流式构建的 Decision 执行与 _parse_decision 相同的防御逻辑（原地修改）。

    清理顺序固定为：

    1. ``strip_thinking_tags`` —— 剥离 ``<think>`` / ``<thinking>`` 标签。
    2. ``strip_internal_trace_markers`` —— 剥离 ``<<TOOL_TRACE>>`` 等内部
       trace marker section。必须在 ``parse_text_tool_calls`` 之前，否则
       模型整段模仿的 ``<<TOOL_TRACE>>\\n- web_search({...})`` 中的工具
       调用会被误识别为本轮真实意图，触发额外工具执行（安全风险）。
    3. ``parse_text_tool_calls`` —— 从 thinking / text 中提取文本式工具调用。
    4. ``strip bare tool name`` —— 剥离末尾裸工具名。
    5. 更新 decision type。

    第 2 步同步清理 ``thinking_content``（防止下一轮通过 ``reasoning_content``
    回灌再被模型复读）与 ``assistant_content`` 内的 text/thinking block
    （防止持久化后下一轮回放再次泄露）。
    """
    from .response_handler import strip_internal_trace_markers, strip_thinking_tags

    # ── 1) 剥离 text_content 中的 thinking 标签 ──
    raw_text = decision.text_content
    if raw_text and ("<thinking>" in raw_text or "<think>" in raw_text):
        display_text = strip_thinking_tags(raw_text)
        if display_text != raw_text and not decision.thinking_content:
            m = re.search(r"<think(?:ing)?>(.*?)</think(?:ing)?>", raw_text, re.DOTALL)
            if m:
                decision.thinking_content = m.group(1).strip()
        decision.text_content = display_text

    # ── 2) 剥离内部 trace marker（在 parse_text_tool_calls 之前！） ──
    # text_content：模型可能整段模仿 <<TOOL_TRACE>>\n- foo({...})，剥离后
    # 才让下面的工具调用提取在干净文本上跑，避免把模仿的调用当真。
    if decision.text_content:
        cleaned_text = strip_internal_trace_markers(decision.text_content)
        if cleaned_text != decision.text_content:
            logger.info(
                "[post_process] Stripped internal trace marker(s) from text_content "
                f"({len(decision.text_content) - len(cleaned_text)} chars removed)"
            )
        decision.text_content = cleaned_text

    # thinking_content：避免下一轮 reasoning_content 回灌时把 marker 再次
    # 喂回模型，形成"看到 marker → 复读 marker"的反馈环。
    if decision.thinking_content:
        decision.thinking_content = strip_internal_trace_markers(decision.thinking_content)

    # assistant_content text/thinking block：避免持久化后下一轮历史回放
    # 又把 marker 拼回 LLM 上下文。tool_use block 不动。
    for block in decision.assistant_content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            block["text"] = strip_internal_trace_markers(block["text"])
        elif btype == "thinking" and block.get("thinking"):
            block["thinking"] = strip_internal_trace_markers(block["thinking"])

    # ── 3) 从 thinking_content 中提取嵌入工具调用 ──
    if not decision.tool_calls and decision.thinking_content:
        try:
            from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls

            if has_text_tool_calls(decision.thinking_content):
                _, embedded = parse_text_tool_calls(decision.thinking_content)
                if embedded:
                    for tc in embedded:
                        decision.tool_calls.append(
                            {"id": tc.id, "name": tc.name, "input": tc.input}
                        )
                        decision.assistant_content.append(
                            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                        )
                    logger.warning(
                        f"[post_process] Recovered {len(embedded)} tool calls from thinking"
                    )
        except Exception as e:
            logger.debug(f"[post_process] Thinking tool-call check failed: {e}")

    # ── 4) 从 text_content 中提取文本式工具调用 ──
    if not decision.tool_calls and decision.text_content:
        try:
            from ..llm.converters.tools import has_text_tool_calls, parse_text_tool_calls

            if has_text_tool_calls(decision.text_content):
                clean, embedded = parse_text_tool_calls(decision.text_content)
                if embedded:
                    decision.text_content = clean
                    for tc in embedded:
                        decision.tool_calls.append(
                            {"id": tc.id, "name": tc.name, "input": tc.input}
                        )
                        decision.assistant_content.append(
                            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                        )
                    logger.warning(f"[post_process] Recovered {len(embedded)} tool calls from text")
        except Exception as e:
            logger.debug(f"[post_process] Text tool-call check failed: {e}")

    # ── 5) 剥离末尾裸工具名 ──
    if decision.text_content and len(decision.text_content.strip()) < 200:
        lines = decision.text_content.strip().split("\n")
        last = lines[-1].strip() if lines else ""
        if re.match(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$", last):
            decision.text_content = "\n".join(lines[:-1]).strip()
            logger.warning(f"[post_process] Stripped bare tool name '{last}'")

    # ── 6) 更新 decision type ──
    from .reasoning_engine import DecisionType

    if decision.tool_calls:
        decision.type = DecisionType.TOOL_CALLS
    else:
        decision.type = DecisionType.FINAL_ANSWER
