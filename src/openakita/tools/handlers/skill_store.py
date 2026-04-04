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
            return f"❌ 操作失败: {e}"

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
                f"❌ 无法连接到远程 Skill Store: {e}\n\n"
                f"💡 远程市场暂不可用，但你仍可以：\n"
                f"- 使用 `list_skills` 查看已安装的本地技能\n"
                f"- 使用 `install_skill` 从 GitHub 直接安装技能\n"
                f"- 在 Setup Center「技能管理 → 浏览市场」从 skills.sh 搜索安装"
            )

        skills = result.get("skills", result.get("data", []))
        total = result.get("total", len(skills))

        if not skills:
            query = params.get("query", "")
            if query:
                return f"未找到匹配「{query}」的 Skill。"
            return "Skill Store 暂无可用 Skill。"

        trust_icons = {"official": "🏛️", "certified": "✅", "community": "🌐"}

        lines = [f"🔍 搜索结果（共 {total} 个）：\n"]
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
            lines.append(f"\n…还有 {total - 10} 个结果，使用 page 参数翻页查看。")

        lines.append("\n使用 `install_store_skill` 安装感兴趣的 Skill。")
        return "\n".join(lines)

    async def _install(self, params: dict[str, Any]) -> str:
        skill_id = params.get("skill_id", "")
        if not skill_id:
            return "❌ 需要指定 skill_id"

        client = self._get_client()

        try:
            detail = await client.get_detail(skill_id)
        except Exception as e:
            return (
                f"❌ 无法连接远程 Skill Store: {e}\n\n"
                f"💡 如果你知道 Skill 的 GitHub 地址，可以直接使用 `install_skill` 工具安装，\n"
                f"例如：`install_skill` name=my-skill source=owner/repo"
            )

        skill = detail.get("skill", detail)
        install_url = skill.get("installUrl", "")
        if not install_url:
            return f"❌ Skill `{skill_id}` 没有安装地址，无法自动安装。"

        try:
            skill_dir = await client.install_skill(install_url, skill_id=skill_id)
        except Exception as e:
            return (
                f"❌ 安装失败: {e}\n\n"
                f"💡 你也可以使用 `install_skill` 工具直接从 GitHub 安装：\n"
                f"install_url: {install_url}"
            )

        self._try_reload_skills()

        skill_name = skill.get("name", skill_id)
        return (
            f"✅ Skill 从 Store 安装成功！\n\n"
            f"📦 名称: {skill_name}\n"
            f"📂 路径: {skill_dir}\n"
            f"🏷️ 信任等级: {skill.get('trustLevel', 'community')}\n\n"
            f"Skill 已安装到本地并自动加载。"
        )

    def _try_reload_skills(self) -> None:
        """Best-effort reload of skills after installation."""
        try:
            loader = getattr(self.agent, "skill_loader", None)
            if loader:
                from ...config import settings
                loader.load_all(settings.project_root)

            catalog = getattr(self.agent, "skill_catalog", None)
            if catalog:
                catalog.invalidate_cache()
                self.agent._skill_catalog_text = catalog.generate_catalog()

            if hasattr(self.agent, "_update_skill_tools"):
                self.agent._update_skill_tools()

            from ...skills.events import notify_skills_changed, SkillEvent
            notify_skills_changed(SkillEvent.STORE_INSTALL)

            logger.info("Skills reloaded after Store install")
        except Exception as e:
            logger.warning(f"Skill reload after Store install failed (non-blocking): {e}")

    async def _get_detail(self, params: dict[str, Any]) -> str:
        skill_id = params.get("skill_id", "")
        if not skill_id:
            return "❌ 需要指定 skill_id"

        client = self._get_client()
        try:
            detail = await client.get_detail(skill_id)
        except Exception as e:
            return f"❌ 获取详情失败: {e}"

        s = detail.get("skill", detail)
        trust_icons = {"official": "🏛️ Official", "certified": "✅ Certified", "community": "🌐 Community"}

        lines = [
            f"📋 Skill 详情\n",
            f"**名称**: {s.get('name', '?')}",
            f"**ID**: {s.get('id', '?')}",
            f"**版本**: {s.get('version', '?')}",
            f"**作者**: {s.get('authorName', '?')}",
            f"**信任等级**: {trust_icons.get(s.get('trustLevel', ''), s.get('trustLevel', '?'))}",
            f"**分类**: {s.get('category', '无')}",
            f"**安装量**: {s.get('installCount', 0)}",
        ]

        if s.get("avgRating"):
            lines.append(f"**评分**: ⭐{s['avgRating']:.1f} ({s.get('ratingCount', 0)} 人评价)")
        if s.get("description"):
            lines.append(f"\n**描述**: {s['description']}")
        if s.get("sourceRepo"):
            lines.append(f"**源码**: https://github.com/{s['sourceRepo']}")
        if s.get("githubStars"):
            lines.append(f"**GitHub Stars**: ★{s['githubStars']}")

        lines.append(f"\n使用 `install_store_skill` 安装此 Skill。")
        return "\n".join(lines)

    async def _submit_repo(self, params: dict[str, Any]) -> str:
        repo_url = params.get("repo_url", "")
        if not repo_url:
            return "❌ 需要指定 repo_url"

        client = self._get_client()
        try:
            result = await client.submit_repo(repo_url)
        except Exception as e:
            return f"❌ 提交失败: {e}"

        return (
            f"✅ 仓库已提交！\n\n"
            f"📦 {result.get('message', '处理中')}\n"
            f"平台将扫描仓库中的 SKILL.md 文件并创建 Skill 条目。"
        )


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = SkillStoreHandler(agent)
    return handler.handle
