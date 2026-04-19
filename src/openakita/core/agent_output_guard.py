"""
子 Agent 输出守卫

针对 delegate_to_agent / spawn_agent 等子 Agent 调用的输出做轻量
"零代码即下结论" 类幻觉的运行时拦截：

判定条件（同时满足才告警）：
1. 原始任务文本包含 "统计 / 计算 / 概率 / 多少 / 频率 / N 次 ..." 等数值/统计类关键词
2. 子 Agent 输出文本包含具体数字（百分比 / 比例 / 次数 / 概率）
3. 子 Agent 整个 trace 完全没有调用过 run_shell / python_runtime 等代码执行类工具

当全部命中时，不修改子 Agent 的数值结论，只在尾部追加一条
**⚠️ 数据未经代码执行验证** 的免责说明，由父 Agent 决定是否复核。

设计原则：
- 保守拦截：宁可漏报不要误伤可信结论
- 不修改数值：仅追加 disclaimer，避免覆盖正确答案
- 零依赖、纯文本启发式，可单测、可在 CI smoke 卡阈值
"""

from __future__ import annotations

import re

# 数值/统计任务触发词（任务文本侧）
_NUMERIC_TASK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(蒙特卡洛|monte\s*carlo)", re.IGNORECASE),
    re.compile(r"(模拟|仿真|simulate|simulation)", re.IGNORECASE),
    re.compile(r"(概率|probability|chance|odds)"),
    re.compile(r"(频率|frequency|多少次|发生.*次)"),
    re.compile(r"(统计|计算|算出|求.*值)"),
    re.compile(r"(均值|方差|标准差|分位数|百分位|mean|variance|stddev)", re.IGNORECASE),
    re.compile(r"\d+\s*(次|轮|trials|iterations)", re.IGNORECASE),
)

# 输出中"具体数字"的检测：百分比 / 概率 / 次数 / 比例 / 区间
_NUMERIC_OUTPUT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    re.compile(r"概率.*?[:：]?\s*\d+"),
    re.compile(r"约|大约|大概|approximately|about\s*\d", re.IGNORECASE),
    re.compile(r"\b0?\.\d{2,}\b"),
    re.compile(r"\d+\s*/\s*\d+"),
)

# 视为"真实代码执行"的工具名（出现任意一个即视为已用代码验证）
CODE_EXEC_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "shell",
        "execute_shell",
        "python",
        "python_runtime",
        "code_interpreter",
        "execute_code",
    }
)

DISCLAIMER_TEXT = (
    "\n\n> ⚠️ **数据未经代码执行验证**：本次子 Agent 输出包含具体数值，"
    "但任务执行轨迹中未发现任何代码运行（`run_shell` 等）。"
    "若数值用于决策，请要求 Agent 重新跑一次真实计算。"
)


def detect_numeric_task(task_text: str) -> bool:
    """判断任务文本是否属于数值/统计类。"""
    if not task_text:
        return False
    return any(p.search(task_text) for p in _NUMERIC_TASK_PATTERNS)


def detect_numeric_output(output_text: str) -> bool:
    """判断输出文本是否包含具体数值结论。"""
    if not output_text:
        return False
    return any(p.search(output_text) for p in _NUMERIC_OUTPUT_PATTERNS)


def _has_code_exec(tools_used: list[str] | None) -> bool:
    if not tools_used:
        return False
    return any(t in CODE_EXEC_TOOLS for t in tools_used)


def validate_no_fabricated_numbers(
    task_text: str,
    output_text: str,
    tools_used: list[str] | None,
) -> tuple[bool, str]:
    """检测疑似"零代码即下数值结论"。

    Returns:
        (triggered, augmented_output)
        triggered=True 时，augmented_output 在原文末追加 disclaimer；
        triggered=False 时返回原文不变。
    """
    if not output_text:
        return False, output_text or ""
    if not detect_numeric_task(task_text):
        return False, output_text
    if not detect_numeric_output(output_text):
        return False, output_text
    if _has_code_exec(tools_used):
        return False, output_text
    return True, output_text + DISCLAIMER_TEXT
