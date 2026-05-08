from types import SimpleNamespace

import pytest

from openakita.core.validators import FileValidator, ValidationContext, ValidationResult
from openakita.tools.file import FileTool
from openakita.tools.handlers.filesystem import FilesystemHandler


def _handler(tmp_path):
    agent = SimpleNamespace(file_tool=FileTool(str(tmp_path)))
    return FilesystemHandler(agent)


@pytest.mark.asyncio
async def test_move_file_moves_and_verifies_destination(tmp_path):
    source = tmp_path / "memory" / "周报.md"
    source.parent.mkdir()
    source.write_text("weekly report", encoding="utf-8")

    handler = _handler(tmp_path)
    result = await handler.handle(
        "move_file",
        {
            "src": "memory/周报.md",
            "dst": "weekly/周报_20260406-0410.md",
        },
    )

    assert "文件已移动" in result
    assert not source.exists()
    assert (tmp_path / "weekly" / "周报_20260406-0410.md").read_text(
        encoding="utf-8"
    ) == "weekly report"


@pytest.mark.asyncio
async def test_move_file_rejects_null_character_path(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("content", encoding="utf-8")

    handler = _handler(tmp_path)
    result = await handler.handle(
        "move_file",
        {
            "src": "a.md",
            "dst": "b\x00.md",
        },
    )

    assert "无效空字符" in result
    assert source.exists()


@pytest.mark.asyncio
async def test_move_file_into_existing_directory_keeps_source_name(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("content", encoding="utf-8")
    target_dir = tmp_path / "archive"
    target_dir.mkdir()

    handler = _handler(tmp_path)
    result = await handler.handle(
        "move_file",
        {
            "src": "a.md",
            "dst": "archive",
        },
    )

    assert "文件已移动" in result
    assert not source.exists()
    assert (target_dir / "a.md").read_text(encoding="utf-8") == "content"


def test_file_validator_verifies_move_result(tmp_path):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    target.write_text("content", encoding="utf-8")

    result = FileValidator().validate(
        ValidationContext(
            executed_tools=["move_file"],
            tool_results=[
                {
                    "tool_name": "move_file",
                    "content": f"文件已移动: {source} -> {target}",
                    "is_error": False,
                }
            ],
        )
    )

    assert result.result == ValidationResult.PASS
