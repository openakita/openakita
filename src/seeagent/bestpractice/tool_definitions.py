"""BP 工具定义 — 注册到 ToolCatalog 的 7 个 BP 工具。

格式遵循 tool-definition-spec.md 规范，使用 input_schema（非 parameters）。
"""

from __future__ import annotations

BP_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "bp_start",
        "category": "Best Practice",
        "description": "启动一个最佳实践 (Best Practice) 任务流程",
        "input_schema": {
            "type": "object",
            "properties": {
                "bp_id": {
                    "type": "string",
                    "description": "最佳实践模板 ID",
                },
                "input_data": {
                    "type": "object",
                    "description": "初始输入数据",
                },
                "run_mode": {
                    "type": "string",
                    "enum": ["manual", "auto"],
                    "description": "执行模式: manual=手动确认每步, auto=自动执行",
                },
            },
            "required": ["bp_id"],
        },
    },
    {
        "name": "bp_continue",
        "category": "Best Practice",
        "description": "继续执行当前最佳实践的下一个子任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认使用当前活跃实例)",
                },
            },
        },
    },
    {
        "name": "bp_edit_output",
        "category": "Best Practice",
        "description": "修改已完成子任务的输出 (Chat-to-Edit 模式)",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选)",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "要修改的子任务 ID",
                },
                "changes": {
                    "type": "object",
                    "description": "要合并的修改内容 (深度合并，数组完整替换)",
                },
            },
            "required": ["subtask_id", "changes"],
        },
    },
    {
        "name": "bp_switch_task",
        "category": "Best Practice",
        "description": "切换到另一个 BP 实例 (暂停当前任务，恢复目标任务)",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_instance_id": {
                    "type": "string",
                    "description": "要切换到的 BP 实例 ID",
                },
            },
            "required": ["target_instance_id"],
        },
    },
    {
        "name": "bp_get_output",
        "category": "Best Practice",
        "description": "获取子任务的完整输出内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选)",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "子任务 ID",
                },
            },
            "required": ["subtask_id"],
        },
    },
    {
        "name": "bp_cancel",
        "category": "Best Practice",
        "description": "取消一个 BP 实例",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认使用当前活跃实例)",
                },
            },
        },
    },
    {
        "name": "bp_supplement_input",
        "category": "Best Practice",
        "description": "补充子任务缺失的输入数据 (用于输入不完整时)",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "子任务 ID",
                },
                "data": {
                    "type": "object",
                    "description": "补充的字段数据",
                },
            },
            "required": ["instance_id", "subtask_id", "data"],
        },
    },
]


def get_bp_tool_names() -> list[str]:
    """返回所有 BP 工具名称。"""
    return [t["name"] for t in BP_TOOL_DEFINITIONS]
