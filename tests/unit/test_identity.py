"""L1 Unit Tests: Identity document loading and prompt generation."""

import pytest
from pathlib import Path

from openakita.core.identity import Identity


@pytest.fixture
def identity_dir(tmp_path):
    d = tmp_path / "identity"
    d.mkdir()
    (d / "SOUL.md").write_text("# Soul\n\n你是 OpenAkita，一只忠诚的秋田犬AI助手。", encoding="utf-8")
    (d / "AGENT.md").write_text("# Agent\n\n## Core\n永不放弃。\n\n## Tooling\n善用工具。", encoding="utf-8")
    (d / "USER.md").write_text("# User\n\n用户是一名开发者。", encoding="utf-8")
    (d / "MEMORY.md").write_text("# Memory\n\n用户喜欢 Python。", encoding="utf-8")
    return d


class TestIdentityLoading:
    def test_load_all_documents(self, identity_dir):
        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()
        assert "OpenAkita" in identity.soul or "秋田犬" in identity.soul
        assert len(identity.agent) > 0
        assert "开发者" in identity.user

    def test_load_missing_file(self, tmp_path):
        identity = Identity(soul_path=tmp_path / "nonexistent.md")
        identity.load()
        # Should not crash, just have empty content
        assert isinstance(identity.soul, str)

    def test_get_system_prompt(self, identity_dir):
        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()
        prompt = identity.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_get_soul_summary(self, identity_dir):
        identity = Identity(soul_path=identity_dir / "SOUL.md")
        identity.load()
        summary = identity.get_soul_summary()
        assert isinstance(summary, str)


class TestIdentityUpdate:
    def test_update_memory(self, identity_dir):
        identity = Identity(memory_path=identity_dir / "MEMORY.md")
        identity.load()
        original = identity.memory
        # update_memory returns bool
        result = identity.update_memory("preferences", "用户喜欢咖啡")
        assert isinstance(result, bool)

