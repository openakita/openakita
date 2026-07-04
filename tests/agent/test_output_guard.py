"""Tests for ``openakita.agent.output_guard``.

Anchors three things:

1. The numeric-task / numeric-output / no-code-execution gate fires
   only when all three signals align (the conservative-trigger
   contract documented at the top of the module).
2. The disclaimer is appended, never substituted, so the original
   conclusion is preserved verbatim.
3. The legacy ``core.agent_output_guard`` import path keeps yielding
   the same callables (move-compat).
"""

from __future__ import annotations

import pytest

from openakita.agent.output_guard import (
    DISCLAIMER_TEXT,
    detect_numeric_output,
    detect_numeric_task,
    validate_no_fabricated_numbers,
)

# ---------------------------------------------------------------------------
# detect_numeric_task
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "请用蒙特卡洛模拟跑 10000 次",
        "求该事件的概率",
        "Please run a Monte Carlo simulation",
        "统计这组数据的均值和方差",
    ],
)
def test_detect_numeric_task_positive(text: str) -> None:
    assert detect_numeric_task(text) is True


@pytest.mark.parametrize(
    "text",
    ["Hello world", "请帮我画一张图", "", "总结一下今天的会议"],
)
def test_detect_numeric_task_negative(text: str) -> None:
    assert detect_numeric_task(text) is False


# ---------------------------------------------------------------------------
# detect_numeric_output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "概率约为 42%",
        "结果大约是 0.37",
        "命中比例 3/8",
        "approximately 12 trials",
    ],
)
def test_detect_numeric_output_positive(text: str) -> None:
    assert detect_numeric_output(text) is True


@pytest.mark.parametrize(
    "text", ["完成", "我已经分析了你的需求", ""],
)
def test_detect_numeric_output_negative(text: str) -> None:
    assert detect_numeric_output(text) is False


# ---------------------------------------------------------------------------
# validate_no_fabricated_numbers
# ---------------------------------------------------------------------------


def test_full_trigger_appends_disclaimer() -> None:
    triggered, augmented = validate_no_fabricated_numbers(
        task_text="蒙特卡洛模拟 10000 次的概率",
        output_text="结果大约是 0.42",
        tools_used=["read_file", "web_search"],
    )
    assert triggered is True
    assert augmented.startswith("结果大约是 0.42")
    assert augmented.endswith(DISCLAIMER_TEXT)


def test_code_execution_suppresses_trigger() -> None:
    triggered, augmented = validate_no_fabricated_numbers(
        task_text="蒙特卡洛模拟 10000 次的概率",
        output_text="结果大约是 0.42",
        tools_used=["python_runtime"],
    )
    assert triggered is False
    assert augmented == "结果大约是 0.42"


def test_non_numeric_task_suppresses_trigger() -> None:
    triggered, augmented = validate_no_fabricated_numbers(
        task_text="帮我写一首诗",
        output_text="约 80% 的人喜欢春天",
        tools_used=[],
    )
    assert triggered is False
    assert augmented == "约 80% 的人喜欢春天"


def test_empty_output_short_circuits() -> None:
    triggered, augmented = validate_no_fabricated_numbers(
        task_text="蒙特卡洛 100 次",
        output_text="",
        tools_used=None,
    )
    assert triggered is False
    assert augmented == ""


# ---------------------------------------------------------------------------
# Move-compat
# ---------------------------------------------------------------------------


def test_legacy_path_re_exports_same_callables() -> None:
    from openakita.core.agent_output_guard import (
        validate_no_fabricated_numbers as legacy,
    )

    assert legacy is validate_no_fabricated_numbers
