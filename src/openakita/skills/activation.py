"""
Conditional skill activation.

Two activation dimensions:
1. File path patterns (fnmatch) — when a skill declares ``paths``,
   it activates only if matching files exist in the workspace.
2. Toolset fallback (fallback_for_toolsets) — when a skill declares
   the names of toolsets it depends on, it activates only when ALL
   of those toolsets are unavailable, providing a degraded alternative.

The two conditions are OR-ed: satisfying either dimension activates the skill.
Skills with no declared conditions are always active.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import SkillEntry

logger = logging.getLogger(__name__)


class SkillActivationManager:
    """Manage conditional skill activation based on file patterns and toolset availability."""

    def __init__(self) -> None:
        self._dormant: dict[str, list[str]] = {}
        self._fallback_skills: dict[str, list[str]] = {}
        self._active_context_files: set[str] = set()
        self._available_toolsets: set[str] = set()

    def register_conditional(self, skill: SkillEntry) -> None:
        """Register a skill with path-based activation conditions."""
        if skill.paths:
            self._dormant[skill.skill_id] = list(skill.paths)
            logger.debug(
                "Registered conditional skill '%s' with patterns: %s",
                skill.skill_id,
                skill.paths,
            )
        if skill.fallback_for_toolsets:
            self._fallback_skills[skill.skill_id] = list(skill.fallback_for_toolsets)
            logger.debug(
                "Registered fallback skill '%s' for toolsets: %s",
                skill.skill_id,
                skill.fallback_for_toolsets,
            )

    def unregister(self, skill_id: str) -> None:
        """Remove a skill from conditional tracking."""
        self._dormant.pop(skill_id, None)
        self._fallback_skills.pop(skill_id, None)

    def update_available_toolsets(self, toolset_names: set[str]) -> None:
        """Update which toolset categories are currently available.

        A "toolset" is a named group of related tools (e.g. "browser",
        "mcp", "docker").  Skills that declare ``fallback_for_toolsets``
        will only activate when **all** of their listed toolsets are
        absent from this set.
        """
        self._available_toolsets = set(toolset_names)

    def update_context(self, file_paths: list[str]) -> set[str]:
        """Update the current file context and return newly activated skill IDs.

        Args:
            file_paths: List of file paths currently in context
                        (e.g., open editor tabs, referenced files).

        Returns:
            Set of skill_ids that should now be activated.
        """
        self._active_context_files = set(file_paths)
        return self.get_active_skills()

    def get_active_skills(self) -> set[str]:
        """Return skill IDs whose activation conditions are satisfied."""
        activated: set[str] = set()
        for skill_id, patterns in self._dormant.items():
            if self._matches_any(patterns):
                activated.add(skill_id)
        for skill_id, toolsets in self._fallback_skills.items():
            if self._toolsets_missing(toolsets):
                activated.add(skill_id)
        return activated

    def get_dormant_skills(self) -> set[str]:
        """Return skill IDs that are registered but not currently matching."""
        all_conditional = set(self._dormant.keys()) | set(self._fallback_skills.keys())
        return all_conditional - self.get_active_skills()

    def is_active(self, skill_id: str) -> bool:
        """Check if a conditional skill is currently active."""
        has_path_cond = skill_id in self._dormant
        has_fallback_cond = skill_id in self._fallback_skills

        if not has_path_cond and not has_fallback_cond:
            return True

        if has_path_cond and self._matches_any(self._dormant[skill_id]):
            return True
        if has_fallback_cond and self._toolsets_missing(self._fallback_skills[skill_id]):
            return True
        return False

    def _toolsets_missing(self, required: list[str]) -> bool:
        """Return True when ALL listed toolsets are unavailable."""
        return all(ts not in self._available_toolsets for ts in required)

    def _matches_any(self, patterns: list[str]) -> bool:
        """Check if any context file matches any of the given patterns."""
        for fp in self._active_context_files:
            normalized = fp.replace("\\", "/")
            for pattern in patterns:
                if fnmatch.fnmatch(normalized, pattern):
                    return True
                if fnmatch.fnmatch(normalized.rsplit("/", 1)[-1], pattern):
                    return True
        return False

    @property
    def conditional_count(self) -> int:
        return len(set(self._dormant.keys()) | set(self._fallback_skills.keys()))

    def clear(self) -> None:
        self._dormant.clear()
        self._fallback_skills.clear()
        self._active_context_files.clear()
        self._available_toolsets.clear()
