import pytest

from openakita.agents.factory import AgentInstancePool
from openakita.agents.profile import AgentProfile


class _DummyAgent:
    def __init__(self, label: str):
        self.label = label
        self.brain = object()
        self.shutdown_called = False

    async def shutdown(self):
        self.shutdown_called = True


class _DummyFactory:
    def __init__(self):
        self.created = 0
        self.parent_brains = []

    async def create(self, profile: AgentProfile, parent_brain=None):
        self.created += 1
        self.parent_brains.append(parent_brain)
        return _DummyAgent(f"{profile.id}-{self.created}")


@pytest.mark.asyncio
async def test_runtime_config_change_recreates_pooled_agent():
    factory = _DummyFactory()
    pool = AgentInstancePool(factory=factory)
    profile = AgentProfile(id="default", name="Default")

    first = await pool.get_or_create("session-1", profile)
    reused = await pool.get_or_create("session-1", profile)
    assert reused is first

    pool.notify_runtime_config_changed("llm_config")

    recreated = await pool.get_or_create("session-1", profile)
    assert recreated is not first
    assert factory.created == 2


@pytest.mark.asyncio
async def test_runtime_config_change_does_not_reuse_stale_parent_brain():
    factory = _DummyFactory()
    pool = AgentInstancePool(factory=factory)
    default_profile = AgentProfile(id="default", name="Default")
    worker_profile = AgentProfile(id="worker", name="Worker")

    await pool.get_or_create("session-1", default_profile)
    await pool.get_or_create("session-1", worker_profile)

    pool.notify_runtime_config_changed("llm_config")

    await pool.get_or_create("session-1", worker_profile)

    assert factory.parent_brains[-1] is None
