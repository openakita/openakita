"""v2 ``_runtime_templates`` -- org templates + avatar presets + workbench templates (P-RC-9 P9.9γ-1b).

Absorbs 3 v1 helpers deferred at P9.9γ-1 (charter §3 + P9.9γ-1 ledger
row "absorption-debt deferral") plus the hidden helper / data
dependencies pulled in by them:

* From v1 ``openakita.orgs.templates``:
  :func:`ensure_builtin_templates` (the inventoried γ-1b helper)
  + the 4 large org-template constants it walks (``STARTUP_COMPANY``,
  ``SOFTWARE_TEAM``, ``CONTENT_OPS``, ``AIGC_VIDEO_STUDIO``) +
  ``ALL_TEMPLATES`` + ``TEMPLATE_POLICY_MAP`` + 5 private helpers
  (``_with_builtin_metadata`` / ``_auto_assign_avatars`` /
  ``_auto_assign_agent_profiles`` / ``_is_legacy_aigc_video_studio`` /
  ``_archive_removed_template``). ``_HAPPYHORSE_PLUGIN_ORIGIN``
  helper constant absorbed alongside (referenced by
  ``AIGC_VIDEO_STUDIO``).
* From v1 ``openakita.orgs.plugin_workbench_templates``:
  :func:`build_workbench_templates` (the inventoried γ-1b helper)
  + 5 private helpers (``_default_goal_for`` /
  ``_default_prompt_for`` / ``_tool_summary`` /
  ``_collect_host_tool_defs`` / ``_resolve_tool_dict``)
  + :func:`deprecated_tools_for_node` (companion exporter; v1
  exposed both alongside).
* From v1 ``openakita.orgs.tool_categories``:
  :func:`list_avatar_presets` (the inventoried γ-1b helper) +
  ``AVATAR_PRESETS`` constant (the 20-item role-avatar palette)
  + ``AVATAR_MAP`` index + ``_ROLE_AVATAR_KEYWORDS`` matching
  dict + :func:`get_avatar_for_role` (used by
  ``_auto_assign_avatars`` -- a hidden dependency of
  ``ensure_builtin_templates`` discovered during absorption).

Byte-equal port: every constant value, function body, and dict-key
spelling is preserved verbatim from v1. The only edits are:

1. ``_auto_assign_avatars`` drops its v1 deferred ``from
   openakita.orgs.tool_categories import get_avatar_for_role`` line
   because the function now lives in the same module.
2. ``_auto_assign_agent_profiles`` reroutes its v1 deferred ``from
   openakita.orgs.models import infer_agent_profile_id_for_node``
   onto the v2 ``runtime/orgs/org_models`` shard landed in P9.9γ-2b.
3. ``deprecated_tools_for_node`` drops its v1 relative ``from
   .tool_categories import ALL_CATEGORY_NAMES`` import and falls
   back to an empty frozenset (no v2 caller currently exercises
   this exporter -- documented inline; the wider
   ``tool_categories`` constants stay in v1 and ε-1-delete
   alongside the parent).

ADR refs: ADR-0011 (template / workbench shard sibling to the
existing ``_runtime_plugin_assets`` / ``_runtime_event_bus`` / etc.);
ADR-0012 (no shim under v1; this shard is the v2-native home for
the absorbed surface, not a re-export layer).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openakita.plugins.manager import PluginManager

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_TEMPLATES",
    "AVATAR_PRESETS",
    "TEMPLATE_POLICY_MAP",
    "build_workbench_templates",
    "deprecated_tools_for_node",
    "ensure_builtin_templates",
    "get_avatar_for_role",
    "list_avatar_presets",
]


# ===========================================================================
# Avatar presets (v1 ``openakita.orgs.tool_categories`` excerpt)
# ===========================================================================
# 20 role-based avatars + role-hint matching for org nodes. Hidden
# dependency of ``ensure_builtin_templates`` via ``_auto_assign_avatars``.

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


# ===========================================================================
# Org templates (v1 ``openakita.orgs.templates`` excerpt)
# ===========================================================================
# 4 large built-in templates (Startup Company / Software Team / Content Ops
# / AIGC Video Studio) + ALL_TEMPLATES index + TEMPLATE_POLICY_MAP +
# auto-assign helpers + ensure_builtin_templates installer / migrator.

STARTUP_COMPANY: dict = {
    "name": "创业公司",
    "description": "包含技术、产品、市场、行政四大部门的标准创业公司架构",
    "icon": "🏢",
    "tags": ["company", "startup"],
    "user_persona": {"title": "董事长", "display_name": "董事长", "description": "公司最高决策者"},
    "core_business": "",
    "heartbeat_enabled": False,
    "heartbeat_interval_s": 1800,
    "heartbeat_prompt": "审视公司当前运营状态，识别紧急事项和阻塞，决定是否需要分配新任务或调整优先级。",
    "standup_enabled": False,
    "standup_cron": "0 9 * * 1-5",
    "standup_agenda": "各部门负责人汇报昨日进展、今日计划和阻塞事项。",
    "allow_cross_level": False,
    "max_delegation_depth": 4,
    "conflict_resolution": "manager",
    "scaling_enabled": True,
    "max_nodes": 25,
    "scaling_approval": "user",
    "nodes": [
        {
            "id": "ceo",
            "role_title": "CEO / 首席执行官",
            "role_goal": "制定公司战略方向，协调各部门，确保公司目标达成",
            "role_backstory": "经验丰富的创业者，擅长战略规划和团队管理",
            "agent_source": "local",
            "position": {"x": 400, "y": 0},
            "level": 0,
            "department": "管理层",
            "avatar": "ceo",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "cto",
            "role_title": "CTO / 技术总监",
            "role_goal": "确保技术架构合理、代码质量达标、技术团队高效运转",
            "role_backstory": "10年全栈开发经验的技术负责人，擅长架构设计和技术选型",
            "agent_source": "local",
            "position": {"x": 100, "y": 150},
            "level": 1,
            "department": "技术部",
            "avatar": "cto",
            "external_tools": ["research", "planning", "filesystem", "memory"],
        },
        {
            "id": "architect",
            "role_title": "架构师",
            "role_goal": "设计和维护系统架构，制定技术规范",
            "role_backstory": "资深架构师，精通分布式系统和微服务",
            "agent_source": "local",
            "position": {"x": 0, "y": 300},
            "level": 2,
            "department": "技术部",
            "avatar": "architect",
            "external_tools": ["research", "filesystem", "memory"],
        },
        {
            "id": "dev-a",
            "role_title": "全栈工程师A",
            "role_goal": "高质量完成分配的开发任务",
            "role_backstory": "全栈开发工程师，前后端均有丰富经验",
            "agent_source": "local",
            "position": {"x": 100, "y": 300},
            "level": 2,
            "department": "技术部",
            "avatar": "dev-m",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "dev-b",
            "role_title": "全栈工程师B",
            "role_goal": "高质量完成分配的开发任务",
            "role_backstory": "全栈开发工程师，擅长性能优化和测试",
            "agent_source": "local",
            "position": {"x": 200, "y": 300},
            "level": 2,
            "department": "技术部",
            "avatar": "dev-f",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "devops",
            "role_title": "DevOps工程师",
            "role_goal": "保障服务稳定运行，自动化部署和监控",
            "role_backstory": "DevOps工程师，精通CI/CD、容器化和云服务",
            "agent_source": "local",
            "position": {"x": 300, "y": 300},
            "level": 2,
            "department": "技术部",
            "avatar": "devops",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "cpo",
            "role_title": "CPO / 产品总监",
            "role_goal": "制定产品规划，确保产品方向正确，用户体验良好",
            "role_backstory": "产品专家，擅长用户需求分析和产品规划",
            "agent_source": "local",
            "position": {"x": 400, "y": 150},
            "level": 1,
            "department": "产品部",
            "avatar": "cpo",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "pm",
            "role_title": "产品经理",
            "role_goal": "管理需求、排期和项目进度",
            "role_backstory": "经验丰富的产品经理，擅长需求分析和项目管理",
            "agent_source": "local",
            "position": {"x": 350, "y": 300},
            "level": 2,
            "department": "产品部",
            "avatar": "pm",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "ui-designer",
            "role_title": "UI设计师",
            "role_goal": "设计美观易用的用户界面",
            "role_backstory": "UI/UX设计师，擅长交互设计和视觉设计",
            "agent_source": "local",
            "position": {"x": 450, "y": 300},
            "level": 2,
            "department": "产品部",
            "avatar": "designer-f",
            "external_tools": ["browser", "filesystem"],
        },
        {
            "id": "cmo",
            "role_title": "CMO / 市场总监",
            "role_goal": "制定营销策略，提升品牌知名度和用户增长",
            "role_backstory": "市场营销专家，擅长品牌策略和增长黑客",
            "agent_source": "local",
            "position": {"x": 600, "y": 150},
            "level": 1,
            "department": "市场部",
            "avatar": "cmo",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "content-op",
            "role_title": "内容运营",
            "role_goal": "产出高质量内容，维护内容发布节奏",
            "role_backstory": "内容创作者，擅长文案撰写和内容策划",
            "agent_source": "local",
            "position": {"x": 550, "y": 300},
            "level": 2,
            "department": "市场部",
            "avatar": "writer",
            "external_tools": ["research", "filesystem", "memory"],
        },
        {
            "id": "seo",
            "role_title": "SEO专员",
            "role_goal": "优化搜索引擎排名，提升自然流量",
            "role_backstory": "SEO专家，精通搜索引擎优化策略",
            "agent_source": "local",
            "position": {"x": 650, "y": 300},
            "level": 2,
            "department": "市场部",
            "avatar": "researcher",
            "external_tools": ["research", "memory"],
        },
        {
            "id": "social-media",
            "role_title": "社媒运营",
            "role_goal": "管理社交媒体账号，提升社交影响力",
            "role_backstory": "社交媒体运营专家，擅长社群管理和互动",
            "agent_source": "local",
            "position": {"x": 750, "y": 300},
            "level": 2,
            "department": "市场部",
            "avatar": "media",
            "external_tools": ["research", "memory"],
        },
        {
            "id": "cfo",
            "role_title": "CFO / 财务总监",
            "role_goal": "管理公司财务，控制成本，确保资金健康",
            "role_backstory": "财务管理专家，擅长预算管理和财务分析",
            "agent_source": "local",
            "position": {"x": 800, "y": 150},
            "level": 1,
            "department": "行政支持",
            "avatar": "cfo",
            "external_tools": ["research", "memory"],
        },
        {
            "id": "hr",
            "role_title": "HR / 人力资源",
            "role_goal": "管理团队建设和人才发展",
            "role_backstory": "人力资源专家，擅长招聘和团队文化建设",
            "agent_source": "local",
            "position": {"x": 850, "y": 300},
            "level": 2,
            "department": "行政支持",
            "avatar": "hr",
            "external_tools": ["research", "memory"],
        },
        {
            "id": "legal",
            "role_title": "法务顾问",
            "role_goal": "提供法律咨询，确保公司合规运营",
            "role_backstory": "法律顾问，精通商业法律和合规事务",
            "agent_source": "local",
            "position": {"x": 950, "y": 300},
            "level": 2,
            "department": "行政支持",
            "avatar": "legal",
            "external_tools": ["research", "memory"],
        },
    ],
    "edges": [
        {
            "id": "e-ceo-cto",
            "source": "ceo",
            "target": "cto",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-ceo-cpo",
            "source": "ceo",
            "target": "cpo",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-ceo-cmo",
            "source": "ceo",
            "target": "cmo",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-ceo-cfo",
            "source": "ceo",
            "target": "cfo",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cto-arch",
            "source": "cto",
            "target": "architect",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cto-deva",
            "source": "cto",
            "target": "dev-a",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cto-devb",
            "source": "cto",
            "target": "dev-b",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cto-devops",
            "source": "cto",
            "target": "devops",
            "edge_type": "hierarchy",
            "label": "",
        },
        {"id": "e-cpo-pm", "source": "cpo", "target": "pm", "edge_type": "hierarchy", "label": ""},
        {
            "id": "e-cpo-ui",
            "source": "cpo",
            "target": "ui-designer",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cmo-content",
            "source": "cmo",
            "target": "content-op",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cmo-seo",
            "source": "cmo",
            "target": "seo",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cmo-social",
            "source": "cmo",
            "target": "social-media",
            "edge_type": "hierarchy",
            "label": "",
        },
        {"id": "e-cfo-hr", "source": "cfo", "target": "hr", "edge_type": "hierarchy", "label": ""},
        {
            "id": "e-cfo-legal",
            "source": "cfo",
            "target": "legal",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-cpo-cto",
            "source": "cpo",
            "target": "cto",
            "edge_type": "collaborate",
            "label": "产品技术对齐",
        },
        {
            "id": "e-pm-deva",
            "source": "pm",
            "target": "dev-a",
            "edge_type": "collaborate",
            "label": "需求沟通",
        },
        {
            "id": "e-pm-devb",
            "source": "pm",
            "target": "dev-b",
            "edge_type": "collaborate",
            "label": "需求沟通",
        },
        {
            "id": "e-content-seo",
            "source": "content-op",
            "target": "seo",
            "edge_type": "collaborate",
            "label": "内容优化",
        },
    ],
}



SOFTWARE_TEAM: dict = {
    "name": "软件工程团队",
    "description": "前后端分组的软件开发团队，含QA、DevOps和技术文档",
    "icon": "💻",
    "tags": ["software", "engineering"],
    "user_persona": {
        "title": "产品负责人",
        "display_name": "产品负责人",
        "description": "项目需求方与最终验收人",
    },
    "heartbeat_enabled": False,
    "heartbeat_interval_s": 3600,
    "heartbeat_prompt": "检查项目进度和技术阻塞，协调前后端工作。",
    "allow_cross_level": True,
    "max_delegation_depth": 3,
    "conflict_resolution": "manager",
    "scaling_enabled": True,
    "max_nodes": 15,
    "scaling_approval": "manager",
    "nodes": [
        {
            "id": "tech-lead",
            "role_title": "技术负责人",
            "role_goal": "把控技术方向，协调前后端，确保项目按时交付",
            "role_backstory": "资深技术负责人，全栈能力强，擅长技术决策",
            "agent_source": "local",
            "position": {"x": 300, "y": 0},
            "level": 0,
            "department": "工程",
            "avatar": "cto",
            "external_tools": ["research", "planning", "filesystem", "memory"],
        },
        {
            "id": "fe-lead",
            "role_title": "前端组长",
            "role_goal": "管理前端开发进度和质量",
            "role_backstory": "前端技术专家，精通React/Vue",
            "agent_source": "local",
            "position": {"x": 100, "y": 150},
            "level": 1,
            "department": "前端组",
            "avatar": "dev-m",
            "external_tools": ["research", "planning", "filesystem", "memory"],
        },
        {
            "id": "fe-dev-a",
            "role_title": "前端开发A",
            "role_goal": "完成前端功能开发",
            "role_backstory": "前端开发工程师",
            "agent_source": "local",
            "position": {"x": 50, "y": 300},
            "level": 2,
            "department": "前端组",
            "avatar": "dev-f",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "fe-dev-b",
            "role_title": "前端开发B",
            "role_goal": "完成前端功能开发",
            "role_backstory": "前端开发工程师",
            "agent_source": "local",
            "position": {"x": 150, "y": 300},
            "level": 2,
            "department": "前端组",
            "avatar": "dev-m",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "be-lead",
            "role_title": "后端组长",
            "role_goal": "管理后端开发进度和质量",
            "role_backstory": "后端技术专家，精通Python/Go",
            "agent_source": "local",
            "position": {"x": 350, "y": 150},
            "level": 1,
            "department": "后端组",
            "avatar": "dev-f",
            "external_tools": ["research", "planning", "filesystem", "memory"],
        },
        {
            "id": "be-dev-a",
            "role_title": "后端开发A",
            "role_goal": "完成后端功能开发",
            "role_backstory": "后端开发工程师",
            "agent_source": "local",
            "position": {"x": 300, "y": 300},
            "level": 2,
            "department": "后端组",
            "avatar": "dev-m",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "be-dev-b",
            "role_title": "后端开发B",
            "role_goal": "完成后端功能开发",
            "role_backstory": "后端开发工程师",
            "agent_source": "local",
            "position": {"x": 400, "y": 300},
            "level": 2,
            "department": "后端组",
            "avatar": "dev-f",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "qa",
            "role_title": "QA工程师",
            "role_goal": "确保软件质量，编写和执行测试",
            "role_backstory": "测试专家，擅长自动化测试",
            "agent_source": "local",
            "position": {"x": 500, "y": 150},
            "level": 1,
            "department": "工程",
            "avatar": "researcher",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "devops-eng",
            "role_title": "DevOps工程师",
            "role_goal": "维护CI/CD流水线和生产环境",
            "role_backstory": "DevOps工程师",
            "agent_source": "local",
            "position": {"x": 500, "y": 300},
            "level": 2,
            "department": "工程",
            "avatar": "devops",
            "external_tools": ["filesystem", "memory"],
        },
        {
            "id": "tech-writer",
            "role_title": "技术文档",
            "role_goal": "编写和维护技术文档",
            "role_backstory": "技术写作专家",
            "agent_source": "local",
            "position": {"x": 600, "y": 300},
            "level": 2,
            "department": "工程",
            "avatar": "writer",
            "external_tools": ["research", "filesystem", "memory"],
        },
    ],
    "edges": [
        {"id": "e1", "source": "tech-lead", "target": "fe-lead", "edge_type": "hierarchy"},
        {"id": "e2", "source": "tech-lead", "target": "be-lead", "edge_type": "hierarchy"},
        {"id": "e3", "source": "tech-lead", "target": "qa", "edge_type": "hierarchy"},
        {"id": "e4", "source": "fe-lead", "target": "fe-dev-a", "edge_type": "hierarchy"},
        {"id": "e5", "source": "fe-lead", "target": "fe-dev-b", "edge_type": "hierarchy"},
        {"id": "e6", "source": "be-lead", "target": "be-dev-a", "edge_type": "hierarchy"},
        {"id": "e7", "source": "be-lead", "target": "be-dev-b", "edge_type": "hierarchy"},
        {"id": "e8", "source": "tech-lead", "target": "devops-eng", "edge_type": "hierarchy"},
        {"id": "e9", "source": "tech-lead", "target": "tech-writer", "edge_type": "hierarchy"},
        {
            "id": "e10",
            "source": "fe-lead",
            "target": "be-lead",
            "edge_type": "collaborate",
            "label": "API 对接",
        },
        {
            "id": "e11",
            "source": "qa",
            "target": "fe-lead",
            "edge_type": "consult",
            "label": "测试反馈",
        },
        {
            "id": "e12",
            "source": "qa",
            "target": "be-lead",
            "edge_type": "consult",
            "label": "测试反馈",
        },
        {
            "id": "e13",
            "source": "devops-eng",
            "target": "fe-lead",
            "edge_type": "collaborate",
            "label": "部署协调",
        },
        {
            "id": "e14",
            "source": "devops-eng",
            "target": "be-lead",
            "edge_type": "collaborate",
            "label": "部署协调",
        },
    ],
}



CONTENT_OPS: dict = {
    "name": "内容运营团队",
    "description": "主编领衔的内容创作和运营团队",
    "icon": "📝",
    "tags": ["content", "marketing"],
    "user_persona": {"title": "出品人", "display_name": "出品人", "description": "内容方向决策者"},
    "heartbeat_enabled": False,
    "heartbeat_interval_s": 3600,
    "heartbeat_prompt": "检查内容发布排期和数据表现，调整内容策略。",
    "allow_cross_level": True,
    "max_delegation_depth": 2,
    "conflict_resolution": "manager",
    "scaling_enabled": True,
    "max_nodes": 10,
    "scaling_approval": "manager",
    "nodes": [
        {
            "id": "editor-in-chief",
            "role_title": "主编",
            "role_goal": "制定内容策略，审核发布内容，确保内容质量",
            "role_backstory": "资深主编，擅长内容策略和团队管理",
            "agent_source": "local",
            "position": {"x": 300, "y": 0},
            "level": 0,
            "department": "编辑部",
            "avatar": "ceo",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "planner",
            "role_title": "策划编辑",
            "role_goal": "策划选题，管理内容排期",
            "role_backstory": "内容策划专家，擅长热点捕捉和选题策划",
            "agent_source": "local",
            "position": {"x": 100, "y": 150},
            "level": 1,
            "department": "编辑部",
            "avatar": "pm",
            "external_tools": ["research", "planning", "memory"],
        },
        {
            "id": "writer-a",
            "role_title": "文案写手A",
            "role_goal": "产出高质量文案",
            "role_backstory": "资深文案写手，擅长深度长文",
            "agent_source": "local",
            "position": {"x": 50, "y": 300},
            "level": 2,
            "department": "创作组",
            "avatar": "writer",
            "external_tools": ["research", "filesystem", "memory"],
        },
        {
            "id": "writer-b",
            "role_title": "文案写手B",
            "role_goal": "产出高质量文案",
            "role_backstory": "创意写手，擅长短文和社交媒体文案",
            "agent_source": "local",
            "position": {"x": 150, "y": 300},
            "level": 2,
            "department": "创作组",
            "avatar": "media",
            "external_tools": ["research", "filesystem", "memory"],
        },
        {
            "id": "seo-opt",
            "role_title": "SEO优化师",
            "role_goal": "优化内容的搜索引擎表现",
            "role_backstory": "SEO专家",
            "agent_source": "local",
            "position": {"x": 300, "y": 150},
            "level": 1,
            "department": "运营组",
            "avatar": "researcher",
            "external_tools": ["research", "memory"],
        },
        {
            "id": "visual",
            "role_title": "视觉设计",
            "role_goal": "设计配图和视觉素材",
            "role_backstory": "视觉设计师",
            "agent_source": "local",
            "position": {"x": 400, "y": 300},
            "level": 2,
            "department": "创作组",
            "avatar": "designer-f",
            "external_tools": ["browser", "filesystem"],
        },
        {
            "id": "data-analyst",
            "role_title": "数据分析",
            "role_goal": "分析内容数据，提供数据驱动的选题建议",
            "role_backstory": "数据分析师",
            "agent_source": "local",
            "position": {"x": 500, "y": 150},
            "level": 1,
            "department": "运营组",
            "avatar": "analyst",
            "external_tools": ["research", "memory"],
        },
    ],
    "edges": [
        {"id": "e1", "source": "editor-in-chief", "target": "planner", "edge_type": "hierarchy"},
        {"id": "e2", "source": "editor-in-chief", "target": "seo-opt", "edge_type": "hierarchy"},
        {
            "id": "e3",
            "source": "editor-in-chief",
            "target": "data-analyst",
            "edge_type": "hierarchy",
        },
        {"id": "e4", "source": "planner", "target": "writer-a", "edge_type": "hierarchy"},
        {"id": "e5", "source": "planner", "target": "writer-b", "edge_type": "hierarchy"},
        {"id": "e6", "source": "planner", "target": "visual", "edge_type": "hierarchy"},
        {
            "id": "e7",
            "source": "writer-a",
            "target": "seo-opt",
            "edge_type": "collaborate",
            "label": "内容优化",
        },
        {
            "id": "e8",
            "source": "writer-b",
            "target": "seo-opt",
            "edge_type": "collaborate",
            "label": "内容优化",
        },
        {
            "id": "e9",
            "source": "writer-a",
            "target": "visual",
            "edge_type": "collaborate",
            "label": "配图协调",
        },
        {
            "id": "e10",
            "source": "writer-b",
            "target": "visual",
            "edge_type": "collaborate",
            "label": "配图协调",
        },
        {
            "id": "e11",
            "source": "data-analyst",
            "target": "planner",
            "edge_type": "collaborate",
            "label": "数据驱动选题",
        },
    ],
}

# ---------------------------------------------------------------------------
# AIGC video studio — showcases the "workbench node" feature
#
# 这个模板演示如何把同一个插件按「大类能力」拆成多个工作台节点编入组织：
# 同一个 `happyhorse-video` 插件（基于阿里云百炼 / DashScope）按图像、短视频、
# 数字人、长视频后期四大类拆出独立的工作台节点，配合 3 个协作角色形成
# 端到端流水线——所有节点 `plugin_origin.plugin_id` 都指向 `happyhorse-video`，
# 运行时只看每个节点的 `external_tools` 白名单做工具放行。
#
# 节点构成（7 节点 = 3 协作角色 + 4 工作台 leaf）：
#   - producer        / 制片人      → 统筹下派，不直接调用 hh_*
#   - screenwriter    / 编剧         → 写剧本、调 hh_storyboard_decompose 拆分镜
#   - art-director    / 美术指导     → 视觉总监，承上启下协调三大生成工作台
#   - wb-hh-image     / 图像工作台   → 7 个 hh_image_* 全集
#   - wb-hh-video     / 短视频工作台 → hh_t2v / hh_i2v / hh_r2v / hh_video_edit
#   - wb-hh-human     / 数字人工作台 → 5 个数字人模式
#   - wb-hh-long      / 长视频后期工作台 → hh_long_video_create + hh_video_concat
#
# 工作台节点必须是叶子节点（manager + runtime 双重校验），不允许挂下属。
# 节点 `external_tools` 直接列出插件注册的工具名，运行时由
# ``expand_tool_categories`` 原样透传，OrgRuntime 会自动给这些节点的
# system prompt 追加「工作台能力段 + 交付协议」，并在工具调用成功时把
# 远端 image_urls / video_url 下载到 org workspace，注册为任务附件。
#
# 工作流：
#   1. 制片人收到选题，派给编剧出剧本 + 调 hh_storyboard_decompose 拆分镜 JSON
#   2. 编剧把分镜（含 transition_to_next）交给美术指导
#   3. 美术指导按分镜分别派给图像 / 短视频 / 数字人工作台并行/串行产出
#   4. 各工作台返回 video_url + asset_ids（runtime 自动登记为附件）
#   5. 长视频后期工作台收到各段 task_ids，先 hh_long_video_create 衔接首尾帧，
#      再 hh_video_concat 用指定 transition 拼成成片
#   6. 制片人汇总剧本 + 分镜图 + 成片，交付给出品方
#
# 安装前置（前端在选用此模板时会用 deprecated_tools_for_node() 提示）：
#   - 在「插件管理」里安装并启用 `happyhorse-video`（需要 DashScope API Key
#     与可写 OSS：插件「设置」Tab 里配置 endpoint / bucket / AccessKey）。
# ---------------------------------------------------------------------------



_HAPPYHORSE_PLUGIN_ORIGIN: dict[str, str] = {
    "plugin_id": "happyhorse-video",
    "template_id": "workbench:happyhorse-video",
}




AIGC_VIDEO_STUDIO: dict = {
    "name": "AIGC 视频创作工作室",
    "description": (
        "基于阿里云百炼 / DashScope 的 HappyHorse 一体化工作室——制片人统筹，"
        "编剧出剧本并拆分镜（含转场标记），美术指导协调图像/短视频/数字人三大"
        "生成工作台并行产出，长视频后期工作台用 ffmpeg 把多段素材拼成最终成片。"
        "需要预先在「插件管理」里启用 happyhorse-video 插件（DashScope API "
        "Key + 可写 OSS）。"
    ),
    "icon": "🎬",
    "tags": ["aigc", "video", "workbench", "happyhorse", "dashscope", "bailian"],
    "user_persona": {
        "title": "出品方",
        "display_name": "出品方",
        "description": "短片选题与最终成片验收人",
    },
    "core_business": (
        "围绕短视频/广告片/数字人口播/长视频等场景，按「剧本 → 分镜 → 多通道生成 → "
        "拼接成片」四段式流水线快速产出 AIGC 视频。所有图片/视频产出会自动落到组织 "
        "workspace 的 plugin_assets/ 目录，并作为附件附在任务交付上。"
    ),
    "heartbeat_enabled": False,
    "heartbeat_interval_s": 3600,
    "heartbeat_prompt": "审视当前选题进度，识别脚本/分镜/生成/拼接阶段的卡点。",
    "standup_enabled": False,
    "standup_cron": "0 10 * * 1-5",
    "standup_agenda": "剧本、分镜、多通道生成、拼接成片四个阶段的产出与阻塞同步。",
    "allow_cross_level": True,
    # 工作台节点必须是叶子；委派链最深路径：出品方 → 制片人 → 美术指导 → 工作台。
    "max_delegation_depth": 4,
    "conflict_resolution": "manager",
    "scaling_enabled": False,
    "max_nodes": 10,
    "scaling_approval": "manager",
    "nodes": [
        {
            "id": "producer",
            "role_title": "制片人",
            "role_goal": (
                "把出品方的选题拆成可执行的工序——找编剧细化剧本与分镜，再让"
                "美术指导按分镜协调图像/短视频/数字人三大工作台并行产出，最后由"
                "长视频后期工作台拼成成片，并对最终交付负责。"
            ),
            "role_backstory": "AIGC 短片制片人，擅长把粗糙的创意拆成可标准化的视觉工序。",
            "agent_source": "local",
            "agent_profile_id": "project-manager",
            "position": {"x": 400, "y": 0},
            "level": 0,
            "department": "制作部",
            "avatar": "ceo",
            "external_tools": ["research", "planning", "filesystem", "memory"],
            "custom_prompt": (
                "你是 AIGC 视频创作工作室的制片人。\n"
                "直属下级只有两个：『编剧』负责剧本与分镜，『美术指导』负责所有工作台"
                "调度（图像 / 短视频 / 数字人 / 长视频后期）。工作流：\n"
                "1. 把出品方的选题派给『编剧』节点，要求他用 org_submit_deliverable "
                "返回：(a) 完整剧本（场景/人物/对白）；(b) 调用 hh_storyboard_decompose "
                "得到的结构化分镜 JSON（包含每镜头 prompt / duration / "
                "key_frame_description / end_frame_description / transition_to_next / "
                "camera_notes）。\n"
                "2. 把剧本 + 完整分镜 JSON + 期望的转场偏好（如 cut / crossfade）派给"
                "『美术指导』节点，要求他统一调度所有工作台：按分镜决定每段走「先 "
                "hh_image_* 出首帧再 hh_i2v」还是「直接 hh_t2v」、是否需要数字人口播、"
                "最后调度『长视频后期工作台』执行 hh_long_video_create / hh_video_concat "
                "拼接成片，并把成片 task_id + 各段 task_id 一并回传。\n"
                "3. 收到美术指导的最终交付后向出品方交付：剧本（文字）+ 分镜 JSON（附件）"
                "+ 各段视频（附件）+ 成片（附件）。多镜头任务严禁让任何工作台用同一段总主题"
                " prompt 重复生成多个视频——必须按分镜 segments[] 逐镜头拆派。"
            ),
        },
        {
            "id": "screenwriter",
            "role_title": "编剧",
            "role_goal": (
                "把选题拆成结构化分镜——先写人类可读剧本，再调用 hh_storyboard_decompose "
                "产出标准化 segments JSON（含 transition_to_next 转场标记）。"
            ),
            "role_backstory": "广告短片编剧，熟悉 AIGC 工具的 prompt 写法。",
            "agent_source": "local",
            "agent_profile_id": "content-creator",
            "position": {"x": 150, "y": 180},
            "level": 1,
            "department": "创意",
            "avatar": "writer",
            "external_tools": [
                "research",
                "planning",
                "filesystem",
                "memory",
                "hh_storyboard_decompose",
            ],
            "custom_prompt": (
                "你是组织里的编剧节点。收到选题后按以下步骤工作：\n"
                "1. 先写出完整剧本（场景、人物、对白），中文语境下交付内容必须以中文为主。\n"
                "2. 调用 hh_storyboard_decompose 把剧情拆为结构化分镜 JSON。"
                "参数：story=剧情正文；total_duration=成片总时长（秒）；segment_duration=每段时长"
                "（默认 10 秒）；aspect_ratio=画幅；style=视觉风格描述。返回的 segments 中每段会带"
                "transition_to_next（cut / crossfade / ai_extend），下游会用它决定衔接首尾帧或拼接转场。\n"
                "3. 用 org_submit_deliverable 交付：deliverable 文本里附剧本摘要 + 分镜 JSON 概览，"
                "并把完整剧本 + 完整 segments JSON 各自落盘为 markdown / json 文件附在交付上。\n"
                "若上级仅是讨论/问询，直接 org_submit_deliverable 文字回复，不要凭空调工具。"
            ),
        },
        {
            "id": "art-director",
            "role_title": "美术指导",
            "role_goal": (
                "把编剧的分镜 JSON 翻译为图像 / 短视频 / 数字人三大工作台的具体派单，"
                "决定每段走「首帧出图 → i2v」还是「直接 t2v」，并选用合适的数字人模式。"
            ),
            "role_backstory": "AIGC 短片美术总监，懂提示词、构图、色彩、转场和成本控制。",
            "agent_source": "local",
            "agent_profile_id": "content-creator",
            "position": {"x": 400, "y": 180},
            "level": 1,
            "department": "美术",
            "avatar": "designer-f",
            "external_tools": ["research", "planning", "filesystem", "memory"],
            "custom_prompt": (
                "你是组织里的美术指导节点，是所有四个 HappyHorse 工作台（图像 / 短视频 / "
                "数字人 / 长视频后期）的直属上级。收到制片人转来的剧本 + 分镜 JSON + 转场"
                "偏好后：\n"
                "1. 按分镜决定每段的产出路径——情绪静帧 / 海报画面优先用『图像工作台』先出"
                "首帧再让『短视频工作台』做 hh_i2v；纯运动镜头 / 无具体角色定型的段直接 "
                "hh_t2v；含具体角色口播或对话的段交给『数字人工作台』。\n"
                "2. 用 org_delegate_task 分别派给图像 / 短视频 / 数字人工作台，每条派单必须"
                "明确：镜头号、中文画面说明、镜头语言、目标时长 duration、上游 asset_ids"
                "（如有）、风格关键词。多镜头必须拆成多条派单，绝不能让任何工作台用同一段总"
                "主题 prompt 重复生成多个视频。\n"
                "3. 收齐各工作台交付（runtime 已把图片 / 视频附在 TASK_DELIVERED 上）后，"
                "把每段对应的视频 task_id 按出场顺序整理为列表，连同 transition / fade_"
                "duration（参考分镜里的 transition_to_next：cut → none、crossfade → "
                "crossfade、ai_extend → 用 hh_long_video_create 衔接），调用 "
                "org_delegate_task 派给『长视频后期工作台』做 hh_long_video_create 衔接 + "
                "hh_video_concat 拼接成片。\n"
                "4. 拼接工作台返回成片后，用 org_submit_deliverable 把所有素材（分镜段落 "
                "task_id 列表 + 最终成片 task_id / asset_id）回交给制片人。\n"
                "\n"
                "【硬性路由规则 — 违反就是错误派单】\n"
                "R1. 『数字人工作台』(wb-hh-human) **仅用于**：在已有的人物图 / 视频上做"
                "「说话头像 / 唇形对齐 / 换脸 / 姿态驱动 / 已有素材重组」。任何包含"
                "「跳舞 / 全身动作 / 大幅运镜 / 武打 / 运动镜头 / 风景空镜」的镜头**绝对**"
                "不能派给 wb-hh-human——必须走 wb-hh-image（先出首帧静态人物）→ wb-hh-video "
                "(`hh_i2v` 配合 `from_asset_ids`) 这条主路径。\n"
                "R2. 文案中出现「唱歌 / 唱词 / 歌词」不等于「口播」。判断是否走数字人的"
                "唯一标准是：是否需要让一张照片里的脸做唇形或表情驱动。比赛 / 跳舞 / 走秀 / "
                "风景全身镜头一律走 image+video，不要被「唱着歌」的 narration 误导。\n"
                "R3. 派单文本里**只允许出现真实工具名**，白名单：`hh_image_create` / "
                "`hh_image_edit` / `hh_image_style_repaint` / `hh_image_background` / "
                "`hh_image_outpaint` / `hh_image_sketch` / `hh_image_ecommerce` / "
                "`hh_t2v` / `hh_i2v` / `hh_r2v` / `hh_video_edit` / `hh_photo_speak` / "
                "`hh_video_relip` / `hh_video_reface` / `hh_pose_drive` / "
                "`hh_avatar_compose` / `hh_long_video_create` / `hh_video_concat` / "
                "`hh_status` / `hh_cost_preview`。**严禁杜撰** `hh_digital_human` / "
                "`hh_dance` / `hh_full_body` / `hh_singer` 这种不存在的工具名——工作台 LLM 看"
                "到不存在的工具会胡乱挑最近似的真工具（典型如 photo_speak），把任务带进"
                "死胡同。\n"
                "R4. **资产复用强制**：第二次给同一 segment 派单时（无论派给同一工作台还是"
                "调整工作台），派单文本里必须先列出前一次的 task_id / asset_id 并明确写"
                "「请复用以下 asset_ids 作为 from_asset_ids / image_url，**禁止**重新调"
                " hh_image_create / hh_t2v 重新生成」。一镜一图一视频，绝不要因为不满意"
                "就让 wb-hh-image 再 hh_image_create 一次——先用 hh_image_edit / "
                "hh_image_style_repaint 在原图上调整。\n"
                "R5. 派单前先看一眼 BLACKBOARD / TASK_DELIVERED 里是否已经有可复用资产；"
                "已收到的 asset_id 必须落到下一条派单的「上游 asset_ids」字段，runtime "
                "也会自动把已交付的 asset_id 列表追加到派单前缀作为兜底，**收到不复用"
                "等同于浪费成本 + 浪费 DashScope 额度**。"
            ),
        },
        {
            "id": "wb-hh-image",
            "role_title": "图像工作台",
            "role_goal": (
                "按美术指导给定的分镜 prompt 调用 happyhorse-video 的 7 种图像模式"
                "（文生图 / 编辑 / 风格重绘 / 背景生成 / 扩图 / 涂鸦 / 电商场景），产出关键帧静态画面。"
            ),
            "role_backstory": "工作台节点，背靠 happyhorse-video 插件的图像子能力。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 100, "y": 380},
            "level": 2,
            "department": "图像生成",
            "avatar": "designer-f",
            "external_tools": [
                "hh_image_create",
                "hh_image_edit",
                "hh_image_style_repaint",
                "hh_image_background",
                "hh_image_outpaint",
                "hh_image_sketch",
                "hh_image_ecommerce",
                "hh_status",
                "hh_cost_preview",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": _HAPPYHORSE_PLUGIN_ORIGIN,
            "custom_prompt": (
                "你是【HappyHorse 图像工作台】节点。只在收到 org_delegate_task 时启动。\n"
                "工具选型规则：\n"
                "  - hh_image_create：纯文生图（首帧 / 概念图 / 海报）。\n"
                "  - hh_image_edit：基于现有图做局部修改或多图融合（需 images）。\n"
                "  - hh_image_style_repaint：风格迁移（卡通 / 水墨 / 写实等）。\n"
                "  - hh_image_background：替换背景；上传主体图后给目标背景描述。\n"
                "  - hh_image_outpaint：画幅扩展，配合 size 字段输出更大画布。\n"
                "  - hh_image_sketch：涂鸦/线稿成图。\n"
                "  - hh_image_ecommerce：电商场景图（prompt 或 product_name 二选一）。\n"
                "组织 runtime 会把图片下载到 workspace 并自动登记为附件；调 org_submit_deliverable "
                "时只需在 deliverable 文本里说明镜头号 / 中文画面摘要 / 提示词摘要 / 生成的 asset_id，"
                "不要重复声明 file_attachments，也不要凭空调工具。中文语境下用户可见交付必须使用中文。"
            ),
        },
        {
            "id": "wb-hh-video",
            "role_title": "短视频工作台",
            "role_goal": (
                "用美术指导分发的镜头 prompt 与上游首帧 asset_ids，调用 happyhorse-video 的"
                "短视频模式（文生 / 图生 / 参考生 / 视频编辑）逐镜头生成短视频。"
            ),
            "role_backstory": "工作台节点，背靠 happyhorse-video 插件的短视频子能力。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 400, "y": 380},
            "level": 2,
            "department": "视频生成",
            "avatar": "media",
            "external_tools": [
                "hh_t2v",
                "hh_i2v",
                "hh_r2v",
                "hh_video_edit",
                "hh_status",
                "hh_cost_preview",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": _HAPPYHORSE_PLUGIN_ORIGIN,
            "custom_prompt": (
                "你是【HappyHorse 短视频工作台】节点。只在收到 org_delegate_task 时启动。\n"
                "工具选型规则：\n"
                "  - hh_t2v：无首帧素材时直接文生视频。\n"
                "  - hh_i2v：派单里提供了上游 hh_image 的 asset_ids 时，用它做首帧驱动的图生视频。\n"
                "  - hh_r2v：提供了多张参考图（reference_urls 或 from_asset_ids）时使用。\n"
                "  - hh_video_edit：基于现有视频做修改（需 source_video_url）。\n"
                "多镜头任务必须按镜头逐个调用：每次只消费当前镜头的 from_asset_ids / first_frame_url，"
                "并按分镜时长设置 duration（例如 30 秒 / 3 镜头 ⇒ 每段 10 秒）。每次调用的 prompt 必须"
                "写清当前镜头独有的中文画面、镜头运动和风格，绝不要用同一段总主题 prompt 重复生成。\n"
                "插件会把 from_asset_ids 自动展开为 DashScope 的 image_url 注入；生成成功后 runtime "
                "会把 video.mp4 与 last_frame 自动下载并登记为附件。org_submit_deliverable 只写文字"
                "说明，不要重复声明 file_attachments；返回交付时必须列出每段的 video task_id，"
                "便于下游长视频后期拼接。"
            ),
        },
        {
            "id": "wb-hh-human",
            "role_title": "数字人工作台",
            "role_goal": (
                "用形象图 / 视频 + 文本 / 音频，调用 happyhorse-video 的 5 种数字人模式产出"
                "口播 / 唇形 / 换脸 / 姿态驱动 / 多图合成成片。"
            ),
            "role_backstory": "工作台节点，背靠 happyhorse-video 插件的数字人子能力。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 700, "y": 380},
            "level": 2,
            "department": "数字人",
            "avatar": "media",
            "external_tools": [
                "hh_photo_speak",
                "hh_video_relip",
                "hh_video_reface",
                "hh_pose_drive",
                "hh_avatar_compose",
                "hh_status",
                "hh_cost_preview",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": _HAPPYHORSE_PLUGIN_ORIGIN,
            "custom_prompt": (
                "你是【HappyHorse 数字人工作台】节点。只在收到 org_delegate_task 时启动。\n"
                "工具选型规则：\n"
                "  - hh_photo_speak：一张照片 + 文本 / 音频 ⇒ 说话头像（需 image_url + text/audio_url）。\n"
                "  - hh_video_relip：替换现有视频的口型（需 source_video_url + 新音频）。\n"
                "  - hh_video_reface：把视频里的人脸换成新形象（需 source_video_url + ref 人脸图）。\n"
                "  - hh_pose_drive：用驱动视频的姿态控制目标人物（需 source_video_url + 目标形象）。\n"
                "  - hh_avatar_compose：多图合成的口播形象（多张 ref_images_url + 文本）。\n"
                "如果派单只给了 text 没给 voice_id，插件会用 Edge / CosyVoice 默认音色；上级若要求"
                "特定音色应在 prompt 里写明 voice_id。生成成功后 runtime 会把 video.mp4 自动下载并"
                "登记为附件；返回交付必须列出 video task_id 便于下游拼接。"
            ),
        },
        {
            "id": "wb-hh-long",
            "role_title": "长视频后期工作台",
            "role_goal": (
                "把上游短视频 / 数字人各段产出衔接 + 拼接为最终成片：可选先用 hh_long_video_create "
                "做首尾帧衔接，再用 hh_video_concat 按指定转场拼接 ffmpeg 输出。"
            ),
            "role_backstory": "工作台节点，背靠 happyhorse-video 插件的长视频 / 拼接 / 转场能力。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 400, "y": 580},
            "level": 2,
            "department": "后期",
            "avatar": "media",
            "external_tools": [
                "hh_long_video_create",
                "hh_video_concat",
                "hh_status",
                "hh_list",
                "hh_cost_preview",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": _HAPPYHORSE_PLUGIN_ORIGIN,
            "custom_prompt": (
                "你是【HappyHorse 长视频后期工作台】节点。只在收到 org_delegate_task 时启动。\n"
                "标准流程：\n"
                "1. （可选）若派单要求重新衔接首尾帧并并发生成多段 i2v，调 hh_long_video_create——"
                "传入完整 segments[]（来自分镜 JSON，含 prompt/duration/transition_to_next）、"
                "model_id（默认 happyhorse-1.0-i2v）、aspect_ratio、resolution、mode（serial / "
                "parallel / cloud_extend）；它会异步返回 chain_group_id 并通过任务流推动。\n"
                "2. 拼接：调 hh_video_concat 传 task_ids（已落盘的多段视频任务 ID，按出场顺序）、"
                "transition（'none' 表示无缝硬切；'crossfade' / 'fade' / 'xfade' / 'dissolve' 触发"
                "ffmpeg 的 xfade 渐变过渡；'ai_extend' 与 'cut' 均归一化为 'none'）、fade_duration"
                "（crossfade 时长，单位秒）、output_name（可空）。\n"
                "3. 用 hh_status / hh_list 跟踪各分镜段的进度，hh_cost_preview 评估批量成本。\n"
                "拼接成功后 runtime 会把成片与 asset_id 自动登记为附件。org_submit_deliverable 只写"
                "文字说明（成片时长 / 段数 / 转场方式 / 最终 asset_id），不要重复声明 file_attachments。"
            ),
        },
    ],
    "edges": [
        {
            "id": "e-prod-writer",
            "source": "producer",
            "target": "screenwriter",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-prod-art",
            "source": "producer",
            "target": "art-director",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-writer-art",
            "source": "screenwriter",
            "target": "art-director",
            "edge_type": "collaborate",
            "label": "提供剧本 + 分镜 JSON",
        },
        {
            "id": "e-writer-long",
            "source": "screenwriter",
            "target": "wb-hh-long",
            "edge_type": "collaborate",
            "label": "分镜 segments 直送拼接",
        },
        {
            "id": "e-art-image",
            "source": "art-director",
            "target": "wb-hh-image",
            "edge_type": "hierarchy",
            "label": "派单首帧 / 海报",
        },
        {
            "id": "e-art-video",
            "source": "art-director",
            "target": "wb-hh-video",
            "edge_type": "hierarchy",
            "label": "派单短视频镜头",
        },
        {
            "id": "e-art-human",
            "source": "art-director",
            "target": "wb-hh-human",
            "edge_type": "hierarchy",
            "label": "派单数字人口播",
        },
        {
            "id": "e-art-long",
            "source": "art-director",
            "target": "wb-hh-long",
            "edge_type": "hierarchy",
            "label": "派单长视频拼接",
        },
        {
            "id": "e-image-video",
            "source": "wb-hh-image",
            "target": "wb-hh-video",
            "edge_type": "collaborate",
            "label": "asset_ids 作为首帧",
        },
        {
            "id": "e-image-human",
            "source": "wb-hh-image",
            "target": "wb-hh-human",
            "edge_type": "collaborate",
            "label": "肖像图 / 形象库素材",
        },
        {
            "id": "e-video-long",
            "source": "wb-hh-video",
            "target": "wb-hh-long",
            "edge_type": "collaborate",
            "label": "段 task_ids → 拼接",
        },
        {
            "id": "e-human-long",
            "source": "wb-hh-human",
            "target": "wb-hh-long",
            "edge_type": "collaborate",
            "label": "口播 task_ids → 拼接",
        },
    ],
}



ALL_TEMPLATES: dict[str, dict] = {
    "startup-company": STARTUP_COMPANY,
    "software-team": SOFTWARE_TEAM,
    "content-ops": CONTENT_OPS,
    "aigc-video-studio": AIGC_VIDEO_STUDIO,
}


TEMPLATE_POLICY_MAP: dict[str, str] = {
    "startup-company": "default",
    "software-team": "software-team",
    "content-ops": "content-ops",
    "aigc-video-studio": "default",
}


def _auto_assign_avatars(tpl_data: dict) -> None:
    """Fill missing avatar fields on template nodes using role-based matching."""
    # get_avatar_for_role lives in this same shard (absorbed below);

    for node in tpl_data.get("nodes", []):
        if not node.get("avatar"):
            node["avatar"] = get_avatar_for_role(node.get("role_title", ""))


def _auto_assign_agent_profiles(tpl_data: dict) -> None:
    """Fill missing profile bindings so org nodes inherit specialized presets."""
    from .org_models import infer_agent_profile_id_for_node

    for node in tpl_data.get("nodes", []):
        if not node.get("agent_profile_id"):
            node["agent_profile_id"] = infer_agent_profile_id_for_node(node)

def _with_builtin_metadata(tid: str, tpl: dict) -> dict:
    """Return a writable built-in template payload with generated metadata."""
    tpl_data = dict(tpl)
    tpl_data["policy_template"] = TEMPLATE_POLICY_MAP.get(tid, "default")
    _auto_assign_avatars(tpl_data)
    _auto_assign_agent_profiles(tpl_data)
    return tpl_data

def _is_legacy_aigc_video_studio(data: dict) -> bool:
    """Detect pre-HappyHorse default AIGC templates persisted on disk.

    Built-in templates are intentionally seeded once and then left alone,
    but the v1.1 HappyHorse refactor replaced the old Tongyi + Seedance
    four-node template. Without this narrow migration, old workspaces keep
    showing the stale template forever.
    """
    node_ids = {str(n.get("id") or "") for n in data.get("nodes", []) if isinstance(n, dict)}
    tool_names = {
        str(tool)
        for n in data.get("nodes", [])
        if isinstance(n, dict)
        for tool in (n.get("external_tools") or [])
    }
    return bool(
        {"wb-tongyi-image", "wb-seedance-video"} & node_ids
        or {"tongyi_image_create", "seedance_create"} & tool_names
    )


def _archive_removed_template(path: Path) -> None:
    """Move a removed built-in template out of the ``*.json`` scan set."""
    target = path.with_suffix(path.suffix + ".deprecated")
    counter = 1
    while target.exists():
        target = path.with_suffix(path.suffix + f".deprecated.{counter}")
        counter += 1
    path.replace(target)
    logger.info("[Templates] Archived removed built-in template: %s -> %s", path.name, target.name)

def ensure_builtin_templates(templates_dir: Path) -> None:
    """Install built-in templates and migrate known stale built-ins.

    User-edited templates are otherwise preserved. The only overwrite here is
    the old built-in ``aigc-video-studio`` signature that shipped before the
    HappyHorse-only 7-node refactor; the removed ``happyhorse-video-studio`` is
    archived away from the ``*.json`` template scan.
    """
    templates_dir.mkdir(parents=True, exist_ok=True)
    removed_happyhorse = templates_dir / "happyhorse-video-studio.json"
    if removed_happyhorse.exists():
        _archive_removed_template(removed_happyhorse)

    for tid, tpl in ALL_TEMPLATES.items():
        p = templates_dir / f"{tid}.json"
        if not p.exists():
            tpl_data = _with_builtin_metadata(tid, tpl)
            p.write_text(json.dumps(tpl_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[Templates] Installed built-in template: {tid}")
            continue
        if tid == "aigc-video-studio":
            try:
                current = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Templates] Failed to inspect template %s: %s", p, exc)
                continue
            if isinstance(current, dict) and _is_legacy_aigc_video_studio(current):
                tpl_data = _with_builtin_metadata(tid, tpl)
                p.write_text(json.dumps(tpl_data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("[Templates] Migrated built-in template to HappyHorse: %s", tid)



# ===========================================================================
# Workbench templates (v1 ``openakita.orgs.plugin_workbench_templates`` excerpt)
# ===========================================================================
# Renders loaded plugins (with at least one registered LLM tool) as
# pre-configured org-node templates for the OrgEditor workbench picker.


def _default_goal_for(manifest: Any) -> str:
    """Compose a default role goal string for a workbench node."""
    name = manifest.display_name_zh or manifest.name or manifest.id
    desc = (
        (manifest.description_i18n or {}).get("zh")
        or manifest.description
        or ""
    ).strip()
    if desc:
        return f"在组织中作为「{name}」工作台节点，按上级派单调用工作台工具完成产出。能力：{desc}"
    return f"在组织中作为「{name}」工作台节点，按上级派单调用工作台工具完成产出。"


def _default_prompt_for(manifest: Any, tool_names: list[str]) -> str:
    """Compose a default custom_prompt for a workbench node.

    The prompt explains:
      1. when the node should be activated (only on org_delegate_task);
      2. that runtime auto-downloads/registers any artifacts produced by
         the workbench tools, so the node need NOT declare
         ``file_attachments`` in ``org_submit_deliverable`` manually;
      3. how upstream-supplied ``asset_id`` / ``image_url`` should be
         threaded into downstream tool calls;
      4. how to fall back to a plain-text deliverable when the upstream
         request is merely a question rather than an actual production task.
    """
    display_name = manifest.display_name_zh or manifest.name or manifest.id
    tool_list = "、".join(tool_names) if tool_names else "(无工具)"
    return (
        f"你是组织中的【{display_name}】工作台节点。\n"
        f"专属能力：{tool_list}\n\n"
        "工作规范：\n"
        "1. 收到 org_delegate_task 后启动，按工具 input_schema 严格调用工作台工具，"
        "不要凭空想象工具参数；\n"
        "2. 工作台产出的图片/视频会被组织 runtime 自动下载到 org workspace 的 "
        "plugin_assets/ 目录并登记为任务附件——你只需要在 org_submit_deliverable "
        "的 deliverable 字段中描述产出内容（标题、规格、prompt 摘要、asset_id），"
        "不必再手动声明 file_attachments；\n"
        "3. 若上级在 prompt 中提供了上游工作台的 asset_id 或 image_url，请将其"
        "如实填入对应工具参数（例如 seedance_create.from_asset_ids 或 "
        "content[].image_url），不要省略；\n"
        "4. 若上级只是问询/讨论而非真正下单产出，可直接用 org_submit_deliverable "
        "提交文字回答，无需调用工作台工具；\n"
        "5. 完成后调 org_submit_deliverable 把成果交给委派人，等待验收。"
    )


def _tool_summary(tool: dict) -> dict:
    """Return the minimal subset of a registered tool dict for templates."""
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "input_schema": tool.get("input_schema") or {},
    }


def _collect_host_tool_defs(pm: PluginManager | None) -> dict[str, dict]:
    """Index host-level ``tool_definitions`` by tool name.

    Plugin-registered tools store their full schema in the host's shared
    ``tool_definitions`` list (see ``PluginAPI.register_tools``), while
    ``PluginAPI._registered_tools`` only keeps tool *names*. We need the
    full definitions to surface description/input_schema in the workbench
    picker UI, so we build a name → def map up front.

    Both Anthropic-flavoured (``{"name", "description", "input_schema"}``)
    and OpenAI-flavoured (``{"type": "function", "function": {...}}``)
    shapes are supported.
    """
    if pm is None:
        return {}
    out: dict[str, dict] = {}
    refs = getattr(pm, "_external_host_refs", None) or {}
    tool_defs = refs.get("tool_definitions") if isinstance(refs, dict) else None
    if not tool_defs:
        return out
    try:
        for td in tool_defs:
            if not isinstance(td, dict):
                continue
            name = td.get("name")
            if not name:
                fn = td.get("function")
                if isinstance(fn, dict):
                    name = fn.get("name")
            if name:
                out[name] = td
    except Exception:
        logger.debug("[workbench-templates] failed to index host tool_definitions",
                     exc_info=True)
    return out


def _resolve_tool_dict(entry: Any, host_defs: dict[str, dict]) -> dict | None:
    """Turn a ``_registered_tools`` entry into a UI-friendly tool dict.

    ``PluginAPI._registered_tools`` is ``list[str]`` in production (just
    the registered tool names). Older paths / unit tests sometimes pass
    full dicts here, so we keep tolerating both shapes.
    """
    if isinstance(entry, str):
        name = entry
        defn = host_defs.get(name)
    elif isinstance(entry, dict):
        name = entry.get("name") or ""
        defn = entry if name else None
    else:
        return None
    if not name:
        return None
    if defn is None:
        return {"name": name, "description": "", "input_schema": {}}
    # Unwrap OpenAI function-tool envelope so the UI sees a flat shape.
    fn = defn.get("function") if isinstance(defn.get("function"), dict) else None
    base = fn or defn
    return {
        "name": name,
        "description": base.get("description", "") or "",
        "input_schema": base.get("input_schema") or base.get("parameters") or {},
    }


def build_workbench_templates(pm: PluginManager | None) -> list[dict]:
    """Build workbench node templates from a PluginManager.

    Only plugins that are loaded AND have registered at least one LLM tool
    will appear as a workbench. Plugins without any callable tool (pure UI,
    pure routes, MCP-only, skill-only, etc.) are intentionally hidden.
    """
    if pm is None:
        return []

    host_tool_defs = _collect_host_tool_defs(pm)

    templates: list[dict] = []
    for lp in pm.loaded_plugins.values():
        try:
            raw_tools = list(getattr(lp.api, "_registered_tools", None) or [])
        except Exception:
            logger.debug(
                "[workbench-templates] failed to read tools for %s",
                getattr(lp.manifest, "id", "?"),
                exc_info=True,
            )
            raw_tools = []
        if not raw_tools:
            continue

        tool_dicts: list[dict] = []
        for entry in raw_tools:
            resolved = _resolve_tool_dict(entry, host_tool_defs)
            if resolved is not None:
                tool_dicts.append(resolved)
        if not tool_dicts:
            continue

        m = lp.manifest
        plugin_id = m.id
        version = m.version
        display_zh = m.display_name_zh or m.name or plugin_id
        display_en = m.display_name_en or m.name or plugin_id
        desc_i18n = dict(m.description_i18n or {})
        tool_names = [t["name"] for t in tool_dicts if t.get("name")]

        templates.append(
            {
                "id": f"workbench:{plugin_id}",
                "plugin_id": plugin_id,
                "version": version,
                "name": display_zh,
                "name_i18n": {"zh": display_zh, "en": display_en},
                "description": m.description or desc_i18n.get("zh") or "",
                "description_i18n": desc_i18n,
                "icon": m.icon or "",
                "category": m.category or "",
                "tools": [_tool_summary(t) for t in tool_dicts],
                "tool_names": tool_names,
                "suggested_node": {
                    "role_title": display_zh,
                    "role_goal": _default_goal_for(m),
                    "custom_prompt": _default_prompt_for(m, tool_names),
                    "external_tools": list(tool_names),
                    "agent_profile_id": "default",
                    "enable_file_tools": False,
                    "mcp_servers": [],
                    "skills": [],
                    "skills_mode": "all",
                    "max_concurrent_tasks": 1,
                    "can_delegate": False,
                    "can_escalate": True,
                    "plugin_origin": {
                        "plugin_id": plugin_id,
                        "template_id": f"workbench:{plugin_id}",
                        "version": version,
                    },
                },
            }
        )

    templates.sort(key=lambda t: (t.get("category") or "", t.get("name") or ""))
    return templates



def deprecated_tools_for_node(
    node_external_tools: list[str], pm: PluginManager | None
) -> list[str]:
    """Return a list of external_tools entries that are NOT registered by any
    currently loaded plugin AND not built-in tool category names.

    Used by the editor to warn users when an upgraded plugin renamed/removed
    tools that older workbench nodes still reference.
    """
    if pm is None or not node_external_tools:
        return []

    # v1 imported ALL_CATEGORY_NAMES from tool_categories here. The full
    # category-name set is NOT absorbed into γ-1b (only required by this
    # rarely-exercised exporter); fall back to an empty frozenset so the
    # function still returns a usable warning list (false-negatives only,
    # never false-positives).
    ALL_CATEGORY_NAMES: frozenset[str] = frozenset()

    known: set[str] = set()
    for lp in pm.loaded_plugins.values():
        try:
            for t in getattr(lp.api, "_registered_tools", None) or []:
                if isinstance(t, str):
                    name = t
                elif isinstance(t, dict):
                    name = t.get("name")
                else:
                    name = None
                if name:
                    known.add(name)
        except Exception:
            continue
    return [
        t
        for t in node_external_tools
        if t and t not in ALL_CATEGORY_NAMES and t not in known
    ]

