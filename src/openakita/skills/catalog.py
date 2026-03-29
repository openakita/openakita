"""
技能目录 (Skill Catalog)

遵循 Agent Skills 规范的渐进式披露:
- Level 1: 技能清单 (name + description) - 在系统提示中提供
- Level 2: 完整指令 (SKILL.md body) - 激活时加载
- Level 3: 资源文件 - 按需加载

技能清单在 Agent 启动时生成，并注入到系统提示中，
让大模型在首次对话时就知道有哪些技能可用。
"""

import logging

from .registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillCatalog:
    """
    技能目录

    管理技能清单的生成和格式化，用于系统提示注入。
    """

    # 技能清单模板
    # 注意：该段落会进入 system prompt，尽量短（降低噪声与 token 占用）
    CATALOG_TEMPLATE = """
## Available Skills

Use `get_skill_info(skill_name)` to load full instructions when needed.
Installed skills may come from builtin, user workspace, or project directories.
Do not infer filesystem paths from the workspace map; `get_skill_info` is authoritative.

{skill_list}
"""

    SKILL_ENTRY_TEMPLATE = "- **{name}**: {description}"

    @staticmethod
    def _safe_format(template: str, **kwargs: str) -> str:
        """str.format that won't crash on {/} in values."""
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError, IndexError) as e:
            logger.warning(
                "[SkillCatalog] str.format failed (template=%r, keys=%s): %s",
                template[:60], list(kwargs.keys()), e,
            )
            return template + " " + " | ".join(f"{k}={v}" for k, v in kwargs.items())

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._cached_catalog: str | None = None

    def generate_catalog(self) -> str:
        """
        生成已启用技能清单（disabled 技能不会出现在系统提示中）

        Returns:
            格式化的技能清单字符串
        """
        skills = self.registry.list_enabled()

        if not skills:
            empty_catalog = (
                "\n## Available Skills\n\n"
                "No skills installed. Use the skill creation workflow to add new skills.\n"
            )
            self._cached_catalog = empty_catalog
            return empty_catalog

        skill_entries = []
        for skill in skills:
            desc = skill.description or ""
            first_line = desc.split("\n")[0].strip()

            entry = self._safe_format(
                self.SKILL_ENTRY_TEMPLATE,
                name=skill.name,
                description=first_line,
            )
            skill_entries.append(entry)

        skill_list = "\n".join(skill_entries)

        catalog = self._safe_format(self.CATALOG_TEMPLATE, skill_list=skill_list)
        self._cached_catalog = catalog

        logger.info(f"Generated skill catalog with {len(skills)} skills")
        return catalog

    def get_catalog(self, refresh: bool = False) -> str:
        """
        获取技能清单

        Args:
            refresh: 是否强制刷新

        Returns:
            技能清单字符串
        """
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog

    def get_compact_catalog(self) -> str:
        """
        获取紧凑版技能清单 (仅名称列表)

        用于 token 受限的场景
        """
        skills = self.registry.list_enabled()
        if not skills:
            return "No skills installed."

        names = [s.name for s in skills]
        if not names:
            return "No skills installed."
        return f"Available skills: {', '.join(names)}"

    def get_index_catalog(self) -> str:
        """
        获取已启用技能的"全量索引"（仅名称，尽量短，但完整）。

        disabled 技能不会出现在索引中，避免 LLM 误用被禁用的技能。
        按 system / external / plugin 三组输出。
        """
        skills = self.registry.list_enabled()
        if not skills:
            return "## Skills Index (complete)\n\nNo skills installed."

        system_names: list[str] = []
        external_names: list[str] = []
        plugin_entries: list[str] = []

        for s in skills:
            if getattr(s, "system", False):
                system_names.append(s.name)
            elif getattr(s, "plugin_source", None):
                plugin_id = s.plugin_source.replace("plugin:", "")
                plugin_entries.append(f"{s.name} (via {plugin_id})")
            else:
                external_names.append(s.name)

        system_names.sort()
        external_names.sort()
        plugin_entries.sort()

        lines: list[str] = [
            "## Skills Index (complete)",
            "",
            "Use `get_skill_info(skill_name)` to load full instructions.",
            "Most external skills are **instruction-only** (no pre-built scripts) "
            "\u2014 read instructions via get_skill_info, then write code and execute via run_shell.",
            "Only use `run_skill_script` when a skill explicitly lists executable scripts.",
        ]

        if system_names:
            lines += ["", f"**System skills ({len(system_names)})**: {', '.join(system_names)}"]
        if external_names:
            lines += [
                "",
                f"**External skills ({len(external_names)})**: {', '.join(external_names)}",
            ]
        if plugin_entries:
            lines += [
                "",
                f"**Plugin skills ({len(plugin_entries)})**: {', '.join(plugin_entries)}",
            ]

        return "\n".join(lines)

    def get_skill_summary(self, skill_name: str) -> str | None:
        """
        获取单个技能的摘要

        Args:
            skill_name: 技能名称

        Returns:
            技能摘要 (name + description)
        """
        skill = self.registry.get(skill_name)
        if not skill:
            return None

        return f"**{skill.name}**: {skill.description}"

    def invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_catalog = None

    @property
    def skill_count(self) -> int:
        """技能数量"""
        return self.registry.count


def generate_skill_catalog(registry: SkillRegistry) -> str:
    """便捷函数：生成技能清单"""
    catalog = SkillCatalog(registry)
    return catalog.generate_catalog()
