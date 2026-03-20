"""
Agent Hub handler — search_hub_agents, install_hub_agent, publish_agent, get_hub_agent_detail.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
            return f"❌ 操作失败: {e}"

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
                f"❌ 无法连接到远程 Agent Hub: {e}\n\n"
                f"💡 远程市场暂不可用，但你仍可以：\n"
                f"- 使用 `list_exportable_agents` 查看本地 Agent\n"
                f"- 使用 `export_agent` / `import_agent` 通过 .akita-agent 文件分享\n"
                f"- 在 Setup Center「Agent 管理」中导入导出"
            )

        agents = result.get("agents", result.get("data", []))
        total = result.get("total", len(agents))

        if not agents:
            query = params.get("query", "")
            if query:
                return f"未找到匹配「{query}」的 Agent。"
            return "Agent Store 暂无可用 Agent。"

        lines = [f"🔍 搜索结果（共 {total} 个）：\n"]
        for a in agents[:10]:
            stars = f"⭐{a.get('avgRating', 0):.1f}" if a.get("avgRating") else ""
            downloads = f"📥{a.get('downloads', 0)}"
            lines.append(
                f"- **{a.get('name', '?')}** (`{a.get('id', '?')}`)\n"
                f"  {a.get('description', '')[:100]}\n"
                f"  {downloads} {stars}"
            )

        if total > 10:
            lines.append(f"\n…还有 {total - 10} 个结果，使用 page 参数翻页查看。")

        lines.append("\n使用 `install_hub_agent` 安装感兴趣的 Agent。")
        return "\n".join(lines)

    async def _install(self, params: dict[str, Any]) -> str:
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return "❌ 需要指定 agent_id"

        client = self._get_client()

        try:
            package_path = await client.download(agent_id)
        except Exception as e:
            return (
                f"❌ 从 Hub 下载失败: {e}\n\n"
                f"💡 如果你已有 .akita-agent 文件，可以使用 `import_agent` 工具直接本地导入。"
            )

        from ...agents.packager import AgentInstaller
        from ...agents.profile import get_profile_store
        from ...config import settings

        root = Path(settings.project_root)
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
            return f"❌ 安装失败: {e}"

        from datetime import datetime
        if profile.hub_source is None:
            profile.hub_source = {}
        profile.hub_source.update({
            "platform": "openakita",
            "agent_id": agent_id,
            "installed_at": datetime.now().isoformat(),
        })
        profile_store.save(profile)

        self._try_reload_skills()

        return (
            f"✅ Agent 从 Hub 安装成功！\n\n"
            f"🤖 名称: {profile.name}\n"
            f"🆔 ID: {profile.id}\n"
            f"📝 描述: {profile.description}\n"
            f"🔧 技能: {', '.join(profile.skills) if profile.skills else '无'}\n\n"
            f"你现在可以在 Agent 列表中找到并使用这个 Agent。"
        )

    async def _publish(self, params: dict[str, Any]) -> str:
        profile_id = params.get("profile_id", "")
        if not profile_id:
            return "❌ 需要指定 profile_id"

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
            return f"❌ 打包失败: {e}"

        return (
            f"📦 Agent 已打包: {package_path}\n\n"
            f"⚠️ 自动发布功能需要平台账号认证。\n"
            f"请访问 https://openakita.ai 登录后在「我的 Agent」页面手动上传，\n"
            f"或通过 Setup Center 的 Agent Store 页面发布。"
        )

    def _try_reload_skills(self) -> None:
        """Best-effort reload of skills after installation."""
        try:
            loader = getattr(self.agent, "skill_loader", None)
            if loader:
                from ...config import settings
                loader.load_all(settings.project_root)
                logger.info("Skills reloaded after Hub install")
        except Exception as e:
            logger.warning(f"Skill reload after Hub install failed (non-blocking): {e}")

    async def _get_detail(self, params: dict[str, Any]) -> str:
        agent_id = params.get("agent_id", "")
        if not agent_id:
            return "❌ 需要指定 agent_id"

        client = self._get_client()
        try:
            detail = await client.get_detail(agent_id)
        except Exception as e:
            return f"❌ 获取详情失败: {e}"

        a = detail.get("agent", detail)
        lines = [
            f"📋 Agent 详情\n",
            f"**名称**: {a.get('name', '?')}",
            f"**ID**: {a.get('id', '?')}",
            f"**版本**: {a.get('latestVersion', a.get('version', '?'))}",
            f"**作者**: {a.get('authorName', '?')}",
            f"**分类**: {a.get('category', '无')}",
            f"**下载量**: {a.get('downloads', 0)}",
        ]

        if a.get("avgRating"):
            lines.append(f"**评分**: ⭐{a['avgRating']:.1f} ({a.get('ratingCount', 0)} 人评价)")
        if a.get("description"):
            lines.append(f"\n**描述**: {a['description']}")
        if a.get("tags"):
            lines.append(f"**标签**: {', '.join(a['tags'])}")

        lines.append(f"\n使用 `install_hub_agent` 安装此 Agent。")
        return "\n".join(lines)


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentHubHandler(agent)
    return handler.handle
