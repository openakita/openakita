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
            return f"❌ 操作失败: {e}"

    async def _export(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentPackager

        profile_id = params.get("profile_id", "")
        if not profile_id:
            return "❌ 需要指定 profile_id"

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
            f"✅ Agent 已导出！\n\n"
            f"📦 文件: {output_path}\n"
            f"📏 大小: {size_kb:.1f} KB\n\n"
            f"💡 导出路径: `{output_dir}`\n"
            f"你可以将这个 `.akita-agent` 文件分享给其他用户导入使用。"
        )

    async def _import(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ 需要指定 package_path"

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
            f"✅ Agent 导入成功！\n\n"
            f"🤖 名称: {profile.name}\n"
            f"🆔 ID: {profile.id}\n"
            f"📝 描述: {profile.description}\n"
            f"🔧 技能: {', '.join(profile.skills) if profile.skills else '无'}\n\n"
            f"Agent 及其技能已安装并自动加载。"
        )

    async def _list_exportable(self, params: dict[str, Any]) -> str:
        profile_store, _, _ = _get_stores()
        profiles = profile_store.list_all(include_hidden=False)

        if not profiles:
            return "当前没有可导出的 Agent。"

        lines = ["📋 可导出的 Agent 列表：\n"]
        for p in profiles:
            skills_count = len(p.skills) if p.skills else 0
            type_label = "系统" if p.is_system else "自定义"
            cat = f" [{p.category}]" if p.category else ""
            lines.append(
                f"- **{p.name}** (`{p.id}`) — {type_label}{cat}, "
                f"{skills_count} 个技能"
            )

        lines.append(f"\n共 {len(profiles)} 个 Agent 可导出。")
        lines.append("使用 `export_agent` 工具导出指定 Agent。")
        return "\n".join(lines)

    async def _inspect(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ 需要指定 package_path"

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
            f"📦 Agent 包预览\n",
            f"**名称**: {manifest.get('name', '?')}",
            f"**ID**: {manifest.get('id', '?')}",
            f"**版本**: {manifest.get('version', '?')}",
            f"**作者**: {manifest.get('author', {}).get('name', '?')}",
            f"**分类**: {manifest.get('category', '无')}",
            f"**大小**: {info['package_size'] / 1024:.1f} KB",
        ]

        if info["bundled_skills"]:
            lines.append(f"**捆绑技能**: {', '.join(info['bundled_skills'])}")
        if manifest.get("required_builtin_skills"):
            lines.append(
                f"**需要内置技能**: {', '.join(manifest['required_builtin_skills'])}"
            )

        ext_skills = manifest.get("required_external_skills", [])
        if ext_skills:
            names = [s.get("id", "?") if isinstance(s, dict) else str(s) for s in ext_skills]
            lines.append(f"**外部依赖技能**: {', '.join(names)}")

        if errors:
            lines.append(f"\n⚠️ 校验问题: {'; '.join(errors)}")
        if conflict:
            lines.append(f"\n⚠️ ID 冲突: 本地已存在 `{manifest.get('id')}`，导入时将自动重命名")

        if profile.get("custom_prompt"):
            prompt_preview = profile["custom_prompt"][:200]
            if len(profile["custom_prompt"]) > 200:
                prompt_preview += "..."
            lines.append(f"\n**提示词预览**: {prompt_preview}")

        lines.append("\n使用 `import_agent` 工具导入此 Agent。")
        return "\n".join(lines)

    async def _batch_export(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentPackager

        profile_ids = params.get("profile_ids", [])
        if not profile_ids:
            return "❌ 需要指定 profile_ids 列表"

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

        lines = [f"📦 批量导出完成 — {len(exported)} 成功, {len(errors)} 失败\n"]
        lines.append(f"💡 导出路径: `{output_dir}`\n")
        if exported:
            lines.append("**已导出:**")
            lines.extend(exported)
        if errors:
            lines.append("\n**失败:**")
            lines.extend(errors)
        return "\n".join(lines)

    def _try_reload_skills(self) -> None:
        """Best-effort reload of skills after installation."""
        try:
            loader = getattr(self.agent, "skill_loader", None)
            if loader:
                from ...config import settings
                loader.load_all(settings.project_root)
                logger.info("Skills reloaded after agent package import")
        except Exception as e:
            logger.warning(f"Skill reload after import failed (non-blocking): {e}")


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentPackageHandler(agent)
    return handler.handle
