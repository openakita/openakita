from openakita.core.context_manager import ContextManager
from openakita.memory.manager import MemoryManager
from openakita.sessions.session import Session


def test_precompact_snapshot_persists_to_session_context(tmp_path):
    session = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
    manager = MemoryManager(data_dir=tmp_path / "memory", memory_md_path=tmp_path / "MEMORY.md")
    manager.start_session("s1", user_id="u1", workspace_id="w1")
    manager.attach_session_context(session)

    snapshot = ContextManager._build_precompact_snapshot(
        [{"role": "user", "content": "必须记住当前任务要修改 src/openakita/core/context_manager.py"}],
        manager,
    )
    manager.save_precompact_snapshot(snapshot)

    assert session.context.precompact_snapshot["facts"]
    assert "context_manager.py" in session.context.precompact_snapshot["facts"][0]


def test_precompact_snapshot_context_is_session_scoped(tmp_path):
    manager = MemoryManager(data_dir=tmp_path / "memory", memory_md_path=tmp_path / "MEMORY.md")
    manager.start_session("s1", user_id="u1", workspace_id="w1")
    manager.save_precompact_snapshot({"session_id": "s1", "facts": ["必须保留路径 A.py"]})
    assert "A.py" in manager.get_precompact_snapshot_context()

    manager.start_session("s2", user_id="u1", workspace_id="w1")
    assert manager.get_precompact_snapshot_context() == ""
