"""
记忆系统

管理 USER.md 和 MEMORY.md，以及数据库中的记忆。
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
    记忆系统

    管理:
    - MEMORY.md - 工作记忆（任务进度、经验）
    - USER.md - 用户档案（偏好、习惯）
    - 数据库 - 持久化存储
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
        """初始化记忆系统"""
        if db:
            self.db = db

        # 确保文件存在
        if not self.memory_path.exists():
            self._create_default_memory()

        if not self.user_path.exists():
            self._create_default_user()

        logger.info("Memory system initialized")

    def _create_default_memory(self) -> None:
        """创建默认 MEMORY.md"""
        content = f"""# OpenAkita Memory

## Current Task Progress

### Active Task

[当前没有活跃任务]

## Implementation Plan

### High Priority

[暂无]

## Learned Experiences

[暂无]

## Statistics

- **总任务数**: 0
- **成功任务**: 0
- **失败任务**: 0

---
*最后更新: {datetime.now().isoformat()}*
"""

        self.memory_path.write_text(content, encoding="utf-8")

    def _create_default_user(self) -> None:
        """创建默认 USER.md"""
        content = f"""# User Profile

## Basic Information

- **名称**: [待学习]
- **工作领域**: [待学习]
- **主要语言**: 中文

## Preferences

[待学习]

## Interaction Patterns

[待学习]

---
*最后更新: {datetime.now().isoformat()}*
"""

        self.user_path.write_text(content, encoding="utf-8")

    # ===== MEMORY.md 操作 =====

    def load_memory(self) -> str:
        """加载 MEMORY.md"""
        if self.memory_path.exists():
            self._memory_cache = self.memory_path.read_text(encoding="utf-8")
        else:
            self._create_default_memory()
            self._memory_cache = self.memory_path.read_text(encoding="utf-8")
        return self._memory_cache

    def save_memory(self, content: str) -> None:
        """保存 MEMORY.md"""
        # 更新时间戳
        content = re.sub(
            r"\*最后更新: .+\*",
            f"*最后更新: {datetime.now().isoformat()}*",
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
        """更新当前活跃任务"""
        content = self.load_memory()

        task_info = f"""### Active Task

- **ID**: {task_id}
- **描述**: {description}
- **状态**: {status}
- **尝试次数**: {attempts}
- **更新时间**: {datetime.now().isoformat()}
"""

        # 替换 Active Task 部分
        if "### Active Task" in content:
            # 找到下一个 ## 或 ### 的位置
            start = content.find("### Active Task")
            end = start + len("### Active Task")

            # 查找下一个标题
            next_heading = len(content)
            for pattern in ["## ", "### "]:
                pos = content.find(pattern, end + 1)
                if pos != -1 and pos < next_heading:
                    next_heading = pos

            content = content[:start] + task_info + "\n" + content[next_heading:]
        else:
            # 在 Current Task Progress 后插入
            insert_pos = content.find("## Current Task Progress")
            if insert_pos != -1:
                insert_pos = content.find("\n", insert_pos) + 1
                content = content[:insert_pos] + "\n" + task_info + content[insert_pos:]

        self.save_memory(content)

    def add_experience(self, category: str, content: str) -> None:
        """添加经验记录"""
        memory = self.load_memory()

        entry = f"\n- **[{datetime.now().strftime('%Y-%m-%d %H:%M')}]** [{category}] {content}"

        # 在 Learned Experiences 部分添加
        section = "## Learned Experiences"
        if section in memory:
            pos = memory.find(section)
            end_pos = memory.find("\n## ", pos + 1)
            if end_pos == -1:
                end_pos = memory.find("\n---", pos)
            if end_pos == -1:
                end_pos = len(memory)

            # 在 [暂无] 后或列表末尾添加
            insert_pos = memory.find("[暂无]", pos)
            if insert_pos != -1 and insert_pos < end_pos:
                # 替换 [暂无]
                memory = memory[:insert_pos] + entry[1:] + memory[insert_pos + 4 :]
            else:
                # 在部分末尾添加
                memory = memory[:end_pos] + entry + "\n" + memory[end_pos:]

            self.save_memory(memory)

    def update_statistics(self, **kwargs: int) -> None:
        """更新统计数据"""
        memory = self.load_memory()

        for key, value in kwargs.items():
            # 查找并更新统计项
            pattern = rf"(\*\*{key}\*\*: )(\d+)"
            match = re.search(pattern, memory)
            if match:
                old_value = int(match.group(2))
                new_value = old_value + value
                memory = memory[: match.start()] + f"**{key}**: {new_value}" + memory[match.end() :]

        self.save_memory(memory)

    # ===== USER.md 操作 =====

    def load_user(self) -> str:
        """加载 USER.md"""
        if self.user_path.exists():
            self._user_cache = self.user_path.read_text(encoding="utf-8")
        else:
            self._create_default_user()
            self._user_cache = self.user_path.read_text(encoding="utf-8")
        return self._user_cache

    def save_user(self, content: str) -> None:
        """保存 USER.md"""
        content = re.sub(
            r"\*最后更新: .+\*",
            f"*最后更新: {datetime.now().isoformat()}*",
            content,
        )
        self.user_path.write_text(content, encoding="utf-8")
        self._user_cache = content

    def update_user_field(self, field: str, value: str) -> None:
        """更新用户档案字段"""
        content = self.load_user()

        # 查找并更新字段
        pattern = rf"(\*\*{field}\*\*: )(\[待学习\]|.+?)(\n)"
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
        """学习用户偏好"""
        # 更新 USER.md
        self.update_user_field(key, str(value))

        # 保存到数据库
        if self.db:
            import asyncio

            asyncio.create_task(self.db.set_preference(key, value))

    # ===== 数据库记忆操作 =====

    async def remember(
        self,
        content: str,
        category: str = "general",
        importance: int = 5,
        tags: list[str] | None = None,
    ) -> int:
        """
        记住一条信息

        Args:
            content: 内容
            category: 分类 (task, experience, discovery, error)
            importance: 重要性 0-10
            tags: 标签

        Returns:
            记忆 ID
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
        回忆信息

        Args:
            query: 搜索词
            category: 分类过滤
            limit: 结果数量

        Returns:
            记忆列表
        """
        if not self.db:
            return []

        if query:
            return await self.db.search_memories(query, limit)
        else:
            return await self.db.get_memories(category, limit)

    async def get_context_for_task(self, task_description: str) -> str:
        """
        获取任务相关的上下文记忆

        Args:
            task_description: 任务描述

        Returns:
            相关记忆的摘要
        """
        # 搜索相关记忆
        memories = await self.recall(task_description, limit=5)

        if not memories:
            return ""

        context = "## 相关经验\n\n"
        for mem in memories:
            context += f"- [{mem.category}] {mem.content}\n"

        return context
