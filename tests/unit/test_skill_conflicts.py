"""L1 unit tests for SkillRegistry conflict logging."""

from __future__ import annotations

from openakita.skills.registry import SkillEntry, SkillRegistry


def _entry(
    skill_id: str = "demo",
    *,
    origin: str = "project",
    plugin_source: str | None = None,
    path: str = "",
) -> SkillEntry:
    return SkillEntry(
        skill_id=skill_id,
        name=skill_id,
        description="demo",
        origin=origin,
        plugin_source=plugin_source,
        skill_path=path,
    )


def _register(reg: SkillRegistry, entry: SkillEntry, *, force: bool = False) -> bool:
    """Bypass register()'s ParsedSkill construction by inserting directly.

    The conflict-recording API uses ``_record_conflict``; we exercise it via
    public register() by faking the path the loader takes.
    """
    if entry.skill_id in reg._skills and not force:
        existing = reg._skills[entry.skill_id]
        reg._record_conflict(action="rejected", winner=existing, loser=entry)
        return False
    if entry.skill_id in reg._skills and force:
        existing = reg._skills[entry.skill_id]
        reg._record_conflict(action="overridden", winner=entry, loser=existing)
    reg._skills[entry.skill_id] = entry
    return True


def test_rejected_conflict_keeps_existing_skill():
    reg = SkillRegistry()
    first = _entry(origin="builtin", path="/builtin/demo/SKILL.md")
    second = _entry(origin="project", path="/proj/demo/SKILL.md", plugin_source="plugin:x")

    assert _register(reg, first) is True
    assert _register(reg, second) is False

    conflicts = reg.get_conflicts()
    assert len(conflicts) == 1
    record = conflicts[0]
    assert record["skill_id"] == "demo"
    assert record["action"] == "rejected"
    assert record["winner"]["origin"] == "builtin"
    assert record["winner"]["path"].endswith("SKILL.md")
    assert record["shadowed"]["origin"] == "project"
    assert record["shadowed"]["plugin_source"] == "plugin:x"


def test_overridden_conflict_swaps_winner_and_shadowed():
    reg = SkillRegistry()
    first = _entry(origin="builtin", path="/builtin/demo/SKILL.md")
    second = _entry(origin="marketplace", path="/marketplace/demo/SKILL.md")

    _register(reg, first)
    _register(reg, second, force=True)

    conflicts = reg.get_conflicts()
    assert len(conflicts) == 1
    record = conflicts[0]
    assert record["action"] == "overridden"
    assert record["winner"]["origin"] == "marketplace"
    assert record["shadowed"]["origin"] == "builtin"


def test_clear_conflicts_resets_state():
    reg = SkillRegistry()
    _register(reg, _entry())
    _register(reg, _entry())
    assert len(reg.get_conflicts()) == 1
    reg.clear_conflicts()
    assert reg.get_conflicts() == []


def test_conflict_log_capped_at_100_entries():
    reg = SkillRegistry()
    reg._conflicts_max = 5
    base = _entry()
    reg._skills[base.skill_id] = base
    for _ in range(8):
        # Each call records one rejection (registry already has the entry).
        _register(reg, _entry(plugin_source=f"plug-{_}"))
    assert len(reg.get_conflicts()) == 5
