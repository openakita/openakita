"""
Memory 工具定义

包含记忆系统相关的工具：
- add_memory: 记录重要信息
- search_memory: 搜索相关记忆
- get_memory_stats: 获取记忆统计
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
        "name": "search_conversation_traces",
        "category": "Memory",
        "description": "Search full conversation history including tool calls and results by keyword. Use when you need to recall specific details of what you did in past conversations - what tools were called, what parameters were used, what results were returned. Searches both conversation history (JSONL) and reasoning traces (JSON). Uses keyword matching so provide specific terms.",
        "detail": """按关键词搜索完整的对话历史记录，包括工具调用和结果。

**与 search_memory 的区别**：
- `search_memory`: 搜索已提取的语义记忆（偏好/事实/规则），结果是精炼后的知识条目
- `search_conversation_traces`: 搜索原始对话记录，保留完整上下文（工具名、参数、返回值）

**适用场景**：
- 回忆之前执行过的具体操作（"上次我让你搜索XX的结果是什么"）
- 查找之前调用过的工具和参数（"之前用的那个命令是什么"）
- 追溯某个操作的完整过程（工具调用链）
- 用户问"你之前做了什么"/"上次的结果呢"

**搜索范围**：
- 对话消息内容（用户消息 + 助手回复）
- 工具调用名称和参数
- 工具返回结果
- 支持限定会话 ID 和时间范围

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
