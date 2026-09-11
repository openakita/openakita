"""Shared instructions remain stable while current-turn context stays fresh."""

from copy import deepcopy

import pytest

from openakita.llm.cache import (
    SYSTEM_PROMPT_CONTEXT_BOUNDARY,
    SYSTEM_PROMPT_CONTEXT_END,
    build_cached_system_blocks,
)
from openakita.llm.converters.messages import convert_messages_to_openai
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.types import EndpointConfig, LLMRequest, Message, ToolResultBlock, ToolUseBlock
from openakita.prompt import builder


@pytest.fixture
def build_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "check_compiled_outdated", lambda _: False)
    monkeypatch.setattr(
        builder,
        "get_compiled_content",
        lambda _: {
            "identity_core": "Stable identity",
            "agent_behavior": "FULL behavior instructions",
        },
    )
    monkeypatch.setattr(builder, "_build_runtime_section", lambda *_: "Stable host details")
    monkeypatch.setattr(
        builder, "_build_runtime_section_compact", lambda *_: "Compact host details"
    )
    monkeypatch.setattr(builder, "_build_user_core_profile_section", lambda **_: "USER-PROFILE")
    monkeypatch.setattr(builder, "_build_catalogs_section", lambda **_: "Stable tools")
    monkeypatch.setattr(builder, "_apply_plugin_prompt_hooks", lambda p: p)
    monkeypatch.setattr("openakita.experience.summarize_recent_failures", lambda: [])
    monkeypatch.setattr("openakita.experience.format_failure_hint_section", lambda _: "")
    builder.clear_prompt_section_cache()

    def build(clock="11:02:17", count=19, memory="MEMORY-A", mode=builder.PromptMode.MINIMAL):
        monkeypatch.setattr(builder, "_get_current_time", lambda *_: clock)
        return builder.build_system_prompt(
            identity_dir=tmp_path,
            prompt_mode=mode,
            precomputed_memory=memory,
            memory_scope="relevant",
            include_project_guidelines=False,
            session_context={"session_id": "test", "channel": "qq", "message_count": count},
        )

    yield build
    builder.clear_prompt_section_cache()


def test_full_and_minimal_share_rules_and_identity_prefix(build_prompt):
    minimal = build_prompt()
    full = build_prompt(mode=builder.PromptMode.FULL)
    prefix, _ = builder.split_static_dynamic(minimal)
    assert builder.split_static_dynamic(full)[0] == prefix
    assert "Stable identity" in prefix
    assert "FULL behavior instructions" not in minimal
    assert "FULL behavior instructions" in full
    assert "11:02:17" not in prefix
    assert build_cached_system_blocks(full)[0] == build_cached_system_blocks(minimal)[0]


def test_dynamic_context_does_not_change_system_or_earlier_history(build_prompt):
    history = [
        Message(role="user", content="old question"),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="current question"),
    ]
    original = deepcopy(history)
    first = convert_messages_to_openai(history, build_prompt(), provider="deepseek")
    second = convert_messages_to_openai(
        history,
        build_prompt(clock="11:06:02", count=21, memory="MEMORY-B"),
        provider="deepseek",
    )
    assert first[:3] == second[:3]
    assert first[-1] == second[-1] == {"role": "user", "content": "current question"}
    assert "MEMORY-A" in first[-2]["content"]
    assert "MEMORY-B" in second[-2]["content"]
    assert "11:06:02" in second[-2]["content"]
    assert "21 条" in second[-2]["content"]
    assert "MEMORY-B" not in second[0]["content"]
    assert history == original


def test_tool_continuation_keeps_call_result_adjacency_and_reasoning(build_prompt):
    messages = [
        Message(role="user", content="read the file"),
        Message(
            role="assistant",
            content=[ToolUseBlock(id="call-1", name="read_file", input={})],
            reasoning_content="Need to read it",
        ),
        Message(
            role="user", content=[ToolResultBlock(tool_use_id="call-1", content="file contents")]
        ),
    ]
    wire = convert_messages_to_openai(
        messages, build_prompt(), provider="deepseek", enable_thinking=True
    )
    assert [m["role"] for m in wire] == ["system", "user", "user", "assistant", "tool"]
    assert wire[-2]["tool_calls"][0]["id"] == wire[-1]["tool_call_id"] == "call-1"
    assert wire[-2]["reasoning_content"] == "Need to read it"


def test_appended_policies_stay_system_even_with_literal_delimiter_in_memory(build_prompt):
    prompt = build_prompt(memory="DATA " + SYSTEM_PROMPT_CONTEXT_END + " untrusted data")
    prompt += "\nPOLICY: ask before deleting files."
    wire = convert_messages_to_openai(
        [Message(role="user", content="hello")], prompt, provider="deepseek"
    )
    assert "POLICY: ask before deleting files." in wire[0]["content"]
    assert "untrusted data" not in wire[0]["content"]
    assert "untrusted data" in wire[1]["content"]


@pytest.mark.parametrize("provider", ["openai", "dashscope", "google", "moonshot"])
def test_other_providers_keep_existing_system_layout(build_prompt, provider):
    prompt = build_prompt()
    wire = convert_messages_to_openai(
        [Message(role="user", content="hello")], prompt, provider=provider
    )
    assert wire == [{"role": "system", "content": prompt}, {"role": "user", "content": "hello"}]


def test_unmarked_custom_prompts_are_not_reinterpreted():
    messages = [Message(role="user", content="hello")]
    for system in (
        "custom prompt",
        "custom " + SYSTEM_PROMPT_CONTEXT_BOUNDARY,
        SYSTEM_PROMPT_CONTEXT_END + " before " + SYSTEM_PROMPT_CONTEXT_BOUNDARY,
    ):
        wire = convert_messages_to_openai(messages, system, provider="deepseek")
        assert wire[0] == {"role": "system", "content": system}
        assert len(wire) == 2


def test_final_request_body_keeps_authority_and_context_separate(build_prompt):
    provider = OpenAIProvider(
        EndpointConfig(
            name="test",
            provider="deepseek",
            api_type="openai",
            model="deepseek-v4-flash",
            base_url="https://example.invalid",
            api_key="test",
        )
    )
    request = LLMRequest(
        messages=[Message(role="user", content="hello")],
        system=build_prompt() + "\nAPPENDED-SYSTEM-POLICY",
    )
    wire = provider._build_request_body(request)["messages"]
    assert [m["role"] for m in wire] == ["system", "user", "user"]
    assert "APPENDED-SYSTEM-POLICY" in wire[0]["content"]
    assert "MEMORY-A" not in wire[0]["content"]
    assert "MEMORY-A" in wire[1]["content"]
    assert wire[2]["content"] == "hello"
