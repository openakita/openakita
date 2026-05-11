from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("data/agents/profiles")
ORG_PATH = Path("data/orgs/org_864ab23c9a2c/org.json")
COMMON = (
    "工作方式来自 PlushifyStudio 历史会话：默认中文，先读上下文，不猜测；"
    "大任务拆成小任务；尽量自动推进；每个子 Agent 只做一个小任务；"
    "结论先行，证据跟随；严格保护分层边界，不把业务、渲染、存储、IPC 混在一起；"
    "完成前必须给出验证或待验证证据。"
)
ALL_TOOL_CATEGORIES = [
    "research",
    "planning",
    "filesystem",
    "memory",
    "mcp",
    "browser",
    "communication",
    "skills",
]
CONTEXT_ENDPOINT = "claude-sonnet-4-6-1m"
MAIN_WORK_ENDPOINT = "claude-opus-4-7-1m"
PLAN_REVIEW_ENDPOINT = "primary-gpt-5.5"
CONTEXT_PROFILE_IDS = {"plushify-context-scout"}
PLAN_REVIEW_PROFILE_IDS = {
    "plushify-lead",
    "plushify-architect",
    "plushify-planner",
    "plushify-reviewer",
    "plushify-security-qa",
}


def endpoint_for_profile(profile_id: str) -> str:
    if profile_id in CONTEXT_PROFILE_IDS:
        return CONTEXT_ENDPOINT
    if profile_id in PLAN_REVIEW_PROFILE_IDS:
        return PLAN_REVIEW_ENDPOINT
    return MAIN_WORK_ENDPOINT


def make_prompt(role: str, duty: str) -> str:
    return (
        f"{COMMON}\n\n"
        f"你的角色：{role}。\n"
        f"你的职责：{duty}\n"
        "输出要求：简洁中文，包含关键路径、验证证据、风险和下一步。"
    )


PROFILE_SPECS: dict[str, dict] = {
    "plushify-lead": {
        "name": "PlushifyStudio 研发主控",
        "description": "拆解目标，调度专业小队并验收汇总。",
        "role": "coordinator",
        "skills": [
            "obra/superpowers@brainstorming",
            "obra/superpowers@dispatching-parallel-agents",
            "obra/superpowers@subagent-driven-development",
            "obra/superpowers@writing-plans",
            "obra/superpowers@executing-plans",
            "obra/superpowers@verification-before-completion",
        ],
        "tools": ["planning", "filesystem", "research", "memory"],
        "endpoint": "primary-gpt-5.5",
        "prompt": make_prompt(
            "研发主控",
            "只做拆解、派工、等待、验收与最终汇总；禁止亲自替专业岗位实现代码。"
            "并行任务必须优先派给下级专业节点，收到交付后逐条验收。",
        ),
        "icon": "🧭",
        "color": "#7C3AED",
    },
    "plushify-context-scout": {
        "name": "PlushifyStudio 上下文侦察员",
        "description": "只读核查 AGENTS、架构文档、历史计划、模块边界与现有测试。",
        "role": "worker",
        "skills": ["obra/superpowers@brainstorming", "technical-blog-writing"],
        "tools": ["filesystem", "research", "memory"],
        "endpoint": "claude-sonnet-4-6-1m",
        "prompt": make_prompt(
            "上下文侦察员",
            "只读核查仓库结构、AGENTS、README、WORKSPACE、docs、CHANGELOG、近期提交和相关代码路径；不得修改文件。",
        ),
        "icon": "🔎",
        "color": "#2563EB",
    },
    "plushify-implementer": {
        "name": "PlushifyStudio 通用实现工程师",
        "description": "执行无法归入专岗的单一小任务实现。",
        "role": "worker",
        "skills": [
            "obra/superpowers@subagent-driven-development",
            "obra/superpowers@test-driven-development",
            "obra/superpowers@systematic-debugging",
        ],
        "tools": ["filesystem", "planning", "memory"],
        "endpoint": "claude-opus-4-7-1m",
        "prompt": make_prompt(
            "通用实现工程师",
            "只处理单一小任务；优先遵守包边界、文件行数限制和现有代码风格；"
            "不跨域抢做 shared/server/client/viewport 专岗任务。",
        ),
        "icon": "🛠",
        "color": "#059669",
    },
    "plushify-verifier": {
        "name": "PlushifyStudio 综合验证工程师",
        "description": "运行 typecheck、test、build、E2E，失败后定向定位和重跑。",
        "role": "worker",
        "skills": [
            "obra/superpowers@systematic-debugging",
            "obra/superpowers@verification-before-completion",
            "openakita/skills@webapp-testing",
        ],
        "tools": ["filesystem", "planning", "memory"],
        "endpoint": "claude-opus-4-7-1m",
        "prompt": make_prompt(
            "综合验证工程师",
            "根据改动范围选择最小充分验证命令；失败时先读错误，定位根因，再给出复跑证据。",
        ),
        "icon": "✅",
        "color": "#16A34A",
    },
    "plushify-reviewer": {
        "name": "PlushifyStudio 审查 Agent",
        "description": "审查规格合规、分层边界、重复代码、红线和验证证据。",
        "role": "worker",
        "skills": [
            "openakita/skills@code-review",
            "obra/superpowers@requesting-code-review",
            "obra/superpowers@verification-before-completion",
            "obra/superpowers@systematic-debugging",
        ],
        "tools": ["filesystem", "research", "memory"],
        "endpoint": "primary-gpt-5.5",
        "prompt": make_prompt(
            "审查 Agent",
            "审查需求符合度、架构边界、重复代码、过度实现、测试证据与安全红线；只给可执行问题，不写泛泛建议。",
        ),
        "icon": "🧪",
        "color": "#DC2626",
    },
    "plushify-delivery": {
        "name": "PlushifyStudio 交付收口员",
        "description": "整理完成项、验证证据、风险、commit/push/merge 状态和下一步。",
        "role": "worker",
        "skills": [
            "openakita/skills@changelog-generator",
            "openakita/skills@github-automation",
            "obra/superpowers@finishing-a-development-branch",
            "obra/superpowers@verification-before-completion",
            "technical-blog-writing",
        ],
        "tools": ["planning", "filesystem", "research", "memory", "mcp"],
        "endpoint": "primary-gpt-5.5",
        "prompt": make_prompt(
            "交付收口员",
            "只做交付汇总、验证证据整理、风险说明、变更清单、发布/PR/commit 状态核对；不得替实现岗补代码。",
        ),
        "icon": "📦",
        "color": "#EA580C",
    },
    "plushify-architect": {
        "name": "PlushifyStudio 架构与边界治理师",
        "description": "负责三仓边界、包依赖方向、ADR/RFC 与技术方案取舍。",
        "role": "worker",
        "skills": [
            "obra/superpowers@brainstorming",
            "obra/superpowers@writing-plans",
            "openakita/skills@code-review",
            "technical-blog-writing",
        ],
        "tools": ["filesystem", "research", "planning", "memory"],
        "endpoint": "primary-gpt-5.5",
        "prompt": make_prompt(
            "架构与边界治理师",
            "审查 shared/server/client 物理边界、domain/render/storage/IPC 依赖方向、文件拆分与 ADR/RFC；给出方案，不直接实现。",
        ),
        "icon": "🏗️",
        "color": "#4F46E5",
    },
    "plushify-planner": {
        "name": "PlushifyStudio 实施计划拆解员",
        "description": "把目标拆成可并行派工的小任务，定义验收与验证顺序。",
        "role": "worker",
        "skills": [
            "obra/superpowers@writing-plans",
            "obra/superpowers@executing-plans",
            "obra/superpowers@subagent-driven-development",
        ],
        "tools": ["filesystem", "research", "planning", "memory"],
        "endpoint": "primary-gpt-5.5",
        "prompt": make_prompt(
            "实施计划拆解员",
            "基于上下文和架构约束，把需求拆为 shared/server/client/验证/文档等独立小任务，并给出顺序、依赖和验收标准。",
        ),
        "icon": "📋",
        "color": "#9333EA",
    },
}

SPECIALISTS = {
    "plushify-shared-contracts": ("Shared 契约工程师", "维护 api-contracts、domain-project、sync-protocol、task-protocol。", "📐", "#0F766E"),
    "plushify-server-engineer": ("服务端工程师", "负责 Fastify API、auth、tasks、server-db/cache/object-store。", "🖥️", "#0369A1"),
    "plushify-client-main": ("Electron Main/Preload 工程师", "负责 Electron main、preload、IPC 白名单、本地存储入口。", "🧩", "#2563EB"),
    "plushify-renderer-ui": ("Renderer UI 工程师", "负责 React renderer、面板、命令接线与中文用户文案。", "🎛️", "#DB2777"),
    "plushify-viewport-geometry": ("视口几何工程师", "负责 Three.js/render-viewport、overlay、几何选择、缓存。", "🧊", "#0891B2"),
    "plushify-business-algorithm": ("业务算法工程师", "负责 BIZ-5+ seam/pattern/material/print/export 算法协议与纯逻辑。", "🧠", "#7C2D12"),
    "plushify-sync-storage": ("同步与存储工程师", "负责 outbox、revision、冲突处理、本地/云端一致性。", "🔁", "#15803D"),
    "plushify-test-unit": ("单元契约测试工程师", "负责 shared/server/client 单元测试、契约测试与 fixture。", "🧫", "#65A30D"),
    "plushify-test-e2e": ("E2E 验收工程师", "负责 Electron/Playwright E2E、端到端验收与用户流程回归。", "🎭", "#CA8A04"),
    "plushify-build-release": ("构建发布工程师", "负责 check/build/installer/release/tag 与产物管理。", "🚀", "#EA580C"),
    "plushify-docs-rfc": ("文档与 RFC 工程师", "负责 ADR、RFC、impl-plan、runbook、CHANGELOG 与中文文档。", "📝", "#A16207"),
    "plushify-security-qa": ("安全与质量红线审查员", "负责权限、边界、安全、失败路径、错误码和高风险操作审查。", "🛡️", "#B91C1C"),
}

for profile_id, (role, description, icon, color) in SPECIALISTS.items():
    skills = [
        "obra/superpowers@test-driven-development",
        "obra/superpowers@systematic-debugging",
        "obra/superpowers@verification-before-completion",
        "openakita/skills@code-review",
    ]
    tools = ["filesystem", "planning", "memory"]
    if profile_id in {"plushify-server-engineer", "plushify-business-algorithm", "plushify-sync-storage", "plushify-docs-rfc", "plushify-security-qa"}:
        tools.append("research")
    if profile_id in {"plushify-renderer-ui", "plushify-viewport-geometry", "plushify-test-e2e"}:
        skills.extend(["openakita/skills@frontend-design", "openakita/skills@webapp-testing"])
        tools.append("browser")
    if profile_id == "plushify-test-unit":
        skills = ["obra/superpowers@test-driven-development", "obra/superpowers@verification-before-completion", "obra/superpowers@systematic-debugging"]
    if profile_id == "plushify-test-e2e":
        skills = ["openakita/skills@webapp-testing", "obra/superpowers@systematic-debugging", "obra/superpowers@verification-before-completion"]
    if profile_id == "plushify-build-release":
        skills = [
            "openakita/skills@github-automation",
            "openakita/skills@changelog-generator",
            "obra/superpowers@verification-before-completion",
            "obra/superpowers@finishing-a-development-branch",
        ]
    if profile_id == "plushify-docs-rfc":
        skills = ["technical-blog-writing", "openakita/skills@changelog-generator", "obra/superpowers@writing-plans"]
    if profile_id == "plushify-security-qa":
        skills = [
            "openakita/skills@code-review",
            "obra/superpowers@systematic-debugging",
            "obra/superpowers@verification-before-completion",
            "obra/superpowers@requesting-code-review",
        ]
    PROFILE_SPECS[profile_id] = {
        "name": f"PlushifyStudio {role}",
        "description": description,
        "role": "worker",
        "skills": skills,
        "tools": tools,
        "endpoint": endpoint_for_profile(profile_id),
        "prompt": make_prompt(role, description),
        "icon": icon,
        "color": color,
    }


def default_profile(profile_id: str) -> dict:
    return {
        "id": profile_id,
        "type": "custom",
        "skills_mode": "inclusive",
        "tools_mode": "inclusive",
        "mcp_servers": [],
        "mcp_mode": "all",
        "plugins": [],
        "plugins_mode": "all",
        "fallback_profile_id": None,
        "permission_rules": [],
        "created_by": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "name_i18n": {},
        "description_i18n": {},
        "category": "devops",
        "hidden": False,
        "pixel_appearance": None,
        "user_customized": False,
        "hub_source": None,
        "ephemeral": False,
        "inherit_from": None,
        "identity_mode": "shared",
        "memory_mode": "shared",
        "memory_inherit_global": True,
        "user_profile_content": "",
        "runtime_env_mode": "shared",
        "runtime_env_dependencies": [],
        "runtime_env_python": None,
        "max_turns": None,
        "background": False,
        "omit_system_context": False,
        "timeout_seconds": None,
    }


def write_profiles() -> None:
    for profile_id, spec in PROFILE_SPECS.items():
        path = BASE / f"{profile_id}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default_profile(profile_id)
        data.update(
            {
                "id": profile_id,
                "name": spec["name"],
                "description": spec["description"],
                "type": "custom",
                "role": spec["role"],
                "skills": spec["skills"],
                "skills_mode": "inclusive",
                "tools": [],
                "tools_mode": "all",
                "mcp_servers": [],
                "mcp_mode": "all",
                "plugins": [],
                "plugins_mode": "all",
                "custom_prompt": spec["prompt"],
                "icon": spec["icon"],
                "color": spec["color"],
                "fallback_profile_id": None,
                "preferred_endpoint": endpoint_for_profile(profile_id),
                "endpoint_policy": "prefer",
                "category": "devops",
                "hidden": False,
                "origin": "user",
                "namespace": "user",
                "definition_id": f"user/agent_definition:{profile_id}",
            }
        )
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


NODE_SPECS = [
    ("node_plushify_lead", "研发主控", "拆解目标，调度专业小队并验收汇总。", "plushify-lead", 400, 0, 0, "研发管理", "🧭"),
    ("node_plushify_architect", "架构与边界治理师", "负责三仓边界、包依赖方向、ADR/RFC 与技术方案取舍。", "plushify-architect", -260, 180, 1, "架构治理", "🏗️"),
    ("node_plushify_planner", "实施计划拆解员", "把目标拆成可并行派工的小任务，定义验收与验证顺序。", "plushify-planner", -40, 180, 1, "研发管理", "📋"),
    ("node_plushify_context_scout", "上下文侦察员", "只读核查 AGENTS、架构文档、历史计划、模块边界与现有测试。", "plushify-context-scout", 180, 180, 1, "上下文", "🔎"),
    ("node_plushify_reviewer", "审查 Agent", "审查规格合规、分层边界、重复代码、红线和验证证据。", "plushify-reviewer", 620, 180, 1, "审查", "🧪"),
    ("node_plushify_delivery", "交付收口员", "整理完成项、验证证据、风险、commit/push/merge 状态和下一步。", "plushify-delivery", 840, 180, 1, "交付", "📦"),
    ("node_plushify_shared_contracts", "Shared 契约工程师", "维护 api-contracts、domain-project、sync-protocol、task-protocol。", "plushify-shared-contracts", -360, 380, 2, "Shared 契约", "📐"),
    ("node_plushify_server_engineer", "服务端工程师", "负责 Fastify API、auth、tasks、server-db/cache/object-store。", "plushify-server-engineer", -120, 380, 2, "服务端", "🖥️"),
    ("node_plushify_client_main", "Electron Main/Preload 工程师", "负责 Electron main、preload、IPC 白名单、本地存储入口。", "plushify-client-main", 120, 380, 2, "客户端", "🧩"),
    ("node_plushify_renderer_ui", "Renderer UI 工程师", "负责 React renderer、面板、命令接线与中文用户文案。", "plushify-renderer-ui", 360, 380, 2, "客户端", "🎛️"),
    ("node_plushify_viewport_geometry", "视口几何工程师", "负责 Three.js/render-viewport、overlay、几何选择、缓存。", "plushify-viewport-geometry", 600, 380, 2, "视口几何", "🧊"),
    ("node_plushify_business_algorithm", "业务算法工程师", "负责 BIZ-5+ seam/pattern/material/print/export 算法协议与纯逻辑。", "plushify-business-algorithm", 840, 380, 2, "业务算法", "🧠"),
    ("node_plushify_sync_storage", "同步与存储工程师", "负责 outbox、revision、冲突处理、本地/云端一致性。", "plushify-sync-storage", 1080, 380, 2, "同步存储", "🔁"),
    ("node_plushify_implementer", "通用实现工程师", "执行无法归入专岗的单一小任务实现。", "plushify-implementer", -360, 580, 2, "实现", "🛠"),
    ("node_plushify_test_unit", "单元契约测试工程师", "负责 shared/server/client 单元测试、契约测试与 fixture。", "plushify-test-unit", -120, 580, 2, "验证", "🧫"),
    ("node_plushify_test_e2e", "E2E 验收工程师", "负责 Electron/Playwright E2E、端到端验收与用户流程回归。", "plushify-test-e2e", 120, 580, 2, "验证", "🎭"),
    ("node_plushify_build_release", "构建发布工程师", "负责 check/build/installer/release/tag 与产物管理。", "plushify-build-release", 360, 580, 2, "发布", "🚀"),
    ("node_plushify_docs_rfc", "文档与 RFC 工程师", "负责 ADR、RFC、impl-plan、runbook、CHANGELOG 与中文文档。", "plushify-docs-rfc", 600, 580, 2, "文档", "📝"),
    ("node_plushify_security_qa", "安全与质量红线审查员", "负责权限、边界、安全、失败路径、错误码和高风险操作审查。", "plushify-security-qa", 840, 580, 2, "质量安全", "🛡️"),
    ("node_plushify_verifier", "综合验证工程师", "运行 typecheck、test、build、E2E，失败后定向定位和重跑。", "plushify-verifier", 1080, 580, 2, "验证", "✅"),
]

FREQUENT_NODES = {
    "node_plushify_lead",
    "node_plushify_context_scout",
    "node_plushify_shared_contracts",
    "node_plushify_server_engineer",
    "node_plushify_client_main",
    "node_plushify_renderer_ui",
    "node_plushify_viewport_geometry",
    "node_plushify_business_algorithm",
    "node_plushify_sync_storage",
    "node_plushify_test_unit",
    "node_plushify_test_e2e",
    "node_plushify_reviewer",
    "node_plushify_docs_rfc",
}
MEDIUM_CONCURRENCY = {
    "node_plushify_architect": 4,
    "node_plushify_planner": 4,
    "node_plushify_build_release": 6,
    "node_plushify_security_qa": 6,
    "node_plushify_delivery": 3,
    "node_plushify_verifier": 6,
    "node_plushify_implementer": 12,
}
DELEGATORS = {"plushify-lead", "plushify-planner", "plushify-architect"}


def make_node(spec: tuple, profiles: dict[str, dict]) -> dict:
    node_id, title, goal, profile_id, x, y, level, department, avatar = spec
    profile = profiles[profile_id]
    max_tasks = 99
    clone_enabled = True
    clone_max = 99
    return {
        "id": node_id,
        "role_title": title,
        "role_goal": goal,
        "role_backstory": "",
        "agent_source": f"ref:{profile_id}",
        "agent_profile_id": profile_id,
        "position": {"x": x, "y": y},
        "level": level,
        "department": department,
        "custom_prompt": profile["custom_prompt"],
        "identity_dir": None,
        "mcp_servers": profile.get("mcp_servers", []),
        "skills": profile["skills"],
        "skills_mode": "inclusive",
        "preferred_endpoint": profile["preferred_endpoint"],
        "endpoint_policy": "prefer",
        "max_concurrent_tasks": max_tasks,
        "timeout_s": 0,
        "can_delegate": profile_id in DELEGATORS,
        "can_escalate": True,
        "can_request_scaling": True,
        "auto_clone_enabled": clone_enabled,
        "auto_clone_threshold": 2,
        "auto_clone_max": clone_max,
        "is_clone": False,
        "clone_source": None,
        "ephemeral": False,
        "avatar": avatar,
        "external_tools": ALL_TOOL_CATEGORIES,
        "enable_file_tools": True,
        "frozen_by": None,
        "frozen_reason": None,
        "frozen_at": None,
        "status": "idle",
    }


def make_edge(edge_id: str, source: str, target: str, edge_type: str = "hierarchy", label: str = "", bandwidth: int = 99) -> dict:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "edge_type": edge_type,
        "label": label,
        "bidirectional": True,
        "priority": 0,
        "bandwidth_limit": bandwidth,
    }


def build_edges() -> list[dict]:
    edges: list[dict] = []
    for target in [
        "node_plushify_architect",
        "node_plushify_planner",
        "node_plushify_context_scout",
        "node_plushify_reviewer",
        "node_plushify_delivery",
        "node_plushify_implementer",
        "node_plushify_verifier",
    ]:
        edges.append(make_edge(f"edge_lead_{target.removeprefix('node_plushify_')}", "node_plushify_lead", target))
    for target in [
        "node_plushify_shared_contracts",
        "node_plushify_server_engineer",
        "node_plushify_client_main",
        "node_plushify_renderer_ui",
        "node_plushify_viewport_geometry",
        "node_plushify_business_algorithm",
        "node_plushify_sync_storage",
        "node_plushify_test_unit",
        "node_plushify_test_e2e",
        "node_plushify_build_release",
        "node_plushify_docs_rfc",
        "node_plushify_security_qa",
    ]:
        edges.append(make_edge(f"edge_planner_{target.removeprefix('node_plushify_')}", "node_plushify_planner", target))
    collaborations = [
        ("node_plushify_context_scout", "node_plushify_architect", "上下文交接"),
        ("node_plushify_architect", "node_plushify_planner", "方案转计划"),
        ("node_plushify_shared_contracts", "node_plushify_server_engineer", "契约到服务端"),
        ("node_plushify_shared_contracts", "node_plushify_client_main", "契约到客户端"),
        ("node_plushify_client_main", "node_plushify_renderer_ui", "IPC 到 UI"),
        ("node_plushify_renderer_ui", "node_plushify_viewport_geometry", "UI 到视口"),
        ("node_plushify_business_algorithm", "node_plushify_shared_contracts", "算法协议"),
        ("node_plushify_business_algorithm", "node_plushify_viewport_geometry", "算法可视化"),
        ("node_plushify_sync_storage", "node_plushify_server_engineer", "云端一致性"),
        ("node_plushify_sync_storage", "node_plushify_client_main", "本地一致性"),
        ("node_plushify_test_unit", "node_plushify_reviewer", "单测证据"),
        ("node_plushify_test_e2e", "node_plushify_reviewer", "E2E 证据"),
        ("node_plushify_security_qa", "node_plushify_reviewer", "红线审查"),
        ("node_plushify_build_release", "node_plushify_delivery", "构建交付"),
        ("node_plushify_docs_rfc", "node_plushify_delivery", "文档交付"),
    ]
    for index, (source, target, label) in enumerate(collaborations, 1):
        edges.append(make_edge(f"edge_collab_{index:02d}", source, target, "collaborate", label, 80))
    edges.extend(
        [
            make_edge("edge_delivery_to_lead", "node_plushify_delivery", "node_plushify_lead", "escalate", "总收口回报"),
            make_edge("edge_reviewer_to_lead", "node_plushify_reviewer", "node_plushify_lead", "escalate", "审查结论回报"),
            make_edge("edge_security_to_lead", "node_plushify_security_qa", "node_plushify_lead", "escalate", "红线风险上报"),
        ]
    )
    return edges


def write_org() -> None:
    profiles = {profile_id: json.loads((BASE / f"{profile_id}.json").read_text(encoding="utf-8")) for profile_id in PROFILE_SPECS}
    nodes = [make_node(spec, profiles) for spec in NODE_SPECS]
    org = json.loads(ORG_PATH.read_text(encoding="utf-8"))
    org.update(
        {
            "description": "面向 PlushifyStudio BIZ-5+ 业务算法阶段的自动研发组织：主控编排，按 shared/server/client/viewport/algorithm/sync/test/review/release/doc/security 专业小队并行推进。",
            "status": "active",
            "nodes": nodes,
            "edges": build_edges(),
            "heartbeat_enabled": False,
            "allow_cross_level": False,
            "max_delegation_depth": 6,
            "conflict_resolution": "manager",
            "scaling_enabled": True,
            "max_nodes": 2200,
            "auto_scale_enabled": True,
            "auto_scale_max_per_heartbeat": 12,
            "scaling_approval": "auto",
            "shared_memory_enabled": True,
            "department_memory_enabled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "core_business": "PlushifyStudio BIZ-5+ 业务算法、三仓边界、验证、审查与发布交付",
            "operation_mode": "autonomous",
        }
    )
    ORG_PATH.write_text(json.dumps(org, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    state_path = ORG_PATH.with_name("state.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"status": org.get("status", "active")}
    state["node_statuses"] = {node["id"]: "idle" for node in nodes}
    state["saved_at"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_profiles()
    write_org()
    print(f"wrote {len(PROFILE_SPECS)} profiles, {len(NODE_SPECS)} nodes, {len(build_edges())} edges")
