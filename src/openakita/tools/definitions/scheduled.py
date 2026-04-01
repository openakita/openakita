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
        "category": "Scheduled",
        "description": "Create scheduled task or reminder. IMPORTANT: Must actually call this tool to create task - just saying 'OK I will remind you' does NOT create the task! Task types: (1) reminder - sends message at scheduled time (default, 90%% of cases), (2) task - AI executes operations. NOTIFICATION CHANNEL: By default, reminders/results are automatically sent back to the CURRENT IM channel where the user is chatting (e.g. if user sends message via WeChat, reminder will be pushed to WeChat). NO Webhook URL or extra config needed! Only set target_channel if user explicitly asks to push to a DIFFERENT channel.",
        "detail": """创建定时任务或提醒。

⚠️ **重要: 必须调用此工具才能创建任务！只是说"好的我会提醒你"不会创建任务！**

## ⏰ 时间填写规则（最重要！）

**trigger_config.run_at 必须填写精确的绝对时间（YYYY-MM-DD HH:MM 格式）！**

- 系统 prompt 中已给出「当前时间」和「明天日期」，根据这些信息推算用户说的"明天"、"后天"、"下周一"对应的具体日期
- 用户说"明天晚上7点" → 看 system prompt 中的「明天是 YYYY-MM-DD」→ 填 `run_at: "YYYY-MM-DD 19:00"`
- 用户说"3分钟后" → 用当前时间 + 3分钟 → 填精确时间
- **如果无法确定用户想要的具体日期/时间，必须先向用户确认，不要猜测！**
- 创建后回复中必须明确告知用户设定的**具体日期和时间**（如"2月23日 19:00"），让用户可以核实

## 📢 推送通道规则
- **默认行为**: 自动推送到用户 **当前正在聊天的 IM 通道**
- **不需要问用户要 Webhook URL！** 通道已由系统自动配置好
- 只有用户明确要求推送到 **另一个不同的通道** 时，才设置 target_channel

## 📋 任务类型判断
✅ **reminder**（默认，90%%）: 只需发送消息的提醒（"提醒我喝水"、"叫我起床"）
❌ **task**（仅当需要 AI 操作时）: "查询天气告诉我"、"截图发给我"

## 🔧 触发类型（严格区分！）
- **once**: 一次性提醒（run_at 填绝对时间）—— **"X分钟后提醒我"、"明天8点提醒我" 都是 once！**
- **interval**: 持续循环重复（"每30分钟提醒我喝水"、"每天提醒我"）—— 仅当用户明确说"每X分钟/每天"时才用
- **cron**: cron 表达式（"工作日早上9点"）

⚠️ **常见错误**：用户说"5分钟后提醒我" ≠ "每5分钟提醒我"！
- "5分钟后提醒我洗澡" → trigger_type="once", run_at="当前时间+5分钟"
- "每5分钟提醒我喝水" → trigger_type="interval", interval_minutes=5

## 📡 target_channel（通常不需要设置！）
- 默认不传！系统自动用当前 IM 通道
- 仅当用户明确要求时才设置（如 wework/telegram/dingtalk/feishu/slack）""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "任务/提醒名称"},
                "description": {"type": "string", "description": "任务描述"},
                "task_type": {
                    "type": "string",
                    "enum": ["reminder", "task"],
                    "default": "reminder",
                    "description": "默认使用 reminder！reminder=发消息提醒，task=AI 执行操作",
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["once", "interval", "cron"],
                    "description": "触发类型",
                },
                "trigger_config": {
                    "type": "object",
                    "description": "触发配置。once: {run_at: 'YYYY-MM-DD HH:MM'} 必须是精确的绝对时间，根据 system prompt 中的当前时间推算；interval: {interval_minutes: 30} 或 {interval_seconds: 30} 或 {interval_hours: 2}；cron: {cron: '0 9 * * *'}",
                },
                "reminder_message": {
                    "type": "string",
                    "description": "提醒消息内容（仅 reminder 类型需要）",
                },
                "prompt": {
                    "type": "string",
                    "description": "执行时发送给 Agent 的提示（仅 task 类型需要）",
                },
                "target_channel": {
                    "type": "string",
                    "description": "指定推送到哪个已配置的 IM 通道（如 wework/telegram/dingtalk/feishu/slack）。不传则自动使用当前会话通道。⚠️ 不需要 Webhook URL，通道已在系统中配置好！",
                },
                "notify_on_start": {
                    "type": "boolean",
                    "default": True,
                    "description": "执行开始时在 IM 发'开始执行…'状态消息？通常不需要设置。默认 true",
                },
                "notify_on_complete": {
                    "type": "boolean",
                    "default": True,
                    "description": "结果投递时添加'✅ 任务完成'状态标题？任务结果始终会发送到 IM 通道，此项仅控制标题包装。通常不需要设置。默认 true",
                },
            },
            "required": ["name", "description", "task_type", "trigger_type", "trigger_config"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "category": "Scheduled",
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
                "enabled_only": {
                    "type": "boolean",
                    "description": "是否只列出启用的任务",
                    "default": False,
                }
            },
        },
    },
    {
        "name": "cancel_scheduled_task",
        "category": "Scheduled",
        "description": "PERMANENTLY DELETE scheduled task. Use when user says 'cancel/delete/remove task', 'turn off reminder', 'stop reminding me', etc. IMPORTANT: For REMINDER-type tasks, when user says 'turn off/stop/cancel the reminder' → use THIS tool (cancel), NOT update_scheduled_task, because reminder tasks exist solely to send messages — disabling notifications does NOT stop the reminder!",
        "detail": """【永久删除】定时任务。

⚠️ **操作区分**：
- 用户说"取消/删除任务" → 用此工具
- 用户说"关了/关掉/停了/别提醒了"（针对 reminder 类型）→ 用此工具！
- 用户说"暂停任务"（想保留稍后恢复）→ 用 update_scheduled_task 设 enabled=false

⚠️ **reminder 类型任务特殊说明**：
reminder 任务的唯一作用就是发送提醒消息。
关闭 notify_on_start/complete 不会阻止提醒消息发送！
用户说"把XX提醒关了/关掉"= 取消任务，必须用 cancel_scheduled_task。

**注意**：删除后无法恢复！""",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "update_scheduled_task",
        "category": "Scheduled",
        "description": "Modify scheduled task settings WITHOUT deleting. Can modify: notify_on_start, notify_on_complete, enabled, target_channel. Common uses: (1) 'Pause task' → enabled=false, (2) 'Resume task' → enabled=true, (3) 'Push to WeChat' → target_channel='wework'. WARNING: For REMINDER-type tasks, do NOT use notify=false to 'turn off reminder' — that only controls metadata notifications, NOT the reminder message itself! To stop a reminder, use cancel_scheduled_task instead.",
        "detail": """修改定时任务设置【不删除任务】。

**可修改项**：
- notify_on_start: 开始时是否发"开始执行…"状态消息（不影响 reminder 消息！）
- notify_on_complete: 结果前是否添加"✅ 任务完成"标题包装（任务结果始终投递，不影响 reminder 消息！）
- enabled: 是否启用（false=暂停，true=恢复）
- target_channel: 修改推送通道（如 wework/telegram/dingtalk/feishu/slack）

**常见用法**：
- "暂停任务" → enabled=false
- "恢复任务" → enabled=true
- "改推送到企业微信" → target_channel="wework"
- ⚠️ 不需要 Webhook URL，通道已在系统中配置好！

⚠️ **不要用此工具来 "关闭提醒"！**
对 reminder 类型任务，设 notify=false 只关闭执行状态通知，
提醒消息（reminder_message）仍然会正常发送！
要停止提醒 → 用 cancel_scheduled_task 删除，或设 enabled=false 暂停。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要修改的任务 ID"},
                "notify_on_start": {
                    "type": "boolean",
                    "description": "开始时发'开始执行…'状态消息？不传=不修改",
                },
                "notify_on_complete": {
                    "type": "boolean",
                    "description": "结果前添加'✅ 任务完成'标题？任务结果始终投递。不传=不修改",
                },
                "enabled": {"type": "boolean", "description": "启用/暂停任务？不传=不修改"},
                "target_channel": {
                    "type": "string",
                    "description": "修改推送通道（如 wework/telegram/dingtalk/feishu/slack）。不传=不修改。⚠️ 不需要 Webhook URL！",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "trigger_scheduled_task",
        "category": "Scheduled",
        "description": "Immediately trigger scheduled task without waiting for scheduled time. When you need to: (1) Test task execution, (2) Run task ahead of schedule.",
        "detail": """立即触发定时任务（不等待计划时间）。

**适用场景**：
- 测试任务执行
- 提前运行任务

**注意**：
不会影响原有的执行计划""",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        },
    },
]
