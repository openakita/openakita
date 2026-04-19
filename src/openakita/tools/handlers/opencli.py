"""
OpenCLI handler

Transforms websites/Electron apps into structured commands via the opencli CLI:
- opencli_list: discover available commands (including website adapter list)
- opencli_run: execute a command, returns JSON result
- opencli_doctor: diagnose Browser Bridge connectivity
"""

import asyncio
import json
import logging
import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_OPENCLI_CMD_TIMEOUT = 60  # seconds
_OPENCLI_TASK_TIMEOUT = 120  # seconds for run commands


def _find_opencli() -> str | None:
    """Return the path to the opencli executable, or None if not found."""
    return shutil.which("opencli")


class OpenCLIHandler:
    """OpenCLI handler — operates websites using the user's Chrome login session."""

    TOOLS = ["opencli_list", "opencli_run", "opencli_doctor"]

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._opencli_path = _find_opencli()

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if not self._opencli_path:
            self._opencli_path = _find_opencli()
            if not self._opencli_path:
                return (
                    "opencli is not installed. Please run: npm install -g opencli\n"
                    "Details: https://github.com/anthropics/opencli"
                )

        if tool_name == "opencli_list":
            return await self._list(params)
        elif tool_name == "opencli_run":
            return await self._run(params)
        elif tool_name == "opencli_doctor":
            return await self._doctor(params)
        return f"Unknown opencli tool: {tool_name}"

    async def _run_cmd(
        self, args: list[str], timeout: float = _OPENCLI_CMD_TIMEOUT
    ) -> tuple[int, str, str]:
        """Execute opencli with given args, return (returncode, stdout, stderr)."""
        cmd = [self._opencli_path or "opencli"] + args
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
            return -1, "", f"command timed out ({timeout}s)"
        except FileNotFoundError:
            return -1, "", "opencli executable not found"
        except Exception as e:
            return -1, "", str(e)

    async def _list(self, params: dict[str, Any]) -> str:
        fmt = params.get("format", "json")
        rc, stdout, stderr = await self._run_cmd(["list", "-f", fmt])
        if rc != 0:
            return f"opencli list failed (exit {rc}): {stderr or stdout}"

        if fmt == "json":
            try:
                data = json.loads(stdout)
                if isinstance(data, list):
                    lines = [f"{len(data)} available commands:\n"]
                    for item in data:
                        name = item.get("name", item.get("command", "?"))
                        desc = item.get("description", "")
                        lines.append(f"- **{name}**: {desc}")
                    return "\n".join(lines)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "(no output)"

    async def _run(self, params: dict[str, Any]) -> str:
        command = params.get("command", "").strip()
        if not command:
            return "opencli_run missing required parameter 'command'."

        args_list = params.get("args", [])
        use_json = params.get("json_output", True)

        cmd_parts = command.split()
        if isinstance(args_list, list):
            cmd_parts.extend(str(a) for a in args_list)
        if use_json and "--json" not in cmd_parts:
            cmd_parts.append("--json")

        rc, stdout, stderr = await self._run_cmd(
            cmd_parts,
            timeout=_OPENCLI_TASK_TIMEOUT,
        )
        if rc != 0:
            error_msg = stderr.strip() or stdout.strip() or "unknown error"
            return f"opencli command failed (exit {rc}): {error_msg}"

        if use_json and stdout.strip():
            try:
                data = json.loads(stdout)
                return json.dumps(data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "command completed (no output)"

    async def _doctor(self, params: dict[str, Any]) -> str:
        live = params.get("live", False)
        cmd_args = ["doctor"]
        if live:
            cmd_args.append("--live")

        rc, stdout, stderr = await self._run_cmd(cmd_args)
        output = stdout.strip() or stderr.strip() or "(no output)"
        if rc != 0:
            return f"opencli doctor found issues (exit {rc}):\n{output}"
        return f"opencli environment diagnostics:\n{output}"


def is_available() -> bool:
    """Check if opencli is installed (fast, no subprocess)."""
    return _find_opencli() is not None


def create_handler(agent: "Agent"):
    handler = OpenCLIHandler(agent)
    return handler.handle
