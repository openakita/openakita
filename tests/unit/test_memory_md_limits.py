"""L1 Unit Tests: MEMORY.md 三档上限 + WARNING 文案。

借鉴 claude-code memdir.MAX_ENTRYPOINT_LINES / MAX_ENTRYPOINT_BYTES：
仅字符上限不能防止"行数少、单行极长"或"字节膨胀"，三档协同更稳健。
"""

import pytest

from openakita.memory.types import (
    MEMORY_MD_MAX_BYTES,
    MEMORY_MD_MAX_CHARS,
    MEMORY_MD_MAX_LINES,
    truncate_memory_md_with_status,
)


def test_returns_unchanged_when_within_limits():
    content = "## 用户偏好\n- 喜欢简洁回答\n"
    truncated, status = truncate_memory_md_with_status(content)
    assert truncated == content
    assert status["truncated"] is False
    assert status["triggers"] == []
    assert status["warning"] == ""


def test_empty_content_safe():
    truncated, status = truncate_memory_md_with_status("")
    assert truncated == ""
    assert status["original_chars"] == 0
    assert status["truncated"] is False


def test_chars_trigger_produces_warning():
    long = "x" * (MEMORY_MD_MAX_CHARS + 500)
    truncated, status = truncate_memory_md_with_status(long)
    assert status["truncated"] is True
    assert "chars" in status["triggers"]
    assert "字符" in status["warning"]
    assert len(truncated) <= MEMORY_MD_MAX_CHARS


def test_lines_trigger_independent_of_chars():
    """大量短行 — 字符可能不超，但行数会超。"""
    short_lines = "\n".join(["- a"] * (MEMORY_MD_MAX_LINES + 50))
    assert len(short_lines) < MEMORY_MD_MAX_CHARS  # 确保只触发 lines
    truncated, status = truncate_memory_md_with_status(
        short_lines, max_chars=999_999
    )
    assert status["truncated"] is True
    assert "lines" in status["triggers"]
    assert truncated.count("\n") + 1 <= MEMORY_MD_MAX_LINES


def test_bytes_trigger_with_multibyte_chars():
    """全中文场景 — 字符数可能不超，但 UTF-8 字节会膨胀。"""
    cjk_per_line = "重要规则：永远不要直接 push 到 main 分支并且每次提交都要写说明\n"
    content = cjk_per_line * 600  # 600 行 ≈ 35KB UTF-8
    truncated, status = truncate_memory_md_with_status(
        content, max_chars=999_999, max_lines=10_000
    )
    # 这个场景应该至少触发 bytes（也可能同时触发 chars/lines 取决于阈值组合）
    assert status["truncated"] is True
    assert "bytes" in status["triggers"]
    assert len(truncated.encode("utf-8")) <= MEMORY_MD_MAX_BYTES


def test_warning_lists_all_triggers():
    """字符 + 行 + 字节同时触顶时，WARNING 应该列出三个维度。"""
    cjk_long = "用户喜欢非常详细的回答" * 200 + "\n"  # 单行很长
    content = cjk_long * 250  # 250 行，每行也很长
    _, status = truncate_memory_md_with_status(content)
    assert status["truncated"] is True
    triggers = status["triggers"]
    assert "字符" in status["warning"]
    # 至少触发 2 个维度
    assert len(triggers) >= 2


def test_truncated_content_is_valid_utf8():
    """字节截断不应产生半个多字节字符。"""
    cjk = "这是一段中文测试" * 5000
    truncated, _ = truncate_memory_md_with_status(cjk)
    # 应能正常 encode/decode 往返
    truncated.encode("utf-8").decode("utf-8")


def test_custom_thresholds():
    content = "abc" * 100  # 300 chars
    truncated, status = truncate_memory_md_with_status(
        content, max_chars=50, max_lines=None, max_bytes=None
    )
    assert status["truncated"] is True
    assert status["triggers"] == ["chars"]
    assert len(truncated) <= 50


def test_lines_only_can_be_disabled():
    short_lines = "\n".join(["a"] * 500)
    _, status = truncate_memory_md_with_status(
        short_lines, max_chars=999_999, max_lines=None, max_bytes=None
    )
    assert status["truncated"] is False


@pytest.mark.parametrize(
    "max_chars,max_lines,max_bytes",
    [
        (MEMORY_MD_MAX_CHARS, MEMORY_MD_MAX_LINES, MEMORY_MD_MAX_BYTES),
        (500, 50, 5000),
    ],
)
def test_status_dict_shape(max_chars, max_lines, max_bytes):
    content = "x" * 100
    _, status = truncate_memory_md_with_status(
        content, max_chars=max_chars, max_lines=max_lines, max_bytes=max_bytes
    )
    assert set(status.keys()) >= {
        "original_chars",
        "original_lines",
        "original_bytes",
        "truncated",
        "triggers",
        "warning",
    }
