"""Utilities for querying preset agent skill references.

Extracted from agent.py to break the circular dependency between
skill_manager and agent.
"""


def collect_preset_referenced_skills() -> set[str]:
    """Collect all skill names referenced by system preset agents."""
    try:
        from openakita.agents.presets import SYSTEM_PRESETS

        skills: set[str] = set()
        for preset in SYSTEM_PRESETS:
            skills.update(preset.skills)
        return skills
    except Exception:
        return set()
