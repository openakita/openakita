"""
Scheduled Tasks 工具定义

包含定时任务管理相关的工具：
- schedule_task: 创建定时任务
- list_scheduled_tasks: 列出所有任务
- cancel_scheduled_task: 取消任务
- update_scheduled_task: 更新任务
- trigger_scheduled_task: 立即触发任务
"""

SCHEDULED_TOOLS = [
    {
        "name": "schedule_task",
        "description": "Create scheduled task or reminder. IMPORTANT: Must actually call this tool to create task - just saying 'OK I will remind you' does NOT create the task! Task types: (1) reminder - sends message at scheduled time (default, 90%% of cases), (2) task - AI executes operations.",
        "detail": """创建定时任务或提醒。

⚠️ **重要: 必须调用此工具才能创建任务！只是说"好的我会提醒你"不会创建任务！**

**任务类型判断规则**：
✅ **reminder**（默认优先）: 所有只需要发送消息的提醒
   - "提醒我喝水" → reminder
   - "站立提醒" → reminder
   - "叫我起床" → reminder
   
❌ **task**（仅当需要 AI 执行操作时）:
   - "查询天气告诉我" → task（需要查询）
   - "截图发给我" → task（需要操作）

**90%的提醒都应该是 reminder 类型！**

**触发类型**：
- once: 一次性执行
- interval: 间隔执行
- cron: cron 表达式""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "任务/提醒名称"},
                "description": {"type": "string", "description": "任务描述"},
                "task_type": {
                    "type": "string",
                    "enum": ["reminder", "task"],
                    "default": "reminder",
                    "description": "默认使用 reminder！reminder=发消息提醒，task=AI 执行操作"
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["once", "interval", "cron"],
                    "description": "触发类型"
                },
                "trigger_config": {
                    "type": "object",
                    "description": "触发配置。once: {run_at: '2026-02-01 10:00'}；interval: {interval_minutes: 30}；cron: {cron: '0 9 * * *'}"
                },
                "reminder_message": {
                    "type": "string",
                    "description": "提醒消息内容（仅 reminder 类型需要）"
                },
                "prompt": {
                    "type": "string",
                    "description": "执行时发送给 Agent 的提示（仅 task 类型需要）"
                },
                "notify_on_start": {
                    "type": "boolean",
                    "default": True,
                    "description": "任务开始时发通知？默认 true"
                },
                "notify_on_complete": {
                    "type": "boolean",
                    "default": True,
                    "description": "任务完成时发通知？默认 true"
                }
            },
            "required": ["name", "description", "task_type", "trigger_type", "trigger_config"]
        }
    },
    {
        "name": "list_scheduled_tasks",
        "description": "List all scheduled tasks with their ID, name, type, status, and next execution time. When you need to: (1) Check existing tasks, (2) Find task ID for cancel/update, (3) Verify task creation.",
        "detail": """列出所有定时任务。

**返回信息**：
- 任务 ID
- 名称
- 类型（reminder/task）
- 状态（enabled/disabled）
- 下次执行时间

**适用场景**：
- 查看已创建的任务
- 获取任务 ID 用于取消/更新
- 验证任务是否创建成功""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled_only": {"type": "boolean", "description": "是否只列出启用的任务", "default": False}
            }
        }
    },
    {
        "name": "cancel_scheduled_task",
        "description": "PERMANENTLY DELETE scheduled task. When user says 'cancel/delete task' → use this. When user says 'turn off notification' → use update_scheduled_task with notify=false. When user says 'pause task' → use update_scheduled_task with enabled=false.",
        "detail": """【永久删除】定时任务。

⚠️ **操作区分**：
- 用户说"取消/删除任务" → 用此工具
- 用户说"关闭提醒" → 用 update_scheduled_task 设 notify=false
- 用户说"暂停任务" → 用 update_scheduled_task 设 enabled=false

**注意**：删除后无法恢复！""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "update_scheduled_task",
        "description": "Modify scheduled task settings WITHOUT deleting. Can modify: notify_on_start, notify_on_complete, enabled. Common uses: (1) 'Turn off notification' → notify=false, (2) 'Pause task' → enabled=false, (3) 'Resume task' → enabled=true.",
        "detail": """修改定时任务设置【不删除任务】。

**可修改项**：
- notify_on_start: 开始时是否通知
- notify_on_complete: 完成时是否通知
- enabled: 是否启用

**常见用法**：
- "关闭提醒" → notify_on_start=false, notify_on_complete=false
- "暂停任务" → enabled=false
- "恢复任务" → enabled=true""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要修改的任务 ID"},
                "notify_on_start": {"type": "boolean", "description": "开始时发通知？不传=不修改"},
                "notify_on_complete": {"type": "boolean", "description": "完成时发通知？不传=不修改"},
                "enabled": {"type": "boolean", "description": "启用/暂停任务？不传=不修改"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "trigger_scheduled_task",
        "description": "Immediately trigger scheduled task without waiting for scheduled time. When you need to: (1) Test task execution, (2) Run task ahead of schedule.",
        "detail": """立即触发定时任务（不等待计划时间）。

**适用场景**：
- 测试任务执行
- 提前运行任务

**注意**：
不会影响原有的执行计划""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"}
            },
            "required": ["task_id"]
        }
    },
]
