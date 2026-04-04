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
from typing import Any

logger = logging.getLogger(__name__)


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
                "stop": "end_turn", "length": "max_tokens",
                "tool_calls": "tool_use", "function_call": "tool_use",
            }
            self.stop_reason = _reason_map.get(raw_reason, raw_reason) or self.stop_reason
            self._finalize_openai_tools()
            return []

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

        decision_type = (
            DecisionType.TOOL_CALLS if self.tool_calls else DecisionType.FINAL_ANSWER
        )
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
        block = self._blocks.pop(idx, None)
        if not block:
            return []

        results: list[dict] = []
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
            self.assistant_content.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            })

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
    # 共用: content_block_delta
    # ------------------------------------------------------------------

    def _on_content_block_delta(self, event: dict) -> list[dict]:
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")
        idx = event.get("index")

        # ── Anthropic: text_delta ──
        if delta_type == "text_delta":
            text = delta.get("text", "")
            self.text_content += text
            if idx is not None and idx in self._blocks:
                self._blocks[idx]["text"] = self._blocks[idx].get("text", "") + text
            return [{"type": "text_delta", "content": text}] if text else []

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
                self._blocks[idx]["signature"] = (
                    self._blocks[idx].get("signature", "") + delta.get("signature", "")
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
                        "type": "tool_use", "id": "", "name": "", "input_str": "",
                    }
                self._blocks[idx]["input_str"] = (
                    self._blocks[idx].get("input_str", "") + delta.get("partial_json", "")
                )
            return []

        # ── OpenAI 归一化: text ──
        if delta_type == "text":
            text = delta.get("text", "")
            self.text_content += text
            return [{"type": "text_delta", "content": text}] if text else []

        # ── OpenAI 归一化: thinking ──
        if delta_type == "thinking":
            text = delta.get("text", "")
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
            self.assistant_content.append({
                "type": "tool_use",
                "id": call_id,
                "name": tc["name"],
                "input": args,
            })

        if self._openai_tool_inputs and not self.stop_reason:
            self.stop_reason = "tool_use"
        self._openai_tool_inputs.clear()
        self._openai_current_tool_id = None


def post_process_streamed_decision(decision) -> None:
    """对流式构建的 Decision 执行与 _parse_decision 相同的防御逻辑（原地修改）。

    - 从 thinking_content 中提取嵌入的工具调用
    - 从 text_content 中提取文本式工具调用
    - 剥离 text_content 中的 <thinking>/<think> 标签
    - 剥离末尾裸工具名
    """
    from .response_handler import strip_thinking_tags

    # 1) 剥离 text_content 中的 thinking 标签
    raw_text = decision.text_content
    if raw_text and ("<thinking>" in raw_text or "<think>" in raw_text):
        display_text = strip_thinking_tags(raw_text)
        if display_text != raw_text and not decision.thinking_content:
            m = re.search(r"<think(?:ing)?>(.*?)</think(?:ing)?>", raw_text, re.DOTALL)
            if m:
                decision.thinking_content = m.group(1).strip()
        decision.text_content = display_text

    # 2) 从 thinking_content 中提取嵌入工具调用
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

    # 3) 从 text_content 中提取文本式工具调用
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
                    logger.warning(
                        f"[post_process] Recovered {len(embedded)} tool calls from text"
                    )
        except Exception as e:
            logger.debug(f"[post_process] Text tool-call check failed: {e}")

    # 4) 剥离末尾裸工具名
    if decision.text_content and len(decision.text_content.strip()) < 200:
        lines = decision.text_content.strip().split("\n")
        last = lines[-1].strip() if lines else ""
        if re.match(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$", last):
            decision.text_content = "\n".join(lines[:-1]).strip()
            logger.warning(f"[post_process] Stripped bare tool name '{last}'")

    # 5) 更新 decision type
    from .reasoning_engine import DecisionType
    if decision.tool_calls:
        decision.type = DecisionType.TOOL_CALLS
    else:
        decision.type = DecisionType.FINAL_ANSWER
