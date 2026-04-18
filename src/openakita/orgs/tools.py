"""
Organization node tool definitions.

Every Agent node inside an organization automatically gets these tools for
in-org communication, memory reads/writes, org awareness, policy queries,
personnel management, etc.
"""

from __future__ import annotations

ORG_NODE_TOOLS: list[dict] = [
    # -- Communication --
    {
        "name": "org_send_message",
        "description": "Send a message to a specific colleague. Prefer existing connections where available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {
                    "type": "string",
                    "description": (
                        "The target node's id (form: `node_xxxxxxxx`). Must be an exact id; "
                        "do not supply a role name. If unsure, look it up first via org_find_colleague "
                        "or org_get_org_chart. Your own id is not allowed."
                    ),
                },
                "content": {"type": "string", "description": "Message content"},
                "msg_type": {
                    "type": "string",
                    "enum": ["question", "answer", "feedback", "handshake"],
                    "description": "Message type",
                    "default": "question",
                },
                "priority": {"type": "integer", "description": "Priority: 0=normal, 1=urgent, 2=highest", "default": 0},
            },
            "required": ["to_node", "content"],
        },
    },
    {
        "name": "org_reply_message",
        "description": "Reply to a previously received message",
        "input_schema": {
            "type": "object",
            "properties": {
                "reply_to": {"type": "string", "description": "ID of the message being replied to"},
                "content": {"type": "string", "description": "Reply content"},
            },
            "required": ["reply_to", "content"],
        },
    },
    {
        "name": "org_delegate_task",
        "description": (
            "Assign a task to a direct subordinate. Only direct reports may be assigned to; "
            "peers and yourself are not allowed. For peer collaboration use org_send_message. "
            "If unsure of a subordinate's id, look it up first with org_get_org_chart or org_find_colleague."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {
                    "type": "string",
                    "description": (
                        "Target direct subordinate node id (form: `node_xxxxxxxx`). Must be a direct report; "
                        "role names and your own id are forbidden. Use exact ids to disambiguate similarly-named colleagues."
                    ),
                },
                "task": {"type": "string", "description": "Task description"},
                "deadline": {"type": "string", "description": "Deadline (ISO format, optional). AI nodes usually finish tasks within minutes; a 5-30 minute deadline is recommended."},
                "priority": {"type": "integer", "default": 0},
            },
            "required": ["to_node", "task"],
        },
    },
    {
        "name": "org_escalate",
        "description": "Escalate a problem to a superior or request a decision",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Escalation content"},
                "priority": {"type": "integer", "default": 1},
            },
            "required": ["content"],
        },
    },
    {
        "name": "org_broadcast",
        "description": "Broadcast a one-way notification (an announcement that does not expect replies). level=0 broadcasts org-wide; otherwise department-only. Note: if a discussion/meeting is needed, use org_request_meeting instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Broadcast content"},
                "scope": {"type": "string", "enum": ["department", "organization"], "default": "department"},
            },
            "required": ["content"],
        },
    },
    # -- Organization awareness --
    {
        "name": "org_get_org_chart",
        "description": "View the full org chart (all departments/positions/responsibilities/reporting relations/current status)",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_find_colleague",
        "description": "Search for a suitable colleague by capability/skill/department",
        "input_schema": {
            "type": "object",
            "properties": {
                "need": {"type": "string", "description": "Description of the required capability or skill"},
                "prefer_department": {"type": "string", "description": "Preferred department (optional)"},
            },
            "required": ["need"],
        },
    },
    {
        "name": "org_get_node_status",
        "description": "View a colleague's current status (busy/idle/task queue)",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "org_get_org_status",
        "description": "View a summary of the organization's overall runtime status",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Memory --
    {
        "name": "org_read_blackboard",
        "description": "Read the latest content from the organization's shared blackboard (org-level shared memory)",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "description": "Number of entries to return"},
                "tag": {"type": "string", "description": "Filter by tag (optional)"},
            },
        },
    },
    {
        "name": "org_write_blackboard",
        "description": "Write to the organization's shared blackboard. Record important facts, decisions, progress, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Memory content"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "rule", "progress", "lesson", "resource"],
                    "default": "fact",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                "importance": {"type": "number", "description": "Importance 0.0~1.0", "default": 0.5},
            },
            "required": ["content"],
        },
    },
    {
        "name": "org_read_dept_memory",
        "description": "Read shared memory for your department",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "org_write_dept_memory",
        "description": "Write to shared department memory",
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
    # -- Node-level private memory --
    {
        "name": "org_read_node_memory",
        "description": "Read your own private (node-level) memory; visible only to you",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "org_write_node_memory",
        "description": "Write to your own private (node-level) memory. Use this for personal work notes, lessons learned, to-dos, etc.; visible only to you.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Memory content"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "rule", "progress", "lesson", "resource"],
                    "default": "fact",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                "importance": {"type": "number", "default": 0.5},
            },
            "required": ["content"],
        },
    },
    # -- Policies and procedures --
    {
        "name": "org_list_policies",
        "description": "List all organization policy/procedure files (returns an index)",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_read_policy",
        "description": "Read the full content of a policy file",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Policy file name (e.g. org-handbook.md)"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "org_search_policy",
        "description": "Search policy content by keyword",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
            },
            "required": ["query"],
        },
    },
    # -- Personnel management --
    {
        "name": "org_freeze_node",
        "description": "Freeze a subordinate node (retains data, pauses activity)",
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
        "description": "Unfreeze a previously frozen subordinate node",
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
        "description": "Request to clone a position (add headcount); requires approval",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_node_id": {"type": "string", "description": "Node ID of the position to clone"},
                "reason": {"type": "string", "description": "Reason for the request"},
                "ephemeral": {"type": "boolean", "default": True, "description": "Whether this is an ephemeral node"},
            },
            "required": ["source_node_id", "reason"],
        },
    },
    {
        "name": "org_request_recruit",
        "description": "Request a new position (new skill); requires approval",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_title": {"type": "string", "description": "Role title"},
                "role_goal": {"type": "string", "description": "Role goal"},
                "department": {"type": "string", "description": "Department"},
                "reason": {"type": "string", "description": "Reason for the request"},
                "parent_node_id": {"type": "string", "description": "The superior node to attach under"},
            },
            "required": ["role_title", "role_goal", "reason", "parent_node_id"],
        },
    },
    {
        "name": "org_dismiss_node",
        "description": "Request dismissal of an ephemeral node (only ephemeral nodes can be dismissed)",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID to dismiss"},
                "reason": {"type": "string", "description": "Reason for dismissal"},
            },
            "required": ["node_id"],
        },
    },
    # -- Meetings --
    {
        "name": "org_request_meeting",
        "description": "Initiate and hold a real-time multi-party meeting. Participants take turns speaking; conclusions are generated automatically and written to the org blackboard. When a 'meeting', 'discussion', or 'report' is needed, use this tool rather than org_broadcast.",
        "input_schema": {
            "type": "object",
            "properties": {
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs participating in the meeting (excluding the initiator)",
                },
                "topic": {"type": "string", "description": "Meeting topic"},
                "max_rounds": {"type": "integer", "default": 3, "description": "Maximum discussion rounds"},
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
