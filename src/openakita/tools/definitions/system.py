"""
System tool definitions

Contains system-related tools:
- ask_user: Ask the user a question and wait for a reply (pauses execution)
- enable_thinking: Control deep thinking mode
- get_session_logs: Retrieve session logs
- get_tool_info: Retrieve detailed tool information
- generate_image: Generate an image via AI
- set_task_timeout: Adjust the task timeout policy
- get_workspace_map: Retrieve workspace directory structure and key paths
"""

SYSTEM_TOOLS = [
    {
        "name": "ask_user",
        "category": "System",
        "description": (
            "Ask the user one or more questions and PAUSE execution until they reply. "
            "Use when: (1) critical information is missing, (2) task is ambiguous and needs "
            "clarification, (3) user confirmation is required before proceeding.\n\n"
            "Do NOT put questions in plain text — only this tool triggers a real pause. "
            "When questions have choices, ALWAYS provide options. Supports both single-select "
            "and multi-select via allow_multiple. For multiple related questions, use the "
            "questions array to ask them all at once.\n\n"
            "Do NOT ask questions when:\n"
            "- The task is clear enough to proceed\n"
            "- You can make a reasonable assumption and proceed\n"
            "- The question is trivial (e.g., confirming obvious next steps)\n"
            "- You're in the middle of execution and asking would break flow\n\n"
            "NEVER put questions in plain text responses — only this tool triggers a real "
            "pause and waits for user reply. Questions in text will be ignored."
        ),
        "detail": """Ask the user a question and pause execution until they reply. Supports single questions or multiple questions.

**When to use**:
- Critical information is missing (e.g. path, account, unclear target)
- The task is ambiguous and needs clarification from the user
- User confirmation is required before proceeding (e.g. destructive operations, multi-option choices)

**Single simple question**:
- Use question + options

**Multiple / complex questions**:
- Use the questions array; each question can have its own options and single/multi-select setting
- The question field serves as the overall description or title

**Options**:
- When a question has a finite set of choices (e.g. binary choice, multiple choice), you **must** provide the options parameter
- The user can click to select rather than having to type
- Defaults to single-select (allow_multiple=false); set allow_multiple=true for multi-select
- Example single-select: "Confirm or cancel?" → options: [{id:"confirm",label:"Confirm"},{id:"cancel",label:"Cancel"}]
- Example multi-select: "Which features to install?" → options: [...], allow_multiple: true
- The user can also pick "Other" and type freely — no need to include an "Other" entry in options

**Important**:
- Calling this tool immediately pauses the current task's execution loop
- Once the user replies, execution resumes with context preserved
- **Do not** ask questions in plain-text replies and then continue executing — question marks in text do not trigger a pause
- Cases where no question is needed: small talk / greetings, simple confirmations, task summaries""",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Single question text, or the overall description/title when asking multiple questions",
                },
                "options": {
                    "type": "array",
                    "description": "Option list for a single question (simple mode). When using the questions array, place options inside each question instead.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique option identifier (used as the user's reply content)",
                            },
                            "label": {
                                "type": "string",
                                "description": "Option display text",
                            },
                        },
                        "required": ["id", "label"],
                    },
                },
                "allow_multiple": {
                    "type": "boolean",
                    "description": "Whether the single-question options allow multi-select (default false = single-select). When using the questions array, set it per question.",
                    "default": False,
                },
                "questions": {
                    "type": "array",
                    "description": "List of multiple questions. Use to ask several related questions at once; each question can have its own options and single/multi-select setting.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique question identifier (used to match the user's reply)",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Question text",
                            },
                            "options": {
                                "type": "array",
                                "description": "Option list for this question",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "string",
                                            "description": "Unique option identifier",
                                        },
                                        "label": {
                                            "type": "string",
                                            "description": "Option display text",
                                        },
                                    },
                                    "required": ["id", "label"],
                                },
                            },
                            "allow_multiple": {
                                "type": "boolean",
                                "description": "Whether to allow multi-select (default false = single-select)",
                                "default": False,
                            },
                        },
                        "required": ["id", "prompt"],
                    },
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "enable_thinking",
        "category": "System",
        "description": "Control deep thinking mode. Default enabled. For very simple tasks (simple reminders, greetings, quick queries), can temporarily disable to speed up response. Auto-restores to enabled after completion.",
        "detail": """Control deep thinking mode.

**Default state**: enabled

**Cases where it can be temporarily disabled**:
- Simple reminders
- Simple greetings
- Quick queries

**Notes**:
- Automatically restored to the default enabled state after completion
- Recommended to keep enabled for complex tasks""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "Whether to enable thinking mode"},
                "reason": {"type": "string", "description": "Brief explanation of the reason"},
            },
            "required": ["enabled", "reason"],
        },
    },
    {
        "name": "get_session_logs",
        "category": "System",
        "description": "Get current session system logs. IMPORTANT: When commands fail, encounter errors, or need to understand previous operation results, call this tool. Logs contain: command details, error info, system status.",
        "detail": """Retrieve the current session's system logs.

**Important**: Call this tool to inspect logs when a command fails, an error is encountered, or you need to understand the result of a previous operation.

**Logs contain**:
- Command execution details
- Error messages
- System status

**When to use**:
1. A command returned an error code
2. An operation did not have the expected effect
3. You need to understand what happened earlier""",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of log entries to return (default 20, max 200)",
                    "default": 20,
                },
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                    "description": "Filter by log level (optional; ERROR quickly pinpoints issues)",
                },
            },
        },
    },
    {
        "name": "get_tool_info",
        "category": "System",
        "description": "Get system tool detailed parameter definition (Level 2 disclosure). When you need to: (1) Understand unfamiliar tool usage, (2) Check tool parameters, (3) Learn tool examples. Call before using unfamiliar tools. NOTE: This is for system TOOLS (run_shell, browser_navigate, etc.). For external SKILL instructions (pdf, docx, etc.), use get_skill_info instead.",
        "detail": """Retrieve a system tool's detailed parameter definition (Level 2 disclosure).

**When to use**:
- Understand how to use an unfamiliar tool
- Inspect a tool's parameters
- Learn from tool examples

**Recommendation**:
Before invoking an unfamiliar tool, use this tool first to learn its full usage, parameter descriptions, and examples.""",
        "input_schema": {
            "type": "object",
            "properties": {"tool_name": {"type": "string", "description": "Tool name"}},
            "required": ["tool_name"],
        },
    },
    {
        "name": "generate_image",
        "category": "System",
        "description": (
            "Generate an image from a text prompt using the configured image model API, "
            "saving to a local .png file.\n\n"
            "STRICT: Only use this tool when the user explicitly asks for an image. "
            "Do NOT generate images 'just to be helpful'.\n\n"
            "Use when user asks for image generation, posters, illustrations, or visual "
            "concepts that must be rendered as an actual image file. "
            "Do NOT use for data visualizations (charts, plots, tables) — generate those "
            "via code instead."
        ),
        "detail": """Text-to-image: generate an image from a prompt and save it as a local PNG file.

Notes:
- Defaults to Tongyi Qwen-Image (e.g. `qwen-image-max`).
- Requires `DASHSCOPE_API_KEY` to be set in the environment (shared with other Tongyi models).
- The API returns a temporary URL (typically valid for 24 hours); this tool automatically downloads and saves it to a local file.

Output:
- Returns a JSON string containing `saved_to` (local path) and `image_url` (temporary link).

Delivery:
- To send the image to an IM channel, call `deliver_artifacts` afterwards and use the receipt as proof of delivery.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Positive prompt (what to generate)"},
                "model": {
                    "type": "string",
                    "description": "Model name (default qwen-image-max)",
                    "default": "qwen-image-max",
                },
                "negative_prompt": {"type": "string", "description": "Negative prompt (optional)"},
                "size": {
                    "type": "string",
                    "description": "Output resolution, format width*height (e.g. 1664*928)",
                    "default": "1664*928",
                },
                "prompt_extend": {
                    "type": "boolean",
                    "description": "Whether to enable smart prompt rewriting (default true)",
                    "default": True,
                },
                "watermark": {
                    "type": "boolean",
                    "description": "Whether to add a watermark (default false)",
                    "default": False,
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed (0–2147483647, optional)",
                },
                "output_path": {
                    "type": "string",
                    "description": "Save path (optional). If omitted, saves under data/generated_images/ with an auto-generated name",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "set_task_timeout",
        "category": "System",
        "description": "Adjust current task timeout policy. Use when the task is expected to take long, or when the system is too aggressive switching models. Prefer increasing timeout for long-running tasks with steady progress; decrease to catch hangs sooner.",
        "detail": """Dynamically adjust the current task's timeout policy (mainly to avoid false-positive \"stuck detection\").\n\n- This project's timeout focuses on **detecting lack of progress**, not limiting long-running tasks.\n- You may raise the timeout before starting a long task, or when timeout warnings are being triggered too often.\n\nNote: this setting only affects the task currently running in this session; it does not change the global configuration.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "progress_timeout_seconds": {
                    "type": "integer",
                    "description": "No-progress timeout threshold (seconds). If no progress is made for this long, timeout handling is triggered. Recommended 600–3600.",
                },
                "hard_timeout_seconds": {
                    "type": "integer",
                    "description": "Hard timeout upper bound (seconds, 0 = disabled). Final backstop only.",
                    "default": 0,
                },
                "reason": {"type": "string", "description": "Brief explanation of the adjustment"},
            },
            "required": ["progress_timeout_seconds", "reason"],
        },
    },
    {
        "name": "get_workspace_map",
        "category": "System",
        "description": "Get the workspace directory structure and key path descriptions. Call this tool first whenever system files (logs/configs/sessions/media/screenshots/etc.) are involved, to understand the directory layout.",
        "detail": """Retrieve the full workspace directory structure and key path descriptions.

**Returns**:
- Core directory structure under the workspace root (data/, logs/, skills/, mcps/, etc.)
- The purpose of each directory
- Key file paths (config files, log files, session files, media files, etc.)

**When to use**:
- Looking for the log file location (e.g. data/logs/)
- Locating configuration files (e.g. .env, data/mcp/)
- Finding screenshots or media files (e.g. data/screenshots/)
- Finding where session history is stored
- When the user asks "where is the XX file?"

**Recommendation**:
- Call this tool before any filesystem operation to avoid blind searching
- The returned result is generated dynamically based on which directories actually exist""",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
