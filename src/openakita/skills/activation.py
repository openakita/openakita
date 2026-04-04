"""
条件技能激活

基于文件路径模式 (fnmatch) 决定技能是否应处于激活状态。
技能在 SKILL.md frontmatter 中声明 `paths` 字段后，
仅当工作区中存在匹配文件时才进入激活状态。

未声明 `paths` 的技能始终处于激活状态。
"""

from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import SkillEntry

logger = logging.getLogger(__name__)


class SkillActivationManager:
    """Manage conditional skill activation based on file path patterns."""

    def __init__(self) -> None:
        self._dormant: dict[str, list[str]] = {}
        self._active_context_files: set[str] = set()

    def register_conditional(self, skill: "SkillEntry") -> None:
        """Register a skill with path-based activation conditions."""
        if skill.paths:
            self._dormant[skill.skill_id] = list(skill.paths)
            logger.debug(
                "Registered conditional skill '%s' with patterns: %s",
                skill.skill_id, skill.paths,
            )

    def unregister(self, skill_id: str) -> None:
        """Remove a skill from conditional tracking."""
        self._dormant.pop(skill_id, None)

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
        """Return skill IDs whose path patterns match the current context."""
        activated: set[str] = set()
        for skill_id, patterns in self._dormant.items():
            if self._matches_any(patterns):
                activated.add(skill_id)
        return activated

    def get_dormant_skills(self) -> set[str]:
        """Return skill IDs that are registered but not currently matching."""
        active = self.get_active_skills()
        return set(self._dormant.keys()) - active

    def is_active(self, skill_id: str) -> bool:
        """Check if a conditional skill is currently active."""
        patterns = self._dormant.get(skill_id)
        if patterns is None:
            return True
        return self._matches_any(patterns)

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
        return len(self._dormant)

    def clear(self) -> None:
        self._dormant.clear()
        self._active_context_files.clear()
