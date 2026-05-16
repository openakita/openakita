"""Workspace 解析器（Phase 2a）。

负责给一个会话决定它的 ``memory_workspace_id`` —— 即"这个会话能看到哪个
工作区的长期记忆"。

历史包袱：
- v1/v2/v3 desktop/CLI/API/web 全部硬编码 ``workspace_id="default"``，
  导致桌面用户在不同项目目录下打开 OpenAkita 时，所有项目共用同一份
  长期记忆，相互污染（"fake desktop workspace isolation"）。

Phase 2a 目标：
- 提供 ``resolve_desktop_workspace_id`` 把 cwd 哈希成一个稳定的
  workspace_id 字符串；
- 通过 opt-in 机制（环境变量 ``OPENAKITA_DESKTOP_PROJECT_WORKSPACE=1``
  或 session metadata ``memory_workspace_mode='project'``）启用，
  默认仍然返回 ``"default"`` —— 不破坏现有用户体验，等 Phase 2a.3 才
  考虑切换默认值；
- 同时暴露 ``LEGACY_DEFAULT_WORKSPACE_ID`` 常量给 ``MemoryManager`` 的
  双读路径使用，让用户在切换到 project 模式后仍能 fallback 看到原来
  "default" 工作区里的记忆。

注意：
- 这里只决定 "查询/写入的 workspace_id 字符串"，不动数据库里的字段；
  实际的数据迁徙由 ``api/routes/memory.py:migrate-workspace`` 端点处理。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


LEGACY_DEFAULT_WORKSPACE_ID = "default"
"""默认/legacy 工作区 ID。所有 v1~v3 desktop / CLI / API 通道都写到这里。"""

PROJECT_WORKSPACE_PREFIX = "proj-"
"""通过 cwd 哈希生成的 workspace_id 前缀，便于日志 / SQL 一眼区分类型。"""

_PROJECT_HASH_LEN = 12
"""项目哈希取前 N 位字符。12 位 hex 碰撞概率 ~2^-48，对桌面场景足够。"""


def _opt_in_env_flag() -> bool:
    """读环境变量 ``OPENAKITA_DESKTOP_PROJECT_WORKSPACE``。

    支持值：``1 / true / yes / on`` (大小写不敏感) → 启用 project 模式。
    """
    val = (os.environ.get("OPENAKITA_DESKTOP_PROJECT_WORKSPACE") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def resolve_desktop_workspace_id(cwd: str | Path | None = None) -> str:
    """根据当前工作目录哈希生成稳定的 project workspace_id。

    用 sha1，取前 12 位 hex；可避免特殊字符 / 路径长度问题，对同一项目
    无论运行多少次都返回同一个值。

    Args:
        cwd: 指定工作目录；None 则取 ``os.getcwd()``。
    """
    if cwd is None:
        try:
            cwd_path = Path(os.getcwd()).resolve()
        except Exception:
            cwd_path = Path("default")
    else:
        cwd_path = Path(cwd).resolve()
    digest = hashlib.sha1(str(cwd_path).encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{PROJECT_WORKSPACE_PREFIX}{digest[:_PROJECT_HASH_LEN]}"


def resolve_memory_workspace_id(
    session: Any | None,
    *,
    cwd: str | Path | None = None,
) -> str:
    """给一个 session 决定 ``memory_workspace_id``。

    决策顺序：

    1. session.metadata['memory_workspace_id'] 显式设值 → 直接用；
    2. channel 是 IM 通道（telegram/feishu/dingtalk/wecom/qq/onebot 等）→
       继续走 ``bot_instance_id / channel`` 命名空间（保持 v3 行为）；
    3. channel 是 desktop/api/cli/web：
       - 若 session.metadata['memory_workspace_mode'] == 'project'
         或环境变量 ``OPENAKITA_DESKTOP_PROJECT_WORKSPACE`` 启用 →
         调用 ``resolve_desktop_workspace_id`` 算项目哈希；
       - 否则保持 ``LEGACY_DEFAULT_WORKSPACE_ID="default"``（不破坏
         现有用户）。
    4. 兜底返回 ``"default"``。
    """
    if session is None:
        return LEGACY_DEFAULT_WORKSPACE_ID

    metadata = getattr(session, "metadata", {}) or {}
    explicit = metadata.get("memory_workspace_id")
    if explicit:
        return str(explicit)

    channel = str(getattr(session, "channel", "") or "")

    if channel in {"desktop", "api", "cli", "web"}:
        mode = (metadata.get("memory_workspace_mode") or "").strip().lower()
        if mode == "project" or _opt_in_env_flag():
            return resolve_desktop_workspace_id(cwd)
        return LEGACY_DEFAULT_WORKSPACE_ID

    if channel:
        namespace = (
            getattr(session, "bot_instance_id", None)
            or metadata.get("bot_instance_id")
            or channel
        )
        return str(namespace) if namespace else LEGACY_DEFAULT_WORKSPACE_ID

    return LEGACY_DEFAULT_WORKSPACE_ID


def is_project_workspace(workspace_id: str | None) -> bool:
    """workspace_id 是否由 cwd 哈希生成（用于 UI 标识 / 日志聚合）。"""
    return bool(workspace_id and workspace_id.startswith(PROJECT_WORKSPACE_PREFIX))
