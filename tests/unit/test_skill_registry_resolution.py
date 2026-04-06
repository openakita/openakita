from openakita.skills.registry import SkillEntry, SkillRegistry


def _make_entry(skill_id: str, name: str) -> SkillEntry:
    return SkillEntry(
        skill_id=skill_id,
        name=name,
        description=f"desc:{skill_id}",
        capability_id=f"project/skill:{skill_id}",
        namespace="project:default",
        origin="project",
    )


def test_registry_refuses_ambiguous_name_resolution():
    registry = SkillRegistry()
    registry._skills["skill-a"] = _make_entry("skill-a", "same-name")
    registry._skills["skill-b"] = _make_entry("skill-b", "same-name")

    assert registry.get("same-name") is None
    assert registry.unregister("same-name") is False


def test_skill_metadata_exposes_capability_fields():
    registry = SkillRegistry()
    registry._skills["skill-a"] = _make_entry("skill-a", "unique-name")

    metadata = registry.list_metadata()
    assert metadata[0]["capability_id"] == "project/skill:skill-a"
    assert metadata[0]["namespace"] == "project:default"
    assert metadata[0]["origin"] == "project"
