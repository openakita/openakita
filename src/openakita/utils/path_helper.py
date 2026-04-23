"""
Cross-platform command lookup utility.

GUI/service launched processes (macOS Finder-launched .app, Linux systemd
services) only inherit a minimum PATH that lacks paths injected by tool
managers like Homebrew, NVM, Volta, pnpm, pyenv, asdf, or ~/.local/bin.

Example — systemd-launched openakita on Linux sees:
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin
…which hides the user's `claude`, `codex`, etc. installed under
~/.nvm/versions/node/<v>/bin — even though `which claude` succeeds in
an interactive terminal.

This module provides a unified command lookup that falls back to the
user's login shell PATH to recover those binaries. The login shell reads
the user's shell init (.bashrc/.zshrc/.profile) which sources nvm/volta/
asdf, producing the same PATH the interactive terminal sees.
"""

import functools
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def resolve_login_shell_path() -> str | None:
    """Retrieve the user's full PATH via the login shell.

    Works on macOS and Linux. The login shell sources the user's shell
    init file, which typically loads nvm/volta/asdf and adds their bin
    directories to PATH. The macOS-only `/usr/libexec/path_helper` fallback
    (reads /etc/paths and /etc/paths.d/) is tried only when the login shell
    fails — it's a macOS tool and does not exist on Linux.

    Cached via lru_cache so the login shell only runs once per process.
    Returns None on timeout, non-zero exit, or an empty PATH.
    """
    path = _resolve_via_login_shell()
    if path:
        return path

    if sys.platform == "darwin":
        path = _resolve_via_path_helper()
        if path:
            return path

    logger.warning(
        "[PATH] All PATH resolution methods failed. Commands installed "
        "via nvm/volta/homebrew may not be found."
    )
    return None


# Deprecated alias — kept so external callers (shell.py, mcp.py, third-party
# plugins) don't break. New code should use resolve_login_shell_path.
resolve_macos_login_shell_path = resolve_login_shell_path


def which_command(cmd: str, extra_path: str | None = None) -> str | None:
    """Look up a command, falling back to the login shell PATH when the
    process PATH is missing tool-manager entries.

    Args:
        cmd: The command name to look up.
        extra_path: Extra search path (used first, e.g. a custom PATH from
            MCP config). When provided, the caller is being explicit and we
            skip login-shell enrichment.

    Returns:
        Absolute path to the command, or None if not found.
    """
    found = shutil.which(cmd, path=extra_path)
    if found:
        return found

    if extra_path:
        return None

    shell_path = resolve_login_shell_path()
    if shell_path:
        return shutil.which(cmd, path=shell_path)

    return None


def get_enriched_env(base_env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Return an env dict whose PATH is enriched via the login shell.

    Used by callers that spawn subprocesses (MCP servers, external CLI
    adapters) so the child process inherits the user's full PATH.

    Args:
        base_env: Base env to merge over. If None/empty, os.environ is used.

    Returns:
        An env dict with PATH merged from the login shell. If enrichment is
        not available (e.g. login shell failed) returns `base_env` unchanged
        — callers never get None back when they passed a dict in.
    """
    shell_path = resolve_login_shell_path()
    if not shell_path:
        return base_env

    if not base_env:
        return {**os.environ, "PATH": shell_path}

    if "PATH" in base_env or "Path" in base_env:
        existing = base_env.get("PATH") or base_env.get("Path") or ""
        merged = _merge_paths(existing, shell_path)
        return {**base_env, "PATH": merged}

    return {**base_env, "PATH": shell_path}


# Deprecated alias — kept so external callers don't break.
get_macos_enriched_env = get_enriched_env


def _merge_paths(primary: str, secondary: str) -> str:
    """Append secondary PATH entries not already present in primary."""
    if not primary:
        return secondary
    if not secondary:
        return primary
    seen: set[str] = set()
    result: list[str] = []
    for entry in primary.split(os.pathsep) + secondary.split(os.pathsep):
        entry = entry.strip()
        if entry and entry not in seen:
            seen.add(entry)
            result.append(entry)
    return os.pathsep.join(result)


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _default_login_shell() -> str:
    """Choose a sensible default login shell when $SHELL is unset."""
    shell = os.environ.get("SHELL")
    if shell:
        return shell
    # systemd services frequently have SHELL unset. Default to bash on Linux,
    # zsh on modern macOS; both are near-universally present.
    if sys.platform == "darwin":
        return "/bin/zsh"
    return "/bin/bash"


def _resolve_via_login_shell() -> str | None:
    """Method 1: retrieve PATH via the login shell.

    Most complete path — picks up nvm/volta/asdf dynamic PATH mutations
    performed in the user's shell init.

    On Linux, `.bashrc` usually has an early return when $- does not
    contain 'i' (non-interactive), so `bash -l` alone does NOT source
    nvm/volta/asdf. We force interactive mode via `-i` on Linux; the
    "cannot set terminal process group / no job control" stderr noise
    is harmless and ignored.
    """
    shell = _default_login_shell()
    # -l: login shell (sources .profile/.zprofile)
    # -i: interactive (forces .bashrc/.zshrc to run past their non-
    #     interactive early-return guards)
    # -c: run the sentinel printf and exit
    flags = "-lic" if sys.platform != "darwin" else "-lc"
    try:
        proc = subprocess.run(
            [shell, flags, 'printf "\\n__AKITA_PATH__\\n%s\\n__AKITA_PATH__\\n" "$PATH"'],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            logger.warning(
                "[PATH] Login shell exited with code %d (shell=%s). stderr: %s",
                proc.returncode,
                shell,
                (proc.stderr or "").strip()[:500],
            )
            return None
        parts = proc.stdout.split("__AKITA_PATH__")
        if len(parts) < 3:
            logger.warning(
                "[PATH] Login shell output missing path markers (shell=%s, stdout length=%d)",
                shell,
                len(proc.stdout),
            )
            return None
        path = parts[1].strip()
        if not path:
            logger.warning("[PATH] Login shell returned empty PATH (shell=%s)", shell)
            return None
        logger.info("[PATH] Resolved PATH via login shell (%d entries)", path.count(os.pathsep) + 1)
        logger.debug("[PATH] Login shell PATH: %s", path)
        return path
    except subprocess.TimeoutExpired:
        logger.warning(
            "[PATH] Login shell timed out after 10s (shell=%s). "
            "Shell config (.zshrc/.bashrc) may be slow to initialize.",
            shell,
        )
    except FileNotFoundError:
        logger.warning("[PATH] Login shell not found: %s", shell)
    except Exception as e:
        logger.warning("[PATH] Failed to run login shell: %s", e)
    return None


def _resolve_via_path_helper() -> str | None:
    """Method 2 (macOS only): retrieve PATH via /usr/libexec/path_helper.

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
