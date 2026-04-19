"""
Agent Package handler — export_agent, import_agent, list_exportable_agents, inspect_agent_package.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


def _get_stores():
    """Resolve profile_store, skills_dir, project_root from config."""
    from ...agents.profile import get_profile_store
    from ...config import settings

    root = Path(settings.project_root)
    profile_store = get_profile_store()
    skills_dir = Path(settings.skills_path)
    return profile_store, skills_dir, root


class AgentPackageHandler:
    """Handles agent package import/export tool calls."""

    TOOLS = [
        "export_agent",
        "import_agent",
        "list_exportable_agents",
        "inspect_agent_package",
        "batch_export_agents",
    ]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            if tool_name == "export_agent":
                return await self._export(params)
            elif tool_name == "import_agent":
                return await self._import(params)
            elif tool_name == "list_exportable_agents":
                return await self._list_exportable(params)
            elif tool_name == "inspect_agent_package":
                return await self._inspect(params)
            elif tool_name == "batch_export_agents":
                return await self._batch_export(params)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"AgentPackageHandler error ({tool_name}): {e}", exc_info=True)
            return f"❌ Operation failed: {e}"

    async def _export(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentPackager

        profile_id = params.get("profile_id", "")
        if not profile_id:
            return "❌ profile_id is required"

        profile_store, skills_dir, root = _get_stores()

        user_output_dir = params.get("output_dir", "")
        if user_output_dir:
            output_dir = Path(user_output_dir)
            if not output_dir.is_absolute():
                output_dir = root / user_output_dir
        else:
            output_dir = root / "data" / "agent_packages"

        packager = AgentPackager(
            profile_store=profile_store,
            skills_dir=skills_dir,
            output_dir=output_dir,
        )

        output_path = packager.package(
            profile_id=profile_id,
            author_name=params.get("author_name", ""),
            author_url=params.get("author_url", ""),
            version=params.get("version", "1.0.0"),
            include_skills=params.get("include_skills"),
        )

        size_kb = output_path.stat().st_size / 1024
        return (
            f"✅ Agent exported!\n\n"
            f"📦 File: {output_path}\n"
            f"📏 Size: {size_kb:.1f} KB\n\n"
            f"💡 Export path: `{output_dir}`\n"
            f"You can share this `.akita-agent` file with other users for import."
        )

    async def _import(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ package_path is required"

        profile_store, skills_dir, root = _get_stores()

        path = Path(package_path)
        if not path.is_absolute():
            path = root / path

        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )

        force = params.get("force", False)
        profile = installer.install(path, force=force)

        self._try_reload_skills()

        return (
            f"✅ Agent imported successfully!\n\n"
            f"🤖 Name: {profile.name}\n"
            f"🆔 ID: {profile.id}\n"
            f"📝 Description: {profile.description}\n"
            f"🔧 Skills: {', '.join(profile.skills) if profile.skills else 'None'}\n\n"
            f"The agent and its skills have been installed and auto-loaded."
        )

    async def _list_exportable(self, params: dict[str, Any]) -> str:
        profile_store, _, _ = _get_stores()
        profiles = profile_store.list_all(include_hidden=False)

        if not profiles:
            return "No agents available for export."

        lines = ["📋 Exportable agents:\n"]
        for p in profiles:
            skills_count = len(p.skills) if p.skills else 0
            type_label = "System" if p.is_system else "Custom"
            cat = f" [{p.category}]" if p.category else ""
            lines.append(f"- **{p.name}** (`{p.id}`) — {type_label}{cat}, {skills_count} skill(s)")

        lines.append(f"\n{len(profiles)} agent(s) available for export.")
        lines.append("Use the `export_agent` tool to export a specific agent.")
        return "\n".join(lines)

    async def _inspect(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ package_path is required"

        profile_store, skills_dir, root = _get_stores()

        path = Path(package_path)
        if not path.is_absolute():
            path = root / path

        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )

        info = installer.inspect(path)

        manifest = info["manifest"]
        profile = info["profile"]
        errors = info["validation_errors"]
        conflict = info["id_conflict"]

        lines = [
            "📦 Agent package preview\n",
            f"**Name**: {manifest.get('name', '?')}",
            f"**ID**: {manifest.get('id', '?')}",
            f"**Version**: {manifest.get('version', '?')}",
            f"**Author**: {manifest.get('author', {}).get('name', '?')}",
            f"**Category**: {manifest.get('category', 'None')}",
            f"**Size**: {info['package_size'] / 1024:.1f} KB",
        ]

        if info["bundled_skills"]:
            lines.append(f"**Bundled skills**: {', '.join(info['bundled_skills'])}")
        if manifest.get("required_builtin_skills"):
            lines.append(f"**Required built-in skills**: {', '.join(manifest['required_builtin_skills'])}")

        ext_skills = manifest.get("required_external_skills", [])
        if ext_skills:
            names = [s.get("id", "?") if isinstance(s, dict) else str(s) for s in ext_skills]
            lines.append(f"**Required external skills**: {', '.join(names)}")

        if errors:
            lines.append(f"\n⚠️ Validation issues: {'; '.join(errors)}")
        if conflict:
            lines.append(f"\n⚠️ ID conflict: `{manifest.get('id')}` already exists locally — it will be auto-renamed on import")

        if profile.get("custom_prompt"):
            prompt_preview = profile["custom_prompt"][:200]
            if len(profile["custom_prompt"]) > 200:
                prompt_preview += "..."
            lines.append(f"\n**Prompt preview**: {prompt_preview}")

        lines.append("\nUse the `import_agent` tool to import this agent.")
        return "\n".join(lines)

    async def _batch_export(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentPackager

        profile_ids = params.get("profile_ids", [])
        if not profile_ids:
            return "❌ profile_ids list is required"

        profile_store, skills_dir, root = _get_stores()

        user_output_dir = params.get("output_dir", "")
        if user_output_dir:
            output_dir = Path(user_output_dir)
            if not output_dir.is_absolute():
                output_dir = root / user_output_dir
        else:
            output_dir = root / "data" / "agent_packages"

        packager = AgentPackager(
            profile_store=profile_store,
            skills_dir=skills_dir,
            output_dir=output_dir,
        )

        exported: list[str] = []
        errors: list[str] = []
        for pid in profile_ids:
            try:
                out = packager.package(profile_id=pid)
                exported.append(f"✅ {pid} → {out.name} ({out.stat().st_size / 1024:.1f} KB)")
            except Exception as e:
                errors.append(f"❌ {pid}: {e}")

        lines = [f"📦 Batch export complete — {len(exported)} succeeded, {len(errors)} failed\n"]
        lines.append(f"💡 Export path: `{output_dir}`\n")
        if exported:
            lines.append("**Exported:**")
            lines.extend(exported)
        if errors:
            lines.append("\n**Failed:**")
            lines.extend(errors)
        return "\n".join(lines)

    def _try_reload_skills(self) -> None:
        """After an agent package import, refresh via the unified entry point so bundled skills take effect immediately."""
        try:
            from ...skills.events import SkillEvent

            if hasattr(self.agent, "propagate_skill_change"):
                self.agent.propagate_skill_change(SkillEvent.INSTALL)
                logger.info("Skills reloaded after agent package import")
        except Exception as e:
            logger.warning(f"Skill reload after import failed (non-blocking): {e}")


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentPackageHandler(agent)
    return handler.handle
