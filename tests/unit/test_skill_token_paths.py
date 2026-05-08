from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openakita.core.prompt_assembler import PromptAssembler
from openakita.prompt.builder import _build_catalogs_section
from openakita.skills.catalog import SKILL_INSTRUCTION_ADVISORY, SkillCatalog
from openakita.skills.registry import SkillEntry
from openakita.tools.handlers.skills import SkillsHandler


class _FakeSkill:
    def __init__(
        self,
        name: str,
        description: str,
        *,
        category: str = "general",
        system: bool = False,
        disabled: bool = False,
        catalog_hidden: bool = False,
        skill_path: str | None = None,
        when_to_use: str = "",
    ) -> None:
        self.skill_id = name
        self.name = name
        self.name_i18n = {}
        self.description = description
        self.when_to_use = when_to_use
        self.category = category
        self.system = system
        self.disabled = disabled
        self.catalog_hidden = catalog_hidden
        self.disable_model_invocation = False
        self.exposure_level = "recommended"
        self.skill_path = skill_path
        self.tool_name = name.replace("-", "_") if system else None
        self.handler = "test" if system else None
        self.plugin_source = None


def _make_catalog(skills: list[_FakeSkill]) -> SkillCatalog:
    registry = MagicMock()
    registry.count_catalog_hidden.return_value = 0
    catalog = SkillCatalog(registry=registry)
    catalog._list_model_visible = lambda exposure_filter=None: skills
    return catalog


def test_generate_catalog_legacy_path_is_index_only():
    long_trigger = "Use this when " + ("very detailed trigger text " * 80)
    catalog = _make_catalog([
        _FakeSkill("long-skill", long_trigger, category="writing", when_to_use=long_trigger)
    ])

    output = catalog.generate_catalog()

    assert "## Skills Index" in output
    assert "long-skill" in output
    assert "very detailed trigger text very detailed trigger text" not in output


def test_catalog_scope_index_uses_skill_index_without_grouped_expansion():
    class _IndexOnlyCatalog:
        def get_index_catalog(self, *, exposure_filter=None):
            return "## Skills Index\n\n**External skills (1)**: compact-only"

        def get_grouped_compact_catalog(self, **kwargs):
            raise AssertionError("index-only prompt must not expand grouped skills")

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=_IndexOnlyCatalog(),
        mcp_catalog=None,
        catalog_scope={"index"},
    )

    assert "compact-only" in output
    assert "技能使用规则" in output


def test_skill_catalog_marks_instructions_as_guidance():
    catalog = _make_catalog([
        _FakeSkill("brainstorming", "Must ask one question first", category="workflow")
    ])

    grouped = catalog.get_grouped_compact_catalog()
    index = catalog.get_index_catalog()

    assert SKILL_INSTRUCTION_ADVISORY in grouped
    assert SKILL_INSTRUCTION_ADVISORY in index


def test_skill_guidance_advisory_is_not_duplicated_in_prompt_rules():
    catalog = _make_catalog([
        _FakeSkill("brainstorming", "Must ask one question first", category="workflow")
    ])

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=catalog,
        mcp_catalog=None,
    )

    assert output.count(SKILL_INSTRUCTION_ADVISORY) == 1


@pytest.mark.asyncio
async def test_prompt_assembler_passes_intent_tool_hints(monkeypatch):
    captured = {}

    def fake_build_system_prompt(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("openakita.prompt.builder.build_system_prompt", fake_build_system_prompt)
    assembler = PromptAssembler(
        tool_catalog=None,
        skill_catalog=None,
        mcp_catalog=None,
        memory_manager=None,
        profile_manager=None,
        brain=None,
    )

    result = await assembler.build_system_prompt_compiled(
        task_description="read a file",
        intent_tool_hints=["File System"],
    )

    assert result == "ok"
    assert captured["intent_tool_hints"] == ["File System"]


def test_list_skills_defaults_to_compact_directory(tmp_path: Path):
    long_description = "A skill with " + ("long trigger description " * 120)
    skill_path = tmp_path / "long-skill" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("---\nname: long-skill\n---\n", encoding="utf-8")
    skills = [
        _FakeSkill(
            "read-file",
            long_description,
            system=True,
            category="Skills",
            skill_path=str(skill_path),
        ),
        _FakeSkill("external-long", long_description, category="writing"),
    ]
    registry = MagicMock()
    registry.list_all.return_value = skills
    handler = SkillsHandler(SimpleNamespace(skill_registry=registry))

    compact = handler._list_skills({})
    verbose = handler._list_skills({"verbose": True, "include_paths": True})

    assert len(compact) < 1200
    assert "long trigger description long trigger description long trigger description" not in compact
    assert "path=" not in compact
    assert "long trigger description long trigger description long trigger description" in verbose
    assert "path=" in verbose


def test_get_skill_info_marks_external_skill_instructions_as_guidance(tmp_path: Path):
    skill_path = tmp_path / "brainstorming" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("---\nname: brainstorming\n---\n", encoding="utf-8")
    skill = SkillEntry(
        skill_id="brainstorming",
        name="brainstorming",
        description="Explore design before implementation.",
        skill_path=str(skill_path),
        _parsed_skill=SimpleNamespace(body="You MUST ask one question at a time."),
    )
    registry = MagicMock()
    registry.get.return_value = skill
    registry.list_all.return_value = [skill]
    handler = SkillsHandler(SimpleNamespace(skill_registry=registry))

    output = handler._get_skill_info({"skill_name": "brainstorming"})

    assert "**类型**: 外部技能" in output
    assert SKILL_INSTRUCTION_ADVISORY in output
    assert "You MUST ask one question at a time." in output
