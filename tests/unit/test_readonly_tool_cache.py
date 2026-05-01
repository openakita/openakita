from openakita.core.reasoning_engine import ReasoningEngine


def test_repeated_web_fetch_uses_cached_summary_without_external_retry():
    engine = ReasoningEngine(
        brain=None,
        tool_executor=None,
        context_manager=None,
        response_handler=None,
        agent_state=None,
    )
    args = {"url": "https://example.com/article", "max_length": 5000}

    assert engine._cached_readonly_tool_result("web_fetch", args, "call_1") is None

    engine._remember_readonly_tool_result("web_fetch", args, "A" * 5000)
    cached = engine._cached_readonly_tool_result("web_fetch", args, "call_2")

    assert cached is not None
    assert cached["tool_use_id"] == "call_2"
    assert cached["cached"] is True
    assert "未再次发起外部请求" in cached["content"]
    assert len(cached["content"]) < 3200
