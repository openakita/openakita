"""Session-scoped short-term working facts.

Ported from ``openakita.agent.working_facts`` per ADR-0003 and the
Phase 2 sub-commit plan in ``docs/revamp/core_audit.md``. The module
is intentionally tiny and side-effect-free: extract user-asserted
facts (a "test code", a temporary name, ...) from a single message
and merge / render them.

Why these facts deserve their own module: long-term memory and
identity text both compete for room in the prompt, and either of them
will happily dilute a fact the user just gave us five seconds ago.
:func:`format_working_facts` puts these in a high-priority section so
the prompt builder can place them ahead of long-term memory.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

__all__ = [
    "extract_working_facts",
    "format_working_facts",
    "merge_working_facts",
]


_FACT_PATTERNS = [
    (
        "test_code",
        re.compile(r"(?:测试代号|测试代码|代号)\s*(?:是|为|=|:)\s*([A-Za-z0-9_.-]{2,64})"),
    ),
    (
        "temporary_name",
        re.compile(
            r"(?:本轮|这次|当前)?(?:临时)?(?:名称|名字)\s*(?:是|为|=|:)\s*"
            r"([A-Za-z0-9_.\-\u4e00-\u9fff]{2,64})"
        ),
    ),
]


def extract_working_facts(message: str, *, source_turn: int = 0) -> dict[str, dict[str, Any]]:
    """Pull short-term working facts out of a single user message.

    Returns ``{key: {value, source_turn, updated_at}}`` for every
    matched pattern. An empty message returns an empty dict; trailing
    Chinese / English punctuation is stripped from values.
    """
    text = (message or "").strip()
    facts: dict[str, dict[str, Any]] = {}
    if not text:
        return facts
    for key, pattern in _FACT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(1).strip(" ，。,.；;")
        if not value:
            continue
        facts[key] = {
            "value": value,
            "source_turn": source_turn,
            "updated_at": datetime.now().isoformat(),
        }
    return facts


def merge_working_facts(
    existing: dict[str, Any] | None,
    updates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Last-write-wins merge of new facts into the existing dict."""
    merged = dict(existing or {})
    for key, value in updates.items():
        merged[key] = value
    return merged


def format_working_facts(facts: dict[str, Any] | None) -> str:
    """Render working facts as a high-priority prompt section.

    Returns an empty string when ``facts`` is empty so the prompt
    builder can omit the section entirely. Each fact line shows
    ``- key: value (source_turn=N)`` when a turn id is present.
    """
    if not facts:
        return ""
    lines = [
        "## Session Working Facts",
        "这些是当前会话中用户明确给出的短期事实，优先级高于长期记忆和身份提示。",
    ]
    for key, payload in sorted(facts.items()):
        if isinstance(payload, dict):
            value = payload.get("value", "")
            source = payload.get("source_turn", "")
        else:
            value = payload
            source = ""
        if value:
            suffix = f" (source_turn={source})" if source != "" else ""
            lines.append(f"- {key}: {value}{suffix}")
    return "\n".join(lines)
