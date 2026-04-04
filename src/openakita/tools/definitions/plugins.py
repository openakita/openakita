"""
插件管理工具定义

让 LLM 能直接查询已安装的插件及其详细信息：
- list_plugins: 列出所有已安装插件
- get_plugin_info: 获取单个插件的详细信息
"""

PLUGIN_TOOLS = [
    {
        "name": "list_plugins",
        "category": "Plugin",
        "description": "List all installed plugins with their status, category, and provided tools/skills. When you need to: (1) Check what plugins are installed, (2) See plugin status (loaded/failed/disabled), (3) Find which plugin provides a specific tool.",
        "detail": """列出所有已安装的插件。

**返回信息**：
- 插件 ID、名称、版本
- 插件类型和分类
- 状态（loaded / failed / disabled）
- 提供的工具列表和技能列表
- 权限状态

**适用场景**：
- 用户问"有哪些插件"
- 检查插件安装和加载状态
- 查找某个工具或技能由哪个插件提供""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_plugin_info",
        "category": "Plugin",
        "description": "Get detailed information about a specific plugin including its README, registered tools, current configuration, and permission status.",
        "detail": """获取单个插件的详细信息。

**返回信息**：
- 插件元数据（ID、名称、版本、描述）
- README 内容
- 注册的工具列表
- 当前配置
- 权限状态（已授权 / 待授权）

**适用场景**：
- 了解插件的完整功能
- 查看插件的配置选项
- 排查插件问题""",
        "input_schema": {
            "type": "object",
            "properties": {
                "plugin_id": {
                    "type": "string",
                    "description": "插件 ID（如 lark-cli-tool、translate-skill）",
                },
            },
            "required": ["plugin_id"],
        },
    },
]
