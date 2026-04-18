"""
Skill Store handler — search_store_skills, install_store_skill, get_store_skill_detail, submit_skill_repo.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class SkillStoreHandler:
    """Handles Skill Store tool calls (platform interaction)."""

    TOOLS = [
        "search_store_skills",
        "install_store_skill",
        "get_store_skill_detail",
        "submit_skill_repo",
    ]

    def __init__(self, agent: Agent):
        self.agent = agent
        self._client = None

    def _get_client(self):
        if self._client is None:
            from ...hub import SkillStoreClient

            self._client = SkillStoreClient()
        return self._client

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            if tool_name == "search_store_skills":
                return await self._search(params)
            elif tool_name == "install_store_skill":
                return await self._install(params)
            elif tool_name == "get_store_skill_detail":
                return await self._get_detail(params)
            elif tool_name == "submit_skill_repo":
                return await self._submit_repo(params)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"SkillStoreHandler error ({tool_name}): {e}", exc_info=True)
            return f"Operation failed: {e}"

    async def _search(self, params: dict[str, Any]) -> str:
        client = self._get_client()
        try:
            result = await client.search(
                query=params.get("query", ""),
                category=params.get("category", ""),
                trust_level=params.get("trust_level", ""),
                sort=params.get("sort", "installs"),
                page=params.get("page", 1),
            )
        except Exception as e:
            return (
                f"Cannot connect to remote Skill Store: {e}\n\n"
                f"The remote marketplace is temporarily unavailable, but you can still:\n"
                f"- Use `list_skills` to view locally installed skills\n"
                f"- Use `install_skill` to install directly from GitHub\n"
                f"- Go to Setup Center > Skill Management > Browse Marketplace to search and install from skills.sh"
            )

        skills = result.get("skills", result.get("data", []))
        total = result.get("total", len(skills))

        if not skills:
            query = params.get("query", "")
            if query:
                return f"No skills found matching \"{query}\"."
            return "The Skill Store has no skills available yet."

        trust_icons = {"official": "🏛️", "certified": "✅", "community": "🌐"}

        lines = [f"Search results ({total} total):\n"]
        for s in skills[:10]:
            trust = s.get("trustLevel", "community")
            icon = trust_icons.get(trust, "")
            stars = f"⭐{s.get('avgRating', 0):.1f}" if s.get("avgRating") else ""
            installs = f"📥{s.get('installCount', 0)}"
            gh_stars = f"★{s.get('githubStars', 0)}" if s.get("githubStars") else ""

            lines.append(
                f"- {icon} **{s.get('name', '?')}** (`{s.get('id', '?')}`)\n"
                f"  {s.get('description', '')[:100]}\n"
                f"  {installs} {stars} {gh_stars}"
            )

        if total > 10:
            lines.append(f"\n...and {total - 10} more results. Use the page parameter to navigate.")

        lines.append("\nUse `install_store_skill` to install a skill you are interested in.")
        return "\n".join(lines)

    async def _install(self, params: dict[str, Any]) -> str:
        skill_id = params.get("skill_id", "")
        if not skill_id:
            return "skill_id is required"

        client = self._get_client()

        try:
            detail = await client.get_detail(skill_id)
        except Exception as e:
            return (
                f"Cannot connect to remote Skill Store: {e}\n\n"
                f"If you know the skill's GitHub URL, you can install it directly using the `install_skill` tool,\n"
                f"e.g.: `install_skill` name=my-skill source=owner/repo"
            )

        skill = detail.get("skill", detail)
        install_url = skill.get("installUrl", "")
        if not install_url:
            return f"Skill `{skill_id}` has no install URL; cannot install automatically."

        try:
            skill_dir = await client.install_skill(install_url, skill_id=skill_id)
        except Exception as e:
            return (
                f"Installation failed: {e}\n\n"
                f"You can also install directly from GitHub using the `install_skill` tool:\n"
                f"install_url: {install_url}"
            )

        # After Store installation: delegate to Agent.propagate_skill_change instead of manual rescan / rebuild.
        try:
            from ...skills.events import SkillEvent

            self.agent.propagate_skill_change(SkillEvent.STORE_INSTALL)
            logger.info("Skills reloaded after Store install")
        except Exception as e:
            logger.warning(f"Skill reload after Store install failed (non-blocking): {e}")

        skill_name = skill.get("name", skill_id)
        return (
            f"Skill installed from Store successfully!\n\n"
            f"Name: {skill_name}\n"
            f"Path: {skill_dir}\n"
            f"Trust level: {skill.get('trustLevel', 'community')}\n\n"
            f"The skill has been installed locally and loaded automatically."
        )

    async def _get_detail(self, params: dict[str, Any]) -> str:
        skill_id = params.get("skill_id", "")
        if not skill_id:
            return "skill_id is required"

        client = self._get_client()
        try:
            detail = await client.get_detail(skill_id)
        except Exception as e:
            return f"Failed to fetch details: {e}"

        s = detail.get("skill", detail)
        trust_icons = {
            "official": "🏛️ Official",
            "certified": "✅ Certified",
            "community": "🌐 Community",
        }

        lines = [
            "Skill Details\n",
            f"**Name**: {s.get('name', '?')}",
            f"**ID**: {s.get('id', '?')}",
            f"**Version**: {s.get('version', '?')}",
            f"**Author**: {s.get('authorName', '?')}",
            f"**Trust Level**: {trust_icons.get(s.get('trustLevel', ''), s.get('trustLevel', '?'))}",
            f"**Category**: {s.get('category', 'N/A')}",
            f"**Installs**: {s.get('installCount', 0)}",
        ]

        if s.get("avgRating"):
            lines.append(f"**Rating**: {s['avgRating']:.1f} ({s.get('ratingCount', 0)} ratings)")
        if s.get("description"):
            lines.append(f"\n**Description**: {s['description']}")
        if s.get("sourceRepo"):
            lines.append(f"**Source**: https://github.com/{s['sourceRepo']}")
        if s.get("githubStars"):
            lines.append(f"**GitHub Stars**: ★{s['githubStars']}")

        lines.append("\nUse `install_store_skill` to install this skill.")
        return "\n".join(lines)

    async def _submit_repo(self, params: dict[str, Any]) -> str:
        repo_url = params.get("repo_url", "")
        if not repo_url:
            return "repo_url is required"

        client = self._get_client()
        try:
            result = await client.submit_repo(repo_url)
        except Exception as e:
            return f"Submission failed: {e}"

        return (
            f"Repository submitted!\n\n"
            f"{result.get('message', 'Processing')}\n"
            f"The platform will scan the SKILL.md file in the repository and create a Skill entry."
        )


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = SkillStoreHandler(agent)
    return handler.handle
