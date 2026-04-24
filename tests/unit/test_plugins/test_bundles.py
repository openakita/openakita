"""Tests for openakita.plugins.bundles — BundleMapper ecosystem detection."""

from __future__ import annotations

import json

import pytest

from openakita.plugins.bundles import BundleInfo, BundleMapper


@pytest.fixture()
def mapper():
    return BundleMapper()


# ---------- OpenClaw detection ----------


class TestDetectOpenClaw:
    def test_openclaw_manifest(self, tmp_path, mapper):
        manifest = tmp_path / "openclaw.plugin.json"
        manifest.write_text(
            json.dumps({"name": "test", "version": "1.0.0"}), encoding="utf-8"
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "openclaw"
        assert result.path == tmp_path

    def test_openclaw_package_json_with_keyword(self, tmp_path, mapper):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps({"name": "test", "keywords": ["openclaw.extensions"]}),
            encoding="utf-8",
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "openclaw"

    def test_openclaw_package_json_with_key(self, tmp_path, mapper):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps({"name": "test", "openclaw": {"config": True}}),
            encoding="utf-8",
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "openclaw"


# ---------- Claude detection ----------


class TestDetectClaude:
    def test_claude_plugin_dir(self, tmp_path, mapper):
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "claude-ext"}), encoding="utf-8"
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "claude"
        assert result.manifest["name"] == "claude-ext"

    def test_claude_skills_dir(self, tmp_path, mapper):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "claude"
        assert any(p.name == "SKILL.md" for p in result.skills)


# ---------- Cursor detection ----------


class TestDetectCursor:
    def test_cursor_dir_with_skills(self, tmp_path, mapper):
        cursor_dir = tmp_path / ".cursor"
        skills_dir = cursor_dir / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "cursor"
        assert any(p.name == "SKILL.md" for p in result.skills)

    def test_cursor_dir_with_rules(self, tmp_path, mapper):
        cursor_dir = tmp_path / ".cursor"
        rules_dir = cursor_dir / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "my-rule.mdc").write_text("rule content", encoding="utf-8")
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "cursor"
        assert any(p.suffix == ".mdc" for p in result.skills)

    def test_cursor_plugin_dir(self, tmp_path, mapper):
        plugin_dir = tmp_path / ".cursor-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "cursor-ext"}), encoding="utf-8"
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "cursor"


# ---------- Codex detection ----------


class TestDetectCodex:
    def test_codex_dir_with_skills(self, tmp_path, mapper):
        codex_dir = tmp_path / ".codex"
        skills_dir = codex_dir / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "codex"
        assert any(p.name == "SKILL.md" for p in result.skills)

    def test_codex_plugin_dir(self, tmp_path, mapper):
        plugin_dir = tmp_path / ".codex-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "codex-ext"}), encoding="utf-8"
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert result.ecosystem == "codex"


# ---------- Unknown ----------


class TestDetectUnknown:
    def test_empty_dir_returns_none(self, tmp_path, mapper):
        result = mapper.detect(tmp_path)
        assert result is None

    def test_non_dir_returns_none(self, tmp_path, mapper):
        f = tmp_path / "file.txt"
        f.write_text("not a dir", encoding="utf-8")
        result = mapper.detect(f)
        assert result is None


# ---------- map_to_manifest ----------


class TestMapToManifest:
    def test_generates_valid_manifest(self, mapper):
        from pathlib import Path

        bundle = BundleInfo(
            ecosystem="openclaw",
            path=Path("/fake/my-plugin"),
            manifest={"version": "2.0.0", "description": "A plugin"},
        )
        result = mapper.map_to_manifest(bundle)
        assert result["id"] == "bundle-openclaw-my-plugin"
        assert result["version"] == "2.0.0"
        assert result["type"] == "skill"
        assert "openclaw" in result["tags"]
        assert "bundle" in result["tags"]

    def test_skills_set_skill_type(self, mapper):
        from pathlib import Path

        bundle = BundleInfo(
            ecosystem="claude",
            path=Path("/fake/my-plugin"),
            skills=[Path("/fake/SKILL.md")],
        )
        result = mapper.map_to_manifest(bundle)
        assert result["type"] == "skill"
        assert result["entry"] == "SKILL.md"

    def test_mcp_overrides_skill(self, mapper):
        from pathlib import Path

        bundle = BundleInfo(
            ecosystem="cursor",
            path=Path("/fake/my-plugin"),
            skills=[Path("/fake/SKILL.md")],
            mcp_configs=[Path("/fake/mcp_config.json")],
        )
        result = mapper.map_to_manifest(bundle)
        assert result["type"] == "mcp"
        assert result["entry"] == "mcp_config.json"


# ---------- Skills recursive discovery ----------


class TestSkillsRecursive:
    def test_skills_discovered_recursively(self, tmp_path, mapper):
        skills_dir = tmp_path / "skills" / "sub1" / "sub2"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# Deep Skill", encoding="utf-8")
        (tmp_path / "openclaw.plugin.json").write_text(
            json.dumps({"name": "test"}), encoding="utf-8"
        )
        result = mapper.detect(tmp_path)
        assert result is not None
        assert any("sub2" in str(p) for p in result.skills)

