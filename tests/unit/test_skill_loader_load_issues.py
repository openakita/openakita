from __future__ import annotations

from pathlib import Path

from openakita.skills.loader import SkillLoader


def _write_skill(path: Path, content: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(content, encoding="utf-8")


def test_invalid_skill_is_reported_without_aborting_full_scan(tmp_path, monkeypatch):
    monkeypatch.setattr("openakita.skills.loader.SKILL_DIRECTORIES", ["skills"])

    _write_skill(
        tmp_path / "skills" / "good-skill",
        "---\nname: good-skill\ndescription: usable\n---\nUse this skill.",
    )
    _write_skill(
        tmp_path / "skills" / "bad-skill",
        "---\nname: Bad Skill\ndescription: invalid name\n---\nBroken skill.",
    )

    loader = SkillLoader()

    assert loader.load_all(tmp_path) == 1
    assert loader.get_skill("good-skill") is not None

    issues = loader.last_load_issues
    assert len(issues) == 1
    assert issues[0]["skill_id"] == "bad-skill"
    assert "name must be lowercase" in issues[0]["error"]
