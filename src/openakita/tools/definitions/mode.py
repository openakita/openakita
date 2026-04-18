"""
Mode tool definitions

Includes mode switching tools:
- switch_mode: tool-driven interaction mode switching
"""

MODE_TOOLS = [
    {
        "name": "switch_mode",
        "category": "System",
        "description": (
            "Switch interaction mode to better match the current task. "
            "Be proactive — don't wait for the user to ask.\n\n"
            "Available modes:\n"
            "- plan: Read-only collaborative mode for designing approaches before execution. "
            "Switch when: multiple valid approaches, architectural decisions needed, "
            "requirements unclear, task touches many files.\n"
            "- ask: Read-only mode for exploring and answering questions without changes.\n"
            "- agent: Full implementation mode (default, resume via this tool).\n\n"
            "Switch proactively when:\n"
            "- Task has multiple approaches with trade-offs → plan\n"
            "- User shifts from requesting changes to asking questions → ask\n"
            "- You're stuck after multiple attempts → plan to rethink\n"
            "- What seemed simple reveals complexity → plan\n\n"
            "Do NOT switch when:\n"
            "- Current mode is working well\n"
            "- Mid-execution with good progress\n"
            "- Simple tasks completable quickly"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_mode": {
                    "type": "string",
                    "enum": ["plan", "ask", "agent"],
                    "description": "Target mode",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the switch (brief explanation)",
                },
            },
            "required": ["target_mode"],
        },
    },
]
