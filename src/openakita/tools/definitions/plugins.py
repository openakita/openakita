"""
Plugin management tool definitions.

Allow the LLM to query installed plugins and their details:
- list_plugins: list all installed plugins
- get_plugin_info: get detailed info for a single plugin
"""

PLUGIN_TOOLS = [
    {
        "name": "list_plugins",
        "category": "Plugin",
        "description": "List all installed plugins with their status, category, and provided tools/skills. When you need to: (1) Check what plugins are installed, (2) See plugin status (loaded/failed/disabled), (3) Find which plugin provides a specific tool.",
        "detail": """List all installed plugins.

**Returned info**:
- Plugin ID, name, version
- Plugin type and category
- Status (loaded / failed / disabled)
- List of provided tools and skills
- Permission status

**Use cases**:
- User asks "what plugins are there"
- Check plugin installation and loading status
- Find which plugin provides a specific tool or skill""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_plugin_info",
        "category": "Plugin",
        "description": "Get detailed information about a specific plugin including its README, registered tools, current configuration, and permission status.",
        "detail": """Get detailed information about a single plugin.

**Returned info**:
- Plugin metadata (ID, name, version, description)
- README content
- List of registered tools
- Current configuration
- Permission status (authorized / pending authorization)

**Use cases**:
- Understand the full functionality of a plugin
- View plugin configuration options
- Troubleshoot plugin issues""",
        "input_schema": {
            "type": "object",
            "properties": {
                "plugin_id": {
                    "type": "string",
                    "description": "Plugin ID (e.g. lark-cli-tool, translate-skill)",
                },
            },
            "required": ["plugin_id"],
        },
    },
]
