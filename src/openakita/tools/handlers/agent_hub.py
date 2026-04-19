"""
Agent Hub handler — search_hub_agents, install_hub_agent, publish_agent, get_hub_agent_detail.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openakita.memory.types import normalize_tags

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class AgentHubHandler:
    """Handles Agent Hub tool calls (platform interaction)."""

    TOOLS = [
        "search_hub_agents",
        "install_hub_agent",
        "publish_agent",
        "get_hub_agent_detail",
    ]

    def __init__(self, agent: Agent):
        self.agent = agent
        self._client = None

    def _get_client(self):
        if self._client is None:
            from ...hub import AgentHubClient

            self._client = AgentHubClient()
        return self._client

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            if tool_name == "search_hub_agents":
                return await self._search(params)
            elif tool_name == "install_hub_agent":
                return await self._install(params)
            elif tool_name == "publish_agent":
                return await self._publish(params)
            elif tool_name == "get_hub_agent_detail":
                return await self._get_detail(params)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"AgentHubHandler error ({tool_name}): {e}", exc_info=True)
            return f"Operation failed: {e}"

    async def _search(self, params: dict[str, Any]) -> str:
        client = self._get_client()
        try:
            result = await client.search(
                query=params.get("query", ""),
                category=params.get("category", ""),
                sort=params.get("sort", "downloads"),
                page=params.get("page", 1),
            )
        except Exception as e:
            return (
                f"Unable to connect to remote Agent Hub: {e}\n\n"
                f"The remote marketplace is temporarily unavailable, but you can still:\n"
                f"- Use `list_exportable_agents` to view local agents\n"
                f"- Use `export_agent` / `import_agent` to share via .akita-agent files\n"
                f"- Import/export via Setup Center \"Agent Management\""
            )

        agents = result.get("agents", result.get("data", []))
        total = result.get("total", len(agents))

        if not agents:
            query = params.get("query", "")
            if query:
                return f"No agents found matching \"{query}\"."
            return "No agents available in the Agent Store yet."

        lines = [f"Search results ({total} total):\n"]
        for a in agents[:10]:
            stars = f"⭐{a.get('avgRating', 0):.1f}" if a.get("avgRating") else ""
            downloads = f"📥{a.get('downloads', 0)}"
            lines.append(
                f"- **{a.get('name', '?')}** (`{a.get('id', '?')}`)\n"
                f"  {a.get('description', '')[:100]}\n"
                f"  {downloads} {stars}"
            )

        if total > 10:
            lines.append(f"\n...and {total - 10} more results. Use the page parameter to browse.")

        lines.append("\nUse `install_hub_agent` to install an agent you are interested in.")
        return "\n".join(lines)

    async def _install(self, params: dict[str, Any]) -> str:
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return "agent_id is required"

        client = self._get_client()

        try:
            package_path = await client.download(agent_id)
        except Exception as e:
            return (
                f"Download from Hub failed: {e}\n\n"
                f"If you already have a .akita-agent file, use the `import_agent` tool to import it locally."
            )

        from ...agents.packager import AgentInstaller
        from ...agents.profile import get_profile_store
        from ...config import settings

        Path(settings.project_root)
        profile_store = get_profile_store()
        skills_dir = Path(settings.skills_path)

        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )

        try:
            force = params.get("force", False)
            profile = installer.install(package_path, force=force)
        except Exception as e:
            return f"Installation failed: {e}"

        from datetime import datetime

        if profile.hub_source is None:
            profile.hub_source = {}
        profile.hub_source.update(
            {
                "platform": "openakita",
                "agent_id": agent_id,
                "installed_at": datetime.now().isoformat(),
            }
        )
        profile_store.save(profile)

        self._try_reload_skills()

        return (
            f"Agent installed from Hub successfully!\n\n"
            f"Name: {profile.name}\n"
            f"ID: {profile.id}\n"
            f"Description: {profile.description}\n"
            f"Skills: {', '.join(profile.skills) if profile.skills else 'None'}\n\n"
            f"You can now find and use this agent in the agent list."
        )

    async def _publish(self, params: dict[str, Any]) -> str:
        profile_id = params.get("profile_id", "")
        if not profile_id:
            return "profile_id is required"

        from ...agents.packager import AgentPackager
        from ...agents.profile import get_profile_store
        from ...config import settings

        root = Path(settings.project_root)
        profile_store = get_profile_store()
        skills_dir = Path(settings.skills_path)
        output_dir = root / "data" / "agent_packages"

        packager = AgentPackager(
            profile_store=profile_store,
            skills_dir=skills_dir,
            output_dir=output_dir,
        )

        try:
            package_path = packager.package(profile_id=profile_id)
        except Exception as e:
            return f"Packaging failed: {e}"

        return (
            f"Agent packaged: {package_path}\n\n"
            f"Automatic publishing requires platform account authentication.\n"
            f"Please visit https://openakita.ai, sign in, and upload manually from \"My Agents\",\n"
            f"or publish via the Agent Store page in Setup Center."
        )

    def _try_reload_skills(self) -> None:
        """After Hub agent installation, trigger unified skill reload so bundled skills take effect immediately."""
        try:
            from ...skills.events import SkillEvent

            if hasattr(self.agent, "propagate_skill_change"):
                self.agent.propagate_skill_change(SkillEvent.INSTALL)
                logger.info("Skills reloaded after Hub install")
        except Exception as e:
            logger.warning(f"Skill reload after Hub install failed (non-blocking): {e}")

    async def _get_detail(self, params: dict[str, Any]) -> str:
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return "agent_id is required"

        client = self._get_client()
        try:
            detail = await client.get_detail(agent_id)
        except Exception as e:
            return f"Failed to get details: {e}"

        a = detail.get("agent", detail)
        lines = [
            "Agent Details\n",
            f"**Name**: {a.get('name', '?')}",
            f"**ID**: {a.get('id', '?')}",
            f"**Version**: {a.get('latestVersion', a.get('version', '?'))}",
            f"**Author**: {a.get('authorName', '?')}",
            f"**Category**: {a.get('category', 'None')}",
            f"**Downloads**: {a.get('downloads', 0)}",
        ]

        if a.get("avgRating"):
            lines.append(f"**Rating**: {a['avgRating']:.1f} ({a.get('ratingCount', 0)} reviews)")
        if a.get("description"):
            lines.append(f"\n**Description**: {a['description']}")
        if a.get("tags"):
            lines.append(f"**Tags**: {', '.join(normalize_tags(a['tags']))}")

        lines.append("\nUse `install_hub_agent` to install this agent.")
        return "\n".join(lines)


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentHubHandler(agent)
    return handler.handle
