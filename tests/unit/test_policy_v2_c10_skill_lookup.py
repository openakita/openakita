"""C10: SKILL.md ``approval_class`` 解析 + SkillRegistry → ApprovalClass lookup.

测试维度（与 docs §3.2 R2-12 + §4.21.4 对齐）：

- D1：parser 接受 ``approval_class:`` canonical 字段
- D2：parser 接受 ``risk_class:`` 作为 alias（带 deprecation WARN）
- D3：非法值降级为 None（不阻塞 SKILL.md 解析）
- D4：``approval_class`` + ``risk_class`` 同时声明且不一致 → 用 canonical + WARN
- D5：``SkillRegistry.get_tool_class`` 反查系统技能（``tool_name``）
- D6：``SkillRegistry.get_tool_class`` 反查外部技能（``skill_<safe-id>``）
- D7：``SkillRegistry.get_tool_class`` 未声明 ``approval_class`` 时返回 None
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openakita.core.policy_v2.enums import ApprovalClass, DecisionSource
from openakita.skills.parser import SkillParser
from openakita.skills.registry import SkillRegistry


def _write_skill(tmp: Path, frontmatter: str) -> Path:
    skill_dir = tmp / "fixture-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\n{frontmatter}\n---\nbody\n", encoding="utf-8")
    return skill_md


@pytest.fixture
def tmp_skills_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestApprovalClassParsing:
    def test_canonical_field_parsed(self, tmp_skills_dir):
        path = _write_skill(
            tmp_skills_dir,
            "name: test-skill\ndescription: A skill\napproval_class: mutating_scoped",
        )
        parsed = SkillParser().parse_file(path)
        assert parsed.metadata.approval_class == "mutating_scoped"

    def test_risk_class_alias_warns_but_parses(self, tmp_skills_dir, caplog):
        path = _write_skill(
            tmp_skills_dir,
            "name: alias-skill\ndescription: alias\nrisk_class: destructive",
        )
        with caplog.at_level("WARNING", logger="openakita.skills.parser"):
            parsed = SkillParser().parse_file(path)
        assert parsed.metadata.approval_class == "destructive"
        assert any("deprecated 'risk_class'" in rec.message for rec in caplog.records)

    def test_invalid_value_returns_none_with_warn(self, tmp_skills_dir, caplog):
        path = _write_skill(
            tmp_skills_dir,
            "name: bad-skill\ndescription: bad\napproval_class: not_a_class",
        )
        with caplog.at_level("WARNING", logger="openakita.skills.parser"):
            parsed = SkillParser().parse_file(path)
        assert parsed.metadata.approval_class is None
        assert any("unknown approval_class" in rec.message for rec in caplog.records)

    def test_both_fields_disagree_warns_and_uses_canonical(
        self, tmp_skills_dir, caplog
    ):
        path = _write_skill(
            tmp_skills_dir,
            "name: dual-skill\ndescription: dual\n"
            "approval_class: readonly_scoped\nrisk_class: destructive",
        )
        with caplog.at_level("WARNING", logger="openakita.skills.parser"):
            parsed = SkillParser().parse_file(path)
        assert parsed.metadata.approval_class == "readonly_scoped"
        assert any(
            "declares both" in rec.message and "using approval_class" in rec.message
            for rec in caplog.records
        )

    def test_missing_field_is_none_no_warn(self, tmp_skills_dir, caplog):
        path = _write_skill(
            tmp_skills_dir, "name: plain-skill\ndescription: plain"
        )
        with caplog.at_level("WARNING", logger="openakita.skills.parser"):
            parsed = SkillParser().parse_file(path)
        assert parsed.metadata.approval_class is None
        # Plain SKILL.md without approval_class must not produce a WARN
        # otherwise the 200+ existing skills would spam logs.
        assert not any(
            "approval_class" in rec.message or "risk_class" in rec.message
            for rec in caplog.records
        )


class TestSkillRegistryGetToolClass:
    def test_system_skill_lookup(self, tmp_skills_dir):
        path = _write_skill(
            tmp_skills_dir,
            "name: write-file\ndescription: write a file\n"
            "system: true\ntool-name: write_file\napproval_class: mutating_scoped",
        )
        parsed = SkillParser().parse_file(path)
        reg = SkillRegistry()
        reg.register(parsed, skill_id="write-file")
        result = reg.get_tool_class("write_file")
        assert result == (ApprovalClass.MUTATING_SCOPED, DecisionSource.SKILL_METADATA)

    def test_external_skill_lookup_via_skill_prefix(self, tmp_skills_dir):
        path = _write_skill(
            tmp_skills_dir,
            "name: my-translate\ndescription: translate text\n"
            "approval_class: network_out",
        )
        parsed = SkillParser().parse_file(path)
        reg = SkillRegistry()
        reg.register(parsed, skill_id="my-translate")
        result = reg.get_tool_class("skill_my_translate")
        assert result == (ApprovalClass.NETWORK_OUT, DecisionSource.SKILL_METADATA)

    def test_skill_without_approval_class_returns_none(self, tmp_skills_dir):
        path = _write_skill(tmp_skills_dir, "name: plain\ndescription: plain")
        parsed = SkillParser().parse_file(path)
        reg = SkillRegistry()
        reg.register(parsed, skill_id="plain")
        assert reg.get_tool_class("skill_plain") is None

    def test_unknown_tool_returns_none(self):
        reg = SkillRegistry()
        assert reg.get_tool_class("nonexistent_tool") is None
