"""
Multi-agent tools — delegate, spawn and create.

Always injected (multi-agent mode is always on).

Tool priority (LLM should follow this order):
1. delegate_to_agent — use existing agent directly
2. spawn_agent — inherit + customize an existing agent (ephemeral)
3. delegate_parallel — parallel delegation (can mix delegate + spawn)
4. create_agent — last resort, create from scratch (defaults to ephemeral)
"""

AGENT_TOOLS = [
    {
        "name": "delegate_to_agent",
        "category": "Agent",
        "description": (
            "Delegate a task to an existing specialized agent. "
            "This is the PREFERRED way to use multi-agent collaboration. "
            "Use when: (1) An existing agent profile matches the task, "
            "(2) You need domain expertise (code, data, browser, docs), "
            "(3) The task can be fully handled by an existing agent without customization.\n\n"
            "IMPORTANT:\n"
            "- Launch multiple agents concurrently whenever possible for independent tasks\n"
            "- Do NOT launch more than 4 concurrent agents\n"
            "- Sub-agent results are not directly visible to the user — summarize them in "
            "your response\n"
            "- Prefer 'fast' model for quick, straightforward sub-tasks to minimize cost\n"
            "- Use 'capable' only when the task requires deep reasoning"
        ),
        "detail": (
            "Delegate a task to an existing specialized agent. This is the **preferred** way to use multi-agent collaboration.\n\n"
            "**Use cases**:\n"
            "- The current task requires another agent's expertise (e.g. code, data analysis, browser operations)\n"
            "- Break down a complex task across multiple collaborating agents\n"
            "- Route a sub-task to an agent with a specific skill set\n\n"
            "**Notes**:\n"
            "- The target agent must already be registered (built-in or dynamically created)\n"
            "- Delegation depth is capped at 5 levels to prevent infinite recursion\n"
            "- The same agent_id can be delegated to multiple times (the pool manages parallel instances automatically)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Target agent profile ID (e.g. 'code-assistant', 'data-analyst', 'browser-agent')",
                },
                "message": {
                    "type": "string",
                    "description": "Task description to send to the target agent",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for delegation (optional, used for logging and tracing)",
                },
                "model": {
                    "type": "string",
                    "enum": ["fast", "default", "capable"],
                    "description": (
                        "Model used by the sub-agent. fast=cheap and fast (for simple tasks), "
                        "default=same as the main agent, capable=stronger model (for complex reasoning)"
                    ),
                    "default": "default",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Background context provided to the sub-agent (optional). "
                        "The sub-agent may not see the full conversation history, so include the key "
                        "information needed to complete the task (known conclusions, relevant constraints, "
                        "expected output format, etc.)."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Whether to run in the background. Background sub-agents do not block the main agent; results can be checked later.",
                    "default": False,
                },
                "fork": {
                    "type": "boolean",
                    "description": (
                        "Fork mode: the sub-agent inherits the full current conversation context and prompt cache. "
                        "Omitting agent_id automatically enables fork mode, creating a clone of the current agent. "
                        "Use case: when the sub-agent needs to understand the full conversation context to handle the sub-task."
                    ),
                    "default": False,
                },
            },
            "required": ["agent_id", "message"],
        },
        "examples": [
            {
                "scenario": "Delegate a coding task to the code assistant",
                "params": {
                    "agent_id": "code-assistant",
                    "message": "Please help me refactor the date handling functions in utils.py",
                    "reason": "Need coding expertise",
                },
                "expected": "Reply from the code assistant",
            },
        ],
    },
    {
        "name": "spawn_agent",
        "category": "Agent",
        "description": (
            "Spawn a temporary agent by inheriting from an existing agent profile. "
            "Use when: (1) An existing agent is close but needs minor customization, "
            "(2) You need a specialized variant with extra skills or a modified prompt, "
            "(3) You need multiple independent clones of the same agent for parallel tasks. "
            "The spawned agent is ephemeral — automatically destroyed after the task completes."
        ),
        "detail": (
            "Create a temporary working agent by inheriting from an existing agent; it is destroyed automatically after the task finishes.\n\n"
            "**Use cases**:\n"
            "- An existing agent is close to what's needed but requires minor tweaks (extra skills or prompt additions)\n"
            "- You need multiple independent clones of the same agent to run different tasks in parallel\n"
            "- One-off tasks that don't need a persistent agent\n\n"
            "**How it works**:\n"
            "1. Copy skills and prompt from the base profile specified by inherit_from\n"
            "2. Merge in extra_skills and custom_prompt_overlay\n"
            "3. Create a temporary profile (in memory only, not written to disk)\n"
            "4. Immediately delegate the message to the temporary agent\n"
            "5. Automatically clean up the temporary profile once the task completes"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inherit_from": {
                    "type": "string",
                    "description": "Base agent profile ID (e.g. 'browser-agent', 'code-assistant')",
                },
                "message": {
                    "type": "string",
                    "description": "Description of the task to execute",
                },
                "extra_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional skills added on top of the base agent's skills (optional)",
                },
                "custom_prompt_overlay": {
                    "type": "string",
                    "description": "Custom prompt appended on top of the base agent's prompt (optional)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why customization is needed (optional, used for logging)",
                },
            },
            "required": ["inherit_from", "message"],
        },
        "examples": [
            {
                "scenario": "Inherit from the browser agent to customize a web research specialist",
                "params": {
                    "inherit_from": "browser-agent",
                    "message": "Research the new features in React 19 and produce a report",
                    "custom_prompt_overlay": "Focus on performance optimizations and concurrency features",
                    "reason": "Needs browser capabilities plus research expertise",
                },
                "expected": "The temporary agent completes the research and returns results",
            },
            {
                "scenario": "Create two independent clones for parallel research",
                "params": {
                    "inherit_from": "browser-agent",
                    "message": "Research the latest developments in Vue 4",
                    "reason": "Parallel research on a second framework",
                },
                "expected": "Each spawn produces a unique temporary ID; multiple can run in parallel",
            },
        ],
    },
    {
        "name": "delegate_parallel",
        "category": "Agent",
        "description": (
            "Delegate tasks to multiple agents in parallel. "
            "IMPORTANT: For multiple similar tasks (e.g. researching 3 topics), "
            "use the SAME suitable agent_id for all tasks — the system auto-creates "
            "independent clones. Do NOT assign unrelated agents to tasks they are not "
            "specialized for."
        ),
        "detail": (
            "Delegate tasks to multiple agents for parallel execution.\n\n"
            "**Core rules**:\n"
            "- Similar tasks (e.g. multiple research tasks) → use the **same best-fit agent_id**; "
            "the system will automatically create an independent copy for each task\n"
            "- Different kinds of tasks (e.g. research + coding + data analysis) → assign to different specialized agents\n"
            "- **Never** assign tasks to mismatched agents just to fan out in parallel\n\n"
            "**Notes**:\n"
            "- All tasks execute in parallel and results are returned together\n"
            "- Tasks must not depend on each other (for dependencies, delegate sequentially using delegate_to_agent)\n"
            "- When sending multiple tasks to the same agent_id, the system automatically creates independent instances"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": (
                        "Background context shared across all sub-tasks (optional). "
                        "Sub-agents may not see the full conversation history, so include the key "
                        "information needed to complete the task (known conclusions, relevant constraints, "
                        "expected output format, etc.). "
                        "This field is automatically prepended to each sub-task's context."
                    ),
                },
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "Target agent profile ID",
                            },
                            "message": {
                                "type": "string",
                                "description": "Task description to send to this agent",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for delegation (optional)",
                            },
                            "context": {
                                "type": "string",
                                "description": "Extra background context for this sub-agent (optional, merged with the top-level context)",
                            },
                        },
                        "required": ["agent_id", "message"],
                    },
                    "description": "List of tasks to execute in parallel (2-5 items)",
                },
            },
            "required": ["tasks"],
        },
        "examples": [
            {
                "scenario": "Correct: researching multiple projects at once (similar tasks -> same agent, multiple copies)",
                "params": {
                    "tasks": [
                        {
                            "agent_id": "browser-agent",
                            "message": "Do an in-depth investigation of the OpenAkita project's architecture, features, and community activity",
                            "reason": "Research project A",
                        },
                        {
                            "agent_id": "browser-agent",
                            "message": "Do an in-depth investigation of the OpenClaw project's architecture, features, and community activity",
                            "reason": "Research project B",
                        },
                    ],
                },
                "expected": "The system creates 2 independent copies of browser-agent, runs them in parallel, and returns merged results",
            },
            {
                "scenario": "Correct: parallel execution of different kinds of tasks (different tasks -> different specialized agents)",
                "params": {
                    "tasks": [
                        {
                            "agent_id": "browser-agent",
                            "message": "Research the new features of React 19 online",
                            "reason": "Online research",
                        },
                        {
                            "agent_id": "code-assistant",
                            "message": "Analyze React version upgrade compatibility for the current project",
                            "reason": "Code analysis",
                        },
                    ],
                },
                "expected": "Research and code analysis run in parallel",
            },
            {
                "scenario": "Wrong: assigning research tasks to a mismatched agent",
                "params": {
                    "tasks": [
                        {"agent_id": "browser-agent", "message": "Research project A"},
                        {"agent_id": "code-assistant", "message": "Research project B"},
                    ],
                },
                "expected": "Not allowed! code-assistant is a coding assistant and is not good at web research. Both research tasks should use browser-agent.",
            },
        ],
    },
    {
        "name": "create_agent",
        "category": "Agent",
        "description": (
            "Create a completely new agent from scratch. "
            "⚠️ This is the LAST RESORT — only use when NO existing agent can be "
            "delegated to or spawned from. "
            "Prefer delegate_to_agent (direct use) or spawn_agent (inherit + customize) first. "
            "Created agents are ephemeral by default (auto-cleanup after task). "
            "Set persistent=true only if the user explicitly wants to keep the agent."
        ),
        "detail": (
            "Create a brand-new agent. This is the **last resort**.\n\n"
            "**Confirm before using**:\n"
            "1. All existing agents have been reviewed and none can be used directly (delegate_to_agent)\n"
            "2. All existing agents have been reviewed and none can be customized by inheritance (spawn_agent)\n"
            "3. A truly new role is required\n\n"
            "**Default behavior**:\n"
            "- Created agents are ephemeral by default and are destroyed automatically after the task\n"
            "- They do not pollute the system agent list\n"
            "- Set persistent=true to save permanently (only when the user explicitly requests it)\n\n"
            "**Limits**:\n"
            "- At most 5 dynamic agents can be created per session\n"
            "- Dynamic agents cannot create further new agents\n"
            "- If the system detects a similar existing agent, it suggests using spawn_agent instead"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent name",
                },
                "description": {
                    "type": "string",
                    "description": "Description of the agent's capabilities",
                },
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of skill IDs to assign (optional)",
                },
                "custom_prompt": {
                    "type": "string",
                    "description": "Custom system prompt (optional)",
                },
                "persistent": {
                    "type": "boolean",
                    "description": "Whether to save this agent permanently (default false = ephemeral, cleaned up after the task)",
                },
                "force": {
                    "type": "boolean",
                    "description": "Skip the similarity check and force creation (default false; use when the system suggests an existing agent but you truly need a new one)",
                },
            },
            "required": ["name", "description"],
        },
        "examples": [
            {
                "scenario": "Create an ephemeral SQL expert (default behavior)",
                "params": {
                    "name": "SQL Expert",
                    "description": "Specializes in SQL query optimization and database design",
                    "custom_prompt": "You are a SQL optimization expert.",
                },
                "expected": "Agent created: ephemeral_sql_expert_xxx (ephemeral)",
            },
        ],
    },
    {
        "name": "task_stop",
        "category": "Agent",
        "should_defer": True,
        "description": (
            "Stop a running background agent or shell process. Use when a background "
            "task is stuck, no longer needed, or should be cancelled. Provide the task "
            "or agent ID to stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "string",
                    "description": "The agent ID or background task ID to stop.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for stopping (optional, for logging).",
                },
            },
            "required": ["target_id"],
        },
    },
    {
        "name": "send_agent_message",
        "category": "Agent",
        "should_defer": True,
        "description": (
            "Send a message to another active agent. Enables inter-agent communication "
            "in multi-agent scenarios. Target can be a specific agent name or '*' for "
            "broadcast to all active agents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Target agent name, or '*' for broadcast to all active agents."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The message content to send.",
                },
                "message_type": {
                    "type": "string",
                    "enum": ["text", "shutdown_request", "status_update", "data"],
                    "description": "Type of message (default: 'text').",
                    "default": "text",
                },
            },
            "required": ["target", "message"],
        },
    },
]
