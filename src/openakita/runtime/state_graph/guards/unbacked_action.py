"""Unbacked-action-claim guard.

Extracted from ``core/reasoning_engine.py`` (P-RC-5 P5.6) as the
largest module-level reasoning-engine helper. Detects two
hallucination patterns the legacy engine flagged before yielding
the LLM’s final answer:

1. **"I already did X" with no tool ran at all.** The LLM uses an
   action-claim phrase (``已保存`` / ``成功发送`` / ...) but no tool
   was called this turn -> append a consistency-hint banner; tighten
   the wording when the claim mentions memory writes.
2. **"I already did X" with the wrong tool ran.** Tools ran but their
   names do not contain any of the fragments registered for the
   claimed verb in ``VERB_TO_TOOL_FRAGMENTS`` (and the verb is not
   inside a historical-recap window) -> append a softer banner
   listing what actually succeeded.

The guard never overwrites the original answer; it only appends a
banner so the user can see both what the model claimed and what
actually happened.

Composes three already-extracted helpers:

* :func:`runtime.state_graph.guards.recap_context.is_recap_context`
* :func:`runtime.state_graph.guards.tool_failure_ack.successful_tool_names`
* :data:`runtime.state_graph.guards._verb_tool_map.{CLAIMED_TOOL_TO_FRAGMENTS, VERB_TO_TOOL_FRAGMENTS}`

The :func:`action_claim_re` accessor is local because no other guard
uses it; if a future guard needs it, lift it into
``_text_patterns`` and re-import here.
"""

from __future__ import annotations

import re

from ._verb_tool_map import CLAIMED_TOOL_TO_FRAGMENTS, VERB_TO_TOOL_FRAGMENTS
from .recap_context import RECAP_NEAR_RE, is_recap_context
from .tool_failure_ack import successful_tool_names

__all__ = [
    "action_claim_re",
    "extract_unbacked_verbs",
    "guard_unbacked_action_claim",
]


def action_claim_re() -> re.Pattern[str]:
    """Compiled regex that detects Chinese action-claim phrases.

    Matches patterns like "已帮你保存", "已完成", "成功发送", "已经删除" — these
    indicate the LLM is *claiming* it performed an operation rather than merely
    analysing or describing content.  Used by the implicit-REPLY heuristic to
    avoid accepting hallucinated action descriptions.
    """
    import re as _re

    pat = getattr(action_claim_re, "_cached", None)
    if pat is not None:
        return pat
    verbs = (
        "保存|发送|创建|删除|修改|更新|上传|下载|执行|生成|导出|复制|移动|"
        "写入|添加|设置|配置|安装|部署|打包|编译|构建|启动|重启|停止|关闭|"
        "记住|记录|存入|保存到记忆|调用|读取"
    )
    pat = _re.compile(
        rf"(?:"
        rf"(?:已[经]?|成功|顺利|我已经|我已)(?:帮你?|为你|给你)?(?:{verbs})"
        rf"|已通过.{{0,30}}(?:验证|读取|检查)"
        rf"|工具已.{{0,10}}(?:调用|执行)"
        rf"|(?:write_file|edit_file|read_file|run_shell|run_powershell)"
        rf".{{0,30}}(?:已调用|已执行|已验证|验证完成)"
        rf")"
    )
    action_claim_re._cached = pat  # type: ignore[attr-defined]
    return pat


def extract_unbacked_verbs(
    text: str,
    successful_tools: set[str],
) -> list[str]:
    """Return action verbs whose claim is not backed by any successful tool call."""
    import re as _re

    prefix_pat = _re.compile(r"(?:已[经]?|成功|顺利|我已经|我已)(?:帮你?|为你|给你)?")
    unbacked: list[str] = []

    for tool_name, fragments in CLAIMED_TOOL_TO_FRAGMENTS.items():
        # Detect the issue #424 shape: the model writes a Markdown table saying
        # "write_file/read_file 已调用" even though no matching tool receipt exists.
        tool_claim_pat = _re.compile(
            rf"{_re.escape(tool_name)}.{{0,40}}"
            r"(?:已调用|已执行|已验证|验证完成|实际调用|执行完成|✅)",
            _re.IGNORECASE,
        )
        reverse_claim_pat = _re.compile(
            r"(?:已通过|通过|验证|读取|检查|调用|执行).{0,40}"
            rf"{_re.escape(tool_name)}",
            _re.IGNORECASE,
        )
        if not (tool_claim_pat.search(text) or reverse_claim_pat.search(text)):
            continue
        if any(any(frag in t for frag in fragments) for t in successful_tools):
            continue
        # 历史回溯放行：模型在复述/汇总以前真正发生过的工具调用时
        # 不应被当成幻觉。
        if is_recap_context(text, tool_name):
            continue
        unbacked.append(f"{tool_name}调用")

    for verb, fragments in VERB_TO_TOOL_FRAGMENTS.items():
        # Must appear right after an action-claim prefix to count as a real claim
        # (avoids matching plain narrative like "我会创建..." or "需要修改...").
        verb_pat = _re.compile(rf"{prefix_pat.pattern}{_re.escape(verb)}")
        if not verb_pat.search(text):
            continue
        if any(any(frag in t for frag in fragments) for t in successful_tools):
            continue
        if is_recap_context(text, verb):
            continue
        unbacked.append(verb)
    return unbacked


def guard_unbacked_action_claim(
    text: str,
    executed_tool_names: list[str],
    tool_results: list[dict] | None = None,
) -> str:
    """Downgrade visible action claims when no successful tool receipt exists.

    Two layers of defence:
    1. If the message contains an action-claim phrase but **no** tool ran at all,
       fall back to a non-deceptive notice (legacy behaviour).
    2. If tools did run but their names don't match the claimed verbs (e.g. text
       says "已删除 X" but only ``get_tool_info`` was called), append a warning
       and refuse to corroborate the claim. This catches the most common
       hallucination class without blocking genuine multi-tool replies.
    """
    if not text or not action_claim_re().search(text):
        return text

    successful_tools = successful_tool_names(executed_tool_names, tool_results)

    if not executed_tool_names:
        # 整段回复是历史汇总（含时间戳/回溯副词且无新动作迹象）→ 守卫不应介入，
        # 否则用户问"复述一下你做了什么"会被替换成"没有凭证"。
        if RECAP_NEAR_RE.search(text):
            return text
        unbacked = extract_unbacked_verbs(text, set())
        verbs_str = "/".join(unbacked[:3]) if unbacked else "外部动作"
        memory_hint = (
            "当前没有检测到长期记忆写入凭证，所以请勿据此认定已写入长期记忆。"
            if any(v in {"保存", "保存到记忆", "记住", "记录", "存入"} for v in unbacked)
            or "记忆" in text
            else "当前没有检测到实际工具执行凭证，因此请勿据此认定外部动作已经完成。"
        )
        return (
            text.rstrip()
            + f"\n\n⚠️ 一致性提示：上文宣称已『{verbs_str}』，但本轮没有成功工具调用凭证。"
            + memory_hint
        )

    unbacked = extract_unbacked_verbs(text, successful_tools)
    if not unbacked:
        return text

    # 有具体动词宣称但找不到匹配的成功工具调用 → 在原文末尾追加幻觉告警，
    # 不直接覆盖原文（保留 LLM 可能正确的其他部分），但让用户看到不一致。
    verbs_str = "/".join(unbacked[:3])
    succeeded_str = ", ".join(sorted(successful_tools)[:5]) or "无"
    warning = (
        f"\n\n⚠️ 一致性提示：上文宣称已『{verbs_str}』，但本轮成功执行的工具是 "
        f"[{succeeded_str}]，未检测到对应工具的成功凭证。请勿据此认定操作已完成，"
        "如需重试请明确告知。"
    )
    return text.rstrip() + warning


