"""
Worktree 工具定义

参考 CC EnterWorktree / ExitWorktree：暴露 git worktree 管理为工具，
让模型可以在独立的 worktree 中进行实验性修改而不影响主工作区。
"""

WORKTREE_TOOLS: list[dict] = [
    {
        "name": "enter_worktree",
        "category": "System",
        "should_defer": True,
        "description": (
            "Create a new git worktree and switch the working directory to it. "
            "Use for isolated experiments or parallel development. Changes in the "
            "worktree don't affect the main workspace. The worktree gets its own "
            "branch automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Optional name for the worktree. If not provided, a unique "
                        "name is generated automatically."
                    ),
                },
            },
        },
        "triggers": [
            "Need to experiment with code changes safely",
            "Need parallel development in isolated branch",
            "User asks to try something without affecting main code",
        ],
    },
    {
        "name": "exit_worktree",
        "category": "System",
        "should_defer": True,
        "description": (
            "Exit the current worktree and return to the main workspace. "
            "Choose to keep the worktree (for later merge) or remove it. "
            "Has safety checks for uncommitted changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": (
                        "'keep': preserve the worktree and its branch for later merge. "
                        "'remove': delete the worktree and its branch."
                    ),
                    "default": "keep",
                },
                "discard_changes": {
                    "type": "boolean",
                    "description": (
                        "If true, discard uncommitted changes before removing. "
                        "Required when action='remove' and there are uncommitted changes."
                    ),
                    "default": False,
                },
            },
        },
    },
]
