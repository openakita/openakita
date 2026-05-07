"""L1 tests for URL fidelity during context compression."""

from __future__ import annotations

from openakita.core.context_manager import ContextManager


def test_extract_urls_from_messages_keeps_exact_links():
    messages = [
        {"role": "user", "content": "读一下 https://example.com/a?x=1 和 http://docs.example.org/page。"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "工具会读取 https://example.com/a?x=1"},
            ],
        },
    ]

    facts = ContextManager._extract_urls_from_messages(messages)

    assert [f["url"] for f in facts] == [
        "https://example.com/a?x=1",
        "http://docs.example.org/page",
    ]
    assert facts[0]["hostname"] == "example.com"


def test_url_facts_prompt_requires_verbatim_preservation():
    prompt = ContextManager._format_url_facts_for_prompt(
        [
            {
                "message_index": "0",
                "role": "user",
                "hostname": "example.com",
                "url": "https://example.com/a",
            }
        ]
    )

    assert "原始链接清单" in prompt
    assert "不得改写" in prompt
    assert "https://example.com/a" in prompt


def test_previous_summaries_are_keyed_by_conversation():
    manager = ContextManager(brain=object())

    manager._previous_summaries["conv-a"] = "summary a"
    manager._previous_summaries["conv-b"] = "summary b"

    assert manager._previous_summaries["conv-a"] == "summary a"
    assert manager._previous_summaries["conv-b"] == "summary b"
