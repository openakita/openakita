"""
Memory system

Manages USER.md and MEMORY.md, as well as database-stored memories.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from ..storage.database import Database
from ..storage.models import MemoryEntry

logger = logging.getLogger(__name__)


class Memory:
    """
    Memory system

    Manages:
    - MEMORY.md - Working memory (task progress, experience)
    - USER.md - User profile (preferences, habits)
    - Database - Persistent storage
    """

    def __init__(
        self,
        memory_path: Path | None = None,
        user_path: Path | None = None,
        database: Database | None = None,
    ):
        self.memory_path = memory_path or settings.memory_path
        self.user_path = user_path or settings.user_path
        self.db = database

        self._memory_cache: str | None = None
        self._user_cache: str | None = None

    async def initialize(self, db: Database | None = None) -> None:
        """Initialize the memory system."""
        if db:
            self.db = db

        # Ensure files exist
        if not self.memory_path.exists():
            self._create_default_memory()

        if not self.user_path.exists():
            self._create_default_user()

        logger.info("Memory system initialized")

    def _create_default_memory(self) -> None:
        """Create the default MEMORY.md."""
        content = f"""# OpenAkita Memory

## Current Task Progress

### Active Task

[No active task]

## Implementation Plan

### High Priority

[None yet]

## Learned Experiences

[None yet]

## Statistics

- **Total tasks**: 0
- **Successful tasks**: 0
- **Failed tasks**: 0

---
*Last updated: {datetime.now().isoformat()}*
"""

        self.memory_path.write_text(content, encoding="utf-8")

    def _create_default_user(self) -> None:
        """Create the default USER.md."""
        content = f"""# User Profile

## Basic Information

- **Name**: [To be learned]
- **Work domain**: [To be learned]
- **Primary language**: Chinese

## Preferences

[To be learned]

## Interaction Patterns

[To be learned]

---
*Last updated: {datetime.now().isoformat()}*
"""

        self.user_path.write_text(content, encoding="utf-8")

    # ===== MEMORY.md operations =====

    def load_memory(self) -> str:
        """Load MEMORY.md."""
        if self.memory_path.exists():
            self._memory_cache = self.memory_path.read_text(encoding="utf-8")
        else:
            self._create_default_memory()
            self._memory_cache = self.memory_path.read_text(encoding="utf-8")
        return self._memory_cache

    def save_memory(self, content: str) -> None:
        """Save MEMORY.md."""
        # Update timestamp
        content = re.sub(
            r"\*Last updated: .+\*",
            f"*Last updated: {datetime.now().isoformat()}*",
            content,
        )
        self.memory_path.write_text(content, encoding="utf-8")
        self._memory_cache = content

    def update_active_task(
        self,
        task_id: str,
        description: str,
        status: str,
        attempts: int = 0,
    ) -> None:
        """Update the current active task."""
        content = self.load_memory()

        task_info = f"""### Active Task

- **ID**: {task_id}
- **Description**: {description}
- **Status**: {status}
- **Attempts**: {attempts}
- **Updated at**: {datetime.now().isoformat()}
"""

        # Replace the Active Task section
        if "### Active Task" in content:
            # Find the position of the next ## or ###
            start = content.find("### Active Task")
            end = start + len("### Active Task")

            # Find the next heading
            next_heading = len(content)
            for pattern in ["## ", "### "]:
                pos = content.find(pattern, end + 1)
                if pos != -1 and pos < next_heading:
                    next_heading = pos

            content = content[:start] + task_info + "\n" + content[next_heading:]
        else:
            # Insert after Current Task Progress
            insert_pos = content.find("## Current Task Progress")
            if insert_pos != -1:
                insert_pos = content.find("\n", insert_pos) + 1
                content = content[:insert_pos] + "\n" + task_info + content[insert_pos:]

        self.save_memory(content)

    def add_experience(self, category: str, content: str) -> None:
        """Add an experience record."""
        memory = self.load_memory()

        entry = f"\n- **[{datetime.now().strftime('%Y-%m-%d %H:%M')}]** [{category}] {content}"

        # Add to the Learned Experiences section
        section = "## Learned Experiences"
        if section in memory:
            pos = memory.find(section)
            end_pos = memory.find("\n## ", pos + 1)
            if end_pos == -1:
                end_pos = memory.find("\n---", pos)
            if end_pos == -1:
                end_pos = len(memory)

            # Add after [None yet] or at end of list
            insert_pos = memory.find("[None yet]", pos)
            if insert_pos != -1 and insert_pos < end_pos:
                # Replace [None yet]
                memory = memory[:insert_pos] + entry[1:] + memory[insert_pos + 10 :]
            else:
                # Add at end of section
                memory = memory[:end_pos] + entry + "\n" + memory[end_pos:]

            self.save_memory(memory)

    def update_statistics(self, **kwargs: int) -> None:
        """Update statistics."""
        memory = self.load_memory()

        for key, value in kwargs.items():
            # Find and update statistics entry
            pattern = rf"(\*\*{key}\*\*: )(\d+)"
            match = re.search(pattern, memory)
            if match:
                old_value = int(match.group(2))
                new_value = old_value + value
                memory = memory[: match.start()] + f"**{key}**: {new_value}" + memory[match.end() :]

        self.save_memory(memory)

    # ===== USER.md operations =====

    def load_user(self) -> str:
        """Load USER.md."""
        if self.user_path.exists():
            self._user_cache = self.user_path.read_text(encoding="utf-8")
        else:
            self._create_default_user()
            self._user_cache = self.user_path.read_text(encoding="utf-8")
        return self._user_cache

    def save_user(self, content: str) -> None:
        """Save USER.md."""
        content = re.sub(
            r"\*Last updated: .+\*",
            f"*Last updated: {datetime.now().isoformat()}*",
            content,
        )
        self.user_path.write_text(content, encoding="utf-8")
        self._user_cache = content

    def update_user_field(self, field: str, value: str) -> None:
        """Update a user profile field."""
        content = self.load_user()

        # Find and update the field
        pattern = rf"(\*\*{field}\*\*: )(\[To be learned\]|.+?)(\n)"
        match = re.search(pattern, content)
        if match:
            content = (
                content[: match.start()]
                + f"**{field}**: {value}"
                + match.group(3)
                + content[match.end() :]
            )
            self.save_user(content)

    def learn_preference(self, key: str, value: Any) -> None:
        """Learn a user preference."""
        # Update USER.md
        self.update_user_field(key, str(value))

        # Save to database
        if self.db:
            import asyncio

            asyncio.create_task(self.db.set_preference(key, value))

    # ===== Database memory operations =====

    async def remember(
        self,
        content: str,
        category: str = "general",
        importance: int = 5,
        tags: list[str] | None = None,
    ) -> int:
        """
        Remember a piece of information.

        Args:
            content: The content to remember
            category: Category (task, experience, discovery, error)
            importance: Importance 0-10
            tags: Tags

        Returns:
            Memory ID
        """
        if not self.db:
            logger.warning("Database not connected, memory not persisted")
            return -1

        memory_id = await self.db.add_memory(
            category=category,
            content=content,
            importance=importance,
            tags=tags,
        )

        logger.debug(f"Remembered: {content}")
        return memory_id

    async def recall(
        self,
        query: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """
        Recall information.

        Args:
            query: Search term
            category: Category filter
            limit: Number of results

        Returns:
            List of memories
        """
        if not self.db:
            return []

        if query:
            return await self.db.search_memories(query, limit)
        else:
            return await self.db.get_memories(category, limit)

    async def get_context_for_task(self, task_description: str) -> str:
        """
        Retrieve context memories relevant to a task.

        Args:
            task_description: Task description

        Returns:
            Summary of relevant memories
        """
        # Search for related memories
        memories = await self.recall(task_description, limit=5)

        if not memories:
            return ""

        context = "## Relevant Experience\n\n"
        for mem in memories:
            context += f"- [{mem.category}] {mem.content}\n"

        return context
