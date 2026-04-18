"""
Skill Catalog

Follows the Agent Skills progressive disclosure specification:
- Level 1: Skill list (name + description) - provided in system prompt
- Level 2: Full instructions (SKILL.md body) - loaded on activation
- Level 3: Resource files - loaded on demand

The skill list is generated at Agent startup and injected into the system
prompt so the model knows which skills are available from the first conversation.

Three-level budget degradation strategy:
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
    Skill Catalog

    Manages the generation and formatting of the skill list for system prompt injection.
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
                template[:60],
                list(kwargs.keys()),
                e,
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

    def _list_model_visible(self, exposure_filter: str | None = None) -> list:
        """Return enabled skills that are also visible to the model, sorted by usage.

        Args:
            exposure_filter: If provided, only return skills with exposure_level
                in the specified set. Use "core" to get only core skills,
                "core+recommended" to get core and recommended skills.
                None returns all non-hidden skills (backward compatible).
        """
        _allowed_levels = None
        if exposure_filter == "core":
            _allowed_levels = {"core"}
        elif exposure_filter == "core+recommended":
            _allowed_levels = {"core", "recommended"}

        skills = []
        for s in self.registry.list_enabled():
            if s.disable_model_invocation or s.catalog_hidden:
                continue
            if _allowed_levels and getattr(s, "exposure_level", "recommended") not in _allowed_levels:
                continue
            skills.append(s)

        if self._usage_tracker:
            scores = self._usage_tracker.get_all_scores()
            skills.sort(key=lambda s: scores.get(s.skill_id, 0), reverse=True)
        return skills

    def generate_catalog(self, *, exposure_filter: str | None = None) -> str:
        """
        Generate the list of enabled skills (disabled and disable_model_invocation skills are excluded from the system prompt).

        Args:
            exposure_filter: "core" | "core+recommended" | None
                Controls filtering by exposure_level. Pass "core" for CONSUMER_CHAT,
                "core+recommended" for IM_ASSISTANT, and None for LOCAL_AGENT.
        """
        with self._lock:
            skills = self._list_model_visible(exposure_filter=exposure_filter)
            hidden_count = self.registry.count_catalog_hidden()

            if not skills:
                if hidden_count > 0:
                    empty_catalog = (
                        "\n## Available Skills\n\n"
                        "No skills are pre-loaded for this agent profile.\n"
                        f"However, {hidden_count} additional skill(s) are installed. "
                        "Use `list_skills` to discover them, then `get_skill_info(skill_name)` "
                        "to load instructions when the task requires a specific skill.\n"
                    )
                else:
                    empty_catalog = (
                        "\n## Available Skills\n\n"
                        "No skills installed. Use the skill creation workflow to add new skills.\n"
                    )
                if exposure_filter is None:
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

            if hidden_count > 0:
                skill_list += (
                    f"\n\n_({hidden_count} more skill(s) available — "
                    "use `list_skills` to discover all installed skills)_"
                )

            catalog = self._safe_format(self.CATALOG_TEMPLATE, skill_list=skill_list)
            # Only cache unfiltered results (exposure_filter=None)
            if exposure_filter is not None:
                return catalog
            self._cached_catalog = catalog

            logger.info(
                "Generated skill catalog with %d skills (%d hidden)",
                len(skills),
                hidden_count,
            )
            return catalog

    def get_catalog(self, refresh: bool = False) -> str:
        """
        Retrieve the skill catalog.

        Args:
            refresh: Whether to force a refresh.
        """
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog

    def get_compact_catalog(self) -> str:
        """Retrieve the compact skill catalog (names only), for token-constrained scenarios."""
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                result = "No skills installed."
            else:
                names = [s.name for s in skills]
                result = f"Available skills: {', '.join(names)}"
            self._cached_compact = result
            return result

    def get_index_catalog(self, *, exposure_filter: str | None = None) -> str:
        """
        Retrieve a "full index" of enabled skills (names only, as short as possible but complete).

        Args:
            exposure_filter: "core" | "core+recommended" | None
        """
        with self._lock:
            skills = self._list_model_visible(exposure_filter=exposure_filter)
            hidden_count = self.registry.count_catalog_hidden()
            if not skills:
                if hidden_count > 0:
                    result = (
                        "## Skills Index\n\n"
                        "No skills pre-loaded for this profile. "
                        f"{hidden_count} more skill(s) available via `list_skills`."
                    )
                else:
                    result = "## Skills Index (complete)\n\nNo skills installed."
                if exposure_filter is None:
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
            if exposure_filter is None:
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
        """Retrieve a summary for a single skill."""
        skill = self.registry.get(skill_name)
        if not skill:
            return None
        return f"**{skill.name}**: {skill.description}"

    def generate_recommendation_hint(
        self,
        task_description: str,
        *,
        max_hints: int = 3,
        max_chars: int = 250,
        exposure_filter: str | None = None,
    ) -> str:
        """Generate lightweight skill recommendation hints based on user input.

        Performs simple keyword matching against when_to_use and keywords fields; does not call the LLM.
        Returns a format like: "Potentially useful skills: web-search (search the web), ..."

        Args:
            task_description: The user's task description/input.
            max_hints: Maximum number of skills to recommend.
            max_chars: Total length limit for the hint.
            exposure_filter: "core" / "core+recommended" / None (all except hidden).
        """
        if not task_description:
            return ""

        _allowed: set[str] | None = None
        if exposure_filter == "core":
            _allowed = {"core"}
        elif exposure_filter == "core+recommended":
            _allowed = {"core", "recommended"}
        query_lower = task_description.lower()
        candidates: list[tuple[float, str, str]] = []

        for s in self.registry.list_enabled():
            if s.disable_model_invocation or s.catalog_hidden:
                continue
            _exp = getattr(s, "exposure_level", "recommended")
            if _allowed and _exp not in _allowed:
                continue

            score = 0.0
            when = getattr(s, "when_to_use", "") or ""
            kws = getattr(s, "keywords", []) or []

            for kw in kws:
                if kw.lower() in query_lower:
                    score += 2.0
            if when:
                when_words = when.lower().split()
                for w in when_words:
                    if len(w) > 2 and w in query_lower:
                        score += 0.5

            if score > 0:
                short_desc = (s.description or "")[:40]
                candidates.append((score, s.name, short_desc))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:max_hints]
        parts = [f"{name} ({desc})" for _, name, desc in top]
        hint = "Potentially useful skills: " + ", ".join(parts)

        if len(hint) > max_chars:
            hint = hint[:max_chars - 3] + "..."
        return hint

    def invalidate_cache(self) -> None:
        """Invalidate all caches."""
        with self._lock:
            self._cached_catalog = None
            self._cached_index = None
            self._cached_compact = None

    @property
    def skill_count(self) -> int:
        """Skill count."""
        return self.registry.count


def generate_skill_catalog(registry: SkillRegistry) -> str:
    """Convenience function: generate the skill catalog."""
    catalog = SkillCatalog(registry)
    return catalog.generate_catalog()
