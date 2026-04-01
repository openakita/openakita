"""
Bash 沙箱: 命令执行权限控制

参考 Claude Code 的 sandbox 设计:
- 限制文件系统访问范围
- 限制网络访问
- 命令白名单/黑名单
- 超时强制终止

此模块为规则引擎部分，实际 OS 级隔离需配合 Docker/seatbelt/landlock。
"""

from __future__ import annotations

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
    """沙箱策略定义"""

    allowed_dirs: list[str] = field(default_factory=list)
    denied_dirs: list[str] = field(default_factory=lambda: [
        "/etc/shadow", "/etc/passwd", "/root",
        os.path.expanduser("~/.ssh"),
        os.path.expanduser("~/.aws"),
    ])
    allowed_commands: list[str] = field(default_factory=list)
    denied_commands: list[str] = field(default_factory=lambda: [
        "rm -rf /",
        "mkfs*",
        "dd if=/dev/*",
        ":(){ :|:& };:",
    ])
    denied_command_patterns: list[str] = field(default_factory=lambda: [
        r"curl\s+.*\|\s*(?:bash|sh|zsh)",
        r"wget\s+.*\|\s*(?:bash|sh|zsh)",
        r"eval\s+\$\(",
        r">\s*/dev/sd[a-z]",
    ])
    max_execution_time: int = 120
    allow_network: bool = True
    writable_dirs: list[str] = field(default_factory=list)


@dataclass
class SandboxVerdict:
    """沙箱检查结果"""

    allowed: bool
    reason: str = ""
    modified_command: str = ""


class CommandSandbox:
    """命令执行沙箱。

    在实际执行命令前，检查是否符合安全策略。
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
        """检查命令是否被允许执行。"""
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

    def _check_dir_access(self, command: str) -> str:
        """检查命令中引用的路径是否在允许范围内。"""
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
