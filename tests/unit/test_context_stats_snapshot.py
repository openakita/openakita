from __future__ import annotations

from types import SimpleNamespace

from openakita.api.routes.chat import _should_replace_context_usage
from openakita.core.context_stats import (
    get_context_snapshot,
    merge_context_snapshot_into_usage,
    update_context_snapshot,
)


def test_provider_context_usage_wins_within_same_iteration():
    provider = {
        "context_scope_id": "task-1:2",
        "source": "provider",
        "usage_estimated": False,
        "updated_at": 10,
    }
    later_estimate = {
        "context_scope_id": "task-1:2",
        "source": "stream_estimate",
        "usage_estimated": True,
        "updated_at": 11,
    }
    next_iteration = {**later_estimate, "context_scope_id": "task-1:3"}

    assert not _should_replace_context_usage(provider, later_estimate)
    assert _should_replace_context_usage(provider, next_iteration)


class _StubContextManager:
    def __init__(self, brain):
        self._brain = brain

    def estimate_messages_tokens(self, messages):
        return sum(len(str(m.get("content", ""))) for m in messages)

    def get_max_context_tokens(self, conversation_id=None):
        assert conversation_id in (None, "conv-1")
        return 1000


def _make_agent():
    endpoint = SimpleNamespace(name="primary", context_window=2000, max_tokens=200)
    brain = SimpleNamespace(
        _llm_client=SimpleNamespace(endpoints=[endpoint]),
        get_current_model_info=lambda conversation_id=None: {
            "name": "primary",
            "provider": "openai",
            "model": "gpt-test",
        },
    )
    ctx_mgr = _StubContextManager(brain)
    reasoning_engine = SimpleNamespace(
        _context_manager=ctx_mgr,
        _last_working_messages=[{"role": "user", "content": "hello"}],
        _last_context_pressure={"context_safe": True},
    )
    return SimpleNamespace(reasoning_engine=reasoning_engine, context_manager=None)


def test_update_context_snapshot_stores_runtime_fields():
    agent = _make_agent()
    snapshot = update_context_snapshot(agent, "conv-1", source="test")

    assert snapshot is not None
    assert snapshot.context_tokens == 5
    assert snapshot.context_limit == 1000
    assert snapshot.remaining_tokens == 995
    assert snapshot.percent == 0.5
    assert snapshot.endpoint_name == "primary"
    assert snapshot.provider == "openai"
    assert snapshot.model == "gpt-test"
    assert snapshot.context_pressure == {"context_safe": True}

    data = snapshot.to_dict()
    assert data["history_context_tokens"] == 5
    assert data["history_context_limit"] == 1000


def test_get_context_snapshot_reuses_stored_snapshot():
    agent = _make_agent()
    created = update_context_snapshot(agent, "conv-1")

    assert get_context_snapshot(agent, "conv-1") is created


def test_context_snapshots_are_isolated_by_conversation():
    agent = _make_agent()
    first = update_context_snapshot(
        agent,
        "conv-1",
        measured_context_tokens=111,
        source="provider",
    )
    agent.reasoning_engine._context_manager.get_max_context_tokens = lambda conversation_id=None: (
        1000
    )
    second = update_context_snapshot(
        agent,
        "conv-2",
        measured_context_tokens=222,
        source="provider",
    )

    assert first is not None
    assert second is not None
    assert get_context_snapshot(agent, "conv-1").context_tokens == 111
    assert get_context_snapshot(agent, "conv-2").context_tokens == 222


def test_merge_context_snapshot_into_usage_preserves_billable_fields():
    agent = _make_agent()
    snapshot = update_context_snapshot(agent, "conv-1")

    usage = merge_context_snapshot_into_usage({"input_tokens": 10}, snapshot)

    assert usage is not None
    assert usage["input_tokens"] == 10
    assert usage["context_tokens"] == 5
    assert usage["history_context_limit"] == 1000
    assert usage["remaining_tokens"] == 995
