"""
Multi-agent tools — delegate, spawn and create.

Only injected when settings.multi_agent_enabled is True.

Tool priority (LLM should follow this order):
1. delegate_to_agent — use existing agent directly
2. spawn_agent — inherit + customize an existing agent (ephemeral)
3. delegate_parallel — parallel delegation (can mix delegate + spawn)
4. create_agent — last resort, create from scratch (defaults to ephemeral)
"""

AGENT_TOOLS = [
    {
        "name": "delegate_to_agent",
        "category": "Agent",
        "description": (
            "Delegate a task to an existing specialized agent. "
            "This is the PREFERRED way to use multi-agent collaboration. "
            "Use when: (1) An existing agent profile matches the task, "
            "(2) You need domain expertise (code, data, browser, docs), "
            "(3) The task can be fully handled by an existing agent without customization.\n\n"
            "IMPORTANT:\n"
            "- Launch multiple agents concurrently whenever possible for independent tasks\n"
            "- Do NOT launch more than 4 concurrent agents\n"
            "- Sub-agent results are not directly visible to the user — summarize them in "
            "your response\n"
            "- Prefer 'fast' model for quick, straightforward sub-tasks to minimize cost\n"
            "- Use 'capable' only when the task requires deep reasoning"
        ),
        "detail": (
            "将任务委派给已有的专业 Agent。这是多 Agent 协作的**首选**方式。\n\n"
            "**适用场景**：\n"
            "- 当前任务需要另一个 Agent 的专长（如代码、数据分析、浏览器操作）\n"
            "- 拆分复杂任务到多个 Agent 协作完成\n"
            "- 需要特定技能集的 Agent 处理子任务\n\n"
            "**注意事项**：\n"
            "- 目标 Agent 必须已注册（预设或动态创建）\n"
            "- 委派深度上限为 5 层，防止无限递归\n"
            "- 同一个 agent_id 可以被多次委派（池自动管理并行实例）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "目标 Agent Profile ID（如 'code-assistant', 'data-analyst', 'browser-agent'）",
                },
                "message": {
                    "type": "string",
                    "description": "发送给目标 Agent 的任务描述",
                },
                "reason": {
                    "type": "string",
                    "description": "委派原因（可选，用于日志和追踪）",
                },
                "model": {
                    "type": "string",
                    "enum": ["fast", "default", "capable"],
                    "description": (
                        "子代理使用的模型。fast=便宜快速（适合简单任务），"
                        "default=与主代理相同，capable=更强模型（适合复杂推理）"
                    ),
                    "default": "default",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "是否后台运行。后台子代理不阻塞主代理，结果稍后可查。",
                    "default": False,
                },
            },
            "required": ["agent_id", "message"],
        },
        "examples": [
            {
                "scenario": "将代码任务委派给代码助手",
                "params": {
                    "agent_id": "code-assistant",
                    "message": "请帮我重构 utils.py 中的日期处理函数",
                    "reason": "需要代码专长",
                },
                "expected": "代码助手的回复",
            },
        ],
    },
    {
        "name": "spawn_agent",
        "category": "Agent",
        "description": (
            "Spawn a temporary agent by inheriting from an existing agent profile. "
            "Use when: (1) An existing agent is close but needs minor customization, "
            "(2) You need a specialized variant with extra skills or a modified prompt, "
            "(3) You need multiple independent clones of the same agent for parallel tasks. "
            "The spawned agent is ephemeral — automatically destroyed after the task completes."
        ),
        "detail": (
            "继承已有 Agent 创建临时工作 Agent，任务结束后自动销毁。\n\n"
            "**适用场景**：\n"
            "- 已有 Agent 接近需求但需要微调（追加技能或提示词）\n"
            "- 需要同一个 Agent 的多个独立分身并行执行不同任务\n"
            "- 一次性任务不需要持久化 Agent\n\n"
            "**工作原理**：\n"
            "1. 从 inherit_from 指定的基础 Profile 复制技能和提示词\n"
            "2. 合并 extra_skills 和 custom_prompt_overlay\n"
            "3. 创建临时 Profile（仅存内存，不写磁盘）\n"
            "4. 立即委派 message 给临时 Agent 执行\n"
            "5. 任务完成后自动清理临时 Profile"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inherit_from": {
                    "type": "string",
                    "description": "基础 Agent Profile ID（如 'browser-agent', 'code-assistant'）",
                },
                "message": {
                    "type": "string",
                    "description": "要执行的任务描述",
                },
                "extra_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "在基础 Agent 技能之上追加的额外技能（可选）",
                },
                "custom_prompt_overlay": {
                    "type": "string",
                    "description": "追加到基础 Agent 提示词之上的定制提示（可选）",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么需要定制（可选，用于日志）",
                },
            },
            "required": ["inherit_from", "message"],
        },
        "examples": [
            {
                "scenario": "继承浏览器 Agent，定制为网页调研专员",
                "params": {
                    "inherit_from": "browser-agent",
                    "message": "调研 React 19 的新特性并整理报告",
                    "custom_prompt_overlay": "重点关注性能优化和并发特性",
                    "reason": "需要浏览器能力 + 调研专长",
                },
                "expected": "临时 Agent 执行调研后返回结果",
            },
            {
                "scenario": "创建两个独立分身并行调研",
                "params": {
                    "inherit_from": "browser-agent",
                    "message": "调研 Vue 4 的最新动态",
                    "reason": "并行调研第二个框架",
                },
                "expected": "每次 spawn 生成唯一临时 ID，可并行运行多个",
            },
        ],
    },
    {
        "name": "delegate_parallel",
        "category": "Agent",
        "description": (
            "Delegate tasks to multiple agents in parallel. "
            "IMPORTANT: For multiple similar tasks (e.g. researching 3 topics), "
            "use the SAME suitable agent_id for all tasks — the system auto-creates "
            "independent clones. Do NOT assign unrelated agents to tasks they are not "
            "specialized for."
        ),
        "detail": (
            "同时委派任务给多个 Agent 并行执行。\n\n"
            "**核心规则**：\n"
            "- 同类任务（如多个调研任务）→ 用**同一个最合适的 agent_id**，"
            "系统自动为每个任务创建独立副本\n"
            "- 异类任务（如调研+编码+数据分析）→ 才分配给不同专业 Agent\n"
            "- **严禁**为了凑并行把任务分给不对口的 Agent\n\n"
            "**注意事项**：\n"
            "- 所有任务并行执行，结果一起返回\n"
            "- 各任务之间不能有依赖关系（有依赖请用 delegate_to_agent 串行委派）\n"
            "- 对同一个 agent_id 发多个任务时，系统自动创建独立实例"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "目标 Agent Profile ID",
                            },
                            "message": {
                                "type": "string",
                                "description": "发送给该 Agent 的任务描述",
                            },
                            "reason": {
                                "type": "string",
                                "description": "委派原因（可选）",
                            },
                        },
                        "required": ["agent_id", "message"],
                    },
                    "description": "要并行执行的任务列表（2-5个）",
                },
            },
            "required": ["tasks"],
        },
        "examples": [
            {
                "scenario": "✅ 正确：同时调研多个项目（同类任务 → 同一 Agent 多副本）",
                "params": {
                    "tasks": [
                        {"agent_id": "browser-agent", "message": "深入调研 OpenAkita 项目的架构、功能和社区活跃度", "reason": "调研项目A"},
                        {"agent_id": "browser-agent", "message": "深入调研 OpenClaw 项目的架构、功能和社区活跃度", "reason": "调研项目B"},
                    ],
                },
                "expected": "系统自动为 browser-agent 创建2个独立副本，并行执行后合并返回",
            },
            {
                "scenario": "✅ 正确：不同类型任务并行（异类任务 → 不同专业 Agent）",
                "params": {
                    "tasks": [
                        {"agent_id": "browser-agent", "message": "在网上调研 React 19 的新特性", "reason": "网络调研"},
                        {"agent_id": "code-assistant", "message": "分析当前项目的 React 版本升级兼容性", "reason": "代码分析"},
                    ],
                },
                "expected": "调研和代码分析并行执行",
            },
            {
                "scenario": "❌ 错误：把调研任务分给不对口的 Agent",
                "params": {
                    "tasks": [
                        {"agent_id": "browser-agent", "message": "调研项目A"},
                        {"agent_id": "code-assistant", "message": "调研项目B"},
                    ],
                },
                "expected": "严禁！code-assistant 是代码助手，不擅长网络调研。应该让两个调研任务都用 browser-agent",
            },
        ],
    },
    {
        "name": "create_agent",
        "category": "Agent",
        "description": (
            "Create a completely new agent from scratch. "
            "⚠️ This is the LAST RESORT — only use when NO existing agent can be "
            "delegated to or spawned from. "
            "Prefer delegate_to_agent (direct use) or spawn_agent (inherit + customize) first. "
            "Created agents are ephemeral by default (auto-cleanup after task). "
            "Set persistent=true only if the user explicitly wants to keep the agent."
        ),
        "detail": (
            "创建全新 Agent。⚠️ 这是**最后手段**。\n\n"
            "**使用前请确认**：\n"
            "1. ✅ 已检查所有现有 Agent，没有一个能直接使用（delegate_to_agent）\n"
            "2. ✅ 已检查所有现有 Agent，没有一个能继承定制（spawn_agent）\n"
            "3. ✅ 确实需要一个全新角色\n\n"
            "**默认行为**：\n"
            "- 创建的 Agent 默认是临时的（ephemeral），任务结束后自动销毁\n"
            "- 不会污染系统 Agent 列表\n"
            "- 设置 persistent=true 可永久保存（仅在用户明确要求时使用）\n\n"
            "**限制**：\n"
            "- 每个会话最多创建 5 个动态 Agent\n"
            "- 动态 Agent 不能再创建新 Agent\n"
            "- 如果系统检测到已有类似 Agent，会建议使用 spawn_agent 代替"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent 名称",
                },
                "description": {
                    "type": "string",
                    "description": "Agent 功能描述",
                },
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要分配的技能 ID 列表（可选）",
                },
                "custom_prompt": {
                    "type": "string",
                    "description": "自定义系统提示词（可选）",
                },
                "persistent": {
                    "type": "boolean",
                    "description": "是否永久保存此 Agent（默认 false = 临时，任务结束后自动清理）",
                },
                "force": {
                    "type": "boolean",
                    "description": "跳过相似度检查强制创建（默认 false，当系统建议使用已有 Agent 但你确实需要全新的时使用）",
                },
            },
            "required": ["name", "description"],
        },
        "examples": [
            {
                "scenario": "创建临时 SQL 专家（默认行为）",
                "params": {
                    "name": "SQL Expert",
                    "description": "专门处理 SQL 查询优化和数据库设计",
                    "custom_prompt": "你是一个 SQL 优化专家。",
                },
                "expected": "✅ Agent created: ephemeral_sql_expert_xxx (ephemeral)",
            },
        ],
    },
]
