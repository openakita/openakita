"""
Worktree 工具处理器

暴露 utils/worktree.py 的功能为 Agent 工具。
参考 CC EnterWorktree / ExitWorktree。

# ApprovalClass checklist (新增 / 修改工具时必读)
# 1. 在本文件 Handler 类的 TOOLS 列表加新工具名
# 2. 在同 Handler 类的 TOOL_CLASSES 字典加 ApprovalClass 显式声明
#    （或在 agent.py:_init_handlers 的 register() 调用里加 tool_classes={...}）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class WorktreeHandler:
    TOOLS = ["enter_worktree", "exit_worktree"]
    TOOL_CLASSES = {
        "enter_worktree": ApprovalClass.CONTROL_PLANE,
        "exit_worktree": ApprovalClass.CONTROL_PLANE,
    }

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._active_worktree = None
        self._original_cwd: str | None = None

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "enter_worktree":
            return await self._enter(params)
        elif tool_name == "exit_worktree":
            return await self._exit(params)
        return f"Unknown worktree tool: {tool_name}"

    async def _enter(self, params: dict[str, Any]) -> str:
        if self._active_worktree:
            return (
                f"Already in worktree '{self._active_worktree.branch}'. "
                "Use exit_worktree first before entering a new one."
            )

        from ...utils.worktree import create_agent_worktree

        name = params.get("name") or f"wt-{uuid.uuid4().hex[:8]}"
        cwd = getattr(self.agent, "default_cwd", None) or os.getcwd()

        info = await create_agent_worktree(name, project_root=cwd)
        if not info:
            return "Failed to create worktree. Ensure this is a git repository."

        self._active_worktree = info

        # Switch agent's working directory to worktree
        self._original_cwd = getattr(self.agent, "default_cwd", None) or os.getcwd()
        if hasattr(self.agent, "default_cwd"):
            self.agent.default_cwd = str(info.path)

        logger.info(f"[Worktree] Entered: {info.path} (branch: {info.branch})")
        return (
            f"Entered worktree at {info.path}\n"
            f"Branch: {info.branch}\n"
            f"Working directory switched. Changes here won't affect the main workspace."
        )

    async def _exit(self, params: dict[str, Any]) -> str:
        if not self._active_worktree:
            return "Not currently in a worktree."

        action = params.get("action", "keep")
        discard = params.get("discard_changes", False)

        info = self._active_worktree
        cwd = str(info.path)

        # Check for uncommitted changes
        from ...utils.worktree import _run_git

        code, stdout, stderr = await _run_git(["status", "--porcelain"], cwd=cwd)
        has_changes = bool(stdout.strip())

        if action == "remove" and has_changes and not discard:
            return (
                "Worktree has uncommitted changes. Either:\n"
                "1. Commit your changes first\n"
                "2. Set discard_changes=true to discard them\n"
                "3. Use action='keep' to preserve the worktree"
            )

        # Restore original working directory
        if self._original_cwd and hasattr(self.agent, "default_cwd"):
            self.agent.default_cwd = self._original_cwd

        project_root = self._original_cwd or os.getcwd()

        if action == "remove":
            from ...utils.worktree import cleanup_agent_worktree

            success = await cleanup_agent_worktree(info, project_root=project_root)
            self._active_worktree = None
            if success:
                return f"Exited and removed worktree '{info.branch}'."
            return (
                f"Exited worktree but cleanup had issues. Branch '{info.branch}' may still exist."
            )
        else:
            self._active_worktree = None
            return (
                f"Exited worktree '{info.branch}' (kept).\n"
                f"To merge later: git merge {info.branch}\n"
                f"To remove later: git worktree remove {info.path}"
            )


def create_handler(agent: "Agent"):
    handler = WorktreeHandler(agent)
    return handler.handle
