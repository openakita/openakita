"""
Sleep tool definition

Modeled after CC SleepTool: interruptible wait that doesn't occupy a shell process.
Prefer this over run_shell("sleep N") — it doesn't hold a shell session.
"""

SLEEP_TOOLS: list[dict] = [
    {
        "name": "sleep",
        "category": "System",
        "should_defer": True,
        "description": (
            "Wait for a specified duration (seconds). The user can interrupt at "
            "any time. Prefer this over run_shell('sleep ...') — it doesn't hold "
            "a shell process. Can be called concurrently with other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Duration to sleep in seconds (max: 300).",
                },
            },
            "required": ["seconds"],
        },
    },
]
