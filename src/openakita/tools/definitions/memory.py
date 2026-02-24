"""
Memory 工具定义

包含记忆系统相关的工具：
- add_memory: 记录重要信息
- search_memory: 搜索相关记忆
- get_memory_stats: 获取记忆统计
- list_recent_tasks: 列出最近完成的任务
- search_conversation_traces: 搜索完整对话历史（含工具调用和结果）
"""

MEMORY_TOOLS = [
    {
        "name": "consolidate_memories",
        "category": "Memory",
        "description": "Manually trigger memory consolidation. Use when user explicitly asks to organize/consolidate/tidy up memories, or says '整理记忆'. This processes unextracted conversation turns, deduplicates, refreshes MEMORY.md and USER.md.",
        "detail": """手动触发记忆整理。

**适用场景**：
- 用户说"整理一下记忆"、"帮我归纳一下"
- 用户新安装后希望立即整理
- 对话较多后主动整理

**执行内容**：
- 处理未提取的对话
- 去重清理
- 刷新 MEMORY.md / USER.md""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_memory",
        "category": "Memory",
        "description": "Record important information to long-term memory for learning user preferences, successful patterns, and error lessons. When you need to: (1) Remember user preferences, (2) Save successful patterns, (3) Record lessons from errors. NOTE: For structured user profile fields (name, work_field, os, etc.), use update_user_profile instead. Use add_memory for free-form, unstructured information that doesn't fit profile fields.",
        "detail": """记录重要信息到长期记忆。

**适用场景**：
- 学习用户偏好
- 保存成功模式
- 记录错误教训

**记忆类型**：
- fact: 事实信息
- preference: 用户偏好
- skill: 技能知识
- error: 错误教训
- rule: 规则约定

**重要性**：0-1 的数值，越高越重要""",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "skill", "error", "rule"],
                    "description": "记忆类型",
                },
                "importance": {"type": "number", "description": "重要性（0-1）", "default": 0.5},
            },
            "required": ["content", "type"],
        },
    },
    {
        "name": "search_memory",
        "category": "Memory",
        "description": "Search relevant memories by keyword and optional type filter. When you need to: (1) Recall past information, (2) Find user preferences, (3) Check learned patterns.",
        "detail": """搜索相关记忆。

**适用场景**：
- 回忆过去的信息
- 查找用户偏好
- 检查已学习的模式

**搜索方式**：
- 关键词匹配
- 可按类型过滤""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "skill", "error", "rule"],
                    "description": "记忆类型过滤（可选）",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_memory_stats",
        "category": "Memory",
        "description": "Get memory system statistics including total count and breakdown by type. When you need to: (1) Check memory usage, (2) Understand memory distribution.",
        "detail": """获取记忆系统统计信息。

**返回信息**：
- 总记忆数量
- 按类型分布
- 按重要性分布""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_recent_tasks",
        "category": "Memory",
        "description": "List recently completed tasks/episodes. Use FIRST when user asks 'what did you do', 'what happened', '你做了什么', '干了什么', '昨天/今天做了哪些事'. Much faster and more accurate than searching conversation traces by keyword.",
        "detail": """列出最近完成的任务（历史操作记录）。

**优先使用此工具**：当用户问"你做了什么"、"之前干了什么"时，直接调用此工具获取任务列表，
而不是用 search_conversation_traces 盲猜关键词。

每条记录包含：任务目标、结果、使用的工具、时间。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "查看最近几天的任务（默认 3）",
                    "default": 3,
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回几条（默认 15）",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "search_conversation_traces",
        "category": "Memory",
        "description": "Search full conversation history including tool calls and results by keyword. Use when search_memory results lack detail and you need exact tool parameters, return values, or original conversation text. Searches SQLite conversation records, reasoning traces, and conversation history files.",
        "detail": """按关键词搜索完整的对话历史记录，包括工具调用和结果。
这是第二级搜索——当 search_memory 的摘要不够详细时使用。

**与 search_memory 的区别**：
- `search_memory`（第一级）: 搜索提炼后的知识（偏好/事实/规则/经验/操作摘要）
- `search_conversation_traces`（第二级）: 搜索原始对话，保留完整细节（工具名、参数、返回值原文）

**适用场景**：
- search_memory 返回的摘要不够详细，需要操作细节
- 回忆之前执行过的具体操作（"上次搜索XX的结果是什么"）
- 查找之前调用过的工具和参数（"之前用的那个命令是什么"）
- 追溯某个操作的完整过程（工具调用链）

**搜索范围**：
- SQLite 对话记录（最可靠的数据源）
- 推理过程记录（工具调用迭代链）
- 对话历史文件（历史兼容）

**提示**：使用具体的关键词效果更好（如工具名、文件名、错误信息），避免过于宽泛的搜索词。""",
        "related_tools": [
            {"name": "search_memory", "relation": "搜索已学习的语义记忆（偏好/事实/规则）时改用 search_memory"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词（在对话内容、工具名、工具参数、工具结果中匹配）",
                },
                "session_id": {
                    "type": "string",
                    "description": "限定搜索某个会话 ID（可选，不填则搜索所有会话）",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回条数（默认 10）",
                    "default": 10,
                },
                "days_back": {
                    "type": "integer",
                    "description": "搜索最近几天的记录（默认 7）",
                    "default": 7,
                },
            },
            "required": ["keyword"],
        },
    },
]
