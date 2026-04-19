"""
CLI-Anything Handler

Control desktop applications via CLI-Anything generated CLIs:
- cli_anything_discover: scan PATH for installed cli-anything-* tools
- cli_anything_run: execute a cli-anything-<app> subcommand
- cli_anything_help: get help docs for a tool/subcommand
"""

import asyncio
import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_CMD_TIMEOUT = 60  # seconds
_CLI_PREFIX = "cli-anything-"


class CLIAnythingHandler:
    """CLI-Anything Handler — control desktop applications via CLI."""

    TOOLS = ["cli_anything_discover", "cli_anything_run", "cli_anything_help"]

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
        timeout: float = _CMD_TIMEOUT,
    ) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            return (
                proc.returncode or 0,
                stdout_bytes.decode("utf-8", errors="replace"),
                stderr_bytes.decode("utf-8", errors="replace"),
            )
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return -1, "", f"Command timed out ({timeout}s)"
        except FileNotFoundError:
            return -1, "", f"Command not found: {cmd[0]}"
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
                "No installed cli-anything tools found.\n"
                "Installation:\n"
                "1. pip install cli-anything-gimp (install from CLI-Hub)\n"
                "2. Use CLI-Anything to generate a CLI for your software\n"
                "Details: https://github.com/HKUDS/CLI-Anything"
            )

        lines = [f"Found {len(self._cache)} cli-anything tool(s):\n"]
        for item in self._cache:
            lines.append(f"- **{item['app']}** (`{item['command']}`)")
        lines.append("\nUse cli_anything_help for details, cli_anything_run to execute.")
        return "\n".join(lines)

    async def _run(self, params: dict[str, Any]) -> str:
        app = params.get("app", "").strip()
        subcommand = params.get("subcommand", "").strip()

        if not app:
            return "cli_anything_run missing required parameter 'app' (e.g. 'gimp', 'blender')."
        if not subcommand:
            return "cli_anything_run missing required parameter 'subcommand'. Use cli_anything_help first to see available subcommands."

        cmd_name = f"{_CLI_PREFIX}{app}"
        if not shutil.which(cmd_name):
            return f"{cmd_name} is not installed. Run cli_anything_discover to see installed tools."

        args = params.get("args", [])
        use_json = params.get("json_output", True)

        cmd_parts = [cmd_name] + subcommand.split()
        if isinstance(args, list):
            cmd_parts.extend(str(a) for a in args)
        if use_json and "--json" not in cmd_parts:
            cmd_parts.append("--json")

        rc, stdout, stderr = await self._run_cmd(cmd_parts)
        if rc != 0:
            error_msg = stderr.strip() or stdout.strip() or "unknown error"
            return f"{cmd_name} {subcommand} failed (exit {rc}): {error_msg}"

        if use_json and stdout.strip():
            try:
                data = json.loads(stdout)
                return json.dumps(data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "Command completed (no output)"

    async def _help(self, params: dict[str, Any]) -> str:
        app = params.get("app", "").strip()
        if not app:
            return "cli_anything_help missing required parameter 'app' (e.g. 'gimp', 'blender')."

        cmd_name = f"{_CLI_PREFIX}{app}"
        if not shutil.which(cmd_name):
            return f"{cmd_name} is not installed. Run cli_anything_discover to see installed tools."

        subcommand = params.get("subcommand", "").strip()
        cmd_parts = [cmd_name]
        if subcommand:
            cmd_parts += subcommand.split()
        cmd_parts.append("--help")

        rc, stdout, stderr = await self._run_cmd(cmd_parts)
        output = stdout.strip() or stderr.strip() or "(no help output)"
        return f"`{' '.join(cmd_parts)}` help:\n\n{output}"


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
