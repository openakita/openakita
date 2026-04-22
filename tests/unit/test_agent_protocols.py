from __future__ import annotations

from openakita.agents.protocols import AgentLike, BrainLike, ChatSessionLike


class _FakeBrain:
    def append_user(self, *_a, **_kw): pass
    def append_assistant(self, *_a, **_kw): pass
    def append_tool_result(self, *_a, **_kw): pass
    def is_loaded(self) -> bool: return False


class _FakeSession:
    id = "sid"
    cwd = "/tmp"
    conversation_id = "conv"


class _FakeAgent:
    def __init__(self):
        self.agent_state = object()
        self.brain = _FakeBrain()
        self.last_session_id: str | None = None

    async def initialize(self, *, lightweight: bool = True): pass
    async def chat_with_session(self, session, message, *, is_sub_agent=False,
                                image_paths=(), **_): return None
    async def execute_task_from_message(self, message: str): return None
    async def cancel(self): pass
    async def shutdown(self): pass


def test_fake_agent_satisfies_protocol():
    assert isinstance(_FakeAgent(), AgentLike)


def test_fake_brain_satisfies_protocol():
    assert isinstance(_FakeBrain(), BrainLike)


def test_plain_object_does_not_satisfy_protocol():
    assert not isinstance(object(), AgentLike)


def test_session_like_is_structural():
    assert isinstance(_FakeSession(), ChatSessionLike)
