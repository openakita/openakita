"""L1 Unit Tests: Filesystem tools — edit_file, grep, glob, delete_file, list_directory enhancements.

Tests the FileTool methods and FilesystemHandler dispatch for the new/enhanced
filesystem tools introduced to match Cursor-like capabilities.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import aiofiles
import pytest

from openakita.tools.file import DEFAULT_IGNORE_DIRS, FileTool
from openakita.tools.handlers.filesystem import FilesystemHandler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def file_tool(tmp_path):
    """FileTool rooted in a pytest tmp_path."""
    return FileTool(base_path=str(tmp_path))


@pytest.fixture
def handler(tmp_path):
    """FilesystemHandler backed by a minimal mock Agent."""
    agent = MagicMock()
    agent.file_tool = FileTool(base_path=str(tmp_path))
    agent.shell_tool = MagicMock()
    return FilesystemHandler(agent)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _write_raw(path: str, content: str):
    """Write content preserving exact bytes (newline='')."""
    async with aiofiles.open(path, "w", encoding="utf-8", newline="") as f:
        await f.write(content)


# ===========================================================================
# edit_file
# ===========================================================================

class TestEditFile:
    """Tests for FileTool.edit() and handler._edit_file()."""

    async def test_basic_replacement(self, file_tool, tmp_path):
        target = str(tmp_path / "hello.py")
        await file_tool.write(target, "x = 1\ny = 2\nz = 3\n")
        result = await file_tool.edit(target, "y = 2", "y = 42")
        assert result["replaced"] == 1
        content = await file_tool.read(target)
        assert "y = 42" in content
        assert "y = 2" not in content

    async def test_crlf_compatibility(self, file_tool, tmp_path):
        """LLM sends \\n but file has \\r\\n — should adapt and preserve CRLF."""
        target = str(tmp_path / "crlf.txt")
        await _write_raw(target, "line1\r\nline2\r\nline3\r\n")

        result = await file_tool.edit(target, "line2\nline3", "LINE2\nLINE3")
        assert result["replaced"] == 1

        raw = await file_tool._read_preserving_newlines(target)
        assert "LINE2\r\nLINE3" in raw
        assert "\r\n" in raw

    async def test_replace_all(self, file_tool, tmp_path):
        target = str(tmp_path / "multi.txt")
        await file_tool.write(target, "foo bar foo bar foo bar")
        result = await file_tool.edit(target, "foo", "baz", replace_all=True)
        assert result["replaced"] == 3
        content = await file_tool.read(target)
        assert content == "baz bar baz bar baz bar"

    async def test_uniqueness_check(self, file_tool, tmp_path):
        target = str(tmp_path / "dup.txt")
        await file_tool.write(target, "abc\nabc\nabc")
        with pytest.raises(ValueError, match="3 times"):
            await file_tool.edit(target, "abc", "xyz")

    async def test_not_found(self, file_tool, tmp_path):
        target = str(tmp_path / "nf.txt")
        await file_tool.write(target, "hello world")
        with pytest.raises(ValueError, match="not found"):
            await file_tool.edit(target, "nonexistent", "x")

    async def test_file_not_exists(self, file_tool, tmp_path):
        with pytest.raises(FileNotFoundError):
            await file_tool.edit(str(tmp_path / "ghost.txt"), "a", "b")

    async def test_handler_edit_success(self, handler, tmp_path):
        target = str(tmp_path / "h.py")
        await handler.agent.file_tool.write(target, "a = 1\nb = 2\n")
        result = await handler.handle("edit_file", {
            "path": target, "old_string": "a = 1", "new_string": "a = 99",
        })
        assert "文件已编辑" in result

    async def test_handler_edit_missing_path(self, handler):
        result = await handler.handle("edit_file", {
            "path": "", "old_string": "a", "new_string": "b",
        })
        assert "❌" in result

    async def test_handler_edit_same_string(self, handler, tmp_path):
        target = str(tmp_path / "same.txt")
        await handler.agent.file_tool.write(target, "x")
        result = await handler.handle("edit_file", {
            "path": target, "old_string": "x", "new_string": "x",
        })
        assert "相同" in result

    async def test_handler_edit_replace_all_message(self, handler, tmp_path):
        target = str(tmp_path / "ra.txt")
        await handler.agent.file_tool.write(target, "aa aa aa")
        result = await handler.handle("edit_file", {
            "path": target, "old_string": "aa", "new_string": "bb",
            "replace_all": True,
        })
        assert "3 处" in result


# ===========================================================================
# grep
# ===========================================================================

class TestGrep:
    """Tests for FileTool.grep() and handler._grep()."""

    async def test_basic_search(self, file_tool, tmp_path):
        (tmp_path / "code.py").write_text(
            "class Foo:\n    def bar(self):\n        pass\n", encoding="utf-8",
        )
        results = await file_tool.grep("def bar", path=str(tmp_path))
        assert len(results) == 1
        assert results[0]["file"] == "code.py"
        assert results[0]["line"] == 2

    async def test_case_insensitive(self, file_tool, tmp_path):
        (tmp_path / "a.txt").write_text("Hello World\n", encoding="utf-8")
        results = await file_tool.grep("hello", path=str(tmp_path), case_insensitive=True)
        assert len(results) == 1

    async def test_context_lines(self, file_tool, tmp_path):
        (tmp_path / "b.py").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
        results = await file_tool.grep("3", path=str(tmp_path), context_lines=1)
        assert len(results) == 1
        r = results[0]
        assert r["context_before"] == ["2"]
        assert r["context_after"] == ["4"]

    async def test_include_filter(self, file_tool, tmp_path):
        (tmp_path / "yes.py").write_text("target\n", encoding="utf-8")
        (tmp_path / "no.txt").write_text("target\n", encoding="utf-8")
        results = await file_tool.grep("target", path=str(tmp_path), include="*.py")
        assert all(r["file"].endswith(".py") for r in results)

    async def test_max_results(self, file_tool, tmp_path):
        (tmp_path / "many.txt").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        results = await file_tool.grep("line", path=str(tmp_path), max_results=5)
        assert len(results) == 5

    async def test_skip_ignore_dirs(self, file_tool, tmp_path):
        git_dir = tmp_path / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "data.txt").write_text("secret\n", encoding="utf-8")
        results = await file_tool.grep("secret", path=str(tmp_path))
        assert len(results) == 0

    async def test_skip_binary(self, file_tool, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"findme" * 10)
        results = await file_tool.grep("findme", path=str(tmp_path))
        assert len(results) == 0

    async def test_grep_skips_directories_that_disappear_mid_scan(
        self,
        file_tool,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "ok.txt").write_text("target\n", encoding="utf-8")
        volatile = tmp_path / "volatile"
        volatile.mkdir()
        (volatile / "lost.txt").write_text("target\n", encoding="utf-8")

        original_iterdir = Path.iterdir

        def flaky_iterdir(path):
            if path == volatile:
                raise FileNotFoundError(path)
            return original_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

        results = await file_tool.grep("target", path=str(tmp_path))

        assert [r["file"] for r in results] == ["ok.txt"]
        assert file_tool.last_traversal_skipped == 1

    async def test_invalid_regex(self, file_tool, tmp_path):
        with pytest.raises(ValueError, match="Invalid regex"):
            await file_tool.grep("[bad", path=str(tmp_path))

    async def test_handler_grep_success(self, handler, tmp_path):
        (tmp_path / "x.py").write_text("def hello():\n    pass\n", encoding="utf-8")
        result = await handler.handle("grep", {"pattern": "def hello", "path": str(tmp_path)})
        assert "找到" in result

    async def test_handler_grep_no_match(self, handler, tmp_path):
        (tmp_path / "x.py").write_text("pass\n", encoding="utf-8")
        result = await handler.handle("grep", {"pattern": "zzz_nope", "path": str(tmp_path)})
        assert "未找到" in result

    async def test_handler_grep_bad_regex(self, handler, tmp_path):
        result = await handler.handle("grep", {"pattern": "[bad", "path": str(tmp_path)})
        assert "❌" in result


# ===========================================================================
# glob
# ===========================================================================

class TestGlob:
    """Tests for handler._glob()."""

    async def test_basic_glob(self, handler, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("y", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("z", encoding="utf-8")

        result = await handler.handle("glob", {"pattern": "*.py", "path": str(tmp_path)})
        assert "找到" in result
        assert "a.py" in result
        assert "c.py" in result
        assert "b.txt" not in result

    async def test_auto_prefix(self, handler, tmp_path):
        """Pattern without **/ gets auto-prefixed."""
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        (sub / "target.js").write_text("x", encoding="utf-8")
        result = await handler.handle("glob", {"pattern": "*.js", "path": str(tmp_path)})
        assert "target.js" in result

    async def test_skip_ignore_dirs(self, handler, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("x", encoding="utf-8")
        (tmp_path / "app.js").write_text("y", encoding="utf-8")
        result = await handler.handle("glob", {"pattern": "*.js", "path": str(tmp_path)})
        assert "app.js" in result
        assert "node_modules" not in result

    async def test_glob_skips_directories_that_disappear_mid_scan(
        self,
        handler,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "app.js").write_text("y", encoding="utf-8")
        volatile = tmp_path / "volatile"
        volatile.mkdir()
        (volatile / "lost.js").write_text("x", encoding="utf-8")

        original_iterdir = Path.iterdir

        def flaky_iterdir(path):
            if path == volatile:
                raise FileNotFoundError(path)
            return original_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

        result = await handler.handle("glob", {"pattern": "*.js", "path": str(tmp_path)})

        assert "app.js" in result
        assert "lost.js" not in result
        assert "已跳过 1 个" in result

    async def test_no_match(self, handler, tmp_path):
        result = await handler.handle("glob", {"pattern": "*.zzz", "path": str(tmp_path)})
        assert "未找到" in result

    async def test_sorted_by_mtime(self, handler, tmp_path):
        """Results should be sorted newest first."""
        import time
        (tmp_path / "old.py").write_text("x", encoding="utf-8")
        time.sleep(0.05)
        (tmp_path / "new.py").write_text("y", encoding="utf-8")
        result = await handler.handle("glob", {"pattern": "*.py", "path": str(tmp_path)})
        lines = result.strip().split("\n")
        file_lines = [line for line in lines if line.endswith(".py")]
        assert file_lines[0] == "new.py"


# ===========================================================================
# delete_file
# ===========================================================================

class TestDeleteFile:
    """Tests for handler._delete_file()."""

    async def test_delete_file(self, handler, tmp_path):
        target = tmp_path / "del.txt"
        target.write_text("temp", encoding="utf-8")
        result = await handler.handle("delete_file", {"path": str(target)})
        assert "已删除" in result
        assert not target.exists()

    async def test_delete_empty_dir(self, handler, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        result = await handler.handle("delete_file", {"path": str(d)})
        assert "已删除" in result
        assert not d.exists()

    async def test_reject_nonempty_dir(self, handler, tmp_path):
        d = tmp_path / "full"
        d.mkdir()
        (d / "child.txt").write_text("x", encoding="utf-8")
        result = await handler.handle("delete_file", {"path": str(d)})
        assert "❌" in result
        assert "非空" in result
        assert d.exists()

    async def test_not_found(self, handler, tmp_path):
        result = await handler.handle("delete_file", {"path": str(tmp_path / "nope.txt")})
        assert "❌" in result

    async def test_missing_path(self, handler):
        result = await handler.handle("delete_file", {"path": ""})
        assert "❌" in result


# ===========================================================================
# list_directory enhancements
# ===========================================================================

class TestListDirectoryEnhanced:
    """Tests for the new pattern/recursive params on list_directory."""

    async def test_default_behavior(self, handler, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.py").write_text("y", encoding="utf-8")
        result = await handler.handle("list_directory", {"path": str(tmp_path)})
        assert "a.txt" in result
        assert "b.py" in result

    async def test_pattern_filter(self, handler, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.py").write_text("y", encoding="utf-8")
        result = await handler.handle("list_directory", {
            "path": str(tmp_path), "pattern": "*.py",
        })
        assert "b.py" in result
        assert "a.txt" not in result

    async def test_recursive(self, handler, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("z", encoding="utf-8")
        result = await handler.handle("list_directory", {
            "path": str(tmp_path), "recursive": True, "pattern": "*.py",
        })
        assert "deep.py" in result

    async def test_recursive_list_directory_skips_unstable_subdirs(
        self,
        handler,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "top.py").write_text("x", encoding="utf-8")
        volatile = tmp_path / "volatile"
        volatile.mkdir()
        (volatile / "lost.py").write_text("z", encoding="utf-8")

        original_iterdir = Path.iterdir

        def flaky_iterdir(path):
            if path == volatile:
                raise FileNotFoundError(path)
            return original_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

        result = await handler.handle("list_directory", {
            "path": str(tmp_path), "recursive": True, "pattern": "*.py",
        })

        assert "top.py" in result
        assert "lost.py" not in result
        assert "已跳过 1 个" in result

    async def test_non_recursive_no_deep(self, handler, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("z", encoding="utf-8")
        (tmp_path / "top.py").write_text("x", encoding="utf-8")
        result = await handler.handle("list_directory", {
            "path": str(tmp_path), "pattern": "*.py",
        })
        assert "top.py" in result
        assert "deep.py" not in result


# ===========================================================================
# read_file / write_file safety
# ===========================================================================

class TestFileReadWriteSafety:
    """Regression tests for paginated reads and truncated-preview write guards."""

    async def test_read_file_pagination_says_original_file_is_not_truncated(
        self,
        handler,
        tmp_path,
    ):
        target = tmp_path / "large.txt"
        target.write_text("\n".join(f"line-{i}" for i in range(1, 6)), encoding="utf-8")

        result = await handler.handle("read_file", {
            "path": str(target),
            "limit": 2,
        })

        assert "[PAGE_HAS_MORE]" in result
        assert "原文件未截断" in result
        assert "[OUTPUT_TRUNCATED]" not in result
        assert "offset=3, limit=2" in result

    async def test_read_file_reuses_same_range_cache(self, handler, tmp_path):
        target = tmp_path / "cached.txt"
        target.write_text("first\nsecond", encoding="utf-8")

        first = await handler.handle("read_file", {"path": str(target), "limit": 10})
        second = await handler.handle("read_file", {"path": str(target), "limit": 10})

        assert "文件内容" in first
        assert second.startswith("♻️ 复用本轮 read_file 缓存结果")

    async def test_write_file_rejects_paginated_read_preview(self, handler, tmp_path):
        target = tmp_path / "safe.txt"
        target.write_text("original", encoding="utf-8")
        preview = (
            "文件内容 (第 1-2 行，共 5 行):\nline-1\nline-2\n\n"
            "[PAGE_HAS_MORE] 这是分页读取结果，原文件未截断。"
            "文件共 5 行，当前仅显示第 1-2 行，剩余 3 行。"
        )

        result = await handler.handle("write_file", {
            "path": str(target),
            "content": preview,
        })

        assert "已拒绝写入" in result
        assert target.read_text(encoding="utf-8") == "original"

    async def test_write_file_allows_normal_long_content(self, handler, tmp_path):
        target = tmp_path / "normal.txt"
        content = "\n".join(f"line-{i}" for i in range(500))

        result = await handler.handle("write_file", {
            "path": str(target),
            "content": content,
        })

        assert "文件已写入" in result
        assert target.read_text(encoding="utf-8") == content


# ===========================================================================
# Tool definition integrity
# ===========================================================================

class TestToolDefinitions:
    """Verify tool definitions, catalog, and registration constants."""

    def test_filesystem_tools_count(self):
        from openakita.tools.definitions.filesystem import FILESYSTEM_TOOLS
        assert {tool["name"] for tool in FILESYSTEM_TOOLS} == {
            "run_shell",
            "write_file",
            "read_file",
            "edit_file",
            "list_directory",
            "grep",
            "glob",
            "move_file",
            "delete_file",
        }

    def test_all_in_base_tools(self):
        from openakita.tools.definitions import BASE_TOOLS
        base_names = {t["name"] for t in BASE_TOOLS}
        for name in ["edit_file", "grep", "glob", "delete_file"]:
            assert name in base_names

    def test_high_freq_tools(self):
        from openakita.tools.catalog import HIGH_FREQ_TOOLS
        assert "edit_file" in HIGH_FREQ_TOOLS
        assert "grep" not in HIGH_FREQ_TOOLS

    def test_small_ctx_core_tools(self):
        from openakita.core.agent import SMALL_CTX_CORE_TOOLS
        assert "edit_file" in SMALL_CTX_CORE_TOOLS
        assert "grep" in SMALL_CTX_CORE_TOOLS

    def test_medium_ctx_extra_tools(self):
        from openakita.core.agent import MEDIUM_CTX_EXTRA_TOOLS
        assert "glob" in MEDIUM_CTX_EXTRA_TOOLS
        assert "delete_file" in MEDIUM_CTX_EXTRA_TOOLS

    def test_handler_tools_match_definitions(self):
        from openakita.tools.definitions.filesystem import FILESYSTEM_TOOLS
        def_names = [t["name"] for t in FILESYSTEM_TOOLS]
        assert def_names == FilesystemHandler.TOOLS

    def test_run_shell_guides_local_web_server_verification(self):
        from openakita.tools.definitions.filesystem import FILESYSTEM_TOOLS

        run_shell = next(tool for tool in FILESYSTEM_TOOLS if tool["name"] == "run_shell")

        assert "Flask/FastAPI/Vite" in run_shell["description"]
        assert "HTTP health/API requests" in run_shell["description"]
        assert "timeout as failure" in run_shell["description"]

    def test_all_definitions_valid_schema(self):
        from openakita.tools.definitions.filesystem import FILESYSTEM_TOOLS

        for tool in FILESYSTEM_TOOLS:
            assert tool["category"] == "File System"
            schema = tool["input_schema"]
            assert schema["type"] == "object"
            for r in schema["required"]:
                assert r in schema["properties"]
            json.dumps(tool)  # must be JSON-serializable


# ===========================================================================
# DEFAULT_IGNORE_DIRS sanity
# ===========================================================================

class TestIgnoreDirs:
    def test_common_dirs_present(self):
        for d in [".git", "node_modules", "__pycache__", ".venv"]:
            assert d in DEFAULT_IGNORE_DIRS

