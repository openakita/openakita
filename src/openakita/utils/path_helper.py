"""
Cross-platform command lookup utility.

macOS GUI apps (Finder/Dock-launched .app) only inherit the system minimum PATH:
  /usr/bin:/bin:/usr/sbin:/sbin
This lacks paths injected by tool managers like Homebrew, NVM, and Volta.

This module provides a unified command lookup interface that automatically falls
back to the login shell PATH on macOS, used by MCP connections, system prompt
construction, Chrome DevTools detection, and other scenarios.
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
    """Retrieve the full macOS user PATH via the login shell.

    Finder/Dock-launched .app only gets /usr/bin:/bin:/usr/sbin:/sbin,
    missing paths injected by tool managers like Homebrew, NVM, and Volta.
    This function runs the user's login shell once to extract the full PATH,
    with the result cached by lru_cache.

    If the login shell fails (e.g. .zshrc syntax error, init timeout),
    it falls back to the macOS path_helper reading /etc/paths and /etc/paths.d/.
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
    """Look up a command, falling back to the login shell PATH in macOS GUI environments.

    Args:
        cmd: The command name to look up.
        extra_path: Extra search path (used first, e.g. a custom PATH from MCP config).

    Returns:
        The absolute path to the command, or None if not found.
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
    """Build an environment variable dict with the full PATH for macOS subprocesses.

    Args:
        base_env: Base environment variables (e.g. env from MCP config).
                  If None or empty dict, os.environ is used as the base.

    Returns:
        An environment variable dict with the full PATH.
        Returns base_env unchanged on non-macOS or when no modification is needed.
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
# Internal implementation
# ---------------------------------------------------------------------------


def _resolve_via_login_shell() -> str | None:
    """Method 1: Retrieve PATH via the login shell (most complete, includes nvm/volta dynamic paths)."""
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
    """Method 2: Retrieve PATH via /usr/libexec/path_helper.

    Reads static configuration from /etc/paths and /etc/paths.d/.
    Does not include nvm/volta dynamic paths, but covers Homebrew paths.
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
        # path_helper output format: PATH="..."; export PATH;
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
