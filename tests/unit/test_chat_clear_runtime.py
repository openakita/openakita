import sys
from types import SimpleNamespace

from openakita.api.routes.chat import _cleanup_chat_runtime_state


class _DummyPolicyEngine:
    def __init__(self) -> None:
        self.cleaned: list[str] = []

    def cleanup_session(self, session_id: str) -> None:
        self.cleaned.append(session_id)


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.purged: list[str] = []

    def purge_session_states(self, session_id: str) -> None:
        self.purged.append(session_id)


def test_clear_chat_runtime_state_cleans_policy_todo_and_orchestrator(monkeypatch):
    engine = _DummyPolicyEngine()
    orchestrator = _DummyOrchestrator()
    global_orchestrator = _DummyOrchestrator()
    cleared_todos: list[str] = []

    monkeypatch.setattr("openakita.core.policy.get_policy_engine", lambda: engine)
    monkeypatch.setattr(
        "openakita.tools.handlers.plan.clear_session_todo_state",
        lambda session_id: cleared_todos.append(session_id),
    )
    monkeypatch.setitem(
        sys.modules,
        "openakita.main",
        SimpleNamespace(_orchestrator=global_orchestrator),
    )

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(orchestrator=orchestrator)))
    _cleanup_chat_runtime_state(request, "conv-1")

    assert engine.cleaned == ["conv-1"]
    assert cleared_todos == ["conv-1"]
    assert orchestrator.purged == ["conv-1"]
    assert global_orchestrator.purged == ["conv-1"]
