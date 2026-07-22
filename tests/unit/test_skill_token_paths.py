from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openakita.core.prompt_assembler import PromptAssembler
from openakita.prompt.builder import PromptProfile, _build_catalogs_section
from openakita.skills.catalog import (
    SKILL_INSTRUCTION_ADVISORY,
    SKILL_METADATA_ADVISORY,
    SkillCatalog,
)
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
    catalog = _make_catalog(
        [_FakeSkill("long-skill", long_trigger, category="writing", when_to_use=long_trigger)]
    )

    output = catalog.generate_catalog()

    assert "## Skills Index" in output
    assert "long-skill" in output
    assert "very detailed trigger text very detailed trigger text" not in output


def test_catalog_scope_index_uses_skill_index_without_grouped_expansion():
    class _MetadataOnlyCatalog:
        def get_metadata_catalog(self, **kwargs):
            return "## Available Skills\n\n- compact-only: compact metadata"

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=_MetadataOnlyCatalog(),
        mcp_catalog=None,
        catalog_scope={"index"},
    )

    assert "compact-only" in output
    assert "技能使用规则" not in output


def test_index_catalog_does_not_repeat_full_tools_guide():
    class _MetadataOnlyCatalog:
        def get_metadata_catalog(self, **kwargs):
            return "## Available Skills\n\n- compact-only: compact metadata"

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=_MetadataOnlyCatalog(),
        mcp_catalog=None,
        include_tools_guide=True,
        catalog_scope={"index"},
    )

    assert "compact-only" in output
    assert "## 工具体系" not in output


def test_metadata_catalog_exposes_description_and_source_without_full_instructions():
    catalog = _make_catalog(
        [
            _FakeSkill(
                "document-review",
                "Review Word documents and preserve layout.",
                category="documents",
                skill_path="C:/skills/document-review/SKILL.md",
                when_to_use="MANDATORY internal workflow that must not be injected up front",
            )
        ]
    )

    output = catalog.get_metadata_catalog(context_window=32_000)

    assert "document-review" in output
    assert "Review Word documents and preserve layout." in output
    assert "skill://document-review" in output
    assert "MANDATORY internal workflow" not in output
    assert "get_skill_info(skill_name)" in output


def test_metadata_catalog_is_bounded_to_two_percent_of_context_window():
    skills = [
        _FakeSkill(
            f"skill-{index:03d}",
            "Detailed discovery description " * 30,
            category="external",
            skill_path=f"C:/skills/skill-{index:03d}/SKILL.md",
        )
        for index in range(80)
    ]
    catalog = _make_catalog(skills)

    output = catalog.get_metadata_catalog(context_window=8_000)

    assert SkillCatalog._estimate_metadata_tokens(output) <= 160
    assert "additional skill(s) were omitted" in output
    assert "skill-000" in output
    assert "skill-079" not in output


def test_metadata_catalog_has_absolute_ceiling_for_large_context_windows():
    skills = [
        _FakeSkill(
            f"skill-{index:03d}",
            "Detailed discovery description " * 20,
            category="external",
            skill_path=f"C:/skills/skill-{index:03d}/SKILL.md",
        )
        for index in range(100)
    ]
    catalog = _make_catalog(skills)

    output = catalog.get_metadata_catalog(context_window=200_000)

    assert SkillCatalog._estimate_metadata_tokens(output) <= 1_000
    assert "additional skill(s) were omitted" in output


def test_metadata_catalog_shortens_descriptions_before_omitting_skills():
    skills = [
        _FakeSkill(
            f"skill-{index}",
            "long description segment " * 50,
            category="external",
            skill_path=f"C:/skills/skill-{index}/SKILL.md",
        )
        for index in range(3)
    ]
    catalog = _make_catalog(skills)

    output = catalog.get_metadata_catalog(context_window=32_000)

    assert all(f"skill-{index}" in output for index in range(3))
    assert "additional skill(s) were omitted" not in output
    assert "long description segment " * 50 not in output
    assert "..." in output


def test_catalog_builder_passes_context_window_to_stable_skill_metadata_catalog():
    captured = {}

    class _CapturingCatalog:
        def get_metadata_catalog(self, **kwargs):
            captured.update(kwargs)
            return "## Available Skills\n\n- compact"

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=_CapturingCatalog(),
        mcp_catalog=None,
        context_window=65_536,
        prompt_profile=PromptProfile.CONSUMER_CHAT,
        intent_tool_hints=["File System"],
    )

    assert "compact" in output
    assert captured["context_window"] == 65_536
    assert captured["max_tokens"] == 600
    assert captured["priority_categories"] == ("file-tools", "filesystem", "file")


def test_catalog_builder_uses_progressive_tool_catalog_with_intent_hints():
    captured = {}

    class _CapturingToolCatalog:
        def get_progressive_catalog(self, **kwargs):
            captured.update(kwargs)
            return "## Available System Tools (index)\n**File System**: read_file"

        def get_catalog(self):
            raise AssertionError("full tool catalog must not be loaded into the prompt")

    output = _build_catalogs_section(
        tool_catalog=_CapturingToolCatalog(),
        skill_catalog=None,
        mcp_catalog=None,
        intent_tool_hints=["File System"],
    )

    assert "read_file" in output
    assert captured["expand_categories"] == ["File System"]


def test_skill_catalog_marks_instructions_as_guidance():
    catalog = _make_catalog(
        [_FakeSkill("brainstorming", "Must ask one question first", category="workflow")]
    )

    grouped = catalog.get_grouped_compact_catalog()
    index = catalog.get_index_catalog()

    assert SKILL_INSTRUCTION_ADVISORY in grouped
    assert SKILL_INSTRUCTION_ADVISORY in index


def test_skill_guidance_advisory_is_not_duplicated_in_prompt_rules():
    catalog = _make_catalog(
        [_FakeSkill("brainstorming", "Must ask one question first", category="workflow")]
    )

    output = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=catalog,
        mcp_catalog=None,
    )

    assert output.count(SKILL_METADATA_ADVISORY) == 1
    assert SKILL_INSTRUCTION_ADVISORY not in output


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


@pytest.mark.asyncio
async def test_prompt_assembler_uses_explicit_identity_dir(monkeypatch, tmp_path: Path):
    captured = {}
    identity_dir = tmp_path / "profile-identity"

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

    result = await assembler.build_system_prompt_compiled(identity_dir=identity_dir)

    assert result == "ok"
    assert captured["identity_dir"] == identity_dir


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
    assert (
        "long trigger description long trigger description long trigger description" not in compact
    )
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
