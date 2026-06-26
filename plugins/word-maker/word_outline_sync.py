"""Outline JSON helpers: source-driven outlines and field sync for templates."""

from __future__ import annotations

import re
from typing import Any

_SUMMARY_KEYS = frozenset({"summary", "content", "body", "description", "report_summary", "abstract"})
_MAX_SUMMARY_CHARS = 4000
_DEFAULT_BRIEF_SUMMARY_CHARS = 220
_BRIEF_SUMMARY_CTX_RE = re.compile(r"(\d{2,3})\s*[-–~至到]\s*(\d{2,3})\s*字")
_FIELD_DEDUPE_PRIORITY = (
    "title",
    "doc_title",
    "document_title",
    "meeting_info",
    "summary",
    "conclusions",
    "topic_sections",
    "risks",
    "risk",
    "action_items",
)


def _non_empty(value: Any) -> bool:
    return bool(str(value or "").strip())


def outline_brief_summary(
    outline: dict[str, Any],
    *,
    max_chars: int = _DEFAULT_BRIEF_SUMMARY_CHARS,
    var_context: str = "",
) -> str:
    """Short overview for summary fields (not full outline dump)."""
    ctx_match = _BRIEF_SUMMARY_CTX_RE.search(var_context or "")
    if ctx_match:
        max_chars = min(max_chars, int(ctx_match.group(2)))
    title = str(outline.get("title") or "").strip()
    bullets: list[str] = []
    for section in outline.get("sections") or []:
        if not isinstance(section, dict):
            continue
        sec_title = str(section.get("title") or "")
        if _title_contains_keyword(sec_title, _keywords_for_var("action_items")):
            continue
        if _title_contains_keyword(sec_title, _keywords_for_var("conclusions")):
            continue
        for bullet in section.get("bullets") or []:
            line = str(bullet or "").strip()
            if line:
                bullets.append(line)
            if len(bullets) >= 2:
                break
        if len(bullets) >= 2:
            break
    parts = [p for p in [title, *bullets[:2]] if p]
    if not parts:
        return outline_summary_text(outline, max_chars=max_chars)
    text = "。".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def outline_summary_text(outline: dict[str, Any], *, max_chars: int = _MAX_SUMMARY_CHARS) -> str:
    chunks: list[str] = []
    for section in outline.get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        goal = str(section.get("goal") or "").strip()
        if title:
            chunks.append(title)
        if goal:
            chunks.append(goal)
        for bullet in section.get("bullets") or []:
            line = str(bullet or "").strip()
            if line:
                chunks.append(line)
    text = "\n".join(chunks).strip()
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def merge_outline_into_fields(
    outline: dict[str, Any],
    fields: dict[str, Any] | None,
    *,
    template_variables: list[str] | None = None,
    var_contexts: dict[str, str] | None = None,
    sources_text: str = "",
    requirement: str = "",
) -> dict[str, Any]:
    """Merge outline into template fields without overwriting user-filled values."""
    merged = dict(fields or {})
    title = str(outline.get("title") or "").strip()
    if title and not _non_empty(merged.get("title")):
        merged["title"] = title

    full_summary = outline_summary_text(outline)
    if not full_summary:
        return merged

    vars_set = set(template_variables or [])
    targets = list(_SUMMARY_KEYS & vars_set) if vars_set else list(_SUMMARY_KEYS)
    if vars_set:
        for name in vars_set:
            if name not in merged and name not in targets and name not in ("title",):
                if any(token in name.lower() for token in ("summary", "content", "body", "desc")):
                    targets.append(name)

    var_ctx = var_contexts or {}
    combined_sources = sources_text
    if requirement.strip():
        combined_sources = (combined_sources + "\n" + requirement).strip()
    for key in targets:
        if _non_empty(merged.get(key)):
            continue
        ctx = var_ctx.get(key, "")
        section_hint = _context_has_section_hint(ctx)
        if section_hint:
            value = _extract_structured_field(
                key, outline, sources_text=combined_sources, keywords=[section_hint]
            )
            if value:
                merged[key] = value
                continue
        low = key.lower()
        if low in _SUMMARY_KEYS or low == "summary":
            merged[key] = outline_brief_summary(outline, var_context=ctx) or full_summary
        else:
            merged[key] = full_summary

    if not vars_set and not _non_empty(merged.get("summary")):
        merged["summary"] = outline_brief_summary(outline) or full_summary

    if template_variables:
        field_sources: dict[str, str] = {}
        _sync_structured_fields(
            template_variables,
            merged,
            field_sources,
            outline,
            sources_text=combined_sources,
        )
        dedupe_template_fields(merged, template_variables)
    return merged


def _split_source_blocks(sources_text: str) -> list[tuple[str, str]]:
    if not sources_text.strip():
        return []
    blocks: list[tuple[str, str]] = []
    current_label = "资料"
    current_lines: list[str] = []
    for line in sources_text.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_lines:
                blocks.append((current_label, "\n".join(current_lines).strip()))
            current_label = line.strip("- ").strip() or "资料"
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        blocks.append((current_label, "\n".join(current_lines).strip()))
    if not blocks and sources_text.strip():
        parts = [part.strip() for part in sources_text.split("\n\n") if part.strip()]
        if len(parts) > 1:
            for index, part in enumerate(parts):
                first = part.splitlines()[0].strip()[:80] if part else f"资料 {index + 1}"
                blocks.append((first or f"资料 {index + 1}", part))
        else:
            blocks.append(("资料", sources_text.strip()))
    return blocks


def _bullets_from_text(text: str, *, limit: int = 8) -> list[str]:
    bullets: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[-*#>\d.]+\s*", "", line).strip()
        if len(line) < 2:
            continue
        bullets.append(line[:500])
        if len(bullets) >= limit:
            break
    if not bullets and text.strip():
        bullets.append(text.strip()[:500])
    return bullets


def build_outline_from_sources(
    sources_text: str,
    requirement: str = "",
    *,
    title: str = "",
) -> dict[str, Any]:
    """Rule-based outline when Brain is unavailable."""
    blocks = _split_source_blocks(sources_text)
    sections: list[dict[str, Any]] = []
    missing: list[str] = []

    if not blocks:
        missing.append("sources_text")
        sections.append(
            {
                "id": "main",
                "title": "正文",
                "goal": requirement or "根据需求整理内容",
                "bullets": _bullets_from_text(requirement) if requirement else [],
            }
        )
    else:
        for index, (label, body) in enumerate(blocks):
            inner = _parse_markdown_sections(body)
            if len(inner) > 1:
                for sub_index, (sub_title, sub_body) in enumerate(inner):
                    title = sub_title or label
                    section_id = re.sub(r"[^a-zA-Z0-9_]+", "_", title.lower()).strip("_") or f"section_{index}_{sub_index}"
                    sections.append(
                        {
                            "id": section_id[:40] or f"section_{index}_{sub_index}",
                            "title": title,
                            "goal": f"整理「{title}」要点",
                            "bullets": _bullets_from_text(sub_body),
                        }
                    )
            else:
                section_id = re.sub(r"[^a-zA-Z0-9_]+", "_", label.lower()).strip("_") or f"section_{index + 1}"
                sections.append(
                    {
                        "id": section_id[:40] or f"section_{index + 1}",
                        "title": label,
                        "goal": f"整理来自「{label}」的要点",
                        "bullets": _bullets_from_text(body),
                    }
                )

    doc_title = title.strip() or (sections[0]["title"] if sections else "文档初稿")
    if requirement and not missing:
        missing.append("user_requirements_review")

    return {
        "title": doc_title,
        "sections": sections,
        "missing_inputs": missing,
    }


_DATE_RE = re.compile(
    r"(\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?)"
    r"|(\d{1,2}月\d{1,2}日)"
)
_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}(?:\s*[-–~至到]\s*\d{1,2}:\d{2})?)"
    r"|((?:上午|下午|晚上)?\d{1,2}[点时]\d{0,2}分?)"
)
_DATE_FIELD_TOKENS = frozenset({"date", "meeting_date", "start_date", "due_date", "finish_date", "end_date"})
_TIME_FIELD_TOKENS = frozenset({"time", "meeting_time", "time_range", "start_time", "end_time"})
_PERSON_FIELD_TOKENS = frozenset({
    "participants", "host", "recorder", "owner", "assignee", "task_owner",
    "person_name", "absent", "partner_name",
})
_LOCATION_FIELD_TOKENS = frozenset({"location", "venue", "meeting_location"})
_LIST_FIELD_TOKENS = frozenset({
    "action_items", "next_step", "next_steps", "discussion", "discussion_points",
    "pending_questions", "blocker", "risk", "risks", "todo", "conclusions",
    "next_meeting_focus", "topic_sections", "meeting_info",
    "highlights", "metrics", "next_week_plan", "deliverables", "open_issues",
    "findings", "recommendations", "implementation_plan", "benefits",
    "roles", "prerequisites", "procedure_steps", "exceptions",
})
_STANDALONE_SECTION_TITLES = frozenset({
    "会议主题",
    "会议结论",
    "分主题整理",
    "会后待办清单",
    "会议信息",
    "关键问题与风险",
    "下次会议关注点",
    "本周摘要",
    "关键进展",
    "关键指标",
    "下周计划",
    "交付成果",
    "遗留问题",
    "验收结论",
    "调研背景",
    "主要发现",
    "操作步骤",
    "角色职责",
})

_SEMANTIC_VAR_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("conclusion", "conclusions", "core"), ["会议结论", "核心结论", "主要结论", "结论", "验收结论", "建设目标", "交付成果"]),
    (("discussion", "topic", "topics", "by_topic", "theme", "subject", "section"), ["分主题", "主题", "讨论", "议题", "整理"]),
    (("risk", "risks", "blocker", "issue", "issues", "open_issues"), ["风险", "问题", "卡点", "遗留"]),
    (("action", "todo", "follow_up", "assign"), ["会后待办", "待办清单", "待办事项", "行动项", "待办"]),
    (("next_meeting", "next_meeting", "followup", "follow_up"), ["下次会议", "下次", "后续", "关注点"]),
    (("meeting_info", "meeting_meta"), ["会议信息", "会议时间", "会议日期", "会议地点", "参会"]),
    (("highlight", "highlights", "progress"), ["关键进展", "亮点", "进展", "完成"]),
    (("metric", "metrics", "kpi"), ["指标", "KPI", "数据", "达成"]),
    (("next_week", "next_week_plan"), ["下周", "下周计划", "下周工作"]),
    (("report_period", "period"), ["周期", "报告周期", "本周", "本周期"]),
    (("deliverable", "deliverables", "delivery"), ["交付", "成果", "已交付"]),
    (("background",), ["背景", "项目背景", "调研背景"]),
    (("objective", "goal"), ["目标", "建设目标"]),
    (("solution",), ["方案", "解决方案"]),
    (("implementation", "implementation_plan"), ["实施", "计划", "里程碑"]),
    (("benefit", "benefits"), ["收益", "价值", "预期"]),
    (("methodology", "method"), ["方法", "调研方法"]),
    (("finding", "findings"), ["发现", "主要发现"]),
    (("recommendation", "recommendations"), ["建议", "推荐"]),
    (("purpose",), ["目的", "SOP目的"]),
    (("scope",), ["范围", "适用范围"]),
    (("role", "roles"), ["角色", "职责", "分工"]),
    (("prerequisite", "prerequisites"), ["前置", "前置条件"]),
    (("procedure", "procedure_steps", "step"), ["操作步骤", "步骤", "流程"]),
    (("exception", "exceptions"), ["异常", "异常处理"]),
    (("revision", "revision_info"), ["修订", "版本"]),
    (("company", "company_name", "client"), ["客户", "公司", "单位"]),
    (("project_name", "project"), ["项目", "项目名称"]),
]
_SECTION_HEADER_RE = re.compile(
    r"^(?:#{1,4}\s+|[\d一二三四五六七八九十百千]+[、.．)\s]+)(.+?)\s*$"
)


def _keywords_for_var(var_name: str) -> list[str]:
    from word_template_convert import VAR_TO_ZH

    low = var_name.lower()
    keywords: list[str] = []
    for tokens, kws in _SEMANTIC_VAR_RULES:
        if any(token in low for token in tokens):
            keywords.extend(kws)
    keywords.extend(VAR_TO_ZH.get(low, []))
    return list(dict.fromkeys(keywords))


def _title_contains_keyword(title: str, keywords: list[str]) -> bool:
    """True when a keyword appears inside the section title (not the reverse)."""
    if not title or not keywords:
        return False
    normalized = title.strip().lower()
    for kw in keywords:
        k = kw.strip().lower()
        if k and k in normalized:
            return True
    return False


def _title_matches_keywords(title: str, keywords: list[str]) -> bool:
    if not title or not keywords:
        return False
    if _title_contains_keyword(title, keywords):
        return True
    normalized = title.strip().lower()
    for kw in keywords:
        k = kw.strip().lower()
        if k and len(normalized) >= 2 and normalized in k:
            return True
    return False


def _section_to_text(section: dict[str, Any]) -> str:
    lines: list[str] = []
    goal = str(section.get("goal") or "").strip()
    if goal:
        lines.append(goal)
    for bullet in section.get("bullets") or []:
        line = str(bullet or "").strip()
        if line:
            lines.append(f"- {line}" if not line.startswith(("-", "•", "*")) else line)
    return "\n".join(lines).strip()


def _fallback_list_field_from_outline(var_name: str, outline: dict[str, Any] | None) -> str:
    """Map generic outline sections into list-style template fields when strict keyword match fails."""
    sections = [s for s in (outline or {}).get("sections") or [] if isinstance(s, dict)]
    if not sections:
        return ""
    low = var_name.lower()

    def _match(title: str, tokens: tuple[str, ...]) -> bool:
        return any(token in title for token in tokens)

    if low in {"conclusions", "conclusion"}:
        for sec in sections:
            title = str(sec.get("title") or "")
            if _match(title, ("结论", "目标", "成果", "交付", "建议", "要点")):
                text = _section_to_text(sec)
                if text:
                    return text
        for sec in reversed(sections):
            text = _section_to_text(sec)
            if text:
                return text
    elif low in {"risks", "risk", "open_issues"}:
        for sec in sections:
            title = str(sec.get("title") or "")
            if _match(title, ("风险", "问题", "遗留", "卡点", "障碍")):
                text = _section_to_text(sec)
                if text:
                    return text
    elif low in {"action_items", "next_week_plan", "next_steps", "next_meeting_focus"}:
        for sec in sections:
            title = str(sec.get("title") or "")
            if _match(title, ("待办", "行动", "计划", "下一步", "任务", "跟进", "关注")):
                text = _section_to_text(sec)
                if text:
                    return text
    elif low in {"highlights", "deliverables", "findings"}:
        for sec in sections:
            title = str(sec.get("title") or "")
            if _match(title, ("进展", "亮点", "交付", "成果", "发现", "完成")):
                text = _section_to_text(sec)
                if text:
                    return text
    return ""


def _format_source_body(body: str) -> str:
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        line = re.sub(r"^[-*#>\d.]+\s*", "", line).strip()
        if len(line) >= 2:
            lines.append(f"- {line}" if not line.startswith(("-", "•")) else line)
    return "\n".join(lines).strip()[:4000]


def _is_standalone_section_title(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return False
    if stripped in _STANDALONE_SECTION_TITLES:
        return True
    for title in _STANDALONE_SECTION_TITLES:
        if stripped == title or stripped.startswith(title):
            return True
    return False


def _parse_tabular_action_items(text: str) -> str:
    """Parse TSV/table under 会后待办清单 into bullet lines."""
    if not text.strip():
        return ""
    lines = text.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if "会后待办" in line and "清单" in line:
            start = idx + 1
            break
    if start < 0:
        return ""

    rows: list[str] = []
    header_skipped = False
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if rows:
                break
            continue
        if _is_standalone_section_title(stripped) or _SECTION_HEADER_RE.match(stripped):
            break
        if not header_skipped and ("任务描述" in stripped or "任务执行人" in stripped):
            header_skipped = True
            continue
        cells = [c.strip() for c in re.split(r"\t+", stripped) if c.strip()]
        if len(cells) < 2:
            cells = [c.strip() for c in re.split(r"\s{2,}", stripped) if c.strip()]
        if len(cells) < 2:
            if stripped.startswith(("-", "•", "*")) or re.match(r"^\d+[.、]", stripped):
                rows.append(stripped.lstrip("-•* ").strip())
            continue
        task = cells[0]
        owner = cells[1] if len(cells) > 1 else ""
        status = cells[2] if len(cells) > 2 else ""
        due = cells[3] if len(cells) > 3 else ""
        parts = [p for p in [owner, status, due] if p]
        suffix = f"（{'，'.join(parts)}）" if parts else ""
        rows.append(f"- {task}{suffix}")
    return "\n".join(rows).strip()[:4000]


def _parse_markdown_sections(text: str) -> list[tuple[str, str]]:
    if not text.strip():
        return []
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            if stripped.startswith("---") and current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = stripped.strip("- ").strip() or current_title
                current_lines = []
            continue
        if _is_standalone_section_title(stripped):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = stripped
            current_lines = []
            continue
        if re.match(r"^\d+[.、．]\s*\S", stripped):
            current_lines.append(line)
            continue
        match = _SECTION_HEADER_RE.match(stripped)
        if match and len(match.group(1)) <= 60:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return sections


def _extract_structured_field(
    var_name: str,
    outline: dict[str, Any] | None,
    *,
    sources_text: str = "",
    keywords: list[str] | None = None,
) -> str:
    """Match outline sections or source headings to a template variable."""
    kws = keywords or _keywords_for_var(var_name)
    if not kws:
        return ""

    for section in (outline or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "")
        if _title_matches_keywords(title, kws):
            text = _section_to_text(section)
            if text:
                return text
        blob = " ".join(
            [
                title,
                str(section.get("goal") or ""),
                " ".join(str(b) for b in (section.get("bullets") or [])),
            ]
        )
        if _title_matches_keywords(blob, kws):
            text = _section_to_text(section)
            if text:
                return text

    for sec_title, body in _parse_markdown_sections(sources_text):
        if _title_matches_keywords(sec_title, kws):
            formatted = _format_source_body(body)
            if formatted:
                return formatted

    for sec_title, body in _parse_bracket_sections(sources_text):
        if _title_matches_keywords(sec_title, kws):
            formatted = _format_source_body(body)
            if formatted:
                return formatted

    from word_template_convert import find_label_value_in_text

    block = find_label_value_in_text(sources_text, kws)
    if block and len(block) > 15:
        return block[:4000]

    return ""


def _extract_meeting_info(combined_text: str, merged: dict[str, Any]) -> str:
    from word_template_convert import find_label_value_in_text

    parts: list[str] = []
    labels = [
        ("会议日期", "meeting_date"),
        ("会议时间", "meeting_time"),
        ("会议地点", "location"),
        ("参会人员", "participants"),
        ("主持人", "host"),
        ("记录人", "recorder"),
    ]
    for zh_label, key in labels:
        if _non_empty(merged.get(key)):
            parts.append(f"{zh_label}：{merged[key]}")
            continue
        value = find_label_value_in_text(combined_text, [zh_label])
        if value:
            parts.append(f"{zh_label}：{value}")
    return "\n".join(parts).strip()


def _extract_all_outline_sections(outline: dict[str, Any] | None) -> str:
    chunks: list[str] = []
    for section in (outline or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        body = _section_to_text(section)
        if not body:
            continue
        if title:
            chunks.append(f"【{title}】\n{body}")
        else:
            chunks.append(body)
    return "\n\n".join(chunks).strip()[:4000]


def _sync_structured_fields(
    template_vars: list[str],
    merged: dict[str, Any],
    field_sources: dict[str, str],
    outline: dict[str, Any] | None,
    *,
    sources_text: str = "",
) -> None:
    """Fill section-specific template vars from outline/sources (not only summary)."""
    for name in template_vars:
        if _non_empty(merged.get(name)):
            continue
        low = name.lower()
        if low in {"title", "doc_title", "document_title"}:
            continue
        if low in _DATE_FIELD_TOKENS or low in _TIME_FIELD_TOKENS:
            continue
        if low in _PERSON_FIELD_TOKENS or low in _LOCATION_FIELD_TOKENS:
            continue
        if low == "meeting_info":
            value = _extract_meeting_info(sources_text, merged)
            if value:
                merged[name] = value
                field_sources[name] = "rule"
            continue
        if low == "topic_sections":
            value = _extract_structured_field(name, outline, sources_text=sources_text)
            if not value:
                value = _extract_all_outline_sections(outline)
            if value:
                merged[name] = value
                field_sources[name] = "outline"
            continue
        if low == "action_items":
            table_value = _parse_tabular_action_items(sources_text)
            if table_value:
                merged[name] = table_value
                field_sources[name] = "rule"
                continue
            fallback = _fallback_list_field_from_outline(name, outline)
            if fallback:
                merged[name] = fallback
                field_sources[name] = "outline"
            continue
        kws = _keywords_for_var(name)
        if not kws:
            continue
        value = _extract_structured_field(name, outline, sources_text=sources_text, keywords=kws)
        if value:
            merged[name] = value
            field_sources[name] = "outline" if (outline or {}).get("sections") else "rule"
            continue
        fallback = _fallback_list_field_from_outline(name, outline)
        if fallback:
            merged[name] = fallback
            field_sources[name] = "outline"


def _extract_date(text: str) -> str:
    match = _DATE_RE.search(text)
    return (match.group(0) if match else "").strip()


def _extract_time(text: str) -> str:
    match = _TIME_RE.search(text)
    return (match.group(0) if match else "").strip()


def _extract_person_value(text: str, labels: list[str]) -> str:
    from word_template_convert import find_label_value_in_text
    value = find_label_value_in_text(text, labels)
    if value:
        return value
    person_labels = ["参会人员", "参会", "出席人员", "主持人", "记录人", "负责人", "执行人"]
    return find_label_value_in_text(text, person_labels)


def _extract_location(text: str) -> str:
    from word_template_convert import find_label_value_in_text
    return find_label_value_in_text(text, ["地点", "会议地点", "会议室", "位置", "形式"])


def _extract_list_from_outline(
    outline: dict[str, Any],
    field_name: str,
    *,
    sources_text: str = "",
) -> str:
    return _extract_structured_field(field_name, outline, sources_text=sources_text)


def _context_has_section_hint(var_context: str) -> str | None:
    """Extract section title from a var_context like '位于章节「二. 核心结论」下'."""
    match = re.search(r"位于章节「([^」]+)」", var_context)
    return match.group(1) if match else None


def _normalize_field_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _fields_are_duplicates(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 80 and shorter in longer:
        return True
    if len(a) > 40 and len(b) > 40:
        sa, sb = set(shorter.split()), set(longer.split())
        if sa and len(sa & sb) / len(sa) >= 0.72:
            return True
    return False


def _field_priority(name: str) -> int:
    low = name.lower()
    for idx, token in enumerate(_FIELD_DEDUPE_PRIORITY):
        if low == token or token in low:
            return idx
    return len(_FIELD_DEDUPE_PRIORITY)


_MISSING_FIELD_PLACEHOLDER = "（待补充）"


def fill_missing_template_fields(
    template_vars: list[str],
    fields: dict[str, Any] | None,
    *,
    outline: dict[str, Any] | None = None,
    sources_text: str = "",
    requirement: str = "",
    var_contexts: dict[str, str] | None = None,
    placeholder: str = _MISSING_FIELD_PLACEHOLDER,
) -> dict[str, Any]:
    """Rule-fill still-empty template vars, then use placeholder so render can proceed."""
    merged = dict(fields or {})
    missing = [name for name in template_vars if not _non_empty(merged.get(name))]
    if not missing:
        return merged
    rule_filled, _sources = extract_fields_from_sources(
        missing,
        fields=merged,
        requirement=requirement,
        sources_text=sources_text,
        outline=outline,
        var_contexts=var_contexts,
    )
    merged.update(rule_filled)
    dedupe_template_fields(merged, template_vars)
    for name in template_vars:
        if not _non_empty(merged.get(name)):
            merged[name] = placeholder
    return merged


def dedupe_template_fields(merged: dict[str, Any], template_vars: list[str]) -> None:
    """Clear lower-priority fields that duplicate higher-priority content."""
    filled = [name for name in template_vars if _non_empty(merged.get(name))]
    for i, high in enumerate(filled):
        high_text = _normalize_field_text(merged.get(high))
        for low in filled[i + 1 :]:
            if _field_priority(high) >= _field_priority(low):
                continue
            low_text = _normalize_field_text(merged.get(low))
            if _fields_are_duplicates(high_text, low_text):
                merged.pop(low, None)


def _parse_bracket_sections(text: str) -> list[tuple[str, str]]:
    if "【" not in text or "】" not in text:
        return []
    parts = re.split(r"【([^】]+)】", text)
    if len(parts) < 3:
        return []
    sections: list[tuple[str, str]] = []
    for idx in range(1, len(parts), 2):
        title = parts[idx].strip()
        body = parts[idx + 1].strip() if idx + 1 < len(parts) else ""
        if title and body:
            sections.append((title, body))
    return sections


def extract_fields_from_sources(
    template_vars: list[str],
    *,
    fields: dict[str, Any] | None = None,
    requirement: str = "",
    sources_text: str = "",
    outline: dict[str, Any] | None = None,
    var_contexts: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Rule-based template field fill when Brain is unavailable.

    Returns (fields_dict, field_sources) where field_sources maps field name
    to its extraction source: 'rule', 'outline', 'label', or 'fallback'.
    """
    from word_template_convert import VAR_TO_ZH, find_label_value_in_text

    merged = dict(fields or {})
    field_sources: dict[str, str] = {}
    var_ctx = var_contexts or {}
    full_outline_text = outline_summary_text(outline or {})
    if not full_outline_text:
        full_outline_text = "\n".join(
            line.strip()
            for line in (sources_text or requirement or "").splitlines()
            if line.strip() and not line.startswith("---")
        ).strip()[:4000]
    outline_title = str((outline or {}).get("title") or "").strip()
    combined_text = sources_text + "\n" + requirement

    for name in template_vars:
        if _non_empty(merged.get(name)):
            continue
        low = name.lower()

        # Title fields
        if low in {"title", "doc_title", "document_title"}:
            merged[name] = outline_title or str(merged.get("title") or requirement[:120]).strip()
            field_sources[name] = "outline" if outline_title else "fallback"
            continue

        # Summary fields — but if var_context points to a specific section, use structured extraction instead
        if low in _SUMMARY_KEYS or any(token in low for token in ("summary", "content", "body", "desc", "abstract")):
            ctx = var_ctx.get(name, "")
            section_hint = _context_has_section_hint(ctx) if ctx else None
            if section_hint:
                value = _extract_structured_field(
                    name, outline, sources_text=combined_text, keywords=[section_hint]
                )
                if value:
                    merged[name] = value
                    field_sources[name] = "outline"
                    continue
            merged[name] = outline_brief_summary(outline or {}, var_context=ctx) or full_outline_text
            field_sources[name] = "rule"
            continue

        # Date fields
        if low in _DATE_FIELD_TOKENS or "date" in low or "日期" in name:
            zh_labels = VAR_TO_ZH.get(low, [])
            value = find_label_value_in_text(combined_text, zh_labels) if zh_labels else ""
            if not value:
                value = _extract_date(combined_text)
            if value:
                merged[name] = value
                field_sources[name] = "label" if zh_labels else "rule"
            continue

        # Time fields
        if low in _TIME_FIELD_TOKENS or "time" in low:
            zh_labels = VAR_TO_ZH.get(low, [])
            value = find_label_value_in_text(combined_text, zh_labels) if zh_labels else ""
            if not value:
                value = _extract_time(combined_text)
            if value:
                merged[name] = value
                field_sources[name] = "label" if zh_labels else "rule"
            continue

        # Person/participant fields
        if low in _PERSON_FIELD_TOKENS or any(t in low for t in ("person", "participant", "attendee", "host", "recorder", "owner", "assignee")):
            zh_labels = VAR_TO_ZH.get(low, [])
            value = _extract_person_value(combined_text, zh_labels)
            if value:
                merged[name] = value
                field_sources[name] = "label"
            continue

        # Location fields
        if low in _LOCATION_FIELD_TOKENS or "location" in low or "venue" in low:
            value = _extract_location(combined_text)
            if value:
                merged[name] = value
                field_sources[name] = "label"
            continue

        if low == "meeting_info":
            value = _extract_meeting_info(combined_text, merged)
            if value:
                merged[name] = value
                field_sources[name] = "rule"
            continue

        if low == "topic_sections":
            value = _extract_structured_field(name, outline or {}, sources_text=combined_text)
            if not value:
                value = _extract_all_outline_sections(outline)
            if value:
                merged[name] = value
                field_sources[name] = "outline"
            continue

        if low == "action_items":
            table_value = _parse_tabular_action_items(combined_text)
            if table_value:
                merged[name] = table_value
                field_sources[name] = "rule"
                continue
            fallback = _fallback_list_field_from_outline(name, outline)
            if fallback:
                merged[name] = fallback
                field_sources[name] = "outline"
            continue

        # List / section fields (conclusions, risks, action items, topics, etc.)
        if low in _LIST_FIELD_TOKENS or any(t in low for t in ("action", "todo", "next_step", "discussion", "blocker", "risk", "conclusion", "topic", "meeting")):
            value = _extract_list_from_outline(outline or {}, low, sources_text=combined_text)
            if value:
                merged[name] = value
                field_sources[name] = "outline" if (outline or {}).get("sections") else "rule"
                continue
            fallback = _fallback_list_field_from_outline(name, outline)
            if fallback:
                merged[name] = fallback
                field_sources[name] = "outline"
            continue

        # Company/client fields
        if low in {"company", "company_name", "client", "customer", "partner_name"}:
            for line in (sources_text or "").splitlines():
                cleaned = line.strip()
                if any(token in cleaned for token in ("公司", "客户", "单位", "合作")):
                    merged[name] = cleaned[:200]
                    field_sources[name] = "rule"
                    break
            continue

        # Generic label matching via VAR_TO_ZH
        zh_labels = VAR_TO_ZH.get(low, [])
        if zh_labels:
            value = find_label_value_in_text(combined_text, zh_labels)
            if value:
                merged[name] = value
                field_sources[name] = "label"

    _sync_structured_fields(template_vars, merged, field_sources, outline, sources_text=combined_text)

    for name in template_vars:
        if _non_empty(merged.get(name)):
            continue
        ctx = var_ctx.get(name, "")
        section_hint = _context_has_section_hint(ctx) if ctx else None
        if section_hint:
            value = _extract_structured_field(
                name, outline, sources_text=combined_text, keywords=[section_hint]
            )
            if value:
                merged[name] = value
                field_sources[name] = "outline"

    dedupe_template_fields(merged, template_vars)

    for name in template_vars:
        if _non_empty(merged.get(name)):
            continue
        low = name.lower()
        if low not in _LIST_FIELD_TOKENS and not any(
            t in low for t in ("action", "todo", "risk", "conclusion", "topic")
        ):
            continue
        value = _extract_structured_field(name, outline, sources_text=combined_text)
        if value:
            merged[name] = value
            field_sources[name] = "outline" if (outline or {}).get("sections") else "rule"

    return merged, field_sources
