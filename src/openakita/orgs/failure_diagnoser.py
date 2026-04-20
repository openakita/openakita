"""
Failure diagnoser —— 把 ReAct trace + exit_reason 翻译成"给用户看的根因 + 建议"。

职责边界：
- 纯函数分析：不写文件、不发事件、不依赖 I/O
- 只产出 dict，是否发给前端由 runtime.py 决定
- 与 openakita.evolution.failure_analysis 分离：后者是给 harness/训练用的结构化落盘，
  本模块只关心"人话摘要 + 证据片段 + 下一步建议"，两者职责互不耦合

输出形状:
    {
        "root_cause": str,        # 分类码（稳定字符串，供前端切样式/打点）
        "headline": str,          # 一句话人话标题
        "evidence": list[dict],   # [{iter, tool, args_summary, error}, ...]
        "suggestion": str,        # 给用户的下一步建议（多行文本，markdown 兼容）
        "exit_reason": str,       # 透传 reasoning_engine._last_exit_reason
    }

永不抛异常：分析失败时回退到 root_cause="unknown"。
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

MAX_EVIDENCE_ITEMS = 6
EVIDENCE_ERROR_MAX = 200

_SELF_DELEGATE_MARKERS = (
    "不能把任务委派给自己",
    "不能给自己委派任务",
)
_NON_DIRECT_MARKERS = (
    "不是你的直属下级",
)
_TARGET_NOT_EXIST_MARKERS = (
    "目标节点",
    "可用节点",
)
_GENERIC_FAIL_MARKERS = (
    "[失败]",
    "[org_delegate_task 失败]",
    "❌",
    "⚠️ 工具执行错误",
    "⚠️ 策略拒绝",
    "错误类型:",
)


def _is_error_entry(is_error_flag: bool, result_content: str) -> bool:
    """字段 `is_error` 有时会漏打；再扫一遍文本兜底识别失败。"""
    if is_error_flag:
        return True
    if not result_content:
        return False
    return any(m in result_content for m in _GENERIC_FAIL_MARKERS)


def _summarize_args(args: Any) -> str:
    """把 tool args 压成一行摘要，优先显示与组织编排相关的关键字段。"""
    if not isinstance(args, dict):
        return ""
    priority_keys = ("to_node", "from_node", "node_id", "tool_name", "command", "path")
    parts: list[str] = []
    for key in priority_keys:
        if key in args:
            value = args[key]
            if isinstance(value, str) and len(value) > 40:
                value = value[:40] + "…"
            parts.append(f"{key}={value!r}")
    if not parts:
        for key, value in list(args.items())[:2]:
            if isinstance(value, str) and len(value) > 40:
                value = value[:40] + "…"
            elif isinstance(value, (dict, list)):
                value = f"<{type(value).__name__} len={len(value)}>"
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _extract_evidence(react_trace: list[dict]) -> list[dict]:
    """从 trace 中抽取所有失败的工具调用作为证据条目。"""
    evidence: list[dict] = []
    for iter_trace in react_trace:
        if not isinstance(iter_trace, dict):
            continue
        iteration = int(iter_trace.get("iteration", 0) or 0)
        calls = iter_trace.get("tool_calls") or []
        results_by_id: dict[str, dict] = {}
        for result in (iter_trace.get("tool_results") or []):
            if isinstance(result, dict):
                rid = result.get("tool_use_id") or result.get("id") or ""
                if rid:
                    results_by_id[rid] = result
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool_id = call.get("id") or ""
            result = results_by_id.get(tool_id, {}) if tool_id else {}
            is_error = bool(result.get("is_error"))
            result_content = str(result.get("result_content") or "")
            if not _is_error_entry(is_error, result_content):
                continue
            args = call.get("input") or {}
            # args_raw_truncated: 完整 JSON 截断版本，用于复盘 LLM 实际传参
            # （args_summary 只截关键字段，无法判断 LLM 是否漏传 task_chain_id 等）。
            try:
                import json as _json
                args_raw = _json.dumps(args, ensure_ascii=False, default=str)
            except Exception:
                args_raw = str(args)
            if len(args_raw) > 1024:
                args_raw = args_raw[:1024] + "…"
            evidence.append({
                "iter": iteration,
                "tool": str(call.get("name") or ""),
                "args_summary": _summarize_args(args),
                "args_raw_truncated": args_raw,
                "error": result_content[:EVIDENCE_ERROR_MAX],
            })
    return evidence


def _has_successful_chain_relay(react_trace: list[dict]) -> bool:
    """检测 trace 中是否有"成功的 propagate_chain 接力发送"。

    判定：tool_calls 里存在 name=org_send_message 且 input.propagate_chain=True，
    对应 tool_results 不是 error 且不含失败 marker。这是 LLM 在 delegate 自指
    误判后走 send_message 兜底出口的信号；命中即可对 self_delegate 类
    诊断做豁免，避免给用户报"被强制终止"的硬错误。
    """
    if not react_trace:
        return False
    for iter_trace in react_trace:
        if not isinstance(iter_trace, dict):
            continue
        calls = iter_trace.get("tool_calls") or []
        results_by_id: dict[str, dict] = {}
        for r in (iter_trace.get("tool_results") or []):
            if isinstance(r, dict):
                rid = r.get("tool_use_id") or r.get("id") or ""
                if rid:
                    results_by_id[rid] = r
        for call in calls:
            if not isinstance(call, dict):
                continue
            if str(call.get("name") or "") != "org_send_message":
                continue
            inp = call.get("input") or {}
            if not isinstance(inp, dict):
                continue
            if not bool(inp.get("propagate_chain")):
                continue
            tool_id = call.get("id") or ""
            res = results_by_id.get(tool_id, {}) if tool_id else {}
            res_text = str(res.get("result_content") or "")
            if _is_error_entry(bool(res.get("is_error")), res_text):
                continue
            return True
    return False


def _has_accepted_child_signal(react_trace: list[dict]) -> bool:
    """检测 trace 中是否有「成功验收下属交付」的痕迹。

    判定：tool_calls 里存在 name=org_accept_deliverable 且对应 tool_results
    没有 is_error 也没有失败 marker。命中后 _pick_root_cause 把
    verify_incomplete 切成 verify_incomplete_with_children 软提示模板，
    避免对已通过下属交付完成的协调者节点误报硬错。
    """
    if not react_trace:
        return False
    for iter_trace in react_trace:
        if not isinstance(iter_trace, dict):
            continue
        calls = iter_trace.get("tool_calls") or []
        results_by_id: dict[str, dict] = {}
        for r in (iter_trace.get("tool_results") or []):
            if isinstance(r, dict):
                rid = r.get("tool_use_id") or r.get("id") or ""
                if rid:
                    results_by_id[rid] = r
        for call in calls:
            if not isinstance(call, dict):
                continue
            if str(call.get("name") or "") != "org_accept_deliverable":
                continue
            tool_id = call.get("id") or ""
            res = results_by_id.get(tool_id, {}) if tool_id else {}
            res_text = str(res.get("result_content") or "")
            if _is_error_entry(bool(res.get("is_error")), res_text):
                continue
            return True
    return False


def _classify_delegate_subtype(evidence: list[dict]) -> str | None:
    """死循环场景里，再细分 org_delegate_task 的失败子类型。"""
    delegate_fails = [e for e in evidence if e.get("tool") == "org_delegate_task"]
    if len(delegate_fails) < 3:
        return None
    self_delegation = sum(
        1 for e in delegate_fails
        if any(m in e["error"] for m in _SELF_DELEGATE_MARKERS)
    )
    if self_delegation >= 3:
        return "org_delegate_self"
    non_direct = sum(
        1 for e in delegate_fails
        if any(m in e["error"] for m in _NON_DIRECT_MARKERS)
    )
    if non_direct >= 3:
        return "non_direct_subordinate"
    target_miss = sum(
        1 for e in delegate_fails
        if all(m in e["error"] for m in _TARGET_NOT_EXIST_MARKERS)
    )
    if target_miss >= 3:
        return "delegate_target_not_exist"
    return "org_delegate_loop"


# root_cause -> (headline 模板, suggestion 文案)
# headline 使用 str.format()；预设占位符: tool / iterations / exit_reason
_DIAGNOSIS_TEMPLATES: dict[str, dict[str, str]] = {
    "org_delegate_self": {
        "headline": "节点连续 {iterations} 次把任务委派给了自己，被系统判定为死循环并强制终止",
        "suggestion": (
            "最常见原因是 LLM 把'自己的角色'（例如 CPO=产品总监）和"
            "'下级角色名'（例如 产品经理=pm）搞混。\n\n"
            "**建议**：\n"
            "1. 在指令里直接使用下级的节点 id（例如 `pm`）而不是中文职位名；\n"
            "2. 或者让当前节点使用 `org_submit_deliverable` 亲自完成并交付；\n"
            "3. 长期可调整该节点的 prompt，明确区分'我是谁'和'我的下级是谁'。"
        ),
    },
    "org_delegate_self_recovered": {
        "headline": "节点遇到 {iterations} 次 delegate 自指误判，已通过 send_message 接力把任务派下去",
        "suggestion": (
            "本次 LLM 自动改用 `org_send_message(propagate_chain=true)` 把当前 "
            "task_chain_id 接力给下属，任务链未中断。\n\n"
            "**建议（如频繁出现可关注）**：\n"
            "1. 检查节点 prompt 中目标节点 id 是否清晰；\n"
            "2. 排查日志中的 `[delegate-self-misjudge]` ERROR 行获取自指现场。"
        ),
    },
    "non_direct_subordinate": {
        "headline": "节点连续 {iterations} 次尝试委派给非直属下级，被系统强制终止",
        "suggestion": (
            "`org_delegate_task` 只能把任务委派给**直属下级**。\n\n"
            "**建议**：\n"
            "1. 改由目标节点的直属上司来下派任务；\n"
            "2. 或者用 `org_send_message` 做横向协作提醒。"
        ),
    },
    "delegate_target_not_exist": {
        "headline": "节点连续 {iterations} 次委派到不存在的节点，被系统强制终止",
        "suggestion": (
            "目标 `to_node` 在当前组织中找不到。\n\n"
            "**建议**：\n"
            "1. 调用 `org_get_org_chart` 查看当前所有可用节点 id；\n"
            "2. 检查参数是否拼写错误或混用了中文角色名。"
        ),
    },
    "org_delegate_loop": {
        "headline": "org_delegate_task 陷入死循环（{iterations} 次失败尝试），被系统强制终止",
        "suggestion": (
            "**建议**：\n"
            "1. 确认任务是否应该由当前节点自行完成；\n"
            "2. 若是，改用 `org_submit_deliverable` 交付结果；\n"
            "3. 若需要外部协作，用 `org_send_message` 代替。"
        ),
    },
    "loop_detected_generic": {
        "headline": "工具 `{tool}` 被连续调用陷入死循环，被系统强制终止",
        "suggestion": (
            "**建议**：\n"
            "1. 检查该工具的参数是否反复相同；\n"
            "2. 换一个工具或调整策略；\n"
            "3. 若任务已无法继续，直接用自然语言回复用户当前进展。"
        ),
    },
    "max_iterations": {
        "headline": "节点达到最大迭代次数仍未完成任务",
        "suggestion": (
            "**建议**：\n"
            "1. 把目标拆分成更小的子任务分批下发；\n"
            "2. 检查是否有工具反复失败导致迭代被浪费；\n"
            "3. 如确需长任务，可在配置里放宽 `max_iterations` 上限。"
        ),
    },
    "verify_incomplete": {
        "headline": "节点多轮尝试后，任务验证仍判定为未完成",
        "suggestion": (
            "常见原因是只发送了文字回复，没有真正产出要求的文件 / 交付物。\n\n"
            "**建议**：\n"
            "1. 在指令里明确指定输出方式（如 `write_file` / `deliver_artifacts`）；\n"
            "2. 复查 verify 规则是否过于严格。"
        ),
    },
    "verify_incomplete_with_children": {
        "headline": "节点已通过下属交付完成任务，但 verify 仍标记未完成（提示性）",
        "suggestion": (
            "本节点已 `org_accept_deliverable` 验收下属至少 1 项交付，"
            "实际任务通常已完成；verify 提示更像「严格规则未匹配」，可作为参考而非阻断信号。\n\n"
            "**建议**：\n"
            "1. 直接查看下属上交的文件/链接确认结果是否符合预期；\n"
            "2. 如确需进一步动作，向该节点追加一条明确的指令即可。"
        ),
    },
    "unknown": {
        "headline": "任务非正常结束（exit_reason={exit_reason}）",
        "suggestion": (
            "未匹配到典型根因模式。\n\n"
            "**建议**：查看对应的 react_trace JSON 文件（`data/react_traces/<date>/…`）"
            "了解完整推理过程，或把任务描述改得更明确后重试。"
        ),
    },
}


def _pick_root_cause(
    exit_reason: str,
    evidence: list[dict],
    total_iterations: int,
    react_trace: list[dict] | None = None,
) -> tuple[str, dict[str, Any]]:
    """根据 exit_reason + evidence 决定 root_cause 及模板占位参数。"""
    if exit_reason == "loop_terminated":
        subtype = _classify_delegate_subtype(evidence)
        if subtype:
            delegate_fails_n = sum(1 for e in evidence if e.get("tool") == "org_delegate_task")
            # 豁免：若 LLM 已通过 send_message+propagate_chain=true 兜底接力
            # 把任务派下去，self_delegate 的硬错误降级成"已自愈"提示，
            # 避免给用户报"死循环被强制终止"误导。
            if (
                subtype == "org_delegate_self"
                and react_trace
                and _has_successful_chain_relay(react_trace)
            ):
                subtype = "org_delegate_self_recovered"
            return subtype, {
                "iterations": delegate_fails_n,
                "exit_reason": exit_reason,
                "tool": "org_delegate_task",
            }
        top_tool = ""
        if evidence:
            top_tool = Counter(e.get("tool") or "" for e in evidence).most_common(1)[0][0]
        return "loop_detected_generic", {
            "iterations": total_iterations,
            "exit_reason": exit_reason,
            "tool": top_tool or "?",
        }
    if exit_reason == "max_iterations":
        return "max_iterations", {
            "iterations": total_iterations,
            "exit_reason": exit_reason,
            "tool": "",
        }
    if exit_reason == "verify_incomplete":
        # 若 trace 中存在「成功验收下属交付」的痕迹，降级为提示性卡片，
        # 避免对协调者节点（已通过下属交付完成本任务）报硬错。
        if react_trace and _has_accepted_child_signal(react_trace):
            return "verify_incomplete_with_children", {
                "iterations": total_iterations,
                "exit_reason": exit_reason,
                "tool": "",
            }
        return "verify_incomplete", {
            "iterations": total_iterations,
            "exit_reason": exit_reason,
            "tool": "",
        }
    return "unknown", {
        "iterations": total_iterations,
        "exit_reason": exit_reason,
        "tool": "",
    }


def summarize(
    react_trace: list[dict] | None,
    exit_reason: str,
) -> dict[str, Any]:
    """把 ReAct trace + exit_reason 转成给用户看的诊断 payload。"""
    safe_reason = exit_reason or "unknown"
    trace = react_trace or []
    try:
        evidence = _extract_evidence(trace)
        total_iterations = len(trace)
        root_cause, fmt = _pick_root_cause(
            safe_reason, evidence, total_iterations, react_trace=trace,
        )
        template = _DIAGNOSIS_TEMPLATES.get(root_cause) or _DIAGNOSIS_TEMPLATES["unknown"]
        headline = template["headline"].format(**fmt)
        suggestion = template["suggestion"]

        if len(evidence) > MAX_EVIDENCE_ITEMS:
            trimmed = evidence[:MAX_EVIDENCE_ITEMS]
            omitted = len(evidence) - MAX_EVIDENCE_ITEMS
            trimmed.append({
                "iter": 0,
                "tool": "…",
                "args_summary": "",
                "error": f"（还有 {omitted} 条失败记录未展示，请查看完整 react_trace）",
            })
            evidence = trimmed

        return {
            "root_cause": root_cause,
            "headline": headline,
            "evidence": evidence,
            "suggestion": suggestion,
            "exit_reason": safe_reason,
        }
    except Exception as exc:
        logger.debug("[FailureDiagnoser] summarize failed: %s", exc)
        return {
            "root_cause": "unknown",
            "headline": f"任务非正常结束（exit_reason={safe_reason}）",
            "evidence": [],
            "suggestion": "诊断模块遇到异常，建议查看 `data/react_traces/` 下的完整 trace。",
            "exit_reason": safe_reason,
        }


def format_human_summary(diagnosis: dict[str, Any]) -> str:
    """把 diagnosis dict 格式化成一段可塞进 assistant message 的 markdown 文本。

    runtime 在发 WebSocket 事件时可同步把这段写到最终 assistant 气泡，
    保证用户即使收起时间线也能看到结论。
    """
    if not isinstance(diagnosis, dict):
        return ""
    headline = diagnosis.get("headline") or "任务未正常完成"
    suggestion = diagnosis.get("suggestion") or ""
    evidence = diagnosis.get("evidence") or []

    lines = [f"> **为什么失败**：{headline}"]
    if evidence:
        lines.append(">")
        lines.append("> **关键动作**：")
        for item in evidence[:MAX_EVIDENCE_ITEMS]:
            iter_n = item.get("iter") or "?"
            tool = item.get("tool") or "?"
            args = item.get("args_summary") or ""
            err = (item.get("error") or "").replace("\n", " ").strip()
            if len(err) > 120:
                err = err[:120] + "…"
            args_part = f"({args})" if args else ""
            lines.append(f"> - 第 {iter_n} 轮 `{tool}`{args_part} → {err}")
    if suggestion:
        lines.append(">")
        for sline in suggestion.splitlines():
            lines.append(f"> {sline}" if sline else ">")
    return "\n".join(lines)
