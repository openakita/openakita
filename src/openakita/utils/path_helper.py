"""
跨平台命令查找工具

macOS GUI 应用（Finder/Dock 启动的 .app）只继承系统最小 PATH:
  /usr/bin:/bin:/usr/sbin:/sbin
不含 Homebrew、NVM、Volta 等工具管理器注入的路径。

本模块提供统一的命令查找接口，在 macOS 上自动使用 login shell PATH 回退，
供 MCP 连接、系统提示构建、Chrome DevTools 检测等场景复用。
"""

import functools
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def resolve_macos_login_shell_path() -> str | None:
    """通过 login shell 获取 macOS 用户的完整 PATH。

    Finder/Dock 启动的 .app 只拿到 /usr/bin:/bin:/usr/sbin:/sbin，
    不含 Homebrew、NVM、Volta 等工具管理器注入的路径。
    此函数运行一次用户的 login shell 并提取完整 PATH，结果由 lru_cache 缓存。

    如果 login shell 失败（如 .zshrc 有语法错误、初始化超时），
    会回退到 macOS path_helper 读取 /etc/paths 和 /etc/paths.d/ 配置。
    """
    if sys.platform != "darwin":
        return None

    path = _resolve_via_login_shell()
    if path:
        return path

    path = _resolve_via_path_helper()
    if path:
        return path

    logger.warning(
        "[PATH] All macOS PATH resolution methods failed. Commands like npx/node may not be found."
    )
    return None


def which_command(cmd: str, extra_path: str | None = None) -> str | None:
    """查找命令，macOS GUI 环境下自动回退到 login shell PATH。

    Args:
        cmd: 要查找的命令名
        extra_path: 额外的搜索路径（优先使用，如 MCP 配置中的自定义 PATH）

    Returns:
        命令的绝对路径，未找到返回 None
    """
    found = shutil.which(cmd, path=extra_path)
    if found:
        return found

    if sys.platform == "darwin" and not extra_path:
        shell_path = resolve_macos_login_shell_path()
        if shell_path:
            return shutil.which(cmd, path=shell_path)

    return None


def get_macos_enriched_env(base_env: dict[str, str] | None = None) -> dict[str, str] | None:
    """为 macOS 子进程构建包含完整 PATH 的环境变量字典。

    Args:
        base_env: 基础环境变量（如 MCP 配置中的 env）。
                  为 None 或空 dict 时使用 os.environ 作为基础。

    Returns:
        包含完整 PATH 的环境变量字典。非 macOS 或无需修改时返回 base_env。
    """
    if sys.platform != "darwin":
        return base_env

    shell_path = resolve_macos_login_shell_path()
    if not shell_path:
        return base_env

    if not base_env:
        return {**os.environ, "PATH": shell_path}

    if "PATH" not in base_env and "Path" not in base_env:
        return {**base_env, "PATH": shell_path}

    return base_env


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _resolve_via_login_shell() -> str | None:
    """方法 1: 通过 login shell 获取 PATH（最完整，包含 nvm/volta 等动态路径）"""
    shell = os.environ.get("SHELL", "/bin/zsh")
    try:
        proc = subprocess.run(
            [shell, "-l", "-c", 'printf "\\n__AKITA_PATH__\\n%s\\n__AKITA_PATH__\\n" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            logger.warning(
                "[PATH] macOS login shell exited with code %d (shell=%s). stderr: %s",
                proc.returncode,
                shell,
                (proc.stderr or "").strip()[:500],
            )
            return None
        parts = proc.stdout.split("__AKITA_PATH__")
        if len(parts) < 3:
            logger.warning(
                "[PATH] macOS login shell output missing path markers (shell=%s, stdout length=%d)",
                shell,
                len(proc.stdout),
            )
            return None
        path = parts[1].strip()
        if not path:
            logger.warning("[PATH] macOS login shell returned empty PATH (shell=%s)", shell)
            return None
        logger.info("[PATH] Resolved macOS PATH via login shell (%d entries)", path.count(":") + 1)
        logger.debug("[PATH] macOS shell PATH: %s", path)
        return path
    except subprocess.TimeoutExpired:
        logger.warning(
            "[PATH] macOS login shell timed out after 10s (shell=%s). "
            "Shell config (.zshrc/.bash_profile) may be slow to initialize.",
            shell,
        )
    except Exception as e:
        logger.warning("[PATH] Failed to run macOS login shell: %s", e)
    return None


def _resolve_via_path_helper() -> str | None:
    """方法 2: 通过 /usr/libexec/path_helper 获取 PATH。

    读取 /etc/paths 和 /etc/paths.d/ 的静态配置。
    不包含 nvm/volta 等动态路径，但能覆盖 Homebrew 路径。
    """
    try:
        proc = subprocess.run(
            ["/usr/libexec/path_helper", "-s"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            return None
        # path_helper 输出格式: PATH="..."; export PATH;
        output = proc.stdout.strip()
        if output.startswith('PATH="') and '";' in output:
            path = output.split('"')[1]
            if path:
                logger.info(
                    "[PATH] Resolved macOS PATH via path_helper (%d entries)",
                    path.count(":") + 1,
                )
                return path
    except Exception as e:
        logger.debug("[PATH] path_helper fallback failed: %s", e)
    return None
