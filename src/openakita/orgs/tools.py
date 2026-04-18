"""
组织节点专属工具定义

每个组织内的 Agent 节点自动获得这些工具，用于组织内通信、
记忆读写、组织感知、制度查询、人事管理等。
"""

from __future__ import annotations

ORG_NODE_TOOLS: list[dict] = [
    # ── 通信 ──
    {
        "name": "org_send_message",
        "description": "向指定同事发送消息。优先通过已有连线关系沟通。",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {
                    "type": "string",
                    "description": (
                        "目标节点的 id（`node_xxxxxxxx` 形式）。必须使用精确 id，"
                        "不要填角色名；如果不确定，先用 org_find_colleague 或 "
                        "org_get_org_chart 查询。不能写自己的 id。"
                    ),
                },
                "content": {"type": "string", "description": "消息内容"},
                "msg_type": {
                    "type": "string",
                    "enum": ["question", "answer", "feedback", "handshake"],
                    "description": "消息类型",
                    "default": "question",
                },
                "priority": {"type": "integer", "description": "优先级 0=普通 1=紧急 2=最高", "default": 0},
            },
            "required": ["to_node", "content"],
        },
    },
    {
        "name": "org_reply_message",
        "description": "回复某条已收到的消息",
        "input_schema": {
            "type": "object",
            "properties": {
                "reply_to": {"type": "string", "description": "要回复的消息 ID"},
                "content": {"type": "string", "description": "回复内容"},
            },
            "required": ["reply_to", "content"],
        },
    },
    {
        "name": "org_delegate_task",
        "description": (
            "向直属下级分配任务。只能分配给你的直接下属，不能委派给平级同事或自己。"
            "与平级协作请用 org_send_message。"
            "不确定下属 id 时先用 org_get_org_chart 或 org_find_colleague 查询。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {
                    "type": "string",
                    "description": (
                        "目标直属下级的节点 id（`node_xxxxxxxx` 形式）。必须是你的直接下属，"
                        "禁止填角色名或自己的 id；名字接近的同事要用精确 id 区分。"
                    ),
                },
                "task": {"type": "string", "description": "任务描述"},
                "deadline": {"type": "string", "description": "截止时间（ISO 格式，可选）。AI 节点通常在分钟内完成任务，建议设置 5-30 分钟的 deadline"},
                "priority": {"type": "integer", "default": 0},
            },
            "required": ["to_node", "task"],
        },
    },
    {
        "name": "org_escalate",
        "description": "向上级上报问题或请求决策",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "上报内容"},
                "priority": {"type": "integer", "default": 1},
            },
            "required": ["content"],
        },
    },
    {
        "name": "org_broadcast",
        "description": "广播单向通知消息（不需要回复的公告）。level=0 可全组织广播，其他仅部门广播。注意：如果需要开会讨论，请使用 org_request_meeting。",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "广播内容"},
                "scope": {"type": "string", "enum": ["department", "organization"], "default": "department"},
            },
            "required": ["content"],
        },
    },
    # ── 组织感知 ──
    {
        "name": "org_get_org_chart",
        "description": "查看完整组织架构（所有部门/岗位/职责/汇报关系/当前状态）",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_find_colleague",
        "description": "按能力/技能/部门搜索合适的同事",
        "input_schema": {
            "type": "object",
            "properties": {
                "need": {"type": "string", "description": "需要的能力或技能描述"},
                "prefer_department": {"type": "string", "description": "偏好部门（可选）"},
            },
            "required": ["need"],
        },
    },
    {
        "name": "org_get_node_status",
        "description": "查看某位同事的当前状态（忙/闲/任务队列）",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "节点 ID"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "org_get_org_status",
        "description": "查看组织整体运行状态摘要",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── 记忆 ──
    {
        "name": "org_read_blackboard",
        "description": "读取组织共享黑板（组织级共享记忆）的最新内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "description": "返回条数"},
                "tag": {"type": "string", "description": "按标签过滤（可选）"},
            },
        },
    },
    {
        "name": "org_write_blackboard",
        "description": "写入组织共享黑板。记录重要事实、决策、进度等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "rule", "progress", "lesson", "resource"],
                    "default": "fact",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                "importance": {"type": "number", "description": "重要程度 0.0~1.0", "default": 0.5},
            },
            "required": ["content"],
        },
    },
    {
        "name": "org_read_dept_memory",
        "description": "读取所属部门的共享记忆",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "org_write_dept_memory",
        "description": "写入部门共享记忆",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "memory_type": {"type": "string", "default": "fact"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "number", "default": 0.5},
            },
            "required": ["content"],
        },
    },
    # ── 节点级私有记忆 ──
    {
        "name": "org_read_node_memory",
        "description": "读取你自己的私有记忆（节点级），仅自己可见",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "org_write_node_memory",
        "description": "写入你自己的私有记忆（节点级）。用于记录个人工作笔记、经验教训、待办事项等，仅自己可见",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "rule", "progress", "lesson", "resource"],
                    "default": "fact",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                "importance": {"type": "number", "default": 0.5},
            },
            "required": ["content"],
        },
    },
    # ── 制度流程 ──
    {
        "name": "org_list_policies",
        "description": "列出所有组织制度和流程文件（返回索引）",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_read_policy",
        "description": "读取某个制度文件的完整内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "制度文件名（如 org-handbook.md）"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "org_search_policy",
        "description": "按关键词搜索相关制度内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    },
    # ── 人事管理 ──
    {
        "name": "org_freeze_node",
        "description": "冻结一个下级节点（保留数据，暂停活动）",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["node_id", "reason"],
        },
    },
    {
        "name": "org_unfreeze_node",
        "description": "解冻一个被冻结的下级节点",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "org_request_clone",
        "description": "申请克隆某岗位（加人手），需审批",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_node_id": {"type": "string", "description": "要克隆的岗位节点 ID"},
                "reason": {"type": "string", "description": "申请原因"},
                "ephemeral": {"type": "boolean", "default": True, "description": "是否为临时节点"},
            },
            "required": ["source_node_id", "reason"],
        },
    },
    {
        "name": "org_request_recruit",
        "description": "申请新增岗位（新技能），需审批",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_title": {"type": "string", "description": "岗位名称"},
                "role_goal": {"type": "string", "description": "岗位目标"},
                "department": {"type": "string", "description": "所属部门"},
                "reason": {"type": "string", "description": "申请原因"},
                "parent_node_id": {"type": "string", "description": "挂载在哪个上级下面"},
            },
            "required": ["role_title", "role_goal", "reason", "parent_node_id"],
        },
    },
    {
        "name": "org_dismiss_node",
        "description": "申请裁撤临时节点（仅 ephemeral 节点可裁撤）",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "要裁撤的节点 ID"},
                "reason": {"type": "string", "description": "裁撤原因"},
            },
            "required": ["node_id"],
        },
    },
    # ── 会议 ──
    {
        "name": "org_request_meeting",
        "description": "发起并召开多方实时会议。参与者轮流发言讨论，自动生成会议结论并写入组织黑板。当需要「开会」「讨论」「汇报」时，必须使用此工具而非 org_broadcast。",
        "input_schema": {
            "type": "object",
            "properties": {
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参与会议的节点 ID 列表（不含发起者自己）",
                },
                "topic": {"type": "string", "description": "会议主题"},
                "max_rounds": {"type": "integer", "default": 3, "description": "最大讨论轮次"},
            },
            "required": ["participants", "topic"],
        },
    },
    # ── 定时任务管理 ──
    {
        "name": "org_create_schedule",
        "description": "为自己创建一个定时任务（需上级审批）",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "任务名称（如 '巡检服务器'）"},
                "schedule_type": {"type": "string", "enum": ["cron", "interval", "once"], "default": "interval"},
                "cron": {"type": "string", "description": "cron 表达式（schedule_type=cron 时必填）"},
                "interval_s": {"type": "integer", "description": "间隔秒数（schedule_type=interval 时必填）"},
                "run_at": {"type": "string", "description": "执行时间 ISO 格式（schedule_type=once 时必填）"},
                "prompt": {"type": "string", "description": "触发时执行的指令"},
                "report_to": {"type": "string", "description": "汇报对象节点 ID（可选）"},
                "report_condition": {"type": "string", "enum": ["always", "on_issue", "never"], "default": "on_issue"},
            },
            "required": ["name", "prompt"],
        },
    },
    {
        "name": "org_list_my_schedules",
        "description": "查看自己的定时任务列表",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_assign_schedule",
        "description": "给下级指定一个定时任务（上级专用）",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_node_id": {"type": "string", "description": "目标下级节点 ID"},
                "name": {"type": "string", "description": "任务名称"},
                "schedule_type": {"type": "string", "enum": ["cron", "interval", "once"], "default": "interval"},
                "cron": {"type": "string", "description": "cron 表达式"},
                "interval_s": {"type": "integer", "description": "间隔秒数"},
                "prompt": {"type": "string", "description": "触发时执行的指令"},
                "report_to": {"type": "string", "description": "汇报对象（默认为自己）"},
                "report_condition": {"type": "string", "enum": ["always", "on_issue", "never"], "default": "on_issue"},
            },
            "required": ["target_node_id", "name", "prompt"],
        },
    },
    # ── 任务交付与验收 ──
    {
        "name": "org_submit_deliverable",
        "description": "提交任务交付物给委派人，等待验收。to_node 可省略，系统将自动提交给你的直属上级。",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {"type": "string", "description": "委派人节点 ID（可省略，系统自动提交给直属上级）"},
                "task_chain_id": {"type": "string", "description": "任务链 ID（从收到的任务消息中获取）"},
                "deliverable": {
                    "type": "string",
                    "description": (
                        "交付内容（必须包含实质成果）。要求：\n"
                        "- 如果产出了文档/模板/方案等文本文件，请包含完整文本内容\n"
                        "- 如果产出了代码/配置，请包含关键代码片段\n"
                        "- 如果委托下级完成，请汇总下级的交付内容和文件信息\n"
                        "- 禁止只写'已完成'等空洞简述，必须让接收方能理解具体成果"
                    ),
                },
                "summary": {"type": "string", "description": "工作过程简述"},
                "file_attachments": {
                    "type": "array",
                    "description": (
                        "如果本次交付涉及文件（无论通过 write_file 还是 run_shell 产出），"
                        "必须在此字段声明，否则用户看不到附件。file_path 可以是相对于"
                        "组织工作区的路径或绝对路径。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "文件显示名"},
                            "file_path": {"type": "string", "description": "文件路径（相对于组织工作区或绝对路径）"},
                            "description": {"type": "string", "description": "文件说明（可选）"},
                        },
                        "required": ["filename", "file_path"],
                    },
                },
            },
            "required": ["deliverable"],
        },
    },
    {
        "name": "org_accept_deliverable",
        "description": "验收通过下级提交的交付物。",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "任务链 ID"},
                "from_node": {"type": "string", "description": "交付人节点 ID"},
                "feedback": {"type": "string", "description": "验收意见（可选）"},
            },
            "required": ["task_chain_id", "from_node"],
        },
    },
    {
        "name": "org_reject_deliverable",
        "description": "打回下级提交的交付物，说明问题要求修改。",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "任务链 ID"},
                "from_node": {"type": "string", "description": "交付人节点 ID"},
                "reason": {"type": "string", "description": "打回原因和修改要求"},
            },
            "required": ["task_chain_id", "from_node", "reason"],
        },
    },
    {
        "name": "org_wait_for_deliverable",
        "description": (
            "阻塞等待你派出的下级任务完成（用 org_delegate_task 派的）。"
            "比 org_list_delegated_tasks 轮询高效得多——会在以下任一事件触发时立即返回：\n"
            "  1) 任意指定的子任务链关闭（被你 accept/reject 或被取消）\n"
            "  2) 收到下级新消息（提问/升级），需要你立即处理\n"
            "  3) timeout 到期（默认 60 秒）\n"
            "  4) 用户取消整个命令\n"
            "返回值会告诉你：哪些子链已关闭、是否被消息打断、是否超时。"
            "建议用法：派完一组并行任务后立即 wait，超时后用 org_list_delegated_tasks "
            "看进度，再决定是继续 wait 还是输出阶段性汇总。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chain_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "要等待的具体任务链 ID 列表（可选）。"
                        "省略时自动等待你最近派出的所有未关闭子链。"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "最大等待秒数，默认 60，最大 300。",
                    "default": 60,
                },
            },
        },
    },
    # ── 制度提议 ──
    {
        "name": "org_propose_policy",
        "description": "提议新制度或修改现有制度（需管理层审批）",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "制度文件名（如 workflow-deploy.md）"},
                "title": {"type": "string", "description": "制度标题"},
                "content": {"type": "string", "description": "制度内容（Markdown 格式）"},
                "reason": {"type": "string", "description": "提议原因"},
            },
            "required": ["filename", "title", "content", "reason"],
        },
    },
    # ── 工具申请/授权/收回 ──
    {
        "name": "org_request_tools",
        "description": "向直属上级申请增加外部工具能力（如搜索、文件、计划等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "申请的工具类目或具体工具名列表（如 [\"research\", \"planning\"]）",
                },
                "reason": {"type": "string", "description": "申请原因，说明为什么需要这些工具"},
            },
            "required": ["tools", "reason"],
        },
    },
    {
        "name": "org_grant_tools",
        "description": "授权直属下级使用额外的外部工具（仅上级可用）",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "目标下级节点 ID"},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "授权的工具类目或具体工具名列表",
                },
            },
            "required": ["node_id", "tools"],
        },
    },
    {
        "name": "org_revoke_tools",
        "description": "收回直属下级的外部工具权限（仅上级可用）",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "目标下级节点 ID"},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要收回的工具类目或具体工具名列表",
                },
            },
            "required": ["node_id", "tools"],
        },
    },
    # ── 项目任务进度与查询 ──
    {
        "name": "org_report_progress",
        "description": "汇报当前任务进度（进度百分比、步骤摘要、执行日志）",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "任务链 ID"},
                "progress_pct": {"type": "integer", "description": "进度百分比 0-100", "default": 0},
                "summary": {"type": "string", "description": "进度摘要"},
                "log_entry": {"type": "string", "description": "追加到执行日志的条目"},
            },
            "required": ["task_chain_id"],
        },
    },
    {
        "name": "org_get_task_progress",
        "description": "获取指定任务的进度详情（计划步骤、执行日志、进度百分比）",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "任务链 ID"},
                "task_id": {"type": "string", "description": "项目任务 ID（二选一）"},
            },
        },
    },
    {
        "name": "org_list_my_tasks",
        "description": "列出分配给自己的项目任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "按状态过滤"},
                "limit": {"type": "integer", "default": 10, "description": "返回条数"},
            },
        },
    },
    {
        "name": "org_list_delegated_tasks",
        "description": "列出自己委派给他人的任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "按状态过滤"},
                "limit": {"type": "integer", "default": 10, "description": "返回条数"},
            },
        },
    },
    {
        "name": "org_list_project_tasks",
        "description": "列出指定项目的所有任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "按状态过滤"},
                "limit": {"type": "integer", "default": 20, "description": "返回条数"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "org_update_project_task",
        "description": "更新项目任务（进度、状态、计划步骤、执行日志）",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "项目任务 ID"},
                "task_chain_id": {"type": "string", "description": "任务链 ID（二选一）"},
                "progress_pct": {"type": "integer", "description": "进度百分比 0-100"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"]},
                "plan_steps": {"type": "array", "items": {"type": "object"}, "description": "计划步骤"},
                "execution_log": {"type": "array", "items": {"type": "string"}, "description": "执行日志（追加）"},
            },
        },
    },
    {
        "name": "org_create_project_task",
        "description": "在项目中创建新任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "description": "任务描述"},
                "assignee_node_id": {"type": "string", "description": "执行人节点 ID"},
                "parent_task_id": {"type": "string", "description": "父任务 ID（子任务时）"},
                "chain_id": {"type": "string", "description": "任务链 ID（关联委派）"},
            },
            "required": ["project_id", "title"],
        },
    },
]


def build_org_node_tools(org: "object", node: "object") -> list[dict]:
    """Return a per-node customized copy of ORG_NODE_TOOLS.

    Customizations applied:

    - ``org_delegate_task.to_node`` gets an ``enum`` limited to the node's
      direct subordinate ids, physically preventing the LLM from selecting
      an invalid target (self, peers, grand-children, or non-existing ids).
    - If the node has no direct subordinates (leaf node), ``org_delegate_task``
      is dropped from the returned list entirely. Leaf nodes should use
      ``org_submit_deliverable`` to hand results back upwards.
    - All other tools are returned by reference without mutation, so there
      is zero shared-state risk for them.

    Args:
        org: The owning :class:`Organization` instance. Must expose
            ``get_children(node_id) -> list[OrgNode]``.
        node: The :class:`OrgNode` to build tools for. Must expose ``id``.

    Returns:
        A list of tool definition dicts suitable for injecting into the
        node's agent tool catalog / ``_tools`` list.
    """
    import copy

    children = org.get_children(node.id)
    child_ids = [c.id for c in children]

    out: list[dict] = []
    for tpl in ORG_NODE_TOOLS:
        name = tpl.get("name", "")
        if name == "org_delegate_task":
            if not child_ids:
                continue
            tool = copy.deepcopy(tpl)
            props = tool["input_schema"]["properties"]
            props["to_node"]["enum"] = list(child_ids)
            hint_ids = ", ".join(f"`{cid}`" for cid in child_ids)
            base_desc = props["to_node"].get("description", "")
            props["to_node"]["description"] = f"{base_desc}（只能是：{hint_ids}）"
            out.append(tool)
        else:
            out.append(tpl)
    return out
