"""
插件查询处理器

处理插件管理相关的 LLM 工具调用：
- list_plugins: 列出所有已安装插件
- get_plugin_info: 获取单个插件的详细信息
"""

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class PluginsHandler:
    """插件查询处理器"""

    TOOLS = ["list_plugins", "get_plugin_info"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "list_plugins":
            return self._list_plugins()
        elif tool_name == "get_plugin_info":
            return self._get_plugin_info(params)
        else:
            return f"Unknown plugin tool: {tool_name}"

    def _get_pm(self):
        return getattr(self.agent, "_plugin_manager", None)

    def _list_plugins(self) -> str:
        pm = self._get_pm()
        if pm is None:
            return "Plugin system not initialized."

        loaded = pm.list_loaded()
        failed = pm.list_failed()

        disabled_ids: list[str] = []
        state = pm.state
        if state:
            loaded_ids = {p["id"] for p in loaded}
            failed_ids = set(failed)
            for entry in state.plugins.values():
                if not entry.enabled and entry.plugin_id not in loaded_ids and entry.plugin_id not in failed_ids:
                    disabled_ids.append(entry.plugin_id)

        if not loaded and not failed and not disabled_ids:
            return "No plugins installed."

        lines: list[str] = ["# Installed Plugins", ""]

        if loaded:
            by_category: dict[str, list[dict]] = {}
            for p in loaded:
                cat = p.get("category", "other") or "other"
                by_category.setdefault(cat, []).append(p)

            for cat in sorted(by_category):
                lines.append(f"## {cat}")
                for p in by_category[cat]:
                    tools = self._get_plugin_tools(p["id"])
                    skills = self._get_plugin_skills(p["id"])
                    provides_parts = []
                    if tools:
                        provides_parts.append(f"tools: {', '.join(tools)}")
                    if skills:
                        provides_parts.append(f"skills: {', '.join(skills)}")
                    provides_str = f" | Provides: {'; '.join(provides_parts)}" if provides_parts else ""

                    pending = p.get("pending_permissions", [])
                    status = "loaded"
                    if pending:
                        status += f" (pending permissions: {', '.join(pending)})"

                    lines.append(
                        f"- **{p.get('name', p['id'])}** (`{p['id']}`) "
                        f"v{p.get('version', '?')} — {status}{provides_str}"
                    )
                lines.append("")

        if failed:
            lines.append("## Failed")
            for pid, err in failed.items():
                lines.append(f"- `{pid}`: {err}")
            lines.append("")

        if disabled_ids:
            lines.append("## Disabled")
            for pid in disabled_ids:
                lines.append(f"- `{pid}`")
            lines.append("")

        return "\n".join(lines)

    def _get_plugin_info(self, params: dict[str, Any]) -> str:
        plugin_id = params.get("plugin_id", "")
        if not plugin_id:
            return "Error: plugin_id is required."

        pm = self._get_pm()
        if pm is None:
            return "Plugin system not initialized."

        loaded = pm.get_loaded(plugin_id)
        if loaded is None:
            failed = pm.list_failed()
            if plugin_id in failed:
                return (
                    f"# Plugin: {plugin_id}\n\n"
                    f"**Status**: failed\n"
                    f"**Error**: {failed[plugin_id]}"
                )
            return f"Plugin '{plugin_id}' not found."

        manifest = loaded.manifest
        lines: list[str] = [
            f"# Plugin: {manifest.name}",
            "",
            f"- **ID**: {manifest.id}",
            f"- **Version**: {manifest.version}",
            f"- **Type**: {manifest.plugin_type}",
            f"- **Category**: {manifest.category}",
            f"- **Author**: {manifest.author or 'N/A'}",
            f"- **Status**: loaded",
        ]

        if manifest.description:
            lines += ["", f"## Description", "", manifest.description]

        tools = self._get_plugin_tools(plugin_id)
        if tools:
            lines += ["", "## Registered Tools", ""]
            for t in tools:
                lines.append(f"- `{t}`")

        skills = self._get_plugin_skills(plugin_id)
        if skills:
            lines += ["", "## Provided Skills", ""]
            for s in skills:
                lines.append(f"- `{s}`")

        granted = list(loaded.api._granted_permissions)
        pending = list(loaded.api._pending_permissions) if loaded.api._pending_permissions else []
        if granted or pending:
            lines += ["", "## Permissions"]
            if granted:
                lines.append(f"- **Granted**: {', '.join(granted)}")
            if pending:
                lines.append(f"- **Pending**: {', '.join(pending)}")

        readme_path = loaded.plugin_dir / "README.md"
        if readme_path.exists():
            try:
                readme = readme_path.read_text(encoding="utf-8")[:4000]
                lines += ["", "## README", "", readme]
            except Exception:
                pass

        config = loaded.api.get_config()
        if config:
            lines += ["", "## Current Configuration", ""]
            lines.append(f"```json\n{json.dumps(config, indent=2, ensure_ascii=False)}\n```")

        return "\n".join(lines)

    def _get_plugin_tools(self, plugin_id: str) -> list[str]:
        pm = self._get_pm()
        if pm is None:
            return []
        loaded = pm.get_loaded(plugin_id)
        if loaded is None:
            return []
        return list(loaded.api._registered_tools)

    def _get_plugin_skills(self, plugin_id: str) -> list[str]:
        pm = self._get_pm()
        if pm is None:
            return []
        loaded = pm.get_loaded(plugin_id)
        if loaded is None:
            return []
        skill_file = loaded.manifest.provides.get("skill", "")
        if skill_file:
            return [skill_file.replace("SKILL.md", "").strip("/")]
        if loaded.manifest.plugin_type == "skill":
            return [loaded.manifest.entry.replace("SKILL.md", "").strip("/") or plugin_id]
        return []


def create_handler(agent: "Agent"):
    """Factory function: create the plugins handler callable."""
    handler = PluginsHandler(agent)
    return handler.handle
