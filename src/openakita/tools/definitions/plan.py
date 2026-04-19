"""
Todo & Plan tool definitions

Todo tools (task execution tracking in Agent mode):
- create_todo: Create task execution plan
- update_todo_step: Update step status
- get_todo_status: Get plan execution status
- complete_todo: Complete plan

Plan mode tools:
- create_plan_file: Create structured Plan file
- exit_plan_mode: Exit Plan mode
"""

PLAN_TOOLS = [
    {
        "name": "create_todo",
        "category": "Todo",
        "description": (
            "Create a structured task plan for multi-step tasks. "
            "If user request needs 2+ tool calls (like 'open + search + screenshot'), "
            "call create_todo BEFORE any other tool.\n\n"
            "When to use:\n"
            "- 3+ distinct steps needed\n"
            "- User provides multiple tasks\n"
            "- Complex task requiring careful planning\n\n"
            "When NOT to use:\n"
            "- Single straightforward tasks completable in 1-2 steps\n"
            "- Trivial tasks with no organizational benefit\n"
            "- Purely conversational/informational requests\n\n"
            "IMPORTANT: Mark steps complete IMMEDIATELY after finishing each one. "
            "Only ONE step should be in_progress at a time."
        ),
        "detail": """Create a task execution plan.

**When to use**:
- Task requires more than 2 steps
- User request contains words like "then", "next", "after", etc.
- Involves multiple tool collaboration

**Usage flow**:
1. create_todo → 2. Execute steps → 3. update_todo_step → 4. ... → 5. complete_todo

**Step field descriptions**:
- `id` + `description`: Required
- `tool`: Optional, expected tool name
- `skills`: Optional, list of related skill names (for tracking)
- `depends_on`: Optional, prerequisite steps

**Example**:
User: "Open Baidu, search weather, take a screenshot and send it to me"
→ create_todo(steps=[Open Baidu, Enter keywords, Click search, Take screenshot, Send])""",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_summary": {"type": "string", "description": "One-line summary of the task"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Step ID, e.g., step_1, step_2"},
                            "description": {"type": "string", "description": "Step description"},
                            "tool": {"type": "string", "description": "Expected tool to use (optional)"},
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of skill names associated with this step (optional, for tracking)",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Dependent step IDs (optional)",
                            },
                            "blocks": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of step IDs that can only start after this step completes (optional)",
                            },
                            "owner": {
                                "type": "string",
                                "description": "Agent ID responsible for executing this step (optional, for multi-agent collaboration)",
                            },
                        },
                        "required": ["id", "description"],
                    },
                    "description": "List of steps",
                },
            },
            "required": ["task_summary", "steps"],
        },
    },
    {
        "name": "update_todo_step",
        "category": "Todo",
        "description": "Update the status of a todo step. MUST call after completing each step to track progress.",
        "detail": """Update the status of a step in the plan.

**Must call this tool after completing each step!**

**Status values**:
- pending: Waiting to execute
- in_progress: Executing
- completed: Completed
- failed: Execution failed
- skipped: Skipped

**Example**:
After completing browser_navigate:
→ update_todo_step(step_id="step_1", status="completed", result="Opened Baidu homepage")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string", "description": "Step ID"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "skipped"],
                    "description": "Step status",
                },
                "result": {"type": "string", "description": "Execution result or error message"},
            },
            "required": ["step_id", "status"],
        },
    },
    {
        "name": "get_todo_status",
        "category": "Todo",
        "description": "Get the current todo execution status. Shows all steps and their completion status.",
        "detail": """Get the current plan's execution status.

Returns information including:
- Plan overview
- Each step's status
- Completed/pending count
- Execution log""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_todo",
        "category": "Todo",
        "description": "Mark the todo as completed and generate a summary report. Call when ALL steps are done.",
        "detail": """Mark the plan as completed and generate a final report.

**Call after all steps are completed**

**Returns**:
- Execution summary
- Success/failure statistics
- Total time elapsed""",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "Completion summary"}},
            "required": ["summary"],
        },
    },
    {
        "name": "create_plan_file",
        "category": "Plan",
        "description": (
            "Create a structured plan file (.plan.md) with YAML frontmatter and detailed "
            "Markdown body. Used in Plan mode to produce a reviewable plan document.\n\n"
            "This tool creates a NEW plan file each time it is called. To update an existing "
            "plan, use edit_file directly on the plan file — do NOT call create_plan_file again.\n\n"
            "The plan name should only be specified on the first call. On subsequent updates "
            "via edit_file, the filename stays stable."
        ),
        "detail": """Create a structured Plan file (YAML frontmatter + Markdown body).

**For Plan mode**: Generate a plan file that users can review.

**File format**:
```yaml
---
name: Plan Name
overview: Brief description
todos:
  - id: step_1
    content: "Step description"
    status: pending
isProject: true
---
```

Followed by detailed Markdown content (solution analysis, file lists, risk assessment, etc).""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Plan name"},
                "overview": {"type": "string", "description": "Plan summary (1-2 sentences)"},
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Step ID"},
                            "content": {"type": "string", "description": "Step description"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Initial status (usually pending)",
                            },
                        },
                        "required": ["id", "content"],
                    },
                    "description": "List of steps",
                },
                "body": {
                    "type": "string",
                    "description": "Detailed plan content in Markdown (solution analysis, file lists, risk assessment, etc.)",
                },
            },
            "required": ["name", "todos"],
        },
    },
    {
        "name": "exit_plan_mode",
        "category": "Plan",
        "description": "Signal that planning is complete. Triggers the approval UI for the user to review and approve the plan before execution.",
        "detail": """Exit Plan mode and notify the system that planning is complete.

**Call this after completing create_plan_file**.

After calling, the system will:
1. Notify the frontend to display the Plan approval UI
2. Wait for user approval
3. Automatically switch to Agent mode for execution after approval""",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief description of the completed plan",
                },
            },
        },
    },
]
