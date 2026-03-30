"""
ProfileIdentityResolver — 按 AgentProfile 解析身份文件（两级继承）

继承规则:
  SOUL.md / AGENT.md: Profile 目录有则用，否则继承全局
  USER.md / MEMORY.md: 始终使用 Profile 独立版本（自动创建空白模板）
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.identity import Identity

logger = logging.getLogger(__name__)

_USER_MD_TEMPLATE = """\
# User Profile

此文件由 Agent 独立维护，记录该 Agent 服务的用户偏好。

## Basic Information

- **称呼**: [待学习]
- **主要语言**: 中文

## Preferences

[待学习]

---
*此文件由 OpenAkita 自动维护。*
"""

_MEMORY_MD_TEMPLATE = """\
# 核心记忆

## 偏好

## 事实

## 规则

## 技能

## 教训
"""


class ProfileIdentityResolver:
    """按 AgentProfile 解析身份文件，支持全局 + Profile 两级继承。"""

    def __init__(
        self,
        profile_identity_dir: Path,
        global_identity_dir: Path,
    ) -> None:
        self._profile_dir = profile_identity_dir
        self._global_dir = global_identity_dir

    def ensure_independent_files(self) -> None:
        """确保 USER.md 和 MEMORY.md 存在（始终独立的文件）。"""
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
        """解析单个身份文件的实际路径。

        USER.md / MEMORY.md 始终返回 Profile 目录的版本。
        SOUL.md / AGENT.md 如果 Profile 目录有就用，否则回退全局。
        """
        always_independent = {"USER.md", "MEMORY.md"}
        profile_path = self._profile_dir / filename

        if filename in always_independent:
            return profile_path

        if profile_path.exists() and profile_path.stat().st_size > 0:
            return profile_path

        return self._global_dir / filename

    def build_identity(self) -> Identity:
        """构建一个使用解析后路径的 Identity 实例。"""
        self.ensure_independent_files()
        return Identity(
            soul_path=self.resolve_path("SOUL.md"),
            agent_path=self.resolve_path("AGENT.md"),
            user_path=self.resolve_path("USER.md"),
            memory_path=self.resolve_path("MEMORY.md"),
        )
