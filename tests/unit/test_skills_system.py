"""L1 Unit Tests: Skill registry, loader, and parser."""


from openakita.skills.loader import SkillLoader
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
        if result:
            assert result.metadata.name == "test-skill"

    def test_load_all_from_empty(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        loader = SkillLoader()
        count = loader.load_all(skills_dir)
        assert isinstance(count, int)


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

