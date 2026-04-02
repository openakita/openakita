"""
插件目录 (Plugin Catalog)

生成系统提示词中的 Installed Plugins 段落，
让 LLM 知道装了哪些插件、各插件提供了什么能力。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import PluginManager

logger = logging.getLogger(__name__)


class PluginCatalog:
    """Generate the 'Installed Plugins' section for the system prompt."""

    def __init__(self, plugin_manager: "PluginManager") -> None:
        self._pm = plugin_manager

    def get_catalog(self) -> str:
        """Always rebuild — plugin list is small and may change at runtime."""
        return self._build()

    def invalidate_cache(self) -> None:
        """No-op kept for API consistency."""

    def _build(self) -> str:
        pm = self._pm
        if pm is None:
            return ""

        loaded = pm.list_loaded()
        if not loaded:
            return ""

        rows: list[str] = []
        for p in loaded:
            pid = p["id"]
            name = p.get("name", pid)
            category = p.get("category", "other") or "other"

            provides_parts: list[str] = []
            tools = self._plugin_tools(pid)
            if tools:
                provides_parts.append(f"tools: {', '.join(tools)}")
            skills = self._plugin_skills(pid)
            if skills:
                provides_parts.append(f"skills: {', '.join(skills)}")
            provides = "; ".join(provides_parts) if provides_parts else "—"

            rows.append(f"| {name} (`{pid}`) | {category} | loaded | {provides} |")

        failed = pm.list_failed()
        for pid, err in failed.items():
            short_err = err[:60].replace("|", "/")
            rows.append(f"| {pid} | — | failed | {short_err} |")

        if not rows:
            return ""

        lines = [
            "## Installed Plugins",
            "",
            "Use `list_plugins` to see all plugins; `get_plugin_info(plugin_id)` for details.",
            "",
            "| Plugin | Category | Status | Provides |",
            "|--------|----------|--------|----------|",
            *rows,
        ]
        return "\n".join(lines)

    def _plugin_tools(self, plugin_id: str) -> list[str]:
        loaded = self._pm.get_loaded(plugin_id)
        if loaded is None:
            return []
        return list(loaded.api._registered_tools)

    def _plugin_skills(self, plugin_id: str) -> list[str]:
        loaded = self._pm.get_loaded(plugin_id)
        if loaded is None:
            return []
        provides = loaded.manifest.provides
        if not isinstance(provides, dict):
            return []
        skill_file = provides.get("skill", "")
        if skill_file:
            return [skill_file.replace("SKILL.md", "").strip("/") or loaded.manifest.id]
        if loaded.manifest.plugin_type == "skill":
            return [loaded.manifest.entry.replace("SKILL.md", "").strip("/") or loaded.manifest.id]
        return []
