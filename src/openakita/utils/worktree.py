"""
Git Worktree Isolation

Modeled after Claude Code's worktree.ts design:
- Sub-agents work in isolated git worktrees
- Does not affect the main workspace
- Can be merged or discarded after completion
- Automatically cleans up stale worktrees
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
    """Worktree information."""

    path: Path
    branch: str
    agent_id: str
    created_at: datetime


async def _run_git(args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
    """Execute a git command."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
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
    """Create an isolated git worktree for a sub-agent.

    Args:
        agent_id: Agent ID
        project_root: Project root directory

    Returns:
        WorktreeInfo, or None if creation failed
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
    """Clean up an agent worktree.

    Args:
        info: Worktree information
        merge: Whether to merge the worktree branch back into the current branch
        project_root: Project root directory

    Returns:
        Whether cleanup succeeded
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
    """Clean up stale agent worktrees.

    Returns:
        Number of worktrees cleaned up
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
