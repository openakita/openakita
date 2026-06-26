"""
Agent Package 工具定义

提供 Agent 导入导出相关工具：
- export_agent: 导出 Agent 为 .akita-agent 包
- import_agent: 从 .akita-agent 包导入 Agent
- list_exportable_agents: 列出可导出的 Agent
- inspect_agent_package: 预览包内容
"""

AGENT_PACKAGE_TOOLS = [
    {
        "name": "export_agent",
        "category": "Agent Package",
        "description": "Export a local Agent as a portable .akita-agent package file containing profile, prompt, and bundled skills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "profile_id": {
                    "type": "string",
                    "description": "The ID of the Agent profile to export",
                },
                "author_name": {
                    "type": "string",
                    "description": "Author name for the package",
                },
                "version": {
                    "type": "string",
                    "description": "Package version (SemVer, default: 1.0.0)",
                },
                "include_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skill names to bundle (default: agent's local skills)",
                },
                "output_dir": {
                    "type": "string",
                    "description": "导出文件保存目录（可选，默认保存到 data/agent_packages/）。用户可以指定路径如 ~/Desktop",
                },
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "import_agent",
        "category": "Agent Package",
        "description": "Import an Agent from a .akita-agent package file. Installs bundled skills and creates the Agent profile locally.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the .akita-agent package file",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force overwrite if ID conflicts (default: false)",
                },
            },
            "required": ["package_path"],
        },
    },
    {
        "name": "list_exportable_agents",
        "category": "Agent Package",
        "description": "List all Agent profiles that can be exported as .akita-agent packages.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "inspect_agent_package",
        "category": "Agent Package",
        "description": "Preview the contents of a .akita-agent package file without installing. Shows manifest, profile, skills, and validation status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the .akita-agent package file to inspect",
                },
            },
            "required": ["package_path"],
        },
    },
    {
        "name": "batch_export_agents",
        "category": "Agent Package",
        "description": "Export multiple Agents at once. Packs all specified agents into individual .akita-agent files in the target directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要导出的 Agent Profile ID 列表",
                },
                "output_dir": {
                    "type": "string",
                    "description": "导出文件保存目录（用户可以指定路径如 ~/Desktop）",
                },
            },
            "required": ["profile_ids"],
        },
    },
]
