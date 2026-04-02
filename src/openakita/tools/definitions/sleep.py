"""
Sleep 工具定义

参考 CC SleepTool：可中断的等待，不占 shell 进程。
优于 run_shell("sleep N") — 不持有 shell 会话。
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
