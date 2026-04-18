"""
Scheduled Tasks tool definitions

Contains tools related to scheduled task management:
- schedule_task: create a scheduled task
- list_scheduled_tasks: list all tasks
- cancel_scheduled_task: cancel a task
- update_scheduled_task: update a task
- trigger_scheduled_task: trigger a task immediately
"""

SCHEDULED_TOOLS = [
    {
        "name": "schedule_task",
        "category": "Scheduled",
        "description": "Create scheduled task or reminder. IMPORTANT: Must actually call this tool to create task - just saying 'OK I will remind you' does NOT create the task! Task types: (1) reminder - sends message at scheduled time (default, 90%% of cases), (2) task - AI executes operations. NOTIFICATION CHANNEL: By default, reminders/results are automatically sent back to the CURRENT IM channel where the user is chatting (e.g. if user sends message via WeChat, reminder will be pushed to WeChat). NO Webhook URL or extra config needed! Only set target_channel if user explicitly asks to push to a DIFFERENT channel.",
        "detail": """Create a scheduled task or reminder.

⚠️ **Important: You must call this tool to create a task! Simply saying "OK, I'll remind you" does NOT create a task!**

## ⏰ Time Entry Rules (most important!)

**trigger_config.run_at must be filled with a precise absolute time (YYYY-MM-DD HH:MM format)!**

- The system prompt already provides the "current time" and "tomorrow's date" — use these to compute the specific date the user means by "tomorrow", "the day after tomorrow", "next Monday", etc.
- User says "tomorrow at 7pm" → look at "tomorrow is YYYY-MM-DD" in the system prompt → fill `run_at: "YYYY-MM-DD 19:00"`
- User says "in 3 minutes" → current time + 3 minutes → fill the exact time
- **If you cannot determine the specific date/time the user wants, you must confirm with the user first — do not guess!**
- After creation, your reply must clearly tell the user the **specific date and time** set (e.g. "Feb 23 at 19:00") so the user can verify

## 📢 Push Channel Rules
- **Default behavior**: automatically push to the **IM channel the user is currently chatting in**
- **Do not ask the user for a Webhook URL!** The channel is already configured by the system
- Only set target_channel when the user explicitly asks to push to a **different** channel

## 📋 Task Type Decision
✅ **reminder** (default, 90%%): for reminders that only need to send a message ("remind me to drink water", "wake me up")
❌ **task** (only when AI action is required): "check the weather and tell me", "take a screenshot and send it to me"

## 🔧 Trigger Types (strictly distinguish!)
- **once**: one-time reminder (run_at with absolute time) — **"remind me in X minutes", "remind me at 8am tomorrow" are all once!**
- **interval**: continuously repeating loop ("remind me to drink water every 30 minutes", "remind me every day") — use only when the user explicitly says "every X minutes/every day"
- **cron**: cron expression ("weekdays at 9am")

⚠️ **Common mistake**: user saying "remind me in 5 minutes" ≠ "remind me every 5 minutes"!
- "Remind me to shower in 5 minutes" → trigger_type="once", run_at="current time + 5 minutes"
- "Remind me to drink water every 5 minutes" → trigger_type="interval", interval_minutes=5

## 📡 target_channel (usually no need to set!)
- Do not pass by default! The system automatically uses the current IM channel
- Only set when the user explicitly requests it (e.g. wework/telegram/dingtalk/feishu/slack)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task/reminder name"},
                "description": {"type": "string", "description": "Task description"},
                "task_type": {
                    "type": "string",
                    "enum": ["reminder", "task"],
                    "default": "reminder",
                    "description": "Use reminder by default! reminder=send reminder message, task=AI performs operations",
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["once", "interval", "cron"],
                    "description": "Trigger type",
                },
                "trigger_config": {
                    "type": "object",
                    "description": "Trigger configuration. once: {run_at: 'YYYY-MM-DD HH:MM'} must be a precise absolute time, computed from the current time in the system prompt; interval: {interval_minutes: 30} or {interval_seconds: 30} or {interval_hours: 2}; cron: {cron: '0 9 * * *'}",
                },
                "reminder_message": {
                    "type": "string",
                    "description": "Reminder message content (only required for reminder type)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt sent to the Agent at execution time (only required for task type)",
                },
                "target_channel": {
                    "type": "string",
                    "description": "Which configured IM channel to push to (e.g. wework/telegram/dingtalk/feishu/slack). If not provided, the current session channel is used automatically. ⚠️ No Webhook URL needed — channels are already configured in the system!",
                },
                "notify_on_start": {
                    "type": "boolean",
                    "default": True,
                    "description": "Send a notification when the task starts? Defaults to true",
                },
                "notify_on_complete": {
                    "type": "boolean",
                    "default": True,
                    "description": "Send a notification when the task completes? Defaults to true",
                },
                "silent": {
                    "type": "boolean",
                    "default": False,
                    "description": "Silent mode: execute the task but send no notifications (no start/end/result messages)",
                },
                "no_schedule_tools": {
                    "type": "boolean",
                    "default": False,
                    "description": "Recursion guard: prevents this task from creating/modifying scheduled tasks during execution",
                },
                "skill_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of bound skill IDs: inject only these skills' content into the execution prompt",
                },
            },
            "required": ["name", "description", "task_type", "trigger_type", "trigger_config"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "category": "Scheduled",
        "description": "List all scheduled tasks with their ID, name, type, status, and next execution time. When you need to: (1) Check existing tasks, (2) Find task ID for cancel/update, (3) Verify task creation.",
        "detail": """List all scheduled tasks.

**Returned information**:
- Task ID
- Name
- Type (reminder/task)
- Status (enabled/disabled)
- Next execution time

**Use cases**:
- View already created tasks
- Get a task ID for cancel/update
- Verify that a task was created successfully""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "description": "Whether to list only enabled tasks",
                    "default": False,
                }
            },
        },
    },
    {
        "name": "cancel_scheduled_task",
        "category": "Scheduled",
        "description": "PERMANENTLY DELETE scheduled task. Use when user says 'cancel/delete/remove task', 'turn off reminder', 'stop reminding me', etc. IMPORTANT: For REMINDER-type tasks, when user says 'turn off/stop/cancel the reminder' → use THIS tool (cancel), NOT update_scheduled_task, because reminder tasks exist solely to send messages — disabling notifications does NOT stop the reminder!",
        "detail": """[Permanently delete] a scheduled task.

⚠️ **Distinguishing operations**:
- User says "cancel/delete the task" → use this tool
- User says "turn it off/stop it/don't remind me" (for reminder type) → use this tool!
- User says "pause the task" (wants to keep it and resume later) → use update_scheduled_task with enabled=false

⚠️ **Special note for reminder-type tasks**:
The sole purpose of a reminder task is to send reminder messages.
Turning off notify_on_start/complete will NOT stop the reminder message from being sent!
When the user says "turn off/stop the XX reminder" = cancel the task, you must use cancel_scheduled_task.

**Note**: once deleted, it cannot be recovered!""",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task ID"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "update_scheduled_task",
        "category": "Scheduled",
        "description": "Modify scheduled task settings WITHOUT deleting. Can modify: notify_on_start, notify_on_complete, enabled, target_channel. Common uses: (1) 'Pause task' → enabled=false, (2) 'Resume task' → enabled=true, (3) 'Push to WeChat' → target_channel='wework'. WARNING: For REMINDER-type tasks, do NOT use notify=false to 'turn off reminder' — that only controls metadata notifications, NOT the reminder message itself! To stop a reminder, use cancel_scheduled_task instead.",
        "detail": """Modify scheduled task settings [without deleting the task].

**Modifiable fields**:
- notify_on_start: whether to notify on start (only controls the execution start/completion status notifications; does not affect the reminder message!)
- notify_on_complete: whether to notify on completion (same as above)
- enabled: whether enabled (false=paused, true=resumed)
- target_channel: change the push channel (e.g. wework/telegram/dingtalk/feishu/slack)

**Common usages**:
- "Pause the task" → enabled=false
- "Resume the task" → enabled=true
- "Push to WeCom instead" → target_channel="wework"
- ⚠️ No Webhook URL needed — channels are already configured in the system!

⚠️ **Do not use this tool to "turn off a reminder"!**
For reminder-type tasks, setting notify=false only disables the execution status notifications;
the reminder message (reminder_message) will still be sent normally!
To stop a reminder → use cancel_scheduled_task to delete it, or set enabled=false to pause it.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to modify"},
                "notify_on_start": {"type": "boolean", "description": "Notify on start? Omit = leave unchanged"},
                "notify_on_complete": {
                    "type": "boolean",
                    "description": "Notify on completion? Omit = leave unchanged",
                },
                "enabled": {"type": "boolean", "description": "Enable/pause the task? Omit = leave unchanged"},
                "target_channel": {
                    "type": "string",
                    "description": "Change the push channel (e.g. wework/telegram/dingtalk/feishu/slack). Omit = leave unchanged. ⚠️ No Webhook URL needed!",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "trigger_scheduled_task",
        "category": "Scheduled",
        "description": "Immediately trigger scheduled task without waiting for scheduled time. When you need to: (1) Test task execution, (2) Run task ahead of schedule.",
        "detail": """Trigger a scheduled task immediately (without waiting for the scheduled time).

**Use cases**:
- Test task execution
- Run a task ahead of schedule

**Note**:
Does not affect the original execution schedule""",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task ID"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "query_task_executions",
        "category": "Scheduled",
        "description": "Query execution history of scheduled tasks. View recent execution times, status, duration and error messages.",
        "detail": """Query the execution history of scheduled tasks.

**Use cases**:
- View recent execution results of a task
- Troubleshoot task failure causes
- Understand task execution frequency and duration""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID (optional; if omitted, queries execution records for all tasks)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of records to return; defaults to 10",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
]
