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
    # -- Scheduled task management --
    {
        "name": "org_create_schedule",
        "description": "Create a scheduled task for yourself (requires approval from your superior)",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name (e.g. 'Server inspection')"},
                "schedule_type": {"type": "string", "enum": ["cron", "interval", "once"], "default": "interval"},
                "cron": {"type": "string", "description": "cron expression (required when schedule_type=cron)"},
                "interval_s": {"type": "integer", "description": "Interval in seconds (required when schedule_type=interval)"},
                "run_at": {"type": "string", "description": "Run time in ISO format (required when schedule_type=once)"},
                "prompt": {"type": "string", "description": "Instruction to execute when triggered"},
                "report_to": {"type": "string", "description": "Node ID of the report recipient (optional)"},
                "report_condition": {"type": "string", "enum": ["always", "on_issue", "never"], "default": "on_issue"},
            },
            "required": ["name", "prompt"],
        },
    },
    {
        "name": "org_list_my_schedules",
        "description": "List your own scheduled tasks",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_assign_schedule",
        "description": "Assign a scheduled task to a subordinate (superior only)",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_node_id": {"type": "string", "description": "Target subordinate node ID"},
                "name": {"type": "string", "description": "Task name"},
                "schedule_type": {"type": "string", "enum": ["cron", "interval", "once"], "default": "interval"},
                "cron": {"type": "string", "description": "cron expression"},
                "interval_s": {"type": "integer", "description": "Interval in seconds"},
                "prompt": {"type": "string", "description": "Instruction to execute when triggered"},
                "report_to": {"type": "string", "description": "Report recipient (defaults to yourself)"},
                "report_condition": {"type": "string", "enum": ["always", "on_issue", "never"], "default": "on_issue"},
            },
            "required": ["target_node_id", "name", "prompt"],
        },
    },
    # -- Task delivery and acceptance --
    {
        "name": "org_submit_deliverable",
        "description": "Submit a task deliverable to the delegator and wait for acceptance. to_node may be omitted; the system will automatically submit to your direct superior.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_node": {"type": "string", "description": "Delegator node ID (optional; system auto-submits to your direct superior)"},
                "task_chain_id": {"type": "string", "description": "Task chain ID (obtained from the received task message)"},
                "deliverable": {
                    "type": "string",
                    "description": (
                        "Deliverable content (must include substantive results). Requirements:\n"
                        "- If a document/template/proposal or other text file was produced, include the full text\n"
                        "- If code/config was produced, include the key snippets\n"
                        "- If the work was delegated to a subordinate, summarize the subordinate's deliverable and file info\n"
                        "- Empty summaries such as 'done' are forbidden; the recipient must be able to understand the concrete result"
                    ),
                },
                "summary": {"type": "string", "description": "Brief summary of the work process"},
                "file_attachments": {
                    "type": "array",
                    "description": (
                        "If this delivery involves files (whether produced via write_file or run_shell), "
                        "you must declare them in this field or the user will not see the attachments. "
                        "file_path may be relative to the organization's workspace or absolute."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "Display name of the file"},
                            "file_path": {"type": "string", "description": "File path (relative to the organization's workspace, or absolute)"},
                            "description": {"type": "string", "description": "File description (optional)"},
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
        "description": "Accept a deliverable submitted by a subordinate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "Task chain ID"},
                "from_node": {"type": "string", "description": "Submitter node ID"},
                "feedback": {"type": "string", "description": "Acceptance feedback (optional)"},
            },
            "required": ["task_chain_id", "from_node"],
        },
    },
    {
        "name": "org_reject_deliverable",
        "description": "Reject a subordinate's deliverable, explaining the issues and required changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "Task chain ID"},
                "from_node": {"type": "string", "description": "Submitter node ID"},
                "reason": {"type": "string", "description": "Reason for rejection and required changes"},
            },
            "required": ["task_chain_id", "from_node", "reason"],
        },
    },
    {
        "name": "org_wait_for_deliverable",
        "description": (
            "Block and wait for delegated subordinate tasks (dispatched via org_delegate_task) to complete. "
            "Far more efficient than polling with org_list_delegated_tasks — returns immediately on any of:\n"
            "  1) Any specified sub-task chain closes (accepted, rejected, or cancelled by you)\n"
            "  2) A subordinate sends a new message (question/escalation) requiring your immediate attention\n"
            "  3) The timeout expires (default 60 seconds)\n"
            "  4) The user cancels the entire command\n"
            "The return value indicates which sub-chains have closed, whether a message interrupted the wait, and whether it timed out. "
            "Recommended usage: call wait immediately after dispatching a batch of parallel tasks; "
            "after a timeout, use org_list_delegated_tasks to check progress, then decide whether to continue waiting or emit an interim summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chain_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific task chain IDs to wait for (optional). "
                        "If omitted, automatically waits for all open sub-chains you most recently dispatched."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum wait time in seconds (default 60, max 300).",
                    "default": 60,
                },
            },
        },
    },
    # -- Policy proposals --
    {
        "name": "org_propose_policy",
        "description": "Propose a new policy or a change to an existing policy (requires management approval)",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Policy file name (e.g. workflow-deploy.md)"},
                "title": {"type": "string", "description": "Policy title"},
                "content": {"type": "string", "description": "Policy content (Markdown format)"},
                "reason": {"type": "string", "description": "Reason for the proposal"},
            },
            "required": ["filename", "title", "content", "reason"],
        },
    },
    # -- Tool request / grant / revoke --
    {
        "name": "org_request_tools",
        "description": "Request additional external tool capabilities (e.g. search, file, planning) from your direct superior",
        "input_schema": {
            "type": "object",
            "properties": {
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool categories or specific tool names being requested (e.g. [\"research\", \"planning\"])",
                },
                "reason": {"type": "string", "description": "Reason for the request; explain why these tools are needed"},
            },
            "required": ["tools", "reason"],
        },
    },
    {
        "name": "org_grant_tools",
        "description": "Grant a direct subordinate the use of additional external tools (superior only)",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Target subordinate node ID"},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool categories or specific tool names to grant",
                },
            },
            "required": ["node_id", "tools"],
        },
    },
    {
        "name": "org_revoke_tools",
        "description": "Revoke a direct subordinate's external tool permissions (superior only)",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Target subordinate node ID"},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool categories or specific tool names to revoke",
                },
            },
            "required": ["node_id", "tools"],
        },
    },
    # -- Project task progress and queries --
    {
        "name": "org_report_progress",
        "description": "Report progress on the current task (progress percentage, step summary, execution log)",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "Task chain ID"},
                "progress_pct": {"type": "integer", "description": "Progress percentage 0-100", "default": 0},
                "summary": {"type": "string", "description": "Progress summary"},
                "log_entry": {"type": "string", "description": "Entry to append to the execution log"},
            },
            "required": ["task_chain_id"],
        },
    },
    {
        "name": "org_get_task_progress",
        "description": "Get progress details for a specific task (plan steps, execution log, progress percentage)",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_chain_id": {"type": "string", "description": "Task chain ID"},
                "task_id": {"type": "string", "description": "Project task ID (one of the two)"},
            },
        },
    },
    {
        "name": "org_list_my_tasks",
        "description": "List project tasks assigned to you",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "Filter by status"},
                "limit": {"type": "integer", "default": 10, "description": "Number of entries to return"},
            },
        },
    },
    {
        "name": "org_list_delegated_tasks",
        "description": "List tasks you have delegated to others",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "Filter by status"},
                "limit": {"type": "integer", "default": 10, "description": "Number of entries to return"},
            },
        },
    },
    {
        "name": "org_list_project_tasks",
        "description": "List all tasks in a given project",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"], "description": "Filter by status"},
                "limit": {"type": "integer", "default": 20, "description": "Number of entries to return"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "org_update_project_task",
        "description": "Update a project task (progress, status, plan steps, execution log)",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Project task ID"},
                "task_chain_id": {"type": "string", "description": "Task chain ID (one of the two)"},
                "progress_pct": {"type": "integer", "description": "Progress percentage 0-100"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "delivered", "accepted", "rejected", "blocked"]},
                "plan_steps": {"type": "array", "items": {"type": "object"}, "description": "Plan steps"},
                "execution_log": {"type": "array", "items": {"type": "string"}, "description": "Execution log (appended)"},
            },
        },
    },
    {
        "name": "org_create_project_task",
        "description": "Create a new task within a project",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID"},
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description"},
                "assignee_node_id": {"type": "string", "description": "Assignee node ID"},
                "parent_task_id": {"type": "string", "description": "Parent task ID (for subtasks)"},
                "chain_id": {"type": "string", "description": "Task chain ID (links to delegation)"},
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
            props["to_node"]["description"] = f"{base_desc} (allowed values only: {hint_ids})"
            out.append(tool)
        else:
            out.append(tpl)
    return out
