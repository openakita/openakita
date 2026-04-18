"""
Memory tool definitions

Includes tools related to the memory system:
- add_memory: record important information
- search_memory: search related memories
- get_memory_stats: get memory statistics
- list_recent_tasks: list recently completed tasks
- search_conversation_traces: search full conversation history (including tool calls and results)
- trace_memory: cross-layer navigation (memory <-> episode <-> conversation)
"""

MEMORY_TOOLS = [
    {
        "name": "consolidate_memories",
        "category": "Memory",
        "description": "Manually trigger memory consolidation and LLM-driven cleanup. Use when user asks to organize/clean/tidy memories, says 'organize memories', 'clean up junk memories', 'memories are a mess'. Includes LLM review that removes task artifacts and outdated entries.",
        "detail": """Manually trigger memory consolidation and LLM cleanup.

**When to use**:
- User says "organize my memories", "clean up junk memories", "memories are a mess"
- User wants to organize immediately after a fresh install
- When junk data is detected in the memory system

**What it does**:
- Processes un-extracted conversations
- Deduplication and cleanup
- **LLM smart review**: reviews memory quality entry by entry, removing one-off tasks, outdated info, and junk data
- Refreshes MEMORY.md / USER.md
- Syncs the vector store""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_memory",
        "category": "Memory",
        "description": "Record important information to long-term memory for learning user preferences, successful patterns, and error lessons. When you need to: (1) Remember user preferences, (2) Save successful patterns, (3) Record lessons from errors. NOTE: For structured user profile fields (name, work_field, os, etc.), use update_user_profile instead. Use add_memory for free-form, unstructured information that doesn't fit profile fields.",
        "detail": """Record important information into long-term memory.

**When to use**:
- Learning user preferences
- Saving successful patterns
- Recording lessons from errors

**Memory types**:
- fact: factual information
- preference: user preferences
- skill: skill/know-how
- error: lessons from errors
- rule: rules and conventions

**Importance**: a value between 0 and 1; higher is more important""",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The content to remember"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "skill", "error", "rule"],
                    "description": "Memory type",
                },
                "importance": {"type": "number", "description": "Importance (0-1)", "default": 0.5},
            },
            "required": ["content", "type"],
        },
    },
    {
        "name": "search_memory",
        "category": "Memory",
        "description": "Search relevant memories by keyword and optional type filter. When you need to: (1) Recall past information, (2) Find user preferences, (3) Check learned patterns.",
        "detail": """Search related memories.

**When to use**:
- Recalling past information
- Finding user preferences
- Checking learned patterns

**Search method**:
- Keyword matching
- Optional filter by type""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "preference", "skill", "error", "rule"],
                    "description": "Filter by memory type (optional)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_memory_stats",
        "category": "Memory",
        "description": "Get memory system statistics including total count and breakdown by type. When you need to: (1) Check memory usage, (2) Understand memory distribution.",
        "detail": """Get memory system statistics.

**Returns**:
- Total memory count
- Distribution by type
- Distribution by importance""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_recent_tasks",
        "category": "Memory",
        "description": "List recently completed tasks/episodes. Use FIRST when user asks 'what did you do', 'what happened', 'what did you do yesterday/today'. Much faster and more accurate than searching conversation traces by keyword.",
        "detail": """List recently completed tasks (history of actions).

**Prefer this tool**: when the user asks "what did you do" or "what did you do earlier", call this tool directly
to get the task list, rather than blindly guessing keywords with search_conversation_traces.

Each entry includes: task goal, result, tools used, and timestamp.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many recent days of tasks to view (default 3)",
                    "default": 3,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 15)",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "search_conversation_traces",
        "category": "Memory",
        "description": "Search full conversation history including tool calls and results by keyword. Use when search_memory results lack detail and you need exact tool parameters, return values, or original conversation text. Searches SQLite conversation records, reasoning traces, and conversation history files.",
        "detail": """Search the full conversation history by keyword, including tool calls and results.
This is the second-tier search — use it when summaries from search_memory aren't detailed enough.

**Difference from search_memory**:
- `search_memory` (tier 1): searches distilled knowledge (preferences/facts/rules/lessons/action summaries)
- `search_conversation_traces` (tier 2): searches raw conversations, preserving full detail (tool names, parameters, verbatim return values)

**When to use**:
- search_memory's summary is not detailed enough and you need action-level detail
- Recalling a specific past action ("what were the results of last time's XX search")
- Finding previously called tools and parameters ("what was the exact command I used before")
- Tracing the full process of an operation (tool call chain)

**Search scope**:
- SQLite conversation records (most reliable source)
- Reasoning trace records (tool call iteration chain)
- Conversation history files (legacy compatibility)

**Tip**: specific keywords work better (e.g. tool names, file names, error messages); avoid overly broad search terms.""",
        "related_tools": [
            {
                "name": "search_memory",
                "relation": "When searching learned semantic memories (preferences/facts/rules), use search_memory instead",
            },
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (matched against conversation content, tool names, tool parameters, and tool results)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Limit search to a specific session ID (optional; leave empty to search all sessions)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 10)",
                    "default": 10,
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many recent days of records to search (default 7)",
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
        "detail": """Cross-layer navigation tool — jump between the memory, episode, and conversation layers.

**Usage**:
- Pass memory_id -> returns a summary of the source episode + related conversation snippets
- Pass episode_id -> returns the list of memories linked to that episode + original conversation text

**Typical scenarios**:
- search_memory returned a lesson and you want to see the context it came from -> trace_memory(memory_id=...)
- list_recent_tasks shows a task and you want to see the related memories and conversation -> trace_memory(episode_id=...)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Memory ID to trace back (choose one of memory_id or episode_id)",
                },
                "episode_id": {
                    "type": "string",
                    "description": "Episode ID to expand (choose one of memory_id or episode_id)",
                },
            },
        },
    },
    {
        "name": "search_relational_memory",
        "category": "Memory",
        "description": "Search the relational memory graph (Mode 2) with multi-dimensional traversal. Finds causally linked, temporally connected, and entity-related memories across sessions. Use when user asks about reasons, history, timelines, or cross-session patterns.",
        "detail": """Search the relational memory graph (Mode 2) with multi-dimensional traversal.

**When to use**:
- User asks "why" / "for what reason" -> causal-chain traversal
- User asks "what did I do before" -> timeline traversal
- User asks "all records about XX" -> entity tracking
- When cross-session information linking is needed

**Difference from search_memory**:
- search_memory: fragmented search (keyword matching)
- search_relational_memory: graph traversal (multi-hop search along causal/temporal/entity dimensions)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 10)",
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
        "detail": """Get detailed context of the current session.

**When to use**:
- Information in conversation history isn't detailed enough; need to view a sub-agent's full execution record
- Need to review the current session's tool usage history
- Need to view the full message list (with metadata)

**Note**: prefer using information already present in conversation history. Only call this tool when more detail is needed.""",
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
                        "Which information sections to retrieve. "
                        "summary=session overview, sub_agents=detailed sub-agent execution records, "
                        "tools=tool call history, messages=full message list"
                    ),
                    "default": ["summary", "sub_agents"],
                },
            },
        },
    },
]
