"""
PowerShell 工具处理器

独立的 PowerShell 执行处理器，参考 CC PowerShellTool 设计：
- PS 版本检测（Desktop 5.1 vs Core 7+）
- 版本感知的语法指导注入
- EncodedCommand 沙箱执行
- 只读 cmdlet 识别
"""

import asyncio
import base64
import logging
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# PS 版本检测结果缓存
_ps_version_cache: dict[str, Any] | None = None

# 只读 cmdlet 前缀（参考 CC readOnlyValidation.ts CMDLET_ALLOWLIST）
READ_ONLY_PREFIXES = frozenset({
    "Get-", "Test-", "Resolve-", "Select-", "Where-",
    "Format-", "Measure-", "Compare-", "Find-", "Show-",
    "ConvertTo-", "ConvertFrom-",
})

# 只读 cmdlet 完全匹配
READ_ONLY_EXACT = frozenset({
    "Where-Object", "ForEach-Object", "Sort-Object", "Group-Object",
    "Measure-Object", "Select-Object", "Out-String", "Out-Null",
    "Write-Output", "Write-Host", "Write-Verbose", "Write-Debug",
})


def detect_ps_version() -> dict[str, Any]:
    """检测系统 PowerShell 版本信息。

    返回格式:
        {
            "available": bool,
            "edition": "Core" | "Desktop" | None,
            "version": "7.4.1" | "5.1.19041.4046" | None,
            "major": 7 | 5 | 0,
            "executable": "pwsh" | "powershell" | None,
        }
    """
    global _ps_version_cache
    if _ps_version_cache is not None:
        return _ps_version_cache

    result: dict[str, Any] = {
        "available": False,
        "edition": None,
        "version": None,
        "major": 0,
        "executable": None,
    }

    # 优先检测 PowerShell Core (pwsh)
    for exe in ("pwsh", "powershell"):
        path = shutil.which(exe)
        if not path:
            continue
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command",
                 "$PSVersionTable.PSVersion.ToString() + '|' + $PSVersionTable.PSEdition"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts = proc.stdout.strip().split("|")
                ver_str = parts[0].strip()
                edition = parts[1].strip() if len(parts) > 1 else ("Core" if exe == "pwsh" else "Desktop")
                major = int(ver_str.split(".")[0]) if ver_str else 0

                result = {
                    "available": True,
                    "edition": edition,
                    "version": ver_str,
                    "major": major,
                    "executable": exe,
                }
                break
        except Exception as e:
            logger.debug(f"PS version detection failed for {exe}: {e}")
            continue

    _ps_version_cache = result
    if result["available"]:
        logger.info(
            f"[PowerShell] Detected: {result['edition']} {result['version']} "
            f"(exe: {result['executable']})"
        )
    else:
        logger.info("[PowerShell] Not available on this system")

    return result


def get_ps_syntax_guidance() -> str:
    """根据 PS 版本生成语法指导（参考 CC prompt.ts getEditionSection）。"""
    info = detect_ps_version()
    if not info["available"]:
        return ""

    if info["edition"] == "Core" and info["major"] >= 7:
        return (
            "PowerShell Core 7+ detected. Modern syntax available:\n"
            "- `&&` and `||` operators are supported\n"
            "- Default encoding is UTF-8\n"
            "- Use `$null = ...` to suppress output\n"
            "- Ternary operator: `$x ? 'yes' : 'no'`\n"
            "- Null-coalescing: `$x ?? 'default'`\n"
            "- Pipeline chain: `cmd1 && cmd2`"
        )
    else:
        return (
            "PowerShell Desktop 5.1 detected. Syntax notes:\n"
            "- `&&` is NOT supported — use semicolons or `; if ($?) { ... }`\n"
            "- Default encoding is Windows-1252, not UTF-8\n"
            "- No ternary operator — use `if ($x) {'yes'} else {'no'}`\n"
            "- No null-coalescing — use `if ($null -eq $x) {'default'} else {$x}`\n"
            "- To chain commands: `cmd1; if ($LASTEXITCODE -eq 0) { cmd2 }`"
        )


def is_read_only_command(command: str) -> bool:
    """判断 PowerShell 命令是否为只读操作（参考 CC isReadOnlyCommand）。"""
    stripped = command.strip()
    if not stripped:
        return False

    first_token = stripped.split()[0].split("|")[0].strip()

    if first_token in READ_ONLY_EXACT:
        return True

    return any(first_token.startswith(prefix) for prefix in READ_ONLY_PREFIXES)


class PowerShellHandler:
    """PowerShell 工具处理器"""

    TOOLS = ["run_powershell"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "run_powershell":
            return await self._run_powershell(params)
        return f"Unknown PowerShell tool: {tool_name}"

    async def _run_powershell(self, params: dict[str, Any]) -> str:
        command = params.get("command", "").strip()
        if not command:
            return "run_powershell requires a 'command' parameter."

        ps_info = detect_ps_version()
        if not ps_info["available"]:
            return (
                "PowerShell is not available on this system.\n"
                "Use run_shell for regular shell commands."
            )

        working_dir = params.get("working_directory")
        try:
            timeout = max(10, min(int(params.get("timeout", 120)), 600))
        except (TypeError, ValueError):
            timeout = 120

        exe = ps_info["executable"]

        utf8_preamble = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        )
        full_command = utf8_preamble + command
        encoded = base64.b64encode(full_command.encode("utf-16-le")).decode("ascii")

        cmd_args = [
            exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
        ]

        cwd = working_dir or getattr(self.agent, "default_cwd", None) or os.getcwd()

        env = os.environ.copy()
        try:
            from ...runtime_env import IS_FROZEN, get_python_executable
            if IS_FROZEN:
                _ext_py = get_python_executable()
                if _ext_py:
                    from pathlib import Path
                    _py_dir = str(Path(_ext_py).parent)
                    env["PATH"] = _py_dir + os.pathsep + env.get("PATH", "")
        except Exception:
            pass

        logger.info(f"[PowerShell] Executing: {command[:300]}")

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout,
            )

            stdout = self._decode(stdout_bytes)
            stderr = self._decode(stderr_bytes)
            rc = process.returncode or 0

            logger.info(f"[PowerShell] Exit code: {rc}")

            if rc == 0:
                output = stdout
                if stderr:
                    output += f"\n[warnings]:\n{stderr}"
                return f"PowerShell command succeeded (exit code: 0):\n{output}"
            else:
                parts = [f"PowerShell command failed (exit code: {rc})"]
                if stdout:
                    parts.append(f"[stdout]:\n{stdout}")
                if stderr:
                    parts.append(f"[stderr]:\n{stderr}")
                return "\n".join(parts)

        except TimeoutError:
            logger.error(f"[PowerShell] Command timed out after {timeout}s")
            if process and process.returncode is None:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    pass
            return f"PowerShell command timed out after {timeout} seconds."

        except Exception as e:
            logger.error(f"[PowerShell] Execution error: {e}")
            return f"PowerShell execution error: {e}"

    @staticmethod
    def _decode(data: bytes) -> str:
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                import ctypes
                oem_cp = ctypes.windll.kernel32.GetOEMCP()
                return data.decode(f"cp{oem_cp}", errors="replace")
            except Exception:
                return data.decode("utf-8", errors="replace")


def create_handler(agent: "Agent"):
    handler = PowerShellHandler(agent)
    return handler.handle
