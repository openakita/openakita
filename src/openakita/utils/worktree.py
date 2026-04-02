"""
Git Worktree 隔离

参考 Claude Code 的 worktree.ts 设计:
- 子 Agent 在独立 git worktree 中工作
- 不影响主工作区
- 完成后可合并或丢弃
- 自动清理过期 worktree
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREE_BASE = ".openakita/worktrees"
STALE_THRESHOLD_HOURS = 24


@dataclass
class WorktreeInfo:
    """Worktree 信息"""

    path: Path
    branch: str
    agent_id: str
    created_at: datetime


async def _run_git(args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
    """执行 git 命令。"""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def create_agent_worktree(
    agent_id: str,
    project_root: str | Path | None = None,
) -> WorktreeInfo | None:
    """为子 Agent 创建独立的 git worktree。

    Args:
        agent_id: Agent ID
        project_root: 项目根目录

    Returns:
        WorktreeInfo，如果创建失败返回 None
    """
    root = Path(project_root) if project_root else Path.cwd()
    slug = f"agent-{agent_id[:8]}"
    worktree_path = root / WORKTREE_BASE / slug
    branch = f"worktree-{slug}"

    try:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if worktree already exists
        if worktree_path.exists():
            logger.info("Worktree already exists at %s, reusing", worktree_path)
            return WorktreeInfo(
                path=worktree_path,
                branch=branch,
                agent_id=agent_id,
                created_at=datetime.now(),
            )

        code, stdout, stderr = await _run_git(
            ["worktree", "add", str(worktree_path), "-b", branch],
            cwd=root,
        )

        if code != 0:
            # Branch might already exist, try without -b
            code, stdout, stderr = await _run_git(
                ["worktree", "add", str(worktree_path), branch],
                cwd=root,
            )
            if code != 0:
                logger.error("Failed to create worktree: %s", stderr)
                return None

        logger.info("Created agent worktree at %s (branch: %s)", worktree_path, branch)
        return WorktreeInfo(
            path=worktree_path,
            branch=branch,
            agent_id=agent_id,
            created_at=datetime.now(),
        )

    except Exception as e:
        logger.error("Failed to create agent worktree: %s", e)
        return None


async def cleanup_agent_worktree(
    info: WorktreeInfo,
    *,
    merge: bool = False,
    project_root: str | Path | None = None,
) -> bool:
    """清理 Agent worktree。

    Args:
        info: Worktree 信息
        merge: 是否将 worktree 分支合并回当前分支
        project_root: 项目根目录

    Returns:
        是否成功
    """
    root = Path(project_root) if project_root else Path.cwd()

    try:
        if merge:
            code, stdout, stderr = await _run_git(
                ["merge", info.branch, "--no-ff", "-m", f"Merge agent worktree {info.branch}"],
                cwd=root,
            )
            if code != 0:
                logger.warning("Failed to merge worktree branch %s: %s", info.branch, stderr)

        code, stdout, stderr = await _run_git(
            ["worktree", "remove", str(info.path), "--force"],
            cwd=root,
        )

        if code != 0:
            logger.warning("git worktree remove failed, trying manual cleanup: %s", stderr)
            if info.path.exists():
                shutil.rmtree(str(info.path), ignore_errors=True)

        # Delete the branch
        await _run_git(["branch", "-D", info.branch], cwd=root)

        logger.info("Cleaned up agent worktree: %s", info.path)
        return True

    except Exception as e:
        logger.error("Failed to cleanup worktree: %s", e)
        return False


async def cleanup_stale_worktrees(
    project_root: str | Path | None = None,
    max_age_hours: float = STALE_THRESHOLD_HOURS,
) -> int:
    """清理过期的 agent worktrees。

    Returns:
        清理的 worktree 数量
    """
    root = Path(project_root) if project_root else Path.cwd()
    worktree_dir = root / WORKTREE_BASE

    if not worktree_dir.exists():
        return 0

    cleaned = 0
    threshold = datetime.now() - timedelta(hours=max_age_hours)

    for child in worktree_dir.iterdir():
        if not child.is_dir():
            continue

        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime)
            if mtime < threshold:
                info = WorktreeInfo(
                    path=child,
                    branch=f"worktree-{child.name}",
                    agent_id=child.name.replace("agent-", ""),
                    created_at=mtime,
                )
                await cleanup_agent_worktree(info, project_root=root)
                cleaned += 1
        except Exception as e:
            logger.warning("Failed to check/clean worktree %s: %s", child, e)

    if cleaned:
        logger.info("Cleaned up %d stale worktrees", cleaned)
    return cleaned
