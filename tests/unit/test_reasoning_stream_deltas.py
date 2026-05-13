from openakita.core.stream_accumulator import (
    StreamAccumulator,
    StreamingInternalTraceScrubber,
    post_process_streamed_decision,
)
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.providers.openai_responses import OpenAIResponsesProvider


def _text_delta(chunk: str) -> dict:
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}


def test_stream_accumulator_accepts_reasoning_delta_alias():
    acc = StreamAccumulator()

    events = acc.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "reasoning", "text": "checking sources"},
        }
    )

    assert events == [{"type": "thinking_delta", "content": "checking sources"}]
    assert acc.build_decision().thinking_content == "checking sources"


def test_stream_accumulator_routes_tagged_thinking_text_during_stream():
    acc = StreamAccumulator()

    events = []
    for chunk in ("Answer prefix <thi", "nk>checking", " sources</think> final"):
        events.extend(
            acc.feed(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": chunk},
                }
            )
        )

    assert events == [
        {"type": "text_delta", "content": "Answer prefix "},
        {"type": "thinking_delta", "content": "checking"},
        {"type": "thinking_delta", "content": " sources"},
        {"type": "text_delta", "content": " final"},
    ]
    decision = acc.build_decision()
    assert decision.text_content == "Answer prefix  final"
    assert decision.thinking_content == "checking sources"


def test_stream_accumulator_flushes_partial_tag_text_on_message_stop():
    acc = StreamAccumulator()

    events = acc.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "literal <"},
        }
    )
    events.extend(acc.feed({"type": "message_stop"}))

    assert events == [
        {"type": "text_delta", "content": "literal "},
        {"type": "text_delta", "content": "<"},
    ]
    assert acc.build_decision().text_content == "literal <"


def test_openai_stream_extracts_nested_reasoning_details():
    provider = OpenAIProvider.__new__(OpenAIProvider)

    converted = provider._convert_stream_event(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_details": [
                            {"type": "reasoning.text.delta", "delta": "search first"},
                            {"type": "reasoning.text.delta", "text": ", then summarize"},
                        ]
                    }
                }
            ]
        }
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "search first, then summarize"},
    }


def test_responses_stream_reasoning_summary_becomes_thinking_delta():
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)

    converted = provider._convert_stream_event(
        {"type": "response.reasoning_summary_text.delta", "delta": "checking citations"}
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "checking citations"},
    }


def test_responses_done_reasoning_item_summary_is_preserved():
    provider = OpenAIResponsesProvider.__new__(OpenAIResponsesProvider)

    converted = provider._convert_stream_event(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "summary": [{"text": "tool result is enough; answer now"}],
            },
        }
    )

    assert converted == {
        "type": "content_block_delta",
        "delta": {"type": "thinking", "text": "tool result is enough; answer now"},
    }


# ====================================================================
# Internal trace marker scrubbing — 流式 + 最终清理 + 安全测试
# ====================================================================


def test_trace_scrubber_drops_full_tool_trace_in_single_chunk():
    """单 chunk 内出现完整 <<TOOL_TRACE>> section → 不产生可见 text_delta。"""
    acc = StreamAccumulator()
    events = acc.feed(
        _text_delta(
            "Visible answer.\n\n<<TOOL_TRACE>>\n- web_search({'q': 'x'}) -> ..."
        )
    )
    events.extend(acc.feed({"type": "message_stop"}))

    text_events = [e for e in events if e.get("type") == "text_delta"]
    combined = "".join(e["content"] for e in text_events)
    assert "<<TOOL_TRACE>>" not in combined
    assert "web_search" not in combined
    assert combined.rstrip() == "Visible answer."

    decision = acc.build_decision()
    assert "<<TOOL_TRACE>>" not in decision.text_content
    assert decision.text_content.rstrip() == "Visible answer."


def test_trace_scrubber_holds_marker_split_across_chunks():
    """marker 被拆在多个 chunk → 前半段不会先泄露到前端。"""
    acc = StreamAccumulator()
    events: list[dict] = []
    for chunk in ("Answer.\n\n<<TOOL_TR", "ACE>>\n- foo\n", "- bar\n"):
        events.extend(acc.feed(_text_delta(chunk)))
    events.extend(acc.feed({"type": "message_stop"}))

    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert "<<TOOL_TR" not in combined  # 前半段不应泄露
    assert "<<TOOL_TRACE>>" not in combined
    assert "foo" not in combined
    assert "bar" not in combined
    assert combined.rstrip() == "Answer."


def test_trace_scrubber_preserves_trailing_angle_bracket_after_flush():
    """正文以 `<` / `<<` 结尾但其实不是 marker → flush 后能正常发出。"""
    acc = StreamAccumulator()
    events = acc.feed(_text_delta("Answer with literal <"))
    events.extend(acc.feed({"type": "message_stop"}))

    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert combined == "Answer with literal <"
    assert acc.build_decision().text_content == "Answer with literal <"


def test_trace_scrubber_preserves_trailing_double_angle_bracket():
    acc = StreamAccumulator()
    events = acc.feed(_text_delta("hold <<"))
    events.extend(acc.feed({"type": "message_stop"}))
    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert combined == "hold <<"


def test_trace_scrubber_does_not_eat_inline_marker_discussion():
    """用户在正文行内讨论 <<TOOL_TRACE>> 字面量 → 不被吞掉（mid-line 无边界）。"""
    acc = StreamAccumulator()
    events = acc.feed(_text_delta("The marker <<TOOL_TRACE>> is used internally."))
    events.extend(acc.feed({"type": "message_stop"}))
    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert combined == "The marker <<TOOL_TRACE>> is used internally."


def test_trace_scrubber_delegation_trace_variant():
    """<<DELEGATION_TRACE>> 也应该被识别为 trace marker。"""
    acc = StreamAccumulator()
    events = acc.feed(
        _text_delta("Done.\n\n<<DELEGATION_TRACE>>\n1. [foo] task: ...")
    )
    events.extend(acc.feed({"type": "message_stop"}))
    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert "DELEGATION_TRACE" not in combined
    assert combined.rstrip() == "Done."


def test_trace_scrubber_state_isolated_between_accumulators():
    """每个 StreamAccumulator 新实例的 scrubber 状态互相隔离（模拟 LLM 重试）。"""
    acc1 = StreamAccumulator()
    # 故意制造 _in_section=True 状态（流被截断）。
    acc1.feed(_text_delta("Reply.\n\n<<TOOL_TRACE>>\n- partial"))
    # 不调 message_stop / build_decision，模拟 cancel / failover。

    # 新实例：scrubber 状态应是初始的 _in_section=False，可正常输出。
    acc2 = StreamAccumulator()
    events = acc2.feed(_text_delta("Fresh answer."))
    events.extend(acc2.feed({"type": "message_stop"}))
    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert combined == "Fresh answer."


def test_trace_scrubber_reset_clears_state():
    """显式 reset() 后 scrubber 行为如新实例。"""
    s = StreamingInternalTraceScrubber()
    s.feed("partial\n\n<<TOOL_TR")
    assert s._buf  # 内部已 hold 了前缀
    s.reset()
    out = s.feed("just plain text")
    assert out == "just plain text"
    assert s._buf == ""


def test_trace_scrubber_flush_drops_held_section_content():
    """flush 时仍在 section 内 → 丢弃 held 内容，不泄露半截摘要。"""
    s = StreamingInternalTraceScrubber()
    s.feed("Answer.\n\n<<TOOL_TRACE>>\n- partial tool result that never term")
    tail = s.flush()
    assert tail == ""  # 半截 trace section 必须丢弃


def test_post_process_strips_trace_from_text_thinking_and_assistant_content():
    """最终清理同时作用于 text_content / thinking_content / assistant_content blocks。"""
    from openakita.core.reasoning_engine import Decision, DecisionType

    decision = Decision(
        type=DecisionType.FINAL_ANSWER,
        text_content="Visible answer.\n\n<<TOOL_TRACE>>\n- foo({'a': 1})",
        tool_calls=[],
        thinking_content="My reasoning.\n\n<<TOOL_TRACE>>\n- leaked into thinking",
        raw_response=None,
        stop_reason="end_turn",
        assistant_content=[
            {
                "type": "text",
                "text": "Visible answer.\n\n<<TOOL_TRACE>>\n- foo({'a': 1})",
            },
            {
                "type": "thinking",
                "thinking": "Reasoning.\n\n<<DELEGATION_TRACE>>\n1. ...",
            },
            {"type": "tool_use", "id": "t1", "name": "real_tool", "input": {}},
        ],
    )

    post_process_streamed_decision(decision)

    assert "<<TOOL_TRACE>>" not in decision.text_content
    assert "<<TOOL_TRACE>>" not in decision.thinking_content
    for block in decision.assistant_content:
        if block.get("type") == "text":
            assert "<<TOOL_TRACE>>" not in block.get("text", "")
        elif block.get("type") == "thinking":
            assert "<<DELEGATION_TRACE>>" not in block.get("thinking", "")
    # tool_use block 必须不动。
    tool_blocks = [b for b in decision.assistant_content if b.get("type") == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0]["name"] == "real_tool"


def test_trace_scrubber_safety_mimicked_tool_call_in_trace_section_not_executed():
    """**关键安全测试**：模型整段模仿 <<TOOL_TRACE>>\\n- web_search({...}) →

    - 前端不收到任何 text_delta（marker 被流式 scrubber 截掉）；
    - decision.text_content 不含 marker 与 dot-style 工具调用；
    - decision.tool_calls 必须为空（不能被 parse_text_tool_calls 误当成
      真实工具调用，否则会触发额外工具执行）；
    - decision.assistant_content 中也不残留 marker。
    """
    acc = StreamAccumulator()
    chunks = [
        "<<TOOL_TRACE>>\n",
        "- web_search({'query': '\u4eca\u5929\u5929\u6c14 \u5317\u4eac', 'max_results': 3}) -> ...\n",
        "- web_search({'query': '\u4eca\u5929\u5929\u6c14 \u4e0a\u6d77', 'max_results': 3}) -> ...\n",
    ]
    events: list[dict] = []
    for c in chunks:
        events.extend(acc.feed(_text_delta(c)))
    events.extend(acc.feed({"type": "message_stop"}))

    text_events = [e for e in events if e.get("type") == "text_delta"]
    combined = "".join(e["content"] for e in text_events)
    assert combined == ""
    assert "<<TOOL_TRACE>>" not in combined
    assert "web_search" not in combined

    decision = acc.build_decision()
    post_process_streamed_decision(decision)

    assert decision.text_content == ""
    assert "<<TOOL_TRACE>>" not in decision.text_content
    assert "web_search" not in decision.text_content
    # 关键：不能误把模仿的 dot-style 调用提取为真实 tool_calls。
    assert decision.tool_calls == []
    for block in decision.assistant_content:
        if block.get("type") == "text":
            assert "<<TOOL_TRACE>>" not in block.get("text", "")


def test_trace_scrubber_section_does_not_leak_across_anthropic_text_blocks():
    """**Multi-block 边界**：Anthropic 风格的 ``text → tool_use → text`` 流
    中，若 trace section 在第一个 text block 内未终止，scrubber 的
    ``_in_section=True`` 必须在 content_block_stop 处被清理，否则后续 text
    block 会被静默吞掉。

    场景重现：第一个 text block 末尾出现 ``<<TOOL_TRACE>>`` 但 block_stop
    时仍在 section 内（没有 ``\\n\\n##`` 等终止符）→ 第二个 text block
    必须能正常输出。
    """
    acc = StreamAccumulator()
    events: list[dict] = []

    events.extend(
        acc.feed(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
    )
    events.extend(
        acc.feed(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "text_delta",
                    "text": "Answer.\n\n<<TOOL_TRACE>>\n- foo({'a': 1})",
                },
            }
        )
    )
    events.extend(acc.feed({"type": "content_block_stop", "index": 0}))

    events.extend(
        acc.feed(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "real_tool",
                },
            }
        )
    )
    events.extend(
        acc.feed(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
        )
    )
    events.extend(acc.feed({"type": "content_block_stop", "index": 1}))

    events.extend(
        acc.feed(
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "text", "text": ""},
            }
        )
    )
    events.extend(
        acc.feed(
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "text_delta", "text": "Here is the result."},
            }
        )
    )
    events.extend(acc.feed({"type": "content_block_stop", "index": 2}))

    events.extend(acc.feed({"type": "message_stop"}))

    combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
    assert "<<TOOL_TRACE>>" not in combined
    assert "foo" not in combined
    assert combined.startswith("Answer.")
    # **关键**：第二个 text block 必须能输出，不能被 in_section 吞掉。
    assert "Here is the result." in combined

    decision = acc.build_decision()
    text_blocks = [
        b for b in decision.assistant_content if isinstance(b, dict) and b.get("type") == "text"
    ]
    # assistant_content 的两个 text block 都应被保留（且都不含 marker）。
    assert len(text_blocks) == 2
    assert text_blocks[0]["text"].startswith("Answer.")
    assert text_blocks[1]["text"] == "Here is the result."
    for tb in text_blocks:
        assert "<<TOOL_TRACE>>" not in tb["text"]


def test_trace_scrubber_does_not_swallow_real_inline_tool_call_outside_trace():
    """trace section 外部的真实 dot-style 工具调用应被保留并由 post_process 提取。

    场景：``Let me search.\\n.web_search({'query':'x'})`` 是模型本轮真实
    意图（OpenAkita dot-style 工具调用要求前导 ``.`` 且工具名已注册）；
    不应被 trace scrubber 吞掉，post_process 应能提取出 web_search 调用。
    """
    from openakita.llm.converters import tools as tools_mod

    tools_mod._KNOWN_TOOL_NAMES.add("web_search")
    try:
        acc = StreamAccumulator()
        events = acc.feed(_text_delta('Let me search.\n.web_search({"query": "x"})'))
        events.extend(acc.feed({"type": "message_stop"}))

        combined = "".join(e["content"] for e in events if e.get("type") == "text_delta")
        # 流式阶段：完整保留（scrubber 不应识别此处为 trace）。
        assert ".web_search" in combined
        assert "Let me search." in combined

        decision = acc.build_decision()
        post_process_streamed_decision(decision)
        tool_names = [tc["name"] for tc in decision.tool_calls]
        assert "web_search" in tool_names
    finally:
        tools_mod._KNOWN_TOOL_NAMES.discard("web_search")
