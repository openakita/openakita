"""Session-scoped short-term facts.

These facts are lightweight and current-conversation only. They protect recent
explicit facts from being diluted by long-term memory or identity text.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_FACT_PATTERNS = [
    ("test_code", re.compile(r"(?:测试代号|测试代码|代号)\s*(?:是|为|=|:)\s*([A-Za-z0-9_.-]{2,64})")),
    ("temporary_name", re.compile(r"(?:本轮|这次|当前)?(?:临时)?(?:名称|名字)\s*(?:是|为|=|:)\s*([A-Za-z0-9_.\-\u4e00-\u9fff]{2,64})")),
]


def extract_working_facts(message: str, *, source_turn: int = 0) -> dict[str, dict[str, Any]]:
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


def merge_working_facts(existing: dict[str, Any] | None, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in updates.items():
        merged[key] = value
    return merged


def format_working_facts(facts: dict[str, Any] | None) -> str:
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
