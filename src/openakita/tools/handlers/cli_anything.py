"""
CLI-Anything 处理器

通过 CLI-Anything 生成的 CLI 控制桌面软件：
- cli_anything_discover: 扫描 PATH 中已安装的 cli-anything-* 工具
- cli_anything_run: 执行 cli-anything-<app> 子命令
- cli_anything_help: 获取工具/子命令的帮助文档
"""

import asyncio
import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_CMD_TIMEOUT = 300  # fallback seconds; runtime value comes from settings
_CLI_PREFIX = "cli-anything-"


class CLIAnythingHandler:
    """CLI-Anything 处理器 — 通过 CLI 控制桌面软件。"""

    TOOLS = ["cli_anything_discover", "cli_anything_run", "cli_anything_help"]
    TOOL_CLASSES = {
        "cli_anything_discover": ApprovalClass.READONLY_GLOBAL,
        "cli_anything_run": ApprovalClass.EXEC_CAPABLE,
        "cli_anything_help": ApprovalClass.READONLY_GLOBAL,
    }

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._cache: list[dict[str, str]] | None = None

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "cli_anything_discover":
            return await self._discover(params)
        elif tool_name == "cli_anything_run":
            return await self._run(params)
        elif tool_name == "cli_anything_help":
            return await self._help(params)
        return f"Unknown cli_anything tool: {tool_name}"

    async def _run_cmd(
        self,
        cmd: list[str],
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        if timeout is None:
            timeout = float(getattr(settings, "cli_command_timeout_seconds", _CMD_TIMEOUT) or 0)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            communicate = proc.communicate()
            if timeout > 0:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    communicate,
                    timeout=timeout,
                )
            else:
                stdout_bytes, stderr_bytes = await communicate
            return (
                proc.returncode or 0,
                stdout_bytes.decode("utf-8", errors="replace"),
                stderr_bytes.decode("utf-8", errors="replace"),
            )
        except TimeoutError:
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return -1, "", f"命令超时（{timeout}秒）"
        except FileNotFoundError:
            return -1, "", f"命令未找到: {cmd[0]}"
        except Exception as e:
            return -1, "", str(e)

    def _scan_installed(self) -> list[dict[str, str]]:
        """Scan PATH for cli-anything-* executables."""
        found: list[dict[str, str]] = []
        seen: set[str] = set()
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)

        for d in path_dirs:
            try:
                if not os.path.isdir(d):
                    continue
                for entry in os.listdir(d):
                    lower = entry.lower()
                    if lower.startswith(_CLI_PREFIX) and lower not in seen:
                        full_path = os.path.join(d, entry)
                        if os.access(full_path, os.X_OK) or (
                            os.name == "nt"
                            and any(lower.endswith(ext) for ext in (".exe", ".cmd", ".bat", ".ps1"))
                        ):
                            app_name = entry
                            for ext in (".exe", ".cmd", ".bat", ".ps1"):
                                if app_name.lower().endswith(ext):
                                    app_name = app_name[: -len(ext)]
                                    break
                            app_short = app_name[len(_CLI_PREFIX) :]
                            seen.add(lower)
                            found.append(
                                {
                                    "command": app_name,
                                    "app": app_short,
                                    "path": full_path,
                                }
                            )
            except OSError:
                continue

        return found

    async def _discover(self, params: dict[str, Any]) -> str:
        refresh = params.get("refresh", False)
        if self._cache is None or refresh:
            self._cache = await asyncio.to_thread(self._scan_installed)

        if not self._cache:
            return (
                "未发现已安装的 cli-anything 工具。\n"
                "安装方式：\n"
                "1. pip install cli-anything-gimp（从 CLI-Hub 安装）\n"
                "2. 使用 CLI-Anything 为你的软件生成 CLI\n"
                "详情: https://github.com/HKUDS/CLI-Anything"
            )

        lines = [f"发现 {len(self._cache)} 个 cli-anything 工具：\n"]
        for item in self._cache:
            lines.append(f"- **{item['app']}** (`{item['command']}`)")
        lines.append("\n使用 cli_anything_help 查看具体命令，cli_anything_run 执行。")
        return "\n".join(lines)

    async def _run(self, params: dict[str, Any]) -> str:
        app = params.get("app", "").strip()
        subcommand = params.get("subcommand", "").strip()

        if not app:
            return "cli_anything_run 缺少必要参数 'app'（如 'gimp', 'blender'）。"
        if not subcommand:
            return "cli_anything_run 缺少必要参数 'subcommand'。请先用 cli_anything_help 查看可用子命令。"

        cmd_name = f"{_CLI_PREFIX}{app}"
        if not shutil.which(cmd_name):
            return f"{cmd_name} 未安装。运行 cli_anything_discover 查看已安装的工具。"

        args = params.get("args", [])
        use_json = params.get("json_output", True)

        cmd_parts = [cmd_name] + subcommand.split()
        if isinstance(args, list):
            cmd_parts.extend(str(a) for a in args)
        if use_json and "--json" not in cmd_parts:
            cmd_parts.append("--json")

        rc, stdout, stderr = await self._run_cmd(cmd_parts)
        if rc != 0:
            error_msg = stderr.strip() or stdout.strip() or "未知错误"
            return f"{cmd_name} {subcommand} 失败 (exit {rc}): {error_msg}"

        if use_json and stdout.strip():
            try:
                data = json.loads(stdout)
                return json.dumps(data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "命令执行完成（无输出）"

    async def _help(self, params: dict[str, Any]) -> str:
        app = params.get("app", "").strip()
        if not app:
            return "cli_anything_help 缺少必要参数 'app'（如 'gimp', 'blender'）。"

        cmd_name = f"{_CLI_PREFIX}{app}"
        if not shutil.which(cmd_name):
            return f"{cmd_name} 未安装。运行 cli_anything_discover 查看已安装的工具。"

        subcommand = params.get("subcommand", "").strip()
        cmd_parts = [cmd_name]
        if subcommand:
            cmd_parts += subcommand.split()
        cmd_parts.append("--help")

        rc, stdout, stderr = await self._run_cmd(cmd_parts)
        output = stdout.strip() or stderr.strip() or "（无帮助输出）"
        return f"`{' '.join(cmd_parts)}` 帮助文档:\n\n{output}"


def is_available() -> bool:
    """Check if any cli-anything-* tools are installed."""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        try:
            if not os.path.isdir(d):
                continue
            for entry in os.listdir(d):
                if entry.lower().startswith(_CLI_PREFIX):
                    return True
        except OSError:
            continue
    return False


def create_handler(agent: "Agent"):
    handler = CLIAnythingHandler(agent)
    return handler.handle
