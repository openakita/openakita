"""
技能目录 (Skill Catalog)

遵循 Agent Skills 规范的渐进式披露:
- Level 1: 技能清单 (name + description) - 在系统提示中提供
- Level 2: 完整指令 (SKILL.md body) - 激活时加载
- Level 3: 资源文件 - 按需加载

技能清单在 Agent 启动时生成，并注入到系统提示中，
让大模型在首次对话时就知道有哪些技能可用。

三级降级预算策略:
- Level A (full): name + description + when_to_use
- Level B (compact): name + when_to_use
- Level C (index): names only
"""

import logging
import threading
from typing import TYPE_CHECKING

from .registry import SkillRegistry

if TYPE_CHECKING:
    from .usage import SkillUsageTracker

logger = logging.getLogger(__name__)


class SkillCatalog:
    """
    技能目录

    管理技能清单的生成和格式化，用于系统提示注入。
    """

    CATALOG_TEMPLATE = """
## Available Skills

Use `get_skill_info(skill_name)` to load full instructions when needed.
Installed skills may come from builtin, user workspace, or project directories.
Do not infer filesystem paths from the workspace map; `get_skill_info` is authoritative.

{skill_list}
"""

    SKILL_ENTRY_TEMPLATE = "- **{name}**: {description}"
    SKILL_ENTRY_WITH_HINT_TEMPLATE = "- **{name}**: {description} _(Use when: {when_to_use})_"

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

    def __init__(
        self,
        registry: SkillRegistry,
        usage_tracker: "SkillUsageTracker | None" = None,
    ):
        self.registry = registry
        self._usage_tracker = usage_tracker
        self._lock = threading.Lock()
        self._cached_catalog: str | None = None
        self._cached_index: str | None = None
        self._cached_compact: str | None = None

    def _list_model_visible(self) -> list:
        """Return enabled skills that are also visible to the model, sorted by usage."""
        skills = [
            s for s in self.registry.list_enabled()
            if not s.disable_model_invocation
        ]
        if self._usage_tracker:
            scores = self._usage_tracker.get_all_scores()
            skills.sort(key=lambda s: scores.get(s.skill_id, 0), reverse=True)
        return skills

    def generate_catalog(self) -> str:
        """
        生成已启用技能清单（disabled 和 disable_model_invocation 技能不出现在系统提示中）
        """
        with self._lock:
            skills = self._list_model_visible()

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
                when = getattr(skill, "when_to_use", "") or ""

                if when:
                    entry = self._safe_format(
                        self.SKILL_ENTRY_WITH_HINT_TEMPLATE,
                        name=skill.name,
                        description=first_line,
                        when_to_use=when,
                    )
                else:
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
        """
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog

    def get_compact_catalog(self) -> str:
        """获取紧凑版技能清单 (仅名称列表)，用于 token 受限场景。"""
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                result = "No skills installed."
            else:
                names = [s.name for s in skills]
                result = f"Available skills: {', '.join(names)}"
            self._cached_compact = result
            return result

    def get_index_catalog(self) -> str:
        """
        获取已启用技能的"全量索引"（仅名称，尽量短，但完整）。

        disabled 和 disable_model_invocation 技能不会出现在索引中。
        按 system / external / plugin 三组输出。
        """
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                result = "## Skills Index (complete)\n\nNo skills installed."
                self._cached_index = result
                return result

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

            result = "\n".join(lines)
            self._cached_index = result
            return result

    def generate_catalog_budgeted(self, budget_chars: int = 0) -> str:
        """Generate catalog with three-level degradation if budget_chars is set.

        Level A: full (name + description + when_to_use) via generate_catalog()
        Level B: name + short hint for each skill
        Level C: comma-separated names only

        If budget_chars <= 0, returns full catalog without budget constraint.
        """
        if budget_chars <= 0:
            return self.generate_catalog()

        full = self.generate_catalog()
        if len(full) <= budget_chars:
            return full

        # Level B: name + short hint
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                return "No skills installed."
            b_lines = ["## Skills (compact)"]
            for s in skills:
                hint = getattr(s, "when_to_use", "") or ""
                if hint:
                    b_lines.append(f"- **{s.name}**: {hint[:60]}")
                else:
                    desc_short = (s.description or "")[:40]
                    b_lines.append(f"- **{s.name}**: {desc_short}")
            level_b = "\n".join(b_lines)
            if len(level_b) <= budget_chars:
                return level_b

            # Level C: names only
            names = [s.name for s in skills]
            return f"Skills ({len(skills)}): {', '.join(names)}"

    def get_skill_summary(self, skill_name: str) -> str | None:
        """获取单个技能的摘要"""
        skill = self.registry.get(skill_name)
        if not skill:
            return None
        return f"**{skill.name}**: {skill.description}"

    def invalidate_cache(self) -> None:
        """使所有缓存失效"""
        with self._lock:
            self._cached_catalog = None
            self._cached_index = None
            self._cached_compact = None

    @property
    def skill_count(self) -> int:
        """技能数量"""
        return self.registry.count


def generate_skill_catalog(registry: SkillRegistry) -> str:
    """便捷函数：生成技能清单"""
    catalog = SkillCatalog(registry)
    return catalog.generate_catalog()
