"""Compiled regex patterns shared by reasoning-engine guards.

Each accessor compiles its pattern on
first call and caches it on the function object so repeat lookups
across the reasoning loop stay O(1).

The guard modules share these accessors so recorded fixtures use one pattern
implementation.
"""

from __future__ import annotations

import re

__all__ = [
    "action_done_re",
    "source_tag_re",
]


def source_tag_re() -> re.Pattern[str]:
    """Match ``[来源:工具] / [来源:历史] / [来源:常识] / [来源:不确定]`` tags.

    Used by :func:`check_source_tag_consistency` to detect whether the
    LLM declared a source on its answer at all; the guard only fires
    a banner when a source is declared but contradicted by the actual
    tool-execution count.
    """
    pat = getattr(source_tag_re, "_cached", None)
    if pat is not None:
        return pat
    pat = re.compile(r"\[来源[:：]\s*(工具|历史|常识|不确定)\s*\]")
    source_tag_re._cached = pat  # type: ignore[attr-defined]
    return pat


def action_done_re() -> re.Pattern[str]:
    """Match Chinese "I already X" / "I just X" phrases ("已查/已读/已删/...").

    Complement to the unbacked-action-claim regex
    (``action_claim_re``, extracted in a later P-RC-5 commit): this
    pattern catches read-side framings ("I already looked it up", "I
    already checked") that the action-claim regex deliberately misses
    because they are reasonably ambiguous about whether a tool ran.
    """
    pat = getattr(action_done_re, "_cached", None)
    if pat is not None:
        return pat
    pat = re.compile(
        r"(?:"
        r"已经?(?:查|读|删|改|发|执行|完成|保存|写|跑|搜|检索|获取|拉取|下载)"
        r"|我刚(?:才|刚)?(?:执行|完成|查到|读到|跑|发|删|改|写|拉|获取)"
        r")"
    )
    action_done_re._cached = pat  # type: ignore[attr-defined]
    return pat
