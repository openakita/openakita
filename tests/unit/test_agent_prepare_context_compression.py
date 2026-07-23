import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openakita.agent.core import Agent
from openakita.agent.errors import UserCancelledError
from openakita.core._context_runtime import _CancelledError as _CtxCancelledError
from openakita.core.agent_state import AgentState


class _FakeContextManager:
    def __init__(self, cancel_event: asyncio.Event | None = None) -> None:
        self._cancel_event = cancel_event

    def set_cancel_event(self, event: asyncio.Event | None) -> None:
        self._cancel_event = event


def _make_agent(cancel_event: asyncio.Event | None = None) -> Agent:
    agent = Agent.__new__(Agent)
    agent.agent_state = AgentState()
    agent.context_manager = _FakeContextManager(cancel_event)
    return agent


def _make_chat_agent() -> Agent:
    agent = _make_agent()
    agent._initialized = True
    agent._preferred_endpoint = None
    agent._pending_cancels = {}
    agent.brain = SimpleNamespace(_llm_client=None)
    agent._resolve_conversation_id = lambda _session, session_id: session_id
    agent._cleanup_session_state = lambda _im_tokens: None
    return agent


@pytest.mark.asyncio
async def test_prepare_compression_clears_stale_cancel_event() -> None:
    stale_event = asyncio.Event()
    stale_event.set()
    agent = _make_agent(stale_event)
    messages = [{"role": "user", "content": "继续推进"}]

    async def _compress_context(items, **_kwargs):
        assert agent.context_manager._cancel_event is None
        return items + [{"role": "assistant", "content": "compressed"}]

    agent._compress_context = _compress_context

    result = await agent._compress_context_for_prepare(
        messages,
        session_id="session-1",
        conversation_id="conversation-1",
    )

    assert result[-1]["content"] == "compressed"


@pytest.mark.asyncio
async def test_prepare_compression_passes_turn_specific_system_prompt() -> None:
    agent = _make_agent()
    captured = {}

    async def _compress_context(items, **kwargs):
        captured.update(kwargs)
        return items

    agent._compress_context = _compress_context

    await agent._compress_context_for_prepare(
        [{"role": "user", "content": "question"}],
        session_id="session-1",
        conversation_id="conversation-1",
        system_prompt="minimal turn prompt",
    )

    assert captured["system_prompt"] == "minimal turn prompt"


@pytest.mark.asyncio
async def test_prepare_compression_falls_back_when_cancel_is_not_current_task() -> None:
    agent = _make_agent()
    messages = [{"role": "user", "content": "恢复这个长会话"}]

    async def _compress_context(_items, **_kwargs):
        raise _CtxCancelledError("Context compression cancelled by user")

    agent._compress_context = _compress_context

    result = await agent._compress_context_for_prepare(
        messages,
        session_id="session-1",
        conversation_id="conversation-1",
    )

    assert result is messages
    assert agent.context_manager._cancel_event is None


@pytest.mark.asyncio
async def test_prepare_compression_preserves_real_user_cancel() -> None:
    agent = _make_agent()
    task = agent.agent_state.begin_task(session_id="session-1")
    task.cancel("用户从界面取消任务")
    agent.context_manager.set_cancel_event(task.cancel_event)
    messages = [{"role": "user", "content": "继续推进"}]

    async def _compress_context(_items, **_kwargs):
        raise _CtxCancelledError("Context compression cancelled by user")

    agent._compress_context = _compress_context

    with pytest.raises(UserCancelledError) as exc_info:
        await agent._compress_context_for_prepare(
            messages,
            session_id="session-1",
            conversation_id="conversation-1",
        )

    assert exc_info.value.reason == "用户从界面取消任务"
    assert exc_info.value.source == "prepare_context_compress"


@pytest.mark.asyncio
async def test_chat_with_session_returns_stop_ack_when_prepare_is_cancelled() -> None:
    agent = _make_chat_agent()

    async def _prepare_session_context(**_kwargs):
        raise UserCancelledError(reason="用户从界面取消任务", source="prepare_context_compress")

    agent._prepare_session_context = _prepare_session_context

    result = await agent.chat_with_session(
        message="继续推进",
        session_messages=[],
        session_id="session-1",
        ask_user_reply=SimpleNamespace(answer="继续推进", message_id="ask-1"),
    )

    assert result == "✅ 好的，已停止当前任务。"


@pytest.mark.asyncio
async def test_chat_with_session_stream_returns_stop_ack_when_prepare_is_cancelled() -> None:
    agent = _make_chat_agent()

    async def _prepare_session_context(**_kwargs):
        raise UserCancelledError(reason="用户从界面取消任务", source="prepare_context_compress")

    agent._prepare_session_context = _prepare_session_context

    events = [
        event
        async for event in agent.chat_with_session_stream(
            message="继续推进",
            session_messages=[],
            session_id="session-1",
            ask_user_reply=SimpleNamespace(answer="继续推进", message_id="ask-1"),
        )
    ]

    assert events == [
        {"type": "heartbeat"},
        {"type": "preparation_stage", "stage": "analyzing_intent"},
        {"type": "text_delta", "content": "✅ 好的，已停止当前任务。"},
        {"type": "done"},
    ]


@pytest.mark.asyncio
async def test_chat_with_session_stream_reports_preparation_stages() -> None:
    agent = _make_chat_agent()

    async def _prepare_session_context(**kwargs):
        kwargs["progress_callback"]("building_context")
        await asyncio.sleep(0)
        return [], "cli", None, "session-1", None

    checks = 0

    def _is_session_cancelled(_session_id):
        nonlocal checks
        checks += 1
        return checks >= 2

    agent._prepare_session_context = _prepare_session_context
    agent._is_session_cancelled = _is_session_cancelled
    agent._consume_pending_cancel = lambda _session_id: None
    agent._build_slow_compiler_hint = lambda _session_id: None

    events = [
        event
        async for event in agent.chat_with_session_stream(
            message="继续推进",
            session_messages=[],
            session_id="session-1",
        )
    ]

    assert events == [
        {"type": "heartbeat"},
        {"type": "preparation_stage", "stage": "analyzing_intent"},
        {"type": "preparation_stage", "stage": "building_context"},
        {"type": "preparation_stage", "stage": "ready"},
        {"type": "text_delta", "content": "✅ 好的，已停止当前任务。"},
        {"type": "done"},
    ]


@pytest.mark.asyncio
async def test_build_system_prompt_compiled_includes_ask_user_reply_context() -> None:
    agent = Agent.__new__(Agent)
    captured = {}

    class _PromptAssembler:
        async def build_system_prompt_compiled(self, *_args, **kwargs):
            captured.update(kwargs)
            return "system prompt"

    agent.prompt_assembler = _PromptAssembler()
    agent.brain = SimpleNamespace(
        model="mock-model",
        get_current_model_info=lambda conversation_id=None: {"model": "mock-model"},
    )
    agent.tool_executor = SimpleNamespace(_current_mode="agent")
    agent._current_intent = None
    agent._is_sub_agent_call = False
    agent._system_prompt_cache = {}
    agent._system_prompt_cache_dirty = True
    agent._custom_prompt_suffix = ""
    agent._org_context = None
    agent._get_raw_context_window = lambda: 8192
    agent._resolve_model_lookup_id = lambda session=None, conversation_id=None: (
        conversation_id or ""
    )
    agent._resolve_prompt_strategy = lambda *_args, **_kwargs: SimpleNamespace(
        profile="default",
        prompt_mode="default",
        memory_scope="session",
        catalog_scope=[],
        include_project_guidelines=True,
    )
    agent._resolve_agent_voice = lambda: "default"
    agent._prepare_prompt_identity_dir = lambda: Path("identity")
    agent._build_runtime_env_prompt_section = lambda: ""
    agent._build_multi_agent_prompt_section = lambda: ""

    prompt = await agent._build_system_prompt_compiled(
        task_description="继续",
        ask_user_reply=SimpleNamespace(answer="选择方案 A", message_id="ask-msg-1"),
    )

    assert prompt == "system prompt"
    assert captured["session_context"]["ask_user_reply"] == {
        "answer": "选择方案 A",
        "message_id": "ask-msg-1",
    }


@pytest.mark.asyncio
async def test_trait_mining_background_does_not_block_and_persists(monkeypatch) -> None:
    agent = Agent.__new__(Agent)
    started = asyncio.Event()
    release = asyncio.Event()
    trait = SimpleNamespace(dimension="reply_length", preference="short")
    persisted = []

    class _TraitMiner:
        brain = object()

        async def mine_from_message(self, message, role="user"):
            assert message == "以后回答简短一点"
            assert role == "user"
            started.set()
            await release.wait()
            return [trait]

    agent.trait_miner = _TraitMiner()
    agent.memory_manager = object()
    agent._trait_mining_tasks = set()
    monkeypatch.setattr(
        "openakita.agent.persona.persist_trait_to_memory",
        lambda memory_manager, mined_trait: persisted.append((memory_manager, mined_trait)),
    )

    agent._schedule_trait_mining_background("以后回答简短一点", "session-1")

    assert len(agent._trait_mining_tasks) == 1
    await asyncio.wait_for(started.wait(), timeout=1)
    task = next(iter(agent._trait_mining_tasks))
    assert not task.done()

    release.set()
    await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)

    assert persisted == [(agent.memory_manager, trait)]
    assert not agent._trait_mining_tasks


@pytest.mark.asyncio
async def test_finalize_schedules_trait_mining_after_lifecycle_completion() -> None:
    agent = Agent.__new__(Agent)
    agent.reasoning_engine = SimpleNamespace(_last_react_trace=[], _last_exit_reason="ask_user")
    agent._last_finalized_trace = []
    agent._last_usage_summary = {}
    agent._current_user_message = "记住我喜欢简短回答"
    agent._current_conversation_id = "conversation-1"
    agent._extract_usage_summary = lambda _trace: {}
    agent._extract_outbound_attachments = lambda _calls, _results: []
    agent.memory_manager = SimpleNamespace(record_turn=lambda *_args, **_kwargs: None)
    events = []

    async def _finish_lifecycle(**_kwargs):
        events.append("lifecycle_finished")

    agent._finish_agent_run_lifecycle_once = _finish_lifecycle
    agent._schedule_trait_mining_background = lambda message, session_id: events.append(
        ("trait_scheduled", message, session_id)
    )
    task_monitor = SimpleNamespace(
        complete=lambda **_kwargs: SimpleNamespace(retrospect_needed=False)
    )

    await agent._finalize_session(
        response_text="好的",
        session=None,
        session_id="session-1",
        task_monitor=task_monitor,
    )

    assert events == [
        "lifecycle_finished",
        ("trait_scheduled", "记住我喜欢简短回答", "session-1"),
    ]
