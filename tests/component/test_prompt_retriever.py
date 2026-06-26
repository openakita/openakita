"""L2 Component Tests: Prompt retriever (memory injection)."""

import pytest
from pathlib import Path

from openakita.prompt.retriever import retrieve_memory_simple


class TestRetrieveMemorySimple:
    def test_retrieve_from_file(self, tmp_path):
        mem_file = tmp_path / "MEMORY.md"
        mem_file.write_text(
            "# 记忆\n\n## 用户偏好\n- 喜欢Python\n- 习惯用 VS Code\n\n## 重要事实\n- 用户是后端开发者",
            encoding="utf-8",
        )
        result = retrieve_memory_simple(mem_file, max_chars=800)
        assert isinstance(result, str)
        assert "Python" in result or "记忆" in result

    def test_retrieve_nonexistent_file(self, tmp_path):
        result = retrieve_memory_simple(tmp_path / "missing.md")
        assert isinstance(result, str)

    def test_truncation(self, tmp_path):
        mem_file = tmp_path / "MEMORY.md"
        mem_file.write_text("x" * 5000, encoding="utf-8")
        result = retrieve_memory_simple(mem_file, max_chars=100)
        assert len(result) <= 200  # Allow some margin for truncation markers
