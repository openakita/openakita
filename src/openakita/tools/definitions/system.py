"""
System 工具定义

包含系统功能相关的工具：
- enable_thinking: 控制深度思考模式
- get_session_logs: 获取会话日志
- get_tool_info: 获取工具详细信息
"""

SYSTEM_TOOLS = [
    {
        "name": "enable_thinking",
        "description": "Control deep thinking mode. Default enabled. For very simple tasks (simple reminders, greetings, quick queries), can temporarily disable to speed up response. Auto-restores to enabled after completion.",
        "detail": """控制深度思考模式。

**默认状态**：启用

**可临时关闭的场景**：
- 简单提醒
- 简单问候
- 快速查询

**注意**：
- 完成后会自动恢复默认启用状态
- 复杂任务建议保持启用""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "是否启用 thinking 模式"
                },
                "reason": {
                    "type": "string",
                    "description": "简要说明原因"
                }
            },
            "required": ["enabled", "reason"]
        }
    },
    {
        "name": "get_session_logs",
        "description": "Get current session system logs. IMPORTANT: When commands fail, encounter errors, or need to understand previous operation results, call this tool. Logs contain: command details, error info, system status.",
        "detail": """获取当前会话的系统日志。

**重要**: 当命令执行失败、遇到错误、或需要了解之前的操作结果时，应该调用此工具查看日志。

**日志包含**:
- 命令执行详情
- 错误信息
- 系统状态

**使用场景**:
1. 命令返回错误码
2. 操作没有预期效果
3. 需要了解之前发生了什么""",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "返回的日志条数（默认 20，最大 200）",
                    "default": 20
                },
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                    "description": "过滤日志级别（可选，ERROR 可快速定位问题）"
                }
            }
        }
    },
    {
        "name": "get_tool_info",
        "description": "Get system tool detailed parameter definition (Level 2 disclosure). When you need to: (1) Understand unfamiliar tool usage, (2) Check tool parameters, (3) Learn tool examples. Call before using unfamiliar tools.",
        "detail": """获取系统工具的详细参数定义（Level 2 披露）。

**适用场景**：
- 了解不熟悉的工具用法
- 查看工具参数
- 学习工具示例

**建议**：
在调用不熟悉的工具前，先用此工具了解其完整用法、参数说明和示例。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "工具名称"}
            },
            "required": ["tool_name"]
        }
    },
]
