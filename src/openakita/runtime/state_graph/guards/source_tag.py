"""Source-tag consistency guard.

The guard detects two hallucination patterns before yielding the LLM response:

1. **Mislabelled tool source.** The LLM appended a
   ``[来源:工具]`` tag ("source: tool") to its answer but no tool
   actually ran this turn -> append a system banner that owns up
   to the discrepancy.
2. **Implicit \"I already did X\" without a source tag.** No source
   tag at all and no tool ran, but the text contains
   "已查/已读/已执行"-style action-done phrasing -> append a softer
   banner asking the user to verify.

The guard never rewrites the LLM text; it only returns a banner
the caller appends. This preserves the original wording so the
user can see what the model claimed vs. what the system actually
observed.

The parity harness pins the banner strings returned by
:func:`check_source_tag_consistency`.
"""

from __future__ import annotations

from ._text_patterns import action_done_re, source_tag_re

__all__ = ["check_source_tag_consistency"]


def check_source_tag_consistency(text: str, tools_executed_count: int) -> str | None:
    """检查回答中的来源标签与实际工具调用次数是否一致。

    P0-2 阶段 3：后置一致性检测。

    返回值：
    - None：一致，无需任何处理
    - str：要追加到回答末尾的警告 banner 文本（不替换原文，让用户看到原文+提示）
    """
    if not text:
        return None
    tag_re = source_tag_re()
    if "[来源:工具]" in text or "[来源：工具]" in text:
        if tools_executed_count == 0:
            return (
                "\n\n---\n"
                "⚠️ **系统检测**：本轮回答声明了 `[来源:工具]`，但实际未调用任何工具。"
                "标注不准确，请将上述结论视为来自训练知识（[来源:常识]）或历史对话，"
                "如需精确事实请告诉我去查证。"
            )
    # 无任何来源标签，但出现"动作完成短语"，且未调用工具 → 隐性"已完成"幻觉
    if tools_executed_count == 0:
        if not tag_re.search(text) and action_done_re().search(text):
            return (
                "\n\n---\n"
                "⚠️ **系统提示**：本轮未实际调用任何工具，上述"
                '"已查到/已执行/已读到"等动作完成短语可能不准确，请你核实。'
            )
    return None
