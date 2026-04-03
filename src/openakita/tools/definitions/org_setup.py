"""
Organization setup tool — create and manage organizations through natural language.

Only injected when settings.multi_agent_enabled is True, alongside AGENT_TOOLS.
"""

_EDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": "起点节点的 role_title 或 node_id",
        },
        "target": {
            "type": "string",
            "description": "终点节点的 role_title 或 node_id",
        },
        "edge_type": {
            "type": "string",
            "enum": ["collaborate", "escalate", "consult"],
            "description": (
                "连线类型（不含 hierarchy，层级关系请用节点的 parent_role_title）："
                "collaborate=协作, escalate=上报, consult=咨询"
            ),
        },
        "label": {
            "type": "string",
            "description": "连线标签（如 '需求沟通'、'数据传递'）",
        },
        "bidirectional": {
            "type": "boolean",
            "description": "是否双向通信（默认 true）",
        },
    },
    "required": ["source", "target", "edge_type"],
}

_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {
            "type": "string",
            "description": "现有节点 ID（修改时必填，用于精确匹配；新增时留空）",
        },
        "role_title": {
            "type": "string",
            "description": "岗位名称（必填，如 CEO、CTO、前端开发）",
        },
        "role_goal": {
            "type": "string",
            "description": "岗位目标（如：制定技术路线，保障系统稳定）",
        },
        "department": {
            "type": "string",
            "description": "所属部门（如：技术部、产品部）",
        },
        "level": {
            "type": "integer",
            "description": "层级（0=最高层/根，1=中层，2=基层）",
        },
        "agent_profile_id": {
            "type": "string",
            "description": (
                "关联的系统 Agent ID（非常重要！决定节点的专业能力。"
                "从 get_resources 返回的 agents 列表中选择最合适的）"
            ),
        },
        "parent_role_title": {
            "type": "string",
            "description": "上级岗位名称（用于自动创建层级关系。根节点不需要填写）",
        },
        "external_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "外部工具（类目名或工具名，如 "
                "'research'、'filesystem'、'planning'、'browser'）"
            ),
        },
        "custom_prompt": {
            "type": "string",
            "description": "自定义提示词（可选，用于精细控制节点行为）",
        },
    },
    "required": ["role_title"],
}

ORG_SETUP_TOOLS = [
    {
        "name": "setup_organization",
        "category": "Organization",
        "description": (
            "Create and manage organizational structures for multi-agent collaboration. "
            "Supports: listing available agents/templates (get_resources), "
            "listing existing orgs (list_orgs), viewing an org (get_org), "
            "previewing before creation (preview), creating (create), "
            "creating from template (create_from_template), "
            "modifying an existing org (update_org), deleting (delete_org). "
            "For CREATION: call get_resources first, ask clarifying questions, then create. "
            "For MODIFICATION: call list_orgs to find the org, get_org to see its structure, "
            "then update_org with incremental changes."
        ),
        "detail": (
            "通过自然语言创建和管理组织编排架构。\n\n"
            "## 创建流程\n\n"
            "1. **get_resources** — 获取可用 Agent、模板、工具类目\n"
            "2. **向用户了解需求** — 信息不足时主动询问\n"
            "3. **为每个节点匹配系统 Agent**（agent_profile_id）— 最重要的配置\n"
            "4. **preview** — 展示草案给用户确认\n"
            "5. **create** — 用户确认后正式创建\n\n"
            "## 修改流程\n\n"
            "1. **list_orgs** — 列出现有组织，确定要修改的目标\n"
            "2. **get_org** — 获取完整结构（节点 ID、Agent、工具、连线等）\n"
            "3. **理解用户修改意图** — 确认要增删改哪些节点或连线\n"
            "4. **向用户描述变更方案** — 先用文本说明，让用户确认\n"
            "5. **update_org** — 提交增量修改（保留现有节点 ID）\n\n"
            "## 连线类型\n\n"
            "组织支持 4 种连线关系：\n"
            "- **hierarchy** — 层级（上下级），通过节点的 parent_role_title 自动创建\n"
            "- **collaborate** — 协作，子节点之间的横向协作关系\n"
            "- **escalate** — 上报，跨层级的问题上报通道\n"
            "- **consult** — 咨询，向特定节点请求专业意见\n\n"
            "层级关系由 parent_role_title 管理；"
            "其他三种关系通过 edges（创建时）或 add_edges（修改时）添加。\n\n"
            "## 关键注意事项\n"
            "- 修改时**必须保留现有节点 ID**，因为 ID 关联了任务、记忆、身份文件\n"
            "- update_org 是**增量更新**：只传要改的字段，未提及的节点原样保留\n"
            "- 删除节点用 remove_nodes 参数，会同时清理关联边\n"
            "- 删除连线用 remove_edges 参数（传 edge ID，从 get_org 获取）\n"
            "- remove_edges 仅可删除非层级连线；层级关系请通过修改 parent_role_title\n"
            "- 信息不足时**主动询问**，不要猜测用户意图"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "get_resources", "list_orgs", "get_org",
                        "preview", "create", "create_from_template",
                        "update_org", "delete_org",
                    ],
                    "description": (
                        "操作类型："
                        "get_resources=获取可用资源清单；"
                        "list_orgs=列出现有组织；"
                        "get_org=获取组织完整结构；"
                        "preview=预览新组织架构（不创建）；"
                        "create=创建组织；"
                        "create_from_template=从模板创建；"
                        "update_org=修改现有组织（增量）；"
                        "delete_org=删除组织"
                    ),
                },
                "org_id": {
                    "type": "string",
                    "description": "组织 ID（get_org/update_org/delete_org 时必填）",
                },
                "name": {
                    "type": "string",
                    "description": "组织名称（create/preview 时必填，update_org 时可选）",
                },
                "description": {
                    "type": "string",
                    "description": "组织描述",
                },
                "core_business": {
                    "type": "string",
                    "description": "核心业务描述（如：跨境电商运营、SaaS 产品研发）",
                },
                "nodes": {
                    "type": "array",
                    "items": _NODE_SCHEMA,
                    "description": "节点列表（create/preview 时必填）",
                },
                "edges": {
                    "type": "array",
                    "items": _EDGE_SCHEMA,
                    "description": (
                        "非层级连线列表（create/preview 时可选）。"
                        "用于定义子节点之间的协作/上报/咨询关系。"
                        "层级关系不需要在此指定，由节点的 parent_role_title 自动生成"
                    ),
                },
                "template_id": {
                    "type": "string",
                    "description": "模板 ID（create_from_template 时必填）",
                },
                "overrides": {
                    "type": "object",
                    "description": "模板覆盖字段（create_from_template 时可选）",
                },
                "update_nodes": {
                    "type": "array",
                    "items": _NODE_SCHEMA,
                    "description": (
                        "要修改或新增的节点（update_org 时使用）。"
                        "有 node_id 匹配现有节点则更新，否则按 role_title 匹配或作为新节点添加"
                    ),
                },
                "remove_nodes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要删除的节点 ID 或 role_title 列表（update_org 时使用）",
                },
                "add_edges": {
                    "type": "array",
                    "items": _EDGE_SCHEMA,
                    "description": (
                        "要添加的非层级连线（update_org 时使用）。"
                        "source/target 可用 node_id 或 role_title。"
                        "仅支持 collaborate/escalate/consult 类型"
                    ),
                },
                "remove_edges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "要删除的连线 edge ID 列表（update_org 时使用，"
                        "从 get_org 返回的连线列表中获取 ID）。"
                        "仅可删除非层级连线"
                    ),
                },
                "update_fields": {
                    "type": "object",
                    "description": (
                        "组织级字段更新（update_org 时使用），"
                        "如 name、description、core_business、heartbeat_enabled 等"
                    ),
                },
            },
            "required": ["action"],
        },
        "examples": [
            {
                "scenario": "获取可用资源",
                "params": {"action": "get_resources"},
                "expected": "返回 Agent 列表、模板列表、工具类目",
            },
            {
                "scenario": "列出现有组织",
                "params": {"action": "list_orgs"},
                "expected": "返回组织摘要列表（ID、名称、状态、节点数）",
            },
            {
                "scenario": "查看组织结构",
                "params": {"action": "get_org", "org_id": "org_xxx"},
                "expected": "返回组织完整结构：节点列表、连线关系（含 edge ID）、元数据",
            },
            {
                "scenario": "修改组织：给电商团队加一个数据分析师",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "update_nodes": [
                        {
                            "role_title": "数据分析师",
                            "role_goal": "数据埋点、报表分析、增长策略",
                            "department": "运营部",
                            "level": 1,
                            "agent_profile_id": "data-analyst",
                            "parent_role_title": "运营总监",
                            "external_tools": ["research", "filesystem"],
                        }
                    ],
                },
                "expected": "新增节点并自动创建层级关系",
            },
            {
                "scenario": "修改组织：更换节点的 Agent",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "update_nodes": [
                        {
                            "node_id": "node_abc",
                            "agent_profile_id": "content-creator",
                        }
                    ],
                },
                "expected": "只更新指定字段，其余保持不变",
            },
            {
                "scenario": "修改组织：删除节点",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "remove_nodes": ["客服主管"],
                },
                "expected": "删除节点并清理关联边",
            },
            {
                "scenario": "修改组织：在子节点之间添加协作连线",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "add_edges": [
                        {
                            "source": "数据分析师",
                            "target": "风控专员",
                            "edge_type": "collaborate",
                            "label": "数据共享",
                        }
                    ],
                },
                "expected": "在两个子节点之间建立协作连线",
            },
            {
                "scenario": "删除组织",
                "params": {"action": "delete_org", "org_id": "org_xxx"},
                "expected": "永久删除组织及其所有数据",
            },
        ],
    },
]
