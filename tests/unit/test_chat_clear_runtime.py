import sys
from types import SimpleNamespace

from openakita.api.routes.chat import _cleanup_chat_runtime_state


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.purged: list[str] = []

    def purge_session_states(self, session_id: str) -> None:
        self.purged.append(session_id)


def test_clear_chat_runtime_state_cleans_policy_todo_and_orchestrator(monkeypatch):
    """C8b-3: ``/api/chat/clear`` 路径迁移后由 bus.cleanup_session +
    SessionAllowlistManager.clear 取代 v1 ``pe.cleanup_session``。
    本测试守护：bus 与 session manager 都被正确清理。"""
    from openakita.core.policy_v2 import get_session_allowlist_manager
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    reset_ui_confirm_bus()
    bus = get_ui_confirm_bus()
    bus.store_pending("conv-1-confirm", "write_file", {"path": "/tmp/x"}, session_id="conv-1")
    sess_mgr = get_session_allowlist_manager()
    sess_mgr.clear()
    sess_mgr.add("write_file", {"path": "/tmp/x"}, needs_sandbox=False)

    orchestrator = _DummyOrchestrator()
    global_orchestrator = _DummyOrchestrator()
    cleared_todos: list[str] = []

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

    # bus pending for the cleared session must be gone
    assert not any(p.get("session_id") == "conv-1" for p in bus.list_pending())
    # session allowlist wiped
    assert sess_mgr.is_allowed("write_file", {"path": "/tmp/x"}) is None
    # other concerns still handled
    assert cleared_todos == ["conv-1"]
    assert orchestrator.purged == ["conv-1"]
    assert global_orchestrator.purged == ["conv-1"]
