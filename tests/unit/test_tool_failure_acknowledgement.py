"""Tests for `_check_tool_failure_acknowledgement`.

Covers the OpenClaw-style anti-hallucination belt that detects optimistic
LLM prose when at least one tool call in the turn actually failed
(``is_error=True``). The function returns ``None`` when the LLM has already
acknowledged failure in any language, and returns a warning banner string
otherwise.
"""

from __future__ import annotations

from openakita.core.reasoning_engine import _check_tool_failure_acknowledgement


def _tr(name: str, *, is_error: bool) -> dict:
    return {"tool_name": name, "is_error": is_error}


# ───────────────────────── 早返回 / 防御性输入 ─────────────────────────


def test_returns_none_when_text_empty():
    assert _check_tool_failure_acknowledgement("", [_tr("write_file", is_error=True)]) is None


def test_returns_none_when_tool_results_none():
    assert _check_tool_failure_acknowledgement("已完成保存", None) is None


def test_returns_none_when_tool_results_empty():
    assert _check_tool_failure_acknowledgement("已完成保存", []) is None


def test_returns_none_when_no_failures():
    assert (
        _check_tool_failure_acknowledgement(
            "已完成保存", [_tr("write_file", is_error=False)]
        )
        is None
    )


def test_skips_non_dict_entries():
    results = [
        "not a dict",  # type: ignore[list-item]
        None,  # type: ignore[list-item]
        _tr("write_file", is_error=False),
    ]
    assert _check_tool_failure_acknowledgement("已完成", results) is None


# ───────────────────────── 中文承认词放行 ─────────────────────────


def test_zh_acknowledgement_failure_passthrough():
    out = _check_tool_failure_acknowledgement(
        "写入失败了，请允许我重试", [_tr("write_file", is_error=True)]
    )
    assert out is None


def test_zh_acknowledgement_unable_passthrough():
    out = _check_tool_failure_acknowledgement(
        "无法访问该路径", [_tr("read_file", is_error=True)]
    )
    assert out is None


def test_zh_acknowledgement_permission_passthrough():
    out = _check_tool_failure_acknowledgement(
        "权限不足，未能完成保存", [_tr("write_file", is_error=True)]
    )
    assert out is None


def test_zh_acknowledgement_error_passthrough():
    out = _check_tool_failure_acknowledgement(
        "调用过程中出现错误", [_tr("run_shell", is_error=True)]
    )
    assert out is None


# ───────────────────────── 英文承认词放行 ─────────────────────────


def test_en_acknowledgement_failed_passthrough():
    out = _check_tool_failure_acknowledgement(
        "The write operation failed.", [_tr("write_file", is_error=True)]
    )
    assert out is None


def test_en_acknowledgement_error_passthrough():
    out = _check_tool_failure_acknowledgement(
        "Encountered an error during execution.", [_tr("run_shell", is_error=True)]
    )
    assert out is None


def test_en_acknowledgement_unable_passthrough():
    out = _check_tool_failure_acknowledgement(
        "Unable to read the file due to missing permissions.",
        [_tr("read_file", is_error=True)],
    )
    assert out is None


def test_en_acknowledgement_case_insensitive():
    out = _check_tool_failure_acknowledgement(
        "FAILED to deliver", [_tr("deliver_artifacts", is_error=True)]
    )
    assert out is None


# ───────────────────────── 触发 banner 的核心场景 ─────────────────────────


def test_optimistic_prose_with_single_failure_triggers_banner():
    text = "我已经成功保存了文件。"
    out = _check_tool_failure_acknowledgement(
        text, [_tr("write_file", is_error=True)]
    )
    assert out is not None
    assert "write_file" in out
    assert "1 个工具调用以失败告终" in out


def test_optimistic_prose_with_multiple_failures_triggers_banner():
    text = "全部任务已经按时完成。"
    results = [
        _tr("write_file", is_error=True),
        _tr("write_file", is_error=True),  # 同名再失败一次
        _tr("run_shell", is_error=True),
    ]
    out = _check_tool_failure_acknowledgement(text, results)
    assert out is not None
    # 同名工具最终态汇总为 1 个，与 _successful_tool_names 对偶
    assert "2 个工具调用以失败告终" in out
    assert out.count("write_file") == 1
    assert "run_shell" in out


def test_banner_truncates_summary_to_5_tools():
    text = "全部完成"
    results = [
        _tr(f"tool_{i}", is_error=True) for i in range(8)
    ]
    out = _check_tool_failure_acknowledgement(text, results)
    assert out is not None
    assert "等 8 个" in out


def test_unknown_tool_name_fallback_in_summary():
    text = "成功完成"
    out = _check_tool_failure_acknowledgement(
        text, [{"is_error": True}]  # 没有 tool_name / name 字段
    )
    assert out is not None
    assert "(未知工具)" in out


def test_uses_name_field_when_tool_name_missing():
    text = "成功完成"
    out = _check_tool_failure_acknowledgement(
        text, [{"name": "alt_tool", "is_error": True}]
    )
    assert out is not None
    assert "alt_tool" in out


# ───────────────────────── 混合成功 + 失败 ─────────────────────────


def test_only_failed_tools_appear_in_summary():
    text = "已成功保存所有内容"
    results = [
        _tr("read_file", is_error=False),  # 成功
        _tr("write_file", is_error=True),  # 失败
        _tr("ls", is_error=False),  # 成功
    ]
    out = _check_tool_failure_acknowledgement(text, results)
    assert out is not None
    assert "write_file" in out
    assert "read_file" not in out
    assert "ls" not in out


# ───────────────────────── 对偶约定：同名工具最终态 ─────────────────────────
#
# 与 _successful_tool_names() 保持一致：任一成功 receipt 视为该工具
# "有 backing evidence"，即便此前/此后存在失败 receipt，也不算最终失败。
# 这避免 ReAct 多轮重试场景的稳定误报。


def test_same_tool_fail_then_succeed_treated_as_success():
    """第 1 轮失败 → 第 2 轮重试成功 → LLM 说"已完成" 是正确描述，不该报 banner。"""
    text = "已完成保存"
    results = [
        _tr("write_file", is_error=True),
        _tr("write_file", is_error=False),
    ]
    assert _check_tool_failure_acknowledgement(text, results) is None


def test_same_tool_succeed_then_fail_still_treated_as_success():
    """先成功后失败 —— 与 _successful_tool_names 对偶约定一致：任一成功即放行。

    这条 trade-off 接受 false-negative：第二次失败的具体场景（如写不同文件）
    会漏报，但保持与既有 backing-evidence 语义对偶比"严苛但分裂"更重要。
    """
    text = "已完成保存"
    results = [
        _tr("write_file", is_error=False),
        _tr("write_file", is_error=True),
    ]
    assert _check_tool_failure_acknowledgement(text, results) is None


def test_two_tools_one_recovers_one_remains_failed():
    """一个工具失败 + 重试成功；另一个工具持续失败 → 只报后者。"""
    text = "已完成所有操作"
    results = [
        _tr("write_file", is_error=True),
        _tr("write_file", is_error=False),  # 重试成功
        _tr("run_shell", is_error=True),
        _tr("run_shell", is_error=True),  # 仍失败
    ]
    out = _check_tool_failure_acknowledgement(text, results)
    assert out is not None
    assert "1 个工具调用以失败告终" in out
    assert "run_shell" in out
    assert "write_file" not in out


def test_banner_format_contains_warning_emoji_and_divider():
    text = "已完成"
    out = _check_tool_failure_acknowledgement(
        text, [_tr("write_file", is_error=True)]
    )
    assert out is not None
    assert out.startswith("\n\n---\n")
    assert "⚠️" in out
    assert "系统检测" in out
