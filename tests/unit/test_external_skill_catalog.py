from __future__ import annotations

import json
import tomllib
from collections import Counter
from pathlib import Path

from openakita.agents.presets import SYSTEM_PRESETS
from openakita.skills.category_store import CategoryStore
from openakita.skills.loader import DEFAULT_DISABLED_SKILLS, SkillLoader
from openakita.skills.parser import SkillParser

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "skills"
CATALOG_PATH = SKILLS_ROOT / "catalog.json"


def _catalog_skill_ids() -> list[str]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [skill_id for category in data["categories"] for skill_id in category["skills"]]


def test_catalog_covers_each_top_level_external_skill_once() -> None:
    actual = {
        path.name
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    catalog_ids = _catalog_skill_ids()
    counts = Counter(catalog_ids)

    assert set(catalog_ids) == actual
    assert {skill_id for skill_id, count in counts.items() if count > 1} == set()


def test_packaged_external_skills_are_curated_and_categorized() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    packaged_sources = {
        source
        for source, destination in force_include.items()
        if "/builtin_skills/external/" in destination
    }
    catalog_ids = set(_catalog_skill_ids())

    assert packaged_sources
    for source in packaged_sources:
        source_path = ROOT / source
        assert (source_path / "SKILL.md").is_file()
        assert source_path.name in catalog_ids
        assert source_path.name in DEFAULT_DISABLED_SKILLS


def test_default_disabled_ids_use_existing_registry_ids() -> None:
    actual = {
        path.name
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }

    assert actual >= DEFAULT_DISABLED_SKILLS
    assert all("@" not in skill_id and "/" not in skill_id for skill_id in DEFAULT_DISABLED_SKILLS)


def test_system_presets_do_not_reference_removed_external_skills() -> None:
    parser = SkillParser()
    available_names = {
        parser.parse_file(path / "SKILL.md").metadata.name
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    external_references = {
        skill
        for preset in SYSTEM_PRESETS
        for skill in preset.skills
        if skill.startswith(("openakita/skills@", "obra/superpowers@"))
    }

    assert external_references <= available_names


def test_external_skill_registry_ids_pass_directory_validation() -> None:
    parser = SkillParser()
    directory_warnings: dict[str, list[str]] = {}

    for skill_dir in SKILLS_ROOT.iterdir():
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            continue
        skill = parser.parse_directory(skill_dir)
        warnings = [warning for warning in parser.validate(skill) if "Directory name" in warning]
        if warnings:
            directory_warnings[skill_dir.name] = warnings

    assert directory_warnings == {}


def test_catalog_defaults_apply_but_user_binding_wins(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill\ncategory: Frontmatter\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    (skills_root / "catalog.json").write_text(
        json.dumps(
            {
                "categories": [
                    {"name": "Default", "description": "Default category", "skills": ["demo-skill"]}
                ]
            }
        ),
        encoding="utf-8",
    )

    user_store = CategoryStore(tmp_path / "user-categories.json")
    category_registry_loader = SkillLoader()
    category_registry_loader.category_registry.set_store(user_store)
    category_registry_loader.load_from_directory(skills_root)
    assert category_registry_loader.get_skill("demo-skill").metadata.category == "Default"

    user_store.create_category("Custom")
    user_store.bind_skill("demo-skill", "Custom")
    category_registry_loader.category_registry.clear()
    category_registry_loader.category_registry.load_from_store()
    category_registry_loader.load_from_directory(skills_root)
    assert category_registry_loader.get_skill("demo-skill").metadata.category == "Custom"


def test_namespaced_preset_reference_keeps_registry_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: openakita/skills@demo-skill\ndescription: Demo skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    loader = SkillLoader()
    loader.load_from_directory(skills_root)

    loader.prune_external_by_allowlist(
        set(), agent_referenced_skills={"openakita/skills@demo-skill"}
    )

    assert loader.get_skill("demo-skill") is not None
    assert loader.registry.get("demo-skill").disabled is True
