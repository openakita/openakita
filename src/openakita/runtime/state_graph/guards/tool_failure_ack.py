"""Tool-failure acknowledgement guard + successful-tool name aggregator.

Extracted from ``core/reasoning_engine.py`` (P-RC-5 P5.3) as the
second self-contained guard. Both helpers share the same concern --
filtering tool-call receipts and matching them against the LLM’s
final-answer text -- so they live in one module.

* :func:`check_tool_failure_acknowledgement` -- if any tool failed
  this turn (``is_error=True``) and the LLM’s final answer never
  acknowledges any failure (Chinese or English keyword list), append
  a banner asking the user to verify the outcome.
* :func:`successful_tool_names` -- dual helper for the
  ``unbacked-action-claim`` guard: aggregate executed tool names down
  to those whose latest receipt was NOT an error (a tool that failed
  then succeeded on retry counts as "succeeded").

Both functions are intentionally byte-for-byte equivalent to their
legacy counterparts (``_FAILURE_ACKNOWLEDGE_ZH`` /
``_FAILURE_ACKNOWLEDGE_EN`` tuples are identical word lists, and the
banner string is identical); the parity tests pin this against the
legacy private aliases that ``core/reasoning_engine.py`` re-imports
from here.
"""

from __future__ import annotations

__all__ = [
    "FAILURE_ACKNOWLEDGE_EN",
    "FAILURE_ACKNOWLEDGE_ZH",
    "check_tool_failure_acknowledgement",
    "successful_tool_names",
]


# 关键设计取舍（与 OpenClaw 的差异）：
# - OpenClaw 用 mutating-verb + 100-char window 做配对，精度高但 regex 复杂、
#   维护成本高；本函数只做关键词存在性检查，false-positive 由 banner 措辞"请核对"
#   兜底，false-negative 由其他守卫（_guard_unbacked_action_claim / verify）兜底。
# - 不修改 LLM 原文，只追加 banner，保持与 _check_source_tag_consistency 同风格。
FAILURE_ACKNOWLEDGE_ZH: tuple[str, ...] = (
    "失败",
    "出错",
    "出现错误",
    "报错",
    "错误",
    "异常",
    "无法",
    "未能",
    "没能",
    "不能",
    "失误",
    "未成功",
    "没成功",
    "受阻",
    "被拒",
    "拒绝",
    "拒绝执行",
    "权限不足",
    "找不到",
    "未找到",
    "不存在",
)

FAILURE_ACKNOWLEDGE_EN: tuple[str, ...] = (
    "fail",
    "failed",
    "failure",
    "error",
    "errored",
    "unable",
    "cannot",
    "can't",
    "couldn't",
    "could not",
    "didn't work",
    "doesn't work",
    "did not work",
    "not found",
    "denied",
    "permission",
    "forbidden",
    "rejected",
    "issue",
    "problem",
)


def check_tool_failure_acknowledgement(
    text: str,
    tool_results: list[dict] | None,
) -> str | None:
    """检测：本次任务存在最终失败的工具调用，但 LLM 文本完全没承认任何失败。

    与 _check_source_tag_consistency 互补——后者抓"声明工具但未调"的伪标注幻觉；
    本函数抓"工具失败但措辞乐观"的成功幻觉。参考 OpenClaw 的 MUTATING_FAILURE_ACTION
    检测思路，简化为关键词存在性检查（中英双语），匹配 LLM 输出的双语场景。

    **对偶约定**：与 `_successful_tool_names()` 保持一致——任一成功 receipt 视为
    "该工具有 backing evidence"。因此一个工具被判"最终失败"的条件是：
        - 至少有一条 is_error=True 的 receipt
        - 且**完全没有**成功 receipt（同名工具在后续 iter 没重试成功）

    这样可以避免 ReAct 多轮场景下"第 1 次失败 → 第 2 次重试成功"的**正确**
    流程被误报。如果不做这一层聚合，banner 会在重试成功的所有正常用例里
    持续打扰用户。

    返回值：
    - None：无最终失败 / LLM 已经承认了失败 → 无需处理
    - str：要追加到回答末尾的警告 banner 文本
    """
    if not text or not tool_results:
        return None

    # 把 receipts 按工具名聚合"是否曾经成功过"——按出现顺序记录失败工具，
    # 保留 dict insertion order 以便 banner 给出稳定的展示顺序。
    failed_once: dict[str, int] = {}  # tool_name → 失败次数（仅用作存在性）
    ever_succeeded: set[str] = set()
    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        tn = tr.get("tool_name") or tr.get("name") or "(未知工具)"
        if tr.get("is_error"):
            failed_once[tn] = failed_once.get(tn, 0) + 1
        else:
            ever_succeeded.add(tn)

    failed_tools = [name for name in failed_once if name not in ever_succeeded]
    if not failed_tools:
        return None

    if any(kw in text for kw in FAILURE_ACKNOWLEDGE_ZH):
        return None

    text_lower = text.lower()
    if any(kw in text_lower for kw in FAILURE_ACKNOWLEDGE_EN):
        return None

    fail_summary = "、".join(failed_tools[:5])
    if len(failed_tools) > 5:
        fail_summary += f" 等 {len(failed_tools)} 个"

    return (
        "\n\n---\n"
        f"⚠️ **系统检测**：本次任务中有 {len(failed_tools)} 个工具调用以失败告终"
        f"（{fail_summary}），但上述回答未提及任何失败 / 错误。"
        "请你核对结果，必要时让我重试或换种方式。"
    )

def successful_tool_names(
    executed_tool_names: list[str],
    tool_results: list[dict] | None,
) -> set[str]:
    """Filter executed tool names down to those whose latest result is not an error."""
    if not executed_tool_names:
        return set()
    if not tool_results:
        return set(executed_tool_names)
    executed = set(executed_tool_names)
    seen: set[str] = set()
    succeeded: set[str] = set()
    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        tn = tr.get("tool_name") or tr.get("name") or ""
        if not tn:
            continue
        seen.add(tn)
        if not tr.get("is_error"):
            succeeded.add(tn)
    # Same tool may fail first and then succeed on retry. Treat any successful
    # receipt as backing evidence, while tools without result entries keep the
    # historical optimistic behavior.
    return {name for name in executed if name not in seen or name in succeeded}


