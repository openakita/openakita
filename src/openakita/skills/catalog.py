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

{skill_list}
"""

    SKILL_ENTRY_TEMPLATE = "- **{name}**: {description}"

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._cached_catalog: str | None = None

    def generate_catalog(self) -> str:
        """
        生成技能清单

        Returns:
            格式化的技能清单字符串
        """
        skills = self.registry.list_all()

        if not skills:
            return (
                "\n## Available Skills\n\n"
                "No skills installed. Use the skill creation workflow to add new skills.\n"
            )

        skill_entries = []
        for skill in skills:
            # 获取描述第一行
            desc = skill.description
            first_line = desc.split("\n")[0].strip()

            entry = self.SKILL_ENTRY_TEMPLATE.format(
                name=skill.name,
                description=first_line,
            )
            skill_entries.append(entry)

        skill_list = "\n".join(skill_entries)

        catalog = self.CATALOG_TEMPLATE.format(skill_list=skill_list)
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
        skills = self.registry.list_all()
        if not skills:
            return "No skills installed."

        names = [s.name for s in skills]
        if not names:
            return "No skills installed."
        return f"Available skills: {', '.join(names)}"

    def get_index_catalog(self) -> str:
        """
        获取“全量索引”版技能清单（仅名称，尽量短，但完整）。

        目的：
        - 在 token 预算受限时，也保证模型能“看到所有技能名字”，避免清单被截断成半截。
        - 具体用法再通过 `get_skill_info(skill_name)` 渐进式披露。
        """
        skills = self.registry.list_all()
        if not skills:
            return "## Skills Index (complete)\n\nNo skills installed."

        system_names: list[str] = []
        external_names: list[str] = []

        for s in skills:
            if getattr(s, "system", False):
                system_names.append(s.name)
            else:
                external_names.append(s.name)

        # 稳定排序，减少提示词抖动（也有助于缓存命中/调试）
        system_names.sort()
        external_names.sort()

        lines: list[str] = [
            "## Skills Index (complete)",
            "",
            "Use `get_skill_info(skill_name)` to load full instructions.",
            "Most external skills are **instruction-only** (no pre-built scripts) — read instructions via get_skill_info, then write code and execute via run_shell.",
            "Only use `run_skill_script` when a skill explicitly lists executable scripts.",
        ]

        if system_names:
            lines += ["", f"**System skills ({len(system_names)})**: {', '.join(system_names)}"]
        if external_names:
            lines += [
                "",
                f"**External skills ({len(external_names)})**: {', '.join(external_names)}",
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
