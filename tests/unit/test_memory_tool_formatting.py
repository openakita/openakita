from types import SimpleNamespace
from unittest.mock import MagicMock

from openakita.memory.types import Episode
from openakita.tools.handlers.memory import MemoryHandler


def test_list_recent_tasks_with_non_string_tools_do_not_crash():
    episode = Episode(
        id="episode-1234567890",
        goal="检查记忆返回",
        tools_used=[{"name": {"bad": "shape"}}, {"function": {"name": "search_memory"}}],
    )
    store = MagicMock()
    store.get_recent_episodes.return_value = [episode]
    handler = MemoryHandler(SimpleNamespace(memory_manager=SimpleNamespace(store=store)))

    result = handler._list_recent_tasks({"days": 3, "limit": 15})

    assert "检查记忆返回" in result
    assert '工具: {"bad": "shape"}, search_memory' in result
