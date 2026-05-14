"""
预置组织模板

提供三套预构建的组织架构模板，可通过 OrgManager 安装到 data/org_templates/。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

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
# 这个模板演示如何把已加载的插件以「工作台节点」的形式编入组织：
#   - `wb-tongyi-image`   → 依赖已安装/已加载的 `tongyi-image` 工作台插件
#   - `wb-seedance-video` → 依赖已安装/已加载的 `seedance-video` 工作台插件
#
# 工作台节点必须是叶子节点（manager + runtime 双重校验），不允许挂下属。
# 节点 `external_tools` 直接列出插件注册的工具名，运行时由
# ``expand_tool_categories`` 原样透传，OrgRuntime 会自动给这些节点的
# system prompt 追加「工作台能力段 + 交付协议」，并在工具调用成功时把
# 远端 image_urls / video_url 下载到 org workspace，注册为任务附件。
#
# 工作流（指挥台 → CEO）：
#   1. 制片人收到选题，把脚本与分镜描述交给编剧细化
#   2. 编剧把每个镜头的视觉 prompt 整理好后，制片人派单给「通义生图工作台」
#   3. 通义生图返回 image_urls + asset_ids，runtime 自动登记为附件
#   4. 制片人把 asset_ids 透传给「即梦视频工作台」，请求图生视频
#   5. 即梦视频通过 `from_asset_ids` 直接消费上游分镜，生成成片
#   6. 制片人汇总文字脚本 + 分镜图 + 成片，交付给出品方
#
# 安装前置（前端在选用此模板时会用 deprecated_tools_for_node() 提示）：
#   - 在「插件管理」里安装并启用 `tongyi-image`（需要 DASHSCOPE_API_KEY）
#   - 在「插件管理」里安装并启用 `seedance-video`（需要 ARK_API_KEY 或同等的
#     豆包视频 API 凭证）
# ---------------------------------------------------------------------------

AIGC_VIDEO_STUDIO: dict = {
    "name": "AIGC 视频创作工作室",
    "description": (
        "由制片人统筹、编剧产出脚本、通义生图工作台出分镜、即梦视频工作台合成"
        "成片的端到端 AIGC 短片流水线。需要预先在「插件管理」中启用 tongyi-image "
        "与 seedance-video 两个工作台插件。"
    ),
    "icon": "🎬",
    "tags": ["aigc", "video", "workbench", "tongyi-image", "seedance-video"],
    "user_persona": {
        "title": "出品方",
        "display_name": "出品方",
        "description": "短片选题与最终成片验收人",
    },
    "core_business": (
        "围绕短视频/广告片选题，按「脚本 → 分镜图 → 成片」三段式流水线快速生产 "
        "AIGC 视频。所有图片/视频产出会自动落到组织 workspace 的 plugin_assets/ "
        "目录，并作为附件附在任务交付上。"
    ),
    "heartbeat_enabled": False,
    "heartbeat_interval_s": 3600,
    "heartbeat_prompt": "审视当前选题进度，识别脚本/分镜/成片阶段的卡点。",
    "standup_enabled": False,
    "standup_cron": "0 10 * * 1-5",
    "standup_agenda": "脚本、分镜、视频成片三个阶段的产出与阻塞同步。",
    "allow_cross_level": True,
    # 工作台节点必须是叶子，所以最深委派深度 2 已经够用（出品方 → 制片人 →
    # 编剧 / 工作台）。
    "max_delegation_depth": 3,
    "conflict_resolution": "manager",
    "scaling_enabled": False,
    "max_nodes": 8,
    "scaling_approval": "manager",
    "nodes": [
        {
            "id": "producer",
            "role_title": "制片人",
            "role_goal": (
                "把出品方的选题拆成可执行的工序——找编剧细化脚本与分镜，"
                "再把分镜描述派给通义生图工作台出图，最后把 asset_ids 透传给"
                "即梦视频工作台合成视频，并对最终成片负责。"
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
                "工作流：\n"
                "1. 把出品方的选题派给『编剧』节点，让他用 org_submit_deliverable "
                "返回脚本（含人物/场景/分镜描述与对白）。\n"
                "2. 收到脚本后，把每个镜头的视觉 prompt 整理成清单，调用 "
                "org_delegate_task 派给『通义生图工作台』节点，请求按镜头出图。\n"
                "3. 通义生图工作台交付时，runtime 已经把图片下载到 workspace 并附"
                "在 TASK_DELIVERED 上；同时它的 deliverable 文本会包含 asset_ids。"
                "把这些 asset_ids 与对应镜头的运动/时长/风格描述一起，派给『即梦"
                "视频工作台』节点，要求把 asset_ids 填入 seedance_create 的 "
                "from_asset_ids 字段。\n"
                "4. 视频工作台返回 video_url + 本地路径后，最终向出品方交付：剧本"
                "（文字）+ 分镜图（附件）+ 成片（附件）。"
            ),
        },
        {
            "id": "screenwriter",
            "role_title": "编剧",
            "role_goal": "把选题拆成分镜脚本，给每个镜头写出可直接用于生图的视觉描述。",
            "role_backstory": "广告短片编剧，熟悉 AIGC 工具的 prompt 写法。",
            "agent_source": "local",
            "agent_profile_id": "content-creator",
            "position": {"x": 150, "y": 180},
            "level": 1,
            "department": "创意",
            "avatar": "writer",
            "external_tools": ["research", "planning", "filesystem", "memory"],
            "custom_prompt": (
                "你是组织里的编剧节点。收到选题后产出：(a) 一个完整剧本（场景、"
                "人物、对白）；(b) 每个镜头的视觉 prompt 列表（编号、画面描述、"
                "镜头语言、风格关键词）。最终用 org_submit_deliverable 交付，并把"
                "脚本同时落盘为 markdown 文件作为附件。"
            ),
        },
        {
            "id": "wb-tongyi-image",
            "role_title": "通义生图工作台",
            "role_goal": "按制片人/编剧给定的分镜 prompt 调用通义生图，产出关键帧静态画面。",
            "role_backstory": "工作台节点，背靠 tongyi-image 插件，专职文生图与图生图。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 400, "y": 180},
            "level": 1,
            "department": "图像生成",
            "avatar": "designer-f",
            "external_tools": [
                "tongyi_image_create",
                "tongyi_image_status",
                "tongyi_image_list",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": {
                "plugin_id": "tongyi-image",
                "template_id": "workbench:tongyi-image",
            },
            "custom_prompt": (
                "你是【通义生图】工作台节点。只在收到 org_delegate_task 时启动，"
                "按 input_schema 调用 tongyi_image_create 出图。组织 runtime 会把"
                "图片下载到 workspace 并自动登记为附件；调 org_submit_deliverable "
                "时只需在 deliverable 文本里说明产出（镜头号、prompt 摘要、生成的"
                "asset_id），不要重复声明 file_attachments。若上级仅是问询/讨论"
                "（没有明确派单），直接用 org_submit_deliverable 返回文字答复，"
                "不要凭空调用工具。"
            ),
        },
        {
            "id": "wb-seedance-video",
            "role_title": "即梦视频工作台",
            "role_goal": "用编剧脚本与通义生图的分镜画面，调用即梦视频生成短片成片。",
            "role_backstory": "工作台节点，背靠 seedance-video 插件，专职文生视频与图生视频。",
            "agent_source": "local",
            "agent_profile_id": "default",
            "position": {"x": 650, "y": 180},
            "level": 1,
            "department": "视频生成",
            "avatar": "media",
            "external_tools": [
                "seedance_create",
                "seedance_edit",
                "seedance_extend",
                "seedance_transition",
                "seedance_status",
                "seedance_list",
            ],
            "enable_file_tools": False,
            "can_delegate": False,
            "plugin_origin": {
                "plugin_id": "seedance-video",
                "template_id": "workbench:seedance-video",
            },
            "custom_prompt": (
                "你是【即梦视频】工作台节点。只在收到 org_delegate_task 时启动；"
                "当上级的派单 prompt 里给了上游通义生图的 asset_ids 时，必须把它"
                "们填到 seedance_create 的 from_asset_ids 字段，由插件自动展开为 "
                "Ark 的 content[image_url] 注入（i2v/i2v_end/multimodal 模式会按"
                "位置自动分配 first_frame/last_frame/reference_image 角色）。生成"
                "成功后 runtime 会把 video.mp4 与 last_frame 自动下载并登记为附件。"
                "如果任务是编辑、延长或镜头间 AI 过渡，必须使用上游 seedance "
                "任务返回的公网 video_url，分别调用 seedance_edit、seedance_extend "
                "或 seedance_transition；不能把本地视频文件/base64 当作源视频上传。"
                "提交 org_submit_deliverable 时只需写文字说明，不要重复声明 "
                "file_attachments。"
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
            "id": "e-prod-tongyi",
            "source": "producer",
            "target": "wb-tongyi-image",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-prod-seedance",
            "source": "producer",
            "target": "wb-seedance-video",
            "edge_type": "hierarchy",
            "label": "",
        },
        {
            "id": "e-writer-tongyi",
            "source": "screenwriter",
            "target": "wb-tongyi-image",
            "edge_type": "collaborate",
            "label": "提供分镜 prompt",
        },
        {
            "id": "e-tongyi-seedance",
            "source": "wb-tongyi-image",
            "target": "wb-seedance-video",
            "edge_type": "collaborate",
            "label": "传递 asset_ids 作为首帧/参考帧",
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
    from openakita.orgs.tool_categories import get_avatar_for_role

    for node in tpl_data.get("nodes", []):
        if not node.get("avatar"):
            node["avatar"] = get_avatar_for_role(node.get("role_title", ""))


def _auto_assign_agent_profiles(tpl_data: dict) -> None:
    """Fill missing profile bindings so org nodes inherit specialized presets."""
    from openakita.orgs.models import infer_agent_profile_id_for_node

    for node in tpl_data.get("nodes", []):
        if not node.get("agent_profile_id"):
            node["agent_profile_id"] = infer_agent_profile_id_for_node(node)


def ensure_builtin_templates(templates_dir: Path) -> None:
    """Install built-in templates if they don't exist."""
    templates_dir.mkdir(parents=True, exist_ok=True)
    for tid, tpl in ALL_TEMPLATES.items():
        p = templates_dir / f"{tid}.json"
        if not p.exists():
            tpl_data = dict(tpl)
            tpl_data["policy_template"] = TEMPLATE_POLICY_MAP.get(tid, "default")
            _auto_assign_avatars(tpl_data)
            _auto_assign_agent_profiles(tpl_data)
            p.write_text(json.dumps(tpl_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[Templates] Installed built-in template: {tid}")
