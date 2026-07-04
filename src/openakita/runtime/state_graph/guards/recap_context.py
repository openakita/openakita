"""Historical-recap detector.

Extracted from ``core/reasoning_engine.py`` (P-RC-5 P5.4) into its
own guard module. Detects whether a verb / tool mention in the
LLM’s final-answer text sits inside a "I previously did X" recap
window (timestamp, ``之前`` / ``刚才`` / ``历史`` / ``上文``...) so
the unbacked-action-claim guard does not flag legitimate
summaries of past tool runs as fresh hallucinations.

The compiled regex literal and the ±48-character window are
byte-identical to the legacy version; the parity tests pin this.
"""

from __future__ import annotations

__all__ = ["RECAP_NEAR_RE", "is_recap_context"]


# 历史回溯标记：当声明动词 *附近* 出现这些词，说明 LLM 是在汇总过去动作，
# 不是在本轮做出新声明，一致性守卫应放行。
# - 时间戳：`[17:30]` `[2026-05-09 17:30]`
# - 中文回溯副词：之前 / 刚才 / 历史 / 上文 / 上次 / 早些 / 先前 / 此前
# - 已在……：已在 17:30 / 已经在历史 / 之前已 ...
RECAP_NEAR_RE = __import__("re").compile(
    r"(?:"
    r"\[\d{1,2}:\d{2}\]"
    r"|\[\d{4}-\d{2}-\d{2}[^\]]*\]"
    r"|(?:之前|刚才|此前|先前|上次|上文|历史(?:记录|中|上)|早些时(?:候)?|早前|前面|"
    r"过去|本轮之前|前几轮|最近(?:的)?(?:对话|会话|任务)|根据(?:对话|历史)|"
    r"回顾|总结|复述|汇总|盘点)"
    r")"
)


def is_recap_context(text: str, verb_or_tool: str) -> bool:
    """Return True if the verb/tool mention sits inside a historical-recap window.

    Heuristic: scan a ±48-character window around each occurrence of the verb /
    tool name. If any window contains a timestamp or recap adverb, treat the
    whole claim as a historical summary instead of a fresh action.
    """
    import re as _re

    if not text or not verb_or_tool:
        return False
    half = 48
    for m in _re.finditer(_re.escape(verb_or_tool), text, _re.IGNORECASE):
        start = max(0, m.start() - half)
        end = min(len(text), m.end() + half)
        window = text[start:end]
        if RECAP_NEAR_RE.search(window):
            return True
    return False


