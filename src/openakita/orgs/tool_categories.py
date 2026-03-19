"""
外部工具类目定义、岗位角色工具预设、节点头像预设。

将工具按功能域分组为类目（category），节点的 external_tools 字段
可以混合使用类目名和具体工具名。expand_tool_categories() 负责展开。
"""

from __future__ import annotations

TOOL_CATEGORIES: dict[str, list[str]] = {
    "research": ["web_search", "news_search"],
    "planning": [
        "create_todo", "update_todo_step",
        "get_todo_status", "complete_todo",
    ],
    "filesystem": ["run_shell", "write_file", "read_file", "list_directory"],
    "memory": ["add_memory", "search_memory", "get_memory_stats"],
    "mcp": ["call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"],
    "browser": [
        "browser_task", "browser_open", "browser_navigate",
        "browser_screenshot",
    ],
    "communication": ["deliver_artifacts", "get_chat_history"],
    "skills": ["run_skill_script", "list_skills", "get_skill_info", "get_skill_reference"],
}

ROLE_TOOL_PRESETS: dict[str, list[str]] = {
    "ceo":        ["research", "planning", "memory"],
    "cto":        ["research", "planning", "filesystem", "memory"],
    "cpo":        ["research", "planning", "memory"],
    "cmo":        ["research", "planning", "memory"],
    "cfo":        ["research", "memory"],
    "developer":  ["filesystem", "memory"],
    "engineer":   ["filesystem", "memory"],
    "researcher": ["research", "memory"],
    "writer":     ["research", "filesystem", "memory"],
    "analyst":    ["research", "memory"],
    "designer":   ["browser", "filesystem"],
    "devops":     ["filesystem", "memory"],
    "pm":         ["research", "planning", "memory"],
    "hr":         ["research", "memory"],
    "legal":      ["research", "memory"],
    "seo":        ["research", "memory"],
    "content":    ["research", "filesystem", "memory"],
    "default":    ["research", "memory"],
}

ALL_CATEGORY_NAMES: frozenset[str] = frozenset(TOOL_CATEGORIES.keys())


def expand_tool_categories(entries: list[str] | None) -> set[str]:
    """Expand a mixed list of category names and tool names into a flat set of tool names.

    >>> sorted(expand_tool_categories(["research", "create_todo"]))
    ['create_todo', 'news_search', 'web_search']
    """
    if not entries:
        return set()
    result: set[str] = set()
    for entry in entries:
        if not entry or not entry.strip():
            continue
        if entry in TOOL_CATEGORIES:
            result.update(TOOL_CATEGORIES[entry])
        else:
            result.add(entry)
    return result


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "ceo": ["ceo", "执行官", "总裁"],
    "cto": ["cto", "技术总监"],
    "cpo": ["cpo", "产品总监"],
    "cmo": ["cmo", "市场总监", "营销"],
    "cfo": ["cfo", "财务总监"],
    "developer": ["developer", "dev", "工程师", "开发"],
    "engineer": ["engineer"],
    "researcher": ["researcher", "研究", "调研"],
    "writer": ["writer", "写手", "文案", "编辑"],
    "analyst": ["analyst", "分析"],
    "designer": ["designer", "设计"],
    "devops": ["devops", "运维"],
    "pm": ["pm", "产品经理", "项目经理"],
    "hr": ["hr", "人力", "人事"],
    "legal": ["legal", "法务", "法律"],
    "seo": ["seo"],
    "content": ["content", "运营", "内容"],
}


def get_preset_for_role(role_hint: str) -> list[str]:
    """Match a role hint string to the best preset, returning category names."""
    hint = role_hint.lower()
    for preset_key, keywords in _ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in hint:
                return list(ROLE_TOOL_PRESETS.get(preset_key, ROLE_TOOL_PRESETS["default"]))
    return list(ROLE_TOOL_PRESETS["default"])


def list_categories() -> list[dict[str, str | list[str]]]:
    """Return category info for frontend display."""
    return [
        {"name": name, "tools": tools}
        for name, tools in TOOL_CATEGORIES.items()
    ]


# ---------------------------------------------------------------------------
# Avatar presets — 20 role-based avatars for org nodes
# ---------------------------------------------------------------------------

AVATAR_PRESETS: list[dict[str, str]] = [
    {"id": "ceo",         "bg": "#1a365d", "label": "CEO / 总裁"},
    {"id": "cto",         "bg": "#2b6cb0", "label": "CTO / 技术总监"},
    {"id": "cfo",         "bg": "#2f855a", "label": "CFO / 财务总监"},
    {"id": "cmo",         "bg": "#dd6b20", "label": "CMO / 市场总监"},
    {"id": "cpo",         "bg": "#6b46c1", "label": "CPO / 产品总监"},
    {"id": "architect",   "bg": "#2c5282", "label": "架构师"},
    {"id": "dev-m",       "bg": "#3182ce", "label": "开发工程师 (男)"},
    {"id": "dev-f",       "bg": "#00838f", "label": "开发工程师 (女)"},
    {"id": "devops",      "bg": "#4a5568", "label": "DevOps 工程师"},
    {"id": "designer-m",  "bg": "#d53f8c", "label": "设计师 (男)"},
    {"id": "designer-f",  "bg": "#b83280", "label": "设计师 (女)"},
    {"id": "pm",          "bg": "#805ad5", "label": "产品 / 项目经理"},
    {"id": "analyst",     "bg": "#3182ce", "label": "数据分析师"},
    {"id": "marketer",    "bg": "#e53e3e", "label": "市场营销"},
    {"id": "writer",      "bg": "#744210", "label": "文案 / 写手"},
    {"id": "hr",          "bg": "#c05621", "label": "人力资源"},
    {"id": "legal",       "bg": "#718096", "label": "法务顾问"},
    {"id": "support",     "bg": "#319795", "label": "客服支持"},
    {"id": "researcher",  "bg": "#276749", "label": "研究员"},
    {"id": "media",       "bg": "#e53e3e", "label": "社媒运营"},
]

AVATAR_MAP: dict[str, dict[str, str]] = {a["id"]: a for a in AVATAR_PRESETS}

_ROLE_AVATAR_KEYWORDS: dict[str, list[str]] = {
    "ceo":        ["ceo", "首席执行", "总裁", "总经理"],
    "cto":        ["cto", "技术总监"],
    "cfo":        ["cfo", "财务总监", "财务"],
    "cmo":        ["cmo", "市场总监"],
    "cpo":        ["cpo", "产品总监"],
    "architect":  ["架构"],
    "dev-m":      ["工程师", "developer", "dev", "开发", "全栈"],
    "devops":     ["devops", "运维"],
    "designer-m": ["设计", "designer", "ui"],
    "pm":         ["产品经理", "项目经理", "pm"],
    "analyst":    ["分析", "analyst", "数据"],
    "marketer":   ["营销", "推广", "market"],
    "writer":     ["文案", "写手", "编辑", "内容", "content", "seo"],
    "hr":         ["hr", "人力", "人事", "招聘"],
    "legal":      ["法务", "法律", "legal"],
    "support":    ["客服", "support", "客户"],
    "researcher": ["研究", "research"],
    "media":      ["社媒", "运营", "social"],
}


def get_avatar_for_role(role_hint: str) -> str:
    """Match a role hint to the best avatar preset ID."""
    hint = role_hint.lower()
    for avatar_id, keywords in _ROLE_AVATAR_KEYWORDS.items():
        for kw in keywords:
            if kw in hint:
                return avatar_id
    return "ceo"


def list_avatar_presets() -> list[dict[str, str]]:
    """Return all avatar presets for frontend display."""
    return list(AVATAR_PRESETS)
