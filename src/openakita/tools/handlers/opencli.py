"""
OpenCLI 处理器

通过调用 opencli CLI 将网站/Electron 应用转化为结构化命令：
- opencli_list: 发现可用命令（含网站 adapter 列表）
- opencli_run: 执行命令，返回 JSON 结果
- opencli_doctor: 诊断 Browser Bridge 连通性
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
    """OpenCLI 处理器 — 复用用户 Chrome 登录态操作网站。"""

    TOOLS = ["opencli_list", "opencli_run", "opencli_doctor"]

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._opencli_path = _find_opencli()

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if not self._opencli_path:
            self._opencli_path = _find_opencli()
            if not self._opencli_path:
                return (
                    "opencli 未安装。请运行: npm install -g opencli\n"
                    "详情: https://github.com/anthropics/opencli"
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
            return -1, "", f"命令超时（{timeout}秒）"
        except FileNotFoundError:
            return -1, "", "opencli 可执行文件未找到"
        except Exception as e:
            return -1, "", str(e)

    async def _list(self, params: dict[str, Any]) -> str:
        fmt = params.get("format", "json")
        rc, stdout, stderr = await self._run_cmd(["list", "-f", fmt])
        if rc != 0:
            return f"opencli list 失败 (exit {rc}): {stderr or stdout}"

        if fmt == "json":
            try:
                data = json.loads(stdout)
                if isinstance(data, list):
                    lines = [f"共 {len(data)} 个可用命令：\n"]
                    for item in data:
                        name = item.get("name", item.get("command", "?"))
                        desc = item.get("description", "")
                        lines.append(f"- **{name}**: {desc}")
                    return "\n".join(lines)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "（无输出）"

    async def _run(self, params: dict[str, Any]) -> str:
        command = params.get("command", "").strip()
        if not command:
            return "opencli_run 缺少必要参数 'command'。"

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
            error_msg = stderr.strip() or stdout.strip() or "未知错误"
            return f"opencli 命令失败 (exit {rc}): {error_msg}"

        if use_json and stdout.strip():
            try:
                data = json.loads(stdout)
                return json.dumps(data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        return stdout.strip() or "命令执行完成（无输出）"

    async def _doctor(self, params: dict[str, Any]) -> str:
        live = params.get("live", False)
        cmd_args = ["doctor"]
        if live:
            cmd_args.append("--live")

        rc, stdout, stderr = await self._run_cmd(cmd_args)
        output = stdout.strip() or stderr.strip() or "（无输出）"
        if rc != 0:
            return f"opencli doctor 诊断发现问题 (exit {rc}):\n{output}"
        return f"opencli 环境诊断:\n{output}"


def is_available() -> bool:
    """Check if opencli is installed (fast, no subprocess)."""
    return _find_opencli() is not None


def create_handler(agent: "Agent"):
    handler = OpenCLIHandler(agent)
    return handler.handle
