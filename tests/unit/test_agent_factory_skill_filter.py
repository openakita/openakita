from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.agents.factory import AgentFactory
from openakita.agents.profile import AgentProfile, SkillsMode


class _FakeRegistry:
    def __init__(self, skills):
        self._skills = list(skills)
        self.unregistered: list[str] = []
        self.catalog_hidden: list[str] = []

    def list_all(self, include_disabled: bool = False):
        return list(self._skills)

    def unregister(self, skill_name: str) -> None:
        self.unregistered.append(skill_name)
        self._skills = [skill for skill in self._skills if skill.skill_id != skill_name]

    def set_catalog_hidden(self, skill_name: str, hidden: bool = True) -> bool:
        if hidden:
            self.catalog_hidden.append(skill_name)
        for s in self._skills:
            if s.skill_id == skill_name:
                s.catalog_hidden = hidden
                return True
        return False


class _FakeCatalog:
    def __init__(self) -> None:
        self.invalidated = 0
        self.generated = 0

    def invalidate_cache(self) -> None:
        self.invalidated += 1

    def generate_catalog(self) -> None:
        self.generated += 1


class _CreateFakeAgent:
    def __init__(self, *, name, brain=None, **kwargs):
        self.name = name
        self.brain = brain
        self.kwargs = kwargs
        self.tool_catalog = SimpleNamespace()
        self.mcp_catalog = SimpleNamespace()
        self.prompt_assembler = SimpleNamespace(_tool_catalog=None, _mcp_catalog=None)
        self._context = SimpleNamespace(system="")
        self.configured_profile = None
        self.initialized_with = None

    async def initialize(self, **kwargs):
        self.initialized_with = kwargs

    def configure_runtime_environment(self, profile):
        self.configured_profile = profile

    def _build_system_prompt(self):
        return "rebuilt"


def test_agent_profile_filter_defaults_are_all():
    profile = AgentProfile(id="worker", name="Worker")

    assert profile.skills == []
    assert profile.skills_mode == SkillsMode.ALL
    assert profile.tools == []
    assert profile.tools_mode == "all"
    assert profile.mcp_servers == []
    assert profile.mcp_mode == "all"


@pytest.mark.asyncio
async def test_create_does_not_apply_skill_tool_or_mcp_filters():
    profile = AgentProfile(
        id="filtered",
        name="Filtered",
        skills=["kept-skill"],
        skills_mode=SkillsMode.INCLUSIVE,
        tools=["web_search"],
        tools_mode="inclusive",
        mcp_servers=["srv"],
        mcp_mode="inclusive",
        plugins=["plugin-a"],
        plugins_mode="inclusive",
    )

    with (
        patch("openakita.core.agent.Agent", _CreateFakeAgent),
        patch.object(AgentFactory, "_apply_skill_filter", MagicMock()) as skill_filter,
        patch.object(AgentFactory, "_apply_tool_filter", MagicMock()) as tool_filter,
        patch.object(AgentFactory, "_apply_mcp_filter", MagicMock()) as mcp_filter,
        patch.object(AgentFactory, "_apply_plugin_filter", AsyncMock()) as plugin_filter,
    ):
        agent = await AgentFactory().create(profile)

    skill_filter.assert_not_called()
    tool_filter.assert_not_called()
    mcp_filter.assert_not_called()
    plugin_filter.assert_awaited_once_with(agent, profile)
    assert agent.initialized_with == {"start_scheduler": False, "lightweight": True}
    assert agent.configured_profile is profile
    assert agent.prompt_assembler._tool_catalog is agent.tool_catalog
    assert agent.prompt_assembler._mcp_catalog is agent.mcp_catalog


def test_inclusive_hides_non_selected_from_catalog():
    """INCLUSIVE mode: non-selected skills are catalog_hidden, not unregistered."""
    registry = _FakeRegistry([
        SimpleNamespace(skill_id="plugin-a@duplicate-skill", name="duplicate-skill", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="plugin-b@duplicate-skill", name="duplicate-skill", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="plugin-c@kept-skill", name="kept-skill", disabled=False, catalog_hidden=False),
    ])
    catalog = _FakeCatalog()
    agent = SimpleNamespace(
        skill_registry=registry,
        skill_catalog=catalog,
        _update_skill_tools=lambda: None,
    )
    profile = AgentProfile(
        id="worker",
        name="Worker",
        skills=["plugin-c@kept-skill"],
        skills_mode=SkillsMode.INCLUSIVE,
    )

    AgentFactory._apply_skill_filter(agent, profile)

    assert registry.unregistered == [], "INCLUSIVE should not unregister skills"
    assert sorted(registry.catalog_hidden) == [
        "plugin-a@duplicate-skill",
        "plugin-b@duplicate-skill",
    ]
    assert len(registry._skills) == 3, "All skills should remain in registry"
    assert catalog.invalidated == 1
    assert catalog.generated == 1


def test_inclusive_empty_skills_hides_all_non_essential():
    """INCLUSIVE with empty skills list: all non-essential skills are catalog_hidden."""
    registry = _FakeRegistry([
        SimpleNamespace(skill_id="list-skills", name="list-skills", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="my-external-skill", name="my-external-skill", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="another-skill", name="another-skill", disabled=False, catalog_hidden=False),
    ])
    catalog = _FakeCatalog()
    agent = SimpleNamespace(
        skill_registry=registry,
        skill_catalog=catalog,
        _update_skill_tools=lambda: None,
    )
    profile = AgentProfile(
        id="content-creator",
        name="自媒体达人",
        skills=[],
        skills_mode=SkillsMode.INCLUSIVE,
    )

    AgentFactory._apply_skill_filter(agent, profile)

    assert registry.unregistered == [], "INCLUSIVE should not unregister skills"
    assert sorted(registry.catalog_hidden) == [
        "another-skill",
        "my-external-skill",
    ]
    assert len(registry._skills) == 3, "All skills should remain in registry"
    assert catalog.invalidated == 1
    assert catalog.generated == 1


def test_exclusive_unregisters_blacklisted_skills():
    """EXCLUSIVE mode: blacklisted skills are fully unregistered."""
    registry = _FakeRegistry([
        SimpleNamespace(skill_id="skill-a", name="skill-a", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="skill-b", name="skill-b", disabled=False, catalog_hidden=False),
        SimpleNamespace(skill_id="skill-c", name="skill-c", disabled=False, catalog_hidden=False),
    ])
    catalog = _FakeCatalog()
    agent = SimpleNamespace(
        skill_registry=registry,
        skill_catalog=catalog,
        _update_skill_tools=lambda: None,
    )
    profile = AgentProfile(
        id="worker",
        name="Worker",
        skills=["skill-b"],
        skills_mode=SkillsMode.EXCLUSIVE,
    )

    AgentFactory._apply_skill_filter(agent, profile)

    assert registry.unregistered == ["skill-b"]
    assert registry.catalog_hidden == [], "EXCLUSIVE should not use catalog_hidden"
    assert len(registry._skills) == 2
    assert catalog.invalidated == 1
    assert catalog.generated == 1
