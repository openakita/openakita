"""P1-3: action-claim guard must not warn on historical recaps.

Before the fix, asking the agent "复述一下你刚才做了什么" would emit a
"⚠️ 一致性提示：未检测到对应工具的成功凭证" footer because the model would
say "已保存 / 已记住 / 已删除 / 已交付" while no tool fired *this turn*.
The recap window detector should now suppress the warning whenever the
claim is anchored by a timestamp (`[17:30]`) or an explicit recap adverb
(之前 / 刚才 / 历史 / 上文 / 上次 / 先前 / 此前 / ...).
"""

from openakita.core.reasoning_engine import (
    _extract_unbacked_verbs,
    _guard_unbacked_action_claim,
    _is_recap_context,
)


def test_recap_with_timestamp_is_detected():
    text = "我已在 [17:30] 已保存了你的项目代号"
    assert _is_recap_context(text, "保存")


def test_recap_with_chinese_adverb_is_detected():
    text = "之前我已为你创建了 docs/plan.md"
    assert _is_recap_context(text, "创建")


def test_extract_unbacked_skips_recap_verbs():
    text = "刚才已保存 SEAGULL 项目信息到记忆"
    unbacked = _extract_unbacked_verbs(text, successful_tools=set())
    assert unbacked == [], (
        "Recap-anchored claims must not be flagged; otherwise the model gets "
        "scolded for honestly recapping past turns."
    )


def test_extract_unbacked_still_flags_fresh_unverified_claims():
    text = "✅ 我已删除了 D:/data 下所有日志文件"
    unbacked = _extract_unbacked_verbs(text, successful_tools=set())
    assert "删除" in unbacked, (
        "Fresh action claims with no recap anchor and no successful tool "
        "should still be flagged — that's the whole point of the guard."
    )


def test_guard_passes_recap_text_through_unchanged():
    text = (
        "根据对话历史：\n"
        "- [17:26] 我已为你保存了项目代号 SEAGULL\n"
        "- [17:31] 我已记住你居住在重庆\n"
    )
    out = _guard_unbacked_action_claim(text, executed_tool_names=[], tool_results=None)
    assert out == text, "Pure historical recap must round-trip without warning."


def test_guard_still_intervenes_on_fabricated_claim_without_recap():
    text = "✅ 已删除了 D:/data 下的旧日志（无任何回溯标记）"
    out = _guard_unbacked_action_claim(text, executed_tool_names=[], tool_results=None)
    # Either the legacy fallback notice or the appended warning is acceptable —
    # what matters is the original misleading claim is no longer the literal
    # response.
    assert out != text
