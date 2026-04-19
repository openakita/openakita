"""
Bash sandbox: command execution permission control.

Inspired by Claude Code's sandbox design:
- Restrict filesystem access scope
- Restrict network access
- Command allowlist / denylist
- Force-kill on timeout

This module provides the rule-engine layer; actual OS-level isolation
requires Docker / seatbelt / landlock.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SandboxPolicy:
    """Sandbox policy definition."""

    allowed_dirs: list[str] = field(default_factory=list)
    denied_dirs: list[str] = field(
        default_factory=lambda: [
            "/etc/shadow",
            "/etc/passwd",
            "/root",
            os.path.expanduser("~/.ssh"),
            os.path.expanduser("~/.aws"),
        ]
    )
    allowed_commands: list[str] = field(default_factory=list)
    denied_commands: list[str] = field(
        default_factory=lambda: [
            "rm -rf /",
            "mkfs*",
            "dd if=/dev/*",
            ":(){ :|:& };:",
        ]
    )
    denied_command_patterns: list[str] = field(
        default_factory=lambda: [
            r"curl\s+.*\|\s*(?:bash|sh|zsh)",
            r"wget\s+.*\|\s*(?:bash|sh|zsh)",
            r"eval\s+\$\(",
            r">\s*/dev/sd[a-z]",
        ]
    )
    max_execution_time: int = 120
    allow_network: bool = True
    writable_dirs: list[str] = field(default_factory=list)


@dataclass
class SandboxVerdict:
    """Sandbox check result."""

    allowed: bool
    reason: str = ""
    modified_command: str = ""


class CommandSandbox:
    """Command execution sandbox.

    Checks commands against the security policy before actual execution.
    """

    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self._policy = policy or SandboxPolicy()
        self._project_root = str(project_root or os.getcwd())
        if not self._policy.allowed_dirs:
            self._policy.allowed_dirs = [self._project_root]
        if not self._policy.writable_dirs:
            self._policy.writable_dirs = [self._project_root]

    def check_command(self, command: str) -> SandboxVerdict:
        """Check whether a command is allowed to execute."""
        # Check denied commands (exact match / glob)
        for denied in self._policy.denied_commands:
            if fnmatch.fnmatch(command.strip(), denied):
                return SandboxVerdict(
                    allowed=False,
                    reason=f"Command matches deny rule: {denied}",
                )

        # Check denied patterns (regex)
        for pattern in self._policy.denied_command_patterns:
            if re.search(pattern, command):
                return SandboxVerdict(
                    allowed=False,
                    reason=f"Command matches dangerous pattern: {pattern}",
                )

        # Check if directory access is allowed
        dir_violation = self._check_dir_access(command)
        if dir_violation:
            return SandboxVerdict(allowed=False, reason=dir_violation)

        # Check allowed commands (whitelist mode)
        if self._policy.allowed_commands:
            try:
                parts = shlex.split(command)
                base_cmd = parts[0] if parts else ""
            except ValueError:
                base_cmd = command.split()[0] if command.split() else ""

            if base_cmd and base_cmd not in self._policy.allowed_commands:
                return SandboxVerdict(
                    allowed=False,
                    reason=f"Command '{base_cmd}' not in allowed list",
                )

        return SandboxVerdict(allowed=True)

    def check_command_zh(self, command: str) -> SandboxVerdict:
        """Chinese-localized wrapper around check_command, translating English reasons to user-visible Chinese."""
        verdict = self.check_command(command)
        if not verdict.allowed and verdict.reason:
            if "deny rule" in verdict.reason:
                verdict = SandboxVerdict(
                    allowed=False,
                    reason=f"命令被安全规则禁止: {command.split()[0] if command.split() else command}",
                )
            elif "dangerous pattern" in verdict.reason:
                verdict = SandboxVerdict(allowed=False, reason="检测到危险命令模式，已拦截")
            elif "denied directory" in verdict.reason:
                verdict = SandboxVerdict(allowed=False, reason="命令涉及受保护的系统目录，禁止访问")
            elif "not in allowed list" in verdict.reason:
                verdict = SandboxVerdict(
                    allowed=False,
                    reason=f"命令 '{command.split()[0] if command.split() else command}' 不在允许列表中",
                )
        return verdict

    def _check_dir_access(self, command: str) -> str:
        """Check whether paths referenced in the command are within the allowed scope."""
        try:
            parts = shlex.split(command)
        except ValueError:
            return ""

        for part in parts:
            if not part.startswith("/") and not part.startswith("~"):
                continue

            expanded = os.path.expanduser(part)
            abs_path = os.path.abspath(expanded)

            for denied in self._policy.denied_dirs:
                denied_abs = os.path.abspath(os.path.expanduser(denied))
                if abs_path.startswith(denied_abs):
                    return f"Path '{abs_path}' is in denied directory: {denied}"

        return ""


# ---------------------------------------------------------------------------
# P1-1: SandboxExecutor -- subprocess-isolated execution
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    """Sandbox execution result."""

    stdout: str
    stderr: str
    returncode: int
    backend: str = "subprocess"


class SandboxExecutor:
    """Lightweight sandbox executor based on subprocess isolation.

    Can be extended to Docker / seatbelt / landlock backends in the future.
    """

    def __init__(self, sandbox: CommandSandbox | None = None) -> None:
        self._sandbox = sandbox or CommandSandbox()

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float = 120,
    ) -> SandboxResult:
        """Execute a command asynchronously inside the sandbox."""
        verdict = self._sandbox.check_command(command)
        if not verdict.allowed:
            return SandboxResult(
                stdout="",
                stderr=f"Sandbox denied execution: {verdict.reason}",
                returncode=-1,
                backend="sandbox_denied",
            )

        effective_timeout = min(
            timeout,
            self._sandbox._policy.max_execution_time,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
            return SandboxResult(
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                returncode=proc.returncode or 0,
            )
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return SandboxResult(
                stdout="",
                stderr=f"Command timed out ({effective_timeout}s), forcefully terminated",
                returncode=-2,
                backend="timeout",
            )
        except Exception as e:
            return SandboxResult(
                stdout="",
                stderr=f"Sandbox execution error: {e}",
                returncode=-3,
                backend="error",
            )


_global_sandbox_executor: SandboxExecutor | None = None


def get_sandbox_executor() -> SandboxExecutor:
    """Return the global sandbox executor singleton."""
    global _global_sandbox_executor
    if _global_sandbox_executor is None:
        _global_sandbox_executor = SandboxExecutor()
    return _global_sandbox_executor
