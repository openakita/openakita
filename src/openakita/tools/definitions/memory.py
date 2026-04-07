"""
Memory 工具定义

包含记忆系统相关的工具：
- add_memory: 记录重要信息
- search_memory: 搜索相关记忆
- get_memory_stats: 获取记忆统计
- list_recent_tasks: 列出最近完成的任务
- search_conversation_traces: 搜索完整对话历史（含工具调用和结果）
- trace_memory: 跨层导航（记忆↔情节↔对话）
"""

MEMORY_TOOLS = [
    {
        "name": "consolidate_memories",
        "category": "Memory",
        "description": "Manually trigger memory consolidation and LLM-driven cleanup. Use when user asks to organize/clean/tidy memories, says '整理记忆', '清理垃圾记忆', '记忆太乱了'. Includes LLM review that removes task artifacts and outdated entries.",
        "detail": """手动触发记忆整理与 LLM 清理。

**适用场景**：
- 用户说"整理一下记忆"、"清理垃圾记忆"、"记忆太乱了"
- 用户新安装后希望立即整理
- 发现记忆系统有垃圾数据时

**执行内容**：
- 处理未提取的对话
- 去重清理
- **LLM 智能审查**：逐条审查记忆质量，删除一次性任务、过期信息、垃圾数据
- 刷新 MEMORY.md / USER.md
- 同步向量库""",
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
            {
                "name": "search_memory",
                "relation": "搜索已学习的语义记忆（偏好/事实/规则）时改用 search_memory",
            },
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
    {
        "name": "trace_memory",
        "category": "Memory",
        "description": "Navigate across memory layers: given a memory_id, trace back to its source episode and conversation; given an episode_id, find linked memories and original conversation turns. Use when you see an interesting memory or episode and want more context.",
        "detail": """跨层导航工具 — 在记忆、情节、对话三层之间跳转。

**用法**：
- 传入 memory_id → 返回该记忆的来源情节摘要 + 相关对话片段
- 传入 episode_id → 返回该情节关联的记忆列表 + 对话原文

**典型场景**：
- search_memory 返回了一条经验，想看它产生的上下文 → trace_memory(memory_id=...)
- list_recent_tasks 看到某个任务，想看关联记忆和对话 → trace_memory(episode_id=...)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "要溯源的记忆 ID（与 episode_id 二选一）",
                },
                "episode_id": {
                    "type": "string",
                    "description": "要展开的情节 ID（与 memory_id 二选一）",
                },
            },
        },
    },
    {
        "name": "search_relational_memory",
        "category": "Memory",
        "description": "Search the relational memory graph (Mode 2) with multi-dimensional traversal. Finds causally linked, temporally connected, and entity-related memories across sessions. Use when user asks about reasons, history, timelines, or cross-session patterns.",
        "detail": """搜索关系型记忆图（模式 2），支持多维度遍历。

**适用场景**：
- 用户问"为什么"、"什么原因" → 因果链遍历
- 用户问"之前做过什么" → 时间线遍历
- 用户问"关于XX的所有记录" → 实体追踪
- 需要跨会话关联信息

**与 search_memory 的区别**：
- search_memory: 碎片化搜索（关键词匹配）
- search_relational_memory: 图遍历（沿因果/时间/实体维度多跳搜索）""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "max_results": {
                    "type": "integer",
                    "description": "最大返回条数（默认 10）",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_session_context",
        "category": "Memory",
        "description": "Get detailed context of the current session, including sub-agent execution records, tool usage history, and full message list. Use when conversation history lacks detail about delegation results or you need to review what happened in this session.",
        "detail": """获取当前会话的详细上下文信息。

**适用场景**：
- 对话历史中的信息不够详细，需要查看子 Agent 的完整执行记录
- 需要回顾当前会话的工具使用历史
- 需要查看完整的消息列表（含元数据）

**注意**：优先使用对话历史中已有的信息。只有在需要更多细节时才调用此工具。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["summary", "sub_agents", "tools", "messages"],
                    },
                    "description": (
                        "要获取的信息段落。"
                        "summary=会话概况, sub_agents=子Agent详细执行记录, "
                        "tools=工具调用历史, messages=完整消息列表"
                    ),
                    "default": ["summary", "sub_agents"],
                },
            },
        },
    },
]
