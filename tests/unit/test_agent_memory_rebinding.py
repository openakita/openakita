from types import SimpleNamespace

from openakita.agent.core import Agent
from openakita.agents.factory import AgentFactory
from openakita.agents.profile import AgentProfile, AgentType
from openakita.memory.manager import MemoryManager
from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory


def _memory_manager(data_dir, memory_md_path) -> MemoryManager:
    return MemoryManager(
        data_dir=data_dir,
        memory_md_path=memory_md_path,
        search_backend="fts5",
    )


def _agent_with_memory_consumers(memory_manager: MemoryManager) -> Agent:
    agent = Agent.__new__(Agent)
    agent.brain = None
    agent.memory_manager = memory_manager
    agent.profile_manager = object()
    agent._memory_backends = {}
    agent.prompt_assembler = SimpleNamespace(_memory_manager=memory_manager)
    agent.reasoning_engine = SimpleNamespace(_memory_manager=memory_manager)
    agent.response_handler = SimpleNamespace(_memory_manager=memory_manager)
    agent.proactive_engine = SimpleNamespace(memory_manager=memory_manager)
    agent._task_executor = SimpleNamespace(memory_manager=memory_manager)
    agent._plugin_manager = SimpleNamespace(
        hook_registry=SimpleNamespace(get_hooks=lambda _hook_name: []),
        _external_host_refs={
            "memory_manager": memory_manager,
            "external_retrieval_sources": memory_manager.retrieval_engine._external_sources,
        },
    )
    agent._system_prompt_cache = {"stale": "prompt"}
    agent._system_prompt_cache_dirty = False
    agent._context = SimpleNamespace(system="stale shared prompt")
    agent._build_system_prompt = lambda: "fresh isolated prompt"
    return agent


def _apply_isolation(tmp_path, monkeypatch, *, profile_id: str):
    profile_dir = tmp_path / "profiles" / profile_id
    profile_dir.mkdir(parents=True)
    profile_store = SimpleNamespace(ensure_profile_dir=lambda _profile_id: profile_dir)
    monkeypatch.setattr("openakita.agents.profile.get_profile_store", lambda: profile_store)

    shared = _memory_manager(tmp_path / "shared", tmp_path / "GLOBAL_MEMORY.md")
    agent = _agent_with_memory_consumers(shared)
    profile = AgentProfile(
        id=profile_id,
        name="Isolated Agent",
        description="",
        type=AgentType.CUSTOM,
        created_by="test",
        memory_mode="isolated",
        memory_inherit_global=False,
    )
    AgentFactory._apply_memory_isolation(agent, profile)
    return agent, shared


def test_isolated_memory_rebinds_full_chain_and_cannot_read_global(tmp_path, monkeypatch):
    agent, shared = _apply_isolation(tmp_path, monkeypatch, profile_id="strict-isolation")
    shared.start_session("global-session", user_id="desktop_user", workspace_id="default")
    shared.add_memory(
        SemanticMemory(
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            content="The global launch code is violet-orchid-9472.",
            importance_score=0.95,
        ),
        scope="user",
    )

    isolated = agent.memory_manager
    isolated.start_session("isolated-session", user_id="desktop_user", workspace_id="default")

    assert isolated is not shared
    assert agent.prompt_assembler._memory_manager is isolated
    assert agent.reasoning_engine._memory_manager is isolated
    assert agent.response_handler._memory_manager is isolated
    assert agent.proactive_engine.memory_manager is isolated
    assert agent._task_executor.memory_manager is isolated
    assert agent._plugin_manager._external_host_refs["memory_manager"] is isolated
    assert (
        agent._plugin_manager._external_host_refs["external_retrieval_sources"]
        is isolated.retrieval_engine._external_sources
    )
    assert isolated._global_store_ref is None
    assert isolated.retrieval_engine._external_sources == []
    assert agent._system_prompt_cache == {}
    assert agent._system_prompt_cache_dirty is True
    assert agent._context.system == "fresh isolated prompt"

    query = "What is the global launch code violet orchid?"
    shared_rows = shared.query_visible_semantic(limit=10)
    assert any("violet-orchid-9472" in memory.content for memory in shared_rows)
    assert "violet-orchid-9472" not in agent.prompt_assembler._memory_manager.get_injection_context(
        query
    )


def test_isolated_memory_recalls_long_term_memory_in_a_new_session(tmp_path, monkeypatch):
    agent, _shared = _apply_isolation(tmp_path, monkeypatch, profile_id="cross-session")
    isolated = agent.memory_manager

    isolated.start_session("session-a", user_id="desktop_user", workspace_id="default")
    isolated.add_memory(
        SemanticMemory(
            type=MemoryType.PREFERENCE,
            priority=MemoryPriority.LONG_TERM,
            content="The user prefers release summaries named silver-pine-5831.",
            importance_score=0.95,
        ),
        scope="user",
    )

    isolated.start_session("session-b", user_id="desktop_user", workspace_id="default")
    context = agent.prompt_assembler._memory_manager.get_injection_context(
        "How should release summaries be named?"
    )

    assert "silver-pine-5831" in context
