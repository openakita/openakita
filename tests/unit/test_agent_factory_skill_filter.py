from types import SimpleNamespace

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
