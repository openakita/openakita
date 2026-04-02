"""
Todo & Plan 工具定义

Todo 工具（Agent 模式下的任务执行跟踪）：
- create_todo: 创建任务执行计划
- update_todo_step: 更新步骤状态
- get_todo_status: 获取计划执行状态
- complete_todo: 完成计划

Plan 模式工具：
- create_plan_file: 创建结构化 Plan 文件
- exit_plan_mode: 退出 Plan 模式
"""

PLAN_TOOLS = [
    {
        "name": "create_todo",
        "category": "Todo",
        "description": (
            "Create a structured task plan for multi-step tasks. "
            "If user request needs 2+ tool calls (like 'open + search + screenshot'), "
            "call create_todo BEFORE any other tool.\n\n"
            "When to use:\n"
            "- 3+ distinct steps needed\n"
            "- User provides multiple tasks\n"
            "- Complex task requiring careful planning\n\n"
            "When NOT to use:\n"
            "- Single straightforward tasks completable in 1-2 steps\n"
            "- Trivial tasks with no organizational benefit\n"
            "- Purely conversational/informational requests\n\n"
            "IMPORTANT: Mark steps complete IMMEDIATELY after finishing each one. "
            "Only ONE step should be in_progress at a time."
        ),
        "detail": """创建任务执行计划。

**何时使用**：
- 任务需要超过 2 步完成时
- 用户请求中有"然后"、"接着"、"之后"等词
- 涉及多个工具协作

**使用流程**：
1. create_todo → 2. 执行步骤 → 3. update_todo_step → 4. ... → 5. complete_todo

**步骤字段说明**：
- `id` + `description`: 必填
- `tool`: 可选，预计使用的工具名
- `skills`: 可选，关联的技能名称列表（用于追踪）
- `depends_on`: 可选，前置依赖步骤

**示例**：
用户："打开百度搜索天气并截图发我"
→ create_todo(steps=[打开百度, 输入关键词, 点击搜索, 截图, 发送])""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_summary": {"type": "string", "description": "任务的一句话总结"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤ID，如 step_1, step_2"},
                            "description": {"type": "string", "description": "步骤描述"},
                            "tool": {"type": "string", "description": "预计使用的工具（可选）"},
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本步骤关联的 skill 名称列表（可选，用于追踪）",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "依赖的步骤ID（可选）",
                            },
                            "blocks": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "此步骤完成后才能开始的步骤ID列表（可选）",
                            },
                            "owner": {
                                "type": "string",
                                "description": "负责执行此步骤的 agent ID（可选，多代理协作时使用）",
                            },
                        },
                        "required": ["id", "description"],
                    },
                    "description": "步骤列表",
                },
            },
            "required": ["task_summary", "steps"],
        },
    },
    {
        "name": "update_todo_step",
        "category": "Todo",
        "description": "Update the status of a todo step. MUST call after completing each step to track progress.",
        "detail": """更新计划中某个步骤的状态。

**每完成一步必须调用此工具！**

**状态值**：
- pending: 待执行
- in_progress: 执行中
- completed: 已完成
- failed: 执行失败
- skipped: 已跳过

**示例**：
执行完 browser_navigate 后：
→ update_todo_step(step_id="step_1", status="completed", result="已打开百度首页")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string", "description": "步骤ID"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "skipped"],
                    "description": "步骤状态",
                },
                "result": {"type": "string", "description": "执行结果或错误信息"},
            },
            "required": ["step_id", "status"],
        },
    },
    {
        "name": "get_todo_status",
        "category": "Todo",
        "description": "Get the current todo execution status. Shows all steps and their completion status.",
        "detail": """获取当前计划的执行状态。

返回信息包括：
- 计划总览
- 各步骤状态
- 已完成/待执行数量
- 执行日志""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_todo",
        "category": "Todo",
        "description": "Mark the todo as completed and generate a summary report. Call when ALL steps are done.",
        "detail": """标记计划完成，生成最终报告。

**在所有步骤完成后调用**

**返回**：
- 执行摘要
- 成功/失败统计
- 总耗时""",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "完成总结"}},
            "required": ["summary"],
        },
    },
    {
        "name": "create_plan_file",
        "category": "Plan",
        "description": (
            "Create a structured plan file (.plan.md) with YAML frontmatter and detailed "
            "Markdown body. Used in Plan mode to produce a reviewable plan document.\n\n"
            "This tool creates a NEW plan file each time it is called. To update an existing "
            "plan, use edit_file directly on the plan file — do NOT call create_plan_file again.\n\n"
            "The plan name should only be specified on the first call. On subsequent updates "
            "via edit_file, the filename stays stable."
        ),
        "detail": """创建结构化 Plan 文件（YAML frontmatter + Markdown body）。

**用于 Plan 模式**：生成一个用户可审阅的计划文件。

**文件格式**：
```yaml
---
name: Plan Name
overview: 简要描述
todos:
  - id: step_1
    content: "步骤描述"
    status: pending
isProject: true
---
```

后面跟详细的 Markdown 内容（方案分析、文件列表、风险评估等）。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "计划名称"},
                "overview": {"type": "string", "description": "计划概要（1-2 句话）"},
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤 ID"},
                            "content": {"type": "string", "description": "步骤描述"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "初始状态（通常为 pending）",
                            },
                        },
                        "required": ["id", "content"],
                    },
                    "description": "步骤列表",
                },
                "body": {
                    "type": "string",
                    "description": "Markdown 格式的详细计划内容（方案分析、文件列表、风险评估等）",
                },
            },
            "required": ["name", "todos"],
        },
    },
    {
        "name": "exit_plan_mode",
        "category": "Plan",
        "description": "Signal that planning is complete. Triggers the approval UI for the user to review and approve the plan before execution.",
        "detail": """退出 Plan 模式，通知系统规划已完成。

**在完成 create_plan_file 后调用此工具**。

调用后系统会：
1. 通知前端展示 Plan 审批界面
2. 等待用户审批
3. 用户批准后自动切换到 Agent 模式执行""",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "规划完成的简要说明",
                },
            },
        },
    },
]
