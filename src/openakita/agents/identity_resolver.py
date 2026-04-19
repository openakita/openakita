"""
ProfileIdentityResolver — resolve identity files per AgentProfile (two-level inheritance).

Inheritance rules:
  SOUL.md / AGENT.md: use Profile copy if present, otherwise fall back to global
  USER.md / MEMORY.md: always use a Profile-independent copy (auto-create blank template)
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.identity import Identity

logger = logging.getLogger(__name__)

_USER_MD_TEMPLATE = """\
# User Profile

This file is independently maintained by the Agent, recording user preferences.

## Basic Information

- **Name**: [to be learned]
- **Primary language**: Chinese

## Preferences

[to be learned]

---
*This file is automatically maintained by OpenAkita.*
"""

_MEMORY_MD_TEMPLATE = """\
# Core Memory

## Preferences

## Facts

## Rules

## Skills

## Lessons
"""


class ProfileIdentityResolver:
    """Resolve identity files per AgentProfile, supporting global + Profile two-level inheritance."""

    def __init__(
        self,
        profile_identity_dir: Path,
        global_identity_dir: Path,
    ) -> None:
        self._profile_dir = profile_identity_dir
        self._global_dir = global_identity_dir

    def ensure_independent_files(self) -> None:
        """Ensure USER.md and MEMORY.md exist (always independent files)."""
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        for name, template in [
            ("USER.md", _USER_MD_TEMPLATE),
            ("MEMORY.md", _MEMORY_MD_TEMPLATE),
        ]:
            fp = self._profile_dir / name
            if not fp.exists():
                fp.write_text(template, encoding="utf-8")
                logger.info(f"Created independent {name} for profile at {fp}")

    def resolve_path(self, filename: str) -> Path:
        """Resolve the actual path for a single identity file.

        USER.md / MEMORY.md always return the Profile directory version.
        SOUL.md / AGENT.md use the Profile copy if it exists, otherwise fall back to global.
        """
        always_independent = {"USER.md", "MEMORY.md"}
        profile_path = self._profile_dir / filename

        if filename in always_independent:
            return profile_path

        if profile_path.exists() and profile_path.stat().st_size > 0:
            return profile_path

        return self._global_dir / filename

    def build_identity(self) -> Identity:
        """Build an Identity instance using the resolved paths."""
        self.ensure_independent_files()
        return Identity(
            soul_path=self.resolve_path("SOUL.md"),
            agent_path=self.resolve_path("AGENT.md"),
            user_path=self.resolve_path("USER.md"),
            memory_path=self.resolve_path("MEMORY.md"),
        )
