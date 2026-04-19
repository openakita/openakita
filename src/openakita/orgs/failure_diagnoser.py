"""
Failure diagnoser —— translates ReAct trace + exit_reason into a user-facing
"root cause + suggestion" summary.

Responsibility boundaries:
- Pure analytical function: no file writes, no events emitted, no I/O dependencies
- Only produces a dict; whether to send it to the frontend is decided by runtime.py
- Kept separate from openakita.evolution.failure_analysis: that module persists
  structured data for the harness/training pipeline, while this module only
  concerns itself with "plain-language summary + evidence snippets + next-step
  suggestion". The two have non-overlapping responsibilities.

Output shape:
    {
        "root_cause": str,        # Category code (stable string, for frontend styling/metrics)
        "headline": str,          # One-sentence plain-language headline
        "evidence": list[dict],   # [{iter, tool, args_summary, error}, ...]
        "suggestion": str,        # Next-step suggestion for the user (multi-line, markdown-compatible)
        "exit_reason": str,       # Passes through reasoning_engine._last_exit_reason
    }

Never raises: falls back to root_cause="unknown" if analysis fails.
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
    """The `is_error` field is sometimes missed; re-scan the text as a fallback to detect failures."""
    if is_error_flag:
        return True
    if not result_content:
        return False
    return any(m in result_content for m in _GENERIC_FAIL_MARKERS)


def _summarize_args(args: Any) -> str:
    """Compress tool args into a one-line summary, prioritizing org-orchestration-related key fields."""
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
    """Extract all failed tool calls from the trace as evidence entries."""
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


def _classify_delegate_subtype(evidence: list[dict]) -> str | None:
    """In infinite-loop scenarios, further classify the failure subtype of org_delegate_task."""
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


# root_cause -> (headline template, suggestion text)
# headline uses str.format(); preset placeholders: tool / iterations / exit_reason
_DIAGNOSIS_TEMPLATES: dict[str, dict[str, str]] = {
    "org_delegate_self": {
        "headline": "The node delegated the task to itself {iterations} times in a row, was detected as an infinite loop and force-terminated",
        "suggestion": (
            "The most common cause is that the LLM confused 'its own role' (e.g., CPO = Chief Product Officer) "
            "with a 'subordinate role name' (e.g., Product Manager = pm).\n\n"
            "**Suggestions**:\n"
            "1. In the instruction, use the subordinate's node id directly (e.g., `pm`) instead of a Chinese title;\n"
            "2. Or have the current node use `org_submit_deliverable` to complete and deliver the work itself;\n"
            "3. Longer term, adjust the node's prompt to clearly distinguish 'who I am' from 'who my subordinates are'."
        ),
    },
    "non_direct_subordinate": {
        "headline": "The node tried {iterations} times in a row to delegate to a non-direct subordinate and was force-terminated",
        "suggestion": (
            "`org_delegate_task` can only delegate tasks to **direct subordinates**.\n\n"
            "**Suggestions**:\n"
            "1. Have the target node's direct superior delegate the task instead;\n"
            "2. Or use `org_send_message` for lateral-collaboration notifications."
        ),
    },
    "delegate_target_not_exist": {
        "headline": "The node delegated to a non-existent node {iterations} times in a row and was force-terminated",
        "suggestion": (
            "The target `to_node` cannot be found in the current organization.\n\n"
            "**Suggestions**:\n"
            "1. Call `org_get_org_chart` to view all currently available node ids;\n"
            "2. Check for typos in the parameter or accidental use of a Chinese role name."
        ),
    },
    "org_delegate_loop": {
        "headline": "org_delegate_task entered an infinite loop ({iterations} failed attempts) and was force-terminated",
        "suggestion": (
            "**Suggestions**:\n"
            "1. Check whether the task should have been completed by the current node itself;\n"
            "2. If so, use `org_submit_deliverable` to deliver the result;\n"
            "3. If external collaboration is needed, use `org_send_message` instead."
        ),
    },
    "loop_detected_generic": {
        "headline": "Tool `{tool}` was called repeatedly in an infinite loop and was force-terminated",
        "suggestion": (
            "**Suggestions**:\n"
            "1. Check whether the tool arguments are repeating identically;\n"
            "2. Switch to a different tool or adjust the strategy;\n"
            "3. If the task can no longer proceed, reply to the user in natural language with the current progress."
        ),
    },
    "max_iterations": {
        "headline": "The node hit the maximum number of iterations without completing the task",
        "suggestion": (
            "**Suggestions**:\n"
            "1. Break the goal into smaller subtasks and dispatch them in batches;\n"
            "2. Check whether any tool is failing repeatedly and wasting iterations;\n"
            "3. For genuinely long tasks, raise the `max_iterations` limit in the config."
        ),
    },
    "verify_incomplete": {
        "headline": "After multiple attempts, the node's task was still judged as incomplete by verification",
        "suggestion": (
            "A common cause is only sending a text reply without actually producing the required file / deliverable.\n\n"
            "**Suggestions**:\n"
            "1. Explicitly specify the output method in the instruction (e.g., `write_file` / `deliver_artifacts`);\n"
            "2. Review whether the verify rules are too strict."
        ),
    },
    "unknown": {
        "headline": "Task ended abnormally (exit_reason={exit_reason})",
        "suggestion": (
            "No typical root-cause pattern matched.\n\n"
            "**Suggestion**: inspect the corresponding react_trace JSON file (`data/react_traces/<date>/…`) "
            "to see the full reasoning process, or rewrite the task description more clearly and retry."
        ),
    },
}


def _pick_root_cause(
    exit_reason: str,
    evidence: list[dict],
    total_iterations: int,
) -> tuple[str, dict[str, Any]]:
    """Determine root_cause and template placeholder args based on exit_reason + evidence."""
    if exit_reason == "loop_terminated":
        subtype = _classify_delegate_subtype(evidence)
        if subtype:
            delegate_fails_n = sum(1 for e in evidence if e.get("tool") == "org_delegate_task")
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
    """Convert ReAct trace + exit_reason into a diagnosis payload shown to the user."""
    safe_reason = exit_reason or "unknown"
    trace = react_trace or []
    try:
        evidence = _extract_evidence(trace)
        total_iterations = len(trace)
        root_cause, fmt = _pick_root_cause(safe_reason, evidence, total_iterations)
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
                "error": f"({omitted} more failure records not shown; see the full react_trace)",
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
            "headline": f"Task ended abnormally (exit_reason={safe_reason})",
            "evidence": [],
            "suggestion": "The diagnosis module encountered an exception; inspect the full trace under `data/react_traces/`.",
            "exit_reason": safe_reason,
        }


def format_human_summary(diagnosis: dict[str, Any]) -> str:
    """Format the diagnosis dict into a markdown block suitable for inclusion in an assistant message.

    When runtime emits the WebSocket event, it can also write this block into the
    final assistant bubble so the user still sees the conclusion even if the
    timeline is collapsed.
    """
    if not isinstance(diagnosis, dict):
        return ""
    headline = diagnosis.get("headline") or "Task did not complete normally"
    suggestion = diagnosis.get("suggestion") or ""
    evidence = diagnosis.get("evidence") or []

    lines = [f"> **Why it failed**: {headline}"]
    if evidence:
        lines.append(">")
        lines.append("> **Key actions**:")
        for item in evidence[:MAX_EVIDENCE_ITEMS]:
            iter_n = item.get("iter") or "?"
            tool = item.get("tool") or "?"
            args = item.get("args_summary") or ""
            err = (item.get("error") or "").replace("\n", " ").strip()
            if len(err) > 120:
                err = err[:120] + "…"
            args_part = f"({args})" if args else ""
            lines.append(f"> - Iteration {iter_n} `{tool}`{args_part} → {err}")
    if suggestion:
        lines.append(">")
        for sline in suggestion.splitlines():
            lines.append(f"> {sline}" if sline else ">")
    return "\n".join(lines)
