import sys
import types
from pathlib import Path
from types import SimpleNamespace

from openakita.config import settings
from openakita.skills.catalog import SkillCatalog
from openakita.skills.exposure import build_skill_exposure, get_skill_source_roots
from openakita.skills.registry import SkillEntry, SkillRegistry

if "openakita.core" not in sys.modules:
    core_pkg = types.ModuleType("openakita.core")
    core_pkg.__path__ = [str(Path(__file__).resolve().parents[2] / "src" / "openakita" / "core")]
    sys.modules["openakita.core"] = core_pkg

from openakita.tools.handlers.skills import SkillsHandler
from openakita.tools.handlers.system import SystemHandler


def _make_entry(
    *,
    skill_id: str,
    name: str,
    description: str,
    skill_path: Path,
    system: bool = False,
    disabled: bool = False,
    tool_name: str | None = None,
    handler: str | None = None,
    body: str = "Follow the skill instructions.",
) -> SkillEntry:
    return SkillEntry(
        skill_id=skill_id,
        name=name,
        description=description,
        system=system,
        disabled=disabled,
        tool_name=tool_name,
        handler=handler,
        skill_path=str(skill_path),
        _parsed_skill=SimpleNamespace(body=body),
    )


class TestSkillExposure:
    def test_build_skill_exposure_classifies_origin_and_assets(self, tmp_path):
        project_root = tmp_path / "project"
        user_skills_dir = tmp_path / "workspace-skills"
        builtin_root = tmp_path / "builtin-skills"

        skill_dir = user_skills_dir / "debug-helper"
        references_dir = skill_dir / "references"
        scripts_dir = skill_dir / "scripts"
        references_dir.mkdir(parents=True)
        scripts_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Debug Helper", encoding="utf-8")
        (scripts_dir / "fix.py").write_text("print('ok')", encoding="utf-8")
        (references_dir / "REFERENCE.md").write_text("# Ref", encoding="utf-8")

        entry = _make_entry(
            skill_id="debug-helper",
            name="debug-helper",
            description="Debug helper",
            skill_path=skill_dir / "SKILL.md",
        )

        exposed = build_skill_exposure(
            entry,
            project_root=project_root,
            user_skills_dir=user_skills_dir,
            builtin_root=builtin_root,
        )

        assert exposed.origin == "user_workspace"
        assert exposed.origin_label == "user-workspace"
        assert exposed.skill_dir == str(skill_dir.resolve())
        assert exposed.root_dir == str(user_skills_dir.resolve())
        assert exposed.scripts == ("scripts/fix.py",)
        assert exposed.references == ("REFERENCE.md",)
        assert exposed.instruction_only is False

    def test_get_skill_source_roots_returns_builtin_workspace_and_project(self, tmp_path):
        project_root = tmp_path / "project"
        user_skills_dir = tmp_path / "workspace-skills"
        builtin_root = tmp_path / "builtin-skills"

        roots = get_skill_source_roots(
            project_root=project_root,
            user_skills_dir=user_skills_dir,
            builtin_root=builtin_root,
        )

        assert roots == [
            ("builtin", builtin_root.resolve()),
            ("user_workspace", user_skills_dir.resolve()),
            ("project", (project_root / "skills").resolve()),
        ]


class TestSkillCatalog:
    def test_catalog_warns_against_path_guessing(self, tmp_path):
        registry = SkillRegistry()
        skill_file = tmp_path / "skills" / "writer" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Writer", encoding="utf-8")
        registry._skills["writer"] = _make_entry(
            skill_id="writer",
            name="writer",
            description="Write documents",
            skill_path=skill_file,
        )

        text = SkillCatalog(registry).generate_catalog()

        assert "builtin, user workspace, or project directories" in text
        assert "Do not infer filesystem paths from the workspace map" in text


class TestSkillHandlers:
    def test_get_skill_info_returns_authoritative_origin_and_path(self, tmp_path, monkeypatch):
        project_root = tmp_path / "project"
        project_skill_dir = project_root / "skills" / "writer"
        openakita_home = tmp_path / "home"
        project_skill_dir.mkdir(parents=True)
        skill_file = project_skill_dir / "SKILL.md"
        skill_file.write_text("# Writer", encoding="utf-8")
        (project_skill_dir / "guide.md").write_text("Detailed guide", encoding="utf-8")

        registry = SkillRegistry()
        registry._skills["writer"] = _make_entry(
            skill_id="writer",
            name="writer",
            description="Write documents",
            skill_path=skill_file,
            body="Read [`guide.md`](guide.md) before acting.",
        )

        monkeypatch.setattr(settings, "project_root", project_root)
        monkeypatch.setenv("OPENAKITA_ROOT", str(openakita_home))

        handler = SkillsHandler(SimpleNamespace(skill_registry=registry))
        output = handler._get_skill_info({"skill_name": "writer"})

        assert "**来源**: project" in output
        assert f"**路径**: {project_skill_dir.resolve()}" in output
        assert "**路径规则**" in output
        assert "# [Inlined Reference] guide.md" in output

    def test_get_workspace_map_lists_multi_source_skill_roots(self, tmp_path, monkeypatch):
        project_root = tmp_path / "project"
        openakita_home = tmp_path / "home"
        user_skills_dir = openakita_home / "workspaces" / "default" / "skills"

        monkeypatch.setattr(settings, "project_root", project_root)
        monkeypatch.setenv("OPENAKITA_ROOT", str(openakita_home))
        monkeypatch.setattr(settings, "log_dir", "logs")
        monkeypatch.setattr(settings, "log_file_prefix", "openakita")
        monkeypatch.setattr(
            "openakita.tools.handlers.system.get_skill_source_roots",
            lambda **_: [
                ("builtin", tmp_path / "builtin-skills"),
                ("user_workspace", user_skills_dir),
                ("project", project_root / "skills"),
            ],
        )

        handler = SystemHandler(SimpleNamespace())
        output = handler._get_workspace_map()

        assert "技能系统是多源的" in output
        assert "不要根据 workspace map 猜测 skill 文件路径" in output
        assert f"  - builtin: {tmp_path / 'builtin-skills'}" in output
        assert f"  - user_workspace: {user_skills_dir}" in output
        assert f"  - project: {project_root / 'skills'}" in output
