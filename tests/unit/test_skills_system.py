"""L1 Unit Tests: Skill registry, loader, and parser."""

import logging

from openakita.skills.loader import SkillLoader
from openakita.skills.parser import SkillParser
from openakita.skills.registry import SkillEntry, SkillRegistry


class TestSkillRegistry:
    def test_empty_registry(self):
        reg = SkillRegistry()
        assert reg.count == 0
        assert reg.list_all() == []

    def test_has_nonexistent(self):
        reg = SkillRegistry()
        assert reg.has("nonexistent") is False
        assert reg.get("nonexistent") is None

    def test_search_empty(self):
        reg = SkillRegistry()
        results = reg.search("anything")
        assert results == []

    def test_count_properties(self):
        reg = SkillRegistry()
        assert reg.system_count == 0
        assert reg.external_count == 0


class TestSkillEntry:
    def test_create_entry(self):
        entry = SkillEntry(
            skill_id="test-skill",
            name="test-skill",
            description="A test skill",
            system=False,
        )
        assert entry.name == "test-skill"
        assert entry.system is False

    def test_to_tool_schema(self):
        entry = SkillEntry(
            skill_id="search-tool",
            name="search-tool",
            description="Search the web",
            tool_name="web_search",
        )
        schema = entry.to_tool_schema()
        assert isinstance(schema, dict)


class TestSkillLoader:
    def test_loader_creation(self):
        loader = SkillLoader()
        assert loader.loaded_count == 0

    def test_discover_empty_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        loader = SkillLoader()
        dirs = loader.discover_skill_directories(skills_dir)
        assert isinstance(dirs, list)

    def test_load_skill_from_dir(self, tmp_path):
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test\n---\n# Test Skill\nDoes stuff.",
            encoding="utf-8",
        )
        loader = SkillLoader()
        result = loader.load_skill(skill_dir)
        assert result is not None
        assert result.metadata.name == "test-skill"
        assert result.body_loaded is False

        assert loader.get_skill_body("test-skill") == "# Test Skill\nDoes stuff."
        assert result.body_loaded is True

    def test_load_all_from_empty(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        loader = SkillLoader()
        count = loader.load_all(skills_dir)
        assert isinstance(count, int)

    def test_run_script_adds_skill_dir_to_pythonpath(self, tmp_path):
        skill_dir = tmp_path / "skills" / "news-searcher"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: news-searcher\ndescription: News search\n---\n# News Search\nBody.",
            encoding="utf-8",
        )
        (skill_dir / "news_searcher.py").write_text(
            "def search():\n    return 'ok-from-skill-dir'\n",
            encoding="utf-8",
        )
        (scripts_dir / "main.py").write_text(
            "from news_searcher import search\nprint(search())\n",
            encoding="utf-8",
        )

        loader = SkillLoader()
        loader.load_skill(skill_dir)

        success, output = loader.run_script("news-searcher", "scripts/main.py")

        assert success is True
        assert "ok-from-skill-dir" in output


class TestSkillParser:
    def test_parse_skill_file(self, tmp_path):
        from openakita.skills.parser import parse_skill

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            "---\nname: parser-test\ndescription: Parse test\n---\n# Parser Test\nContent here.",
            encoding="utf-8",
        )
        result = parse_skill(skill_file)
        assert result.metadata.name == "parser-test"

    def test_parse_skill_directory(self, tmp_path):
        from openakita.skills.parser import parse_skill_directory

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: dir-skill\ndescription: Directory skill\n---\n# Skill\nBody.",
            encoding="utf-8",
        )
        result = parse_skill_directory(skill_dir)
        assert result.metadata.name == "dir-skill"

    def test_namespaced_skill_allows_independent_registry_directory_id(self, tmp_path, caplog):
        skill_dir = tmp_path / "superpowers-debugging"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: obra/superpowers@systematic-debugging\n"
            "description: Debug systematically\n"
            "---\n"
            "# Systematic Debugging\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING):
            skill = SkillParser().parse_directory(skill_dir)
            warnings = SkillParser().validate(skill)

        assert warnings == []
        assert not any("directory name" in message.lower() for message in caplog.messages)

    def test_plain_skill_directory_mismatch_is_logged_once(self, tmp_path, caplog):
        skill_dir = tmp_path / "registry-alias"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: canonical-skill\n"
            "description: Plain skill with a mismatched directory\n"
            "---\n"
            "# Canonical Skill\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING):
            result = SkillLoader().load_skill(skill_dir)

        directory_warnings = [
            message for message in caplog.messages if "Directory name 'registry-alias'" in message
        ]
        assert result is not None
        assert len(directory_warnings) == 1

    def test_single_skill_index_events_are_logged_at_debug(self, tmp_path, caplog):
        skill_dir = tmp_path / "quiet-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: quiet-skill\ndescription: Quiet startup logging\n---\n# Quiet Skill\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.DEBUG):
            result = SkillLoader().load_skill(skill_dir)

        debug_messages = [
            record.message for record in caplog.records if record.levelno == logging.DEBUG
        ]
        assert result is not None
        assert any(
            "Registered skill descriptor: quiet-skill" in message for message in debug_messages
        )
        assert any("Indexed skill descriptor: quiet-skill" in message for message in debug_messages)
        assert not any(
            "skill descriptor: quiet-skill" in record.message.lower()
            for record in caplog.records
            if record.levelno == logging.INFO
        )
