"""Sub-agent output guard for hallucinated numerical conclusions.

Ported from ``openakita.core.agent_output_guard`` per ADR-0003 and
the Phase 2 sub-commit plan in ``docs/revamp/core_audit.md``. The
behaviour is unchanged; the legacy path is now a re-export shim
until Phase 8 mechanically removes the legacy ``core/`` tree.

The guard performs a *conservative* runtime check on
``delegate_to_agent`` / ``spawn_agent`` outputs to catch the
"announced a probability without running any code" failure mode.
All three signals must fire to trigger the disclaimer:

1. **Numeric task signal** — the original task text contains
   statistical / numerical language ("概率", "蒙特卡洛", "N 次", ...).
2. **Numeric output signal** — the sub-agent's reply contains an
   explicit number (a percentage, a probability, a count).
3. **No code execution** — the sub-agent's tool trace contains zero
   tools from :data:`CODE_EXEC_TOOLS`.

When all three fire, the original conclusion is **not modified**.
A short disclaimer is appended so the parent agent (or a human
reviewer) can decide whether to ask for verification.

Rationale and design notes are deliberately kept verbatim from the
legacy module: this is the kind of lightweight heuristic that needs
to look the same in code review and in CI smoke output, otherwise
operators will not trust it.
"""

from __future__ import annotations

import re

__all__ = [
    "CODE_EXEC_TOOLS",
    "DISCLAIMER_TEXT",
    "detect_numeric_output",
    "detect_numeric_task",
    "validate_no_fabricated_numbers",
]


_NUMERIC_TASK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(蒙特卡洛|monte\s*carlo)", re.IGNORECASE),
    re.compile(r"(模拟|仿真|simulate|simulation)", re.IGNORECASE),
    re.compile(r"(概率|probability|chance|odds)"),
    re.compile(r"(频率|frequency|多少次|发生.*次)"),
    re.compile(r"(统计|计算|算出|求.*值)"),
    re.compile(
        r"(均值|方差|标准差|分位数|百分位|mean|variance|stddev)",
        re.IGNORECASE,
    ),
    re.compile(r"\d+\s*(次|轮|trials|iterations)", re.IGNORECASE),
)

_NUMERIC_OUTPUT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    re.compile(r"概率.*?[:：]?\s*\d+"),
    re.compile(r"约|大约|大概|approximately|about\s*\d", re.IGNORECASE),
    re.compile(r"\b0?\.\d{2,}\b"),
    re.compile(r"\d+\s*/\s*\d+"),
)

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
    """Return ``True`` when the task text reads as numerical/statistical."""
    if not task_text:
        return False
    return any(p.search(task_text) for p in _NUMERIC_TASK_PATTERNS)


def detect_numeric_output(output_text: str) -> bool:
    """Return ``True`` when the output contains an explicit numerical figure."""
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
    """Detect a "numeric conclusion without code execution" pattern.

    Returns ``(triggered, augmented_output)``. When triggered, the
    augmented output is the original text plus :data:`DISCLAIMER_TEXT`;
    when not triggered, the original text is returned unchanged.
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
