"""
Shell 工具 - 执行系统命令
增强版：支持 Windows PowerShell 命令自动转换

PowerShell 转义策略：
  使用 -EncodedCommand (Base64 UTF-16LE) 传递命令，
  彻底绕过 cmd.exe → PowerShell 的多层引号/特殊字符转义问题。
"""

import asyncio
import base64
import logging
import os
import platform
import re
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """命令执行结果"""

    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        """合并输出"""
        return self.stdout + (f"\n{self.stderr}" if self.stderr else "")


class ShellTool:
    """Shell 工具 - 执行系统命令"""

    # ------------------------------------------------------------------
    # PowerShell cmdlet 显式白名单（大小写不敏感匹配）
    # ------------------------------------------------------------------
    POWERSHELL_PATTERNS = [
        # 原有
        r"Get-EventLog", r"Get-ScheduledTask",
        r"ConvertFrom-Csv", r"ConvertTo-Csv",
        r"Select-Object", r"Where-Object", r"ForEach-Object",
        r"Import-Module", r"Get-Process", r"Get-Service",
        r"Get-ChildItem", r"Set-ExecutionPolicy",
        # 新增常见 cmdlet
        r"Sort-Object", r"Out-File", r"Out-String",
        r"Invoke-WebRequest", r"Invoke-RestMethod",
        r"Test-Path", r"New-Item", r"Remove-Item", r"Copy-Item", r"Move-Item",
        r"Measure-Object", r"Group-Object",
        r"ConvertTo-Json", r"ConvertFrom-Json",
        r"Write-Output", r"Write-Host", r"Write-Error",
        r"Get-Content", r"Set-Content", r"Add-Content",
        r"Get-ItemProperty", r"Set-ItemProperty",
        r"Start-Process", r"Stop-Process",
        r"Get-WmiObject", r"Get-CimInstance",
        r"New-Object", r"Add-Type",
    ]

    # 通用 Verb-Noun 模式：PowerShell cmdlet 格式为 Verb-Noun（如 Get-Item, Test-Path）
    # 匹配常见 approved verbs 开头 + 连字符 + 大写字母开头的名词
    _VERB_NOUN_RE = re.compile(
        r"\b(?:Get|Set|New|Remove|Add|Clear|Copy|Move|Test|Start|Stop|Restart|"
        r"Import|Export|ConvertTo|ConvertFrom|Invoke|Select|Where|ForEach|"
        r"Sort|Group|Measure|Write|Read|Out|Format|Enter|Exit|Enable|Disable|"
        r"Register|Unregister|Update|Find|Save|Show|Hide|Protect|Unprotect|"
        r"Wait|Watch|Assert|Confirm|Compare|Expand|Join|Split|Merge|Resolve|"
        r"Push|Pop|Rename|Reset|Resume|Suspend|Switch|Undo|Use"
        r")-[A-Z][A-Za-z]+",
    )

    def __init__(
        self,
        default_cwd: str | None = None,
        timeout: int = 60,
        shell: bool = True,
    ):
        self.default_cwd = default_cwd or os.getcwd()
        self.timeout = timeout
        self.shell = shell
        self._is_windows = platform.system() == "Windows"

    # ------------------------------------------------------------------
    # PowerShell 检测 & 编码
    # ------------------------------------------------------------------

    def _needs_powershell(self, command: str) -> bool:
        """检查命令是否需要 PowerShell 执行"""
        if not self._is_windows:
            return False

        # 如果 LLM 已经显式写了 powershell/pwsh 前缀，也需要走编码路径
        stripped = command.strip().lower()
        if stripped.startswith(("powershell", "pwsh")):
            return True

        # 1) 白名单精确匹配
        for pattern in self.POWERSHELL_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True

        # 2) 通用 Verb-Noun cmdlet 模式
        if self._VERB_NOUN_RE.search(command):
            return True

        return False

    @staticmethod
    def _encode_for_powershell(command: str) -> str:
        """
        将 PowerShell 命令编码为 -EncodedCommand 格式。

        PowerShell -EncodedCommand 接受 UTF-16LE Base64 编码的字符串，
        完全绕过 cmd.exe 的引号和特殊字符解析。
        """
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"

    @staticmethod
    def _extract_ps_inner_command(command: str) -> str | None:
        """
        从 'powershell -Command "..."' 或 'pwsh -Command "..."' 格式中
        安全提取内部命令字符串。

        Returns:
            提取到的内部命令，或 None（无法安全提取时）。
        """
        # 尝试匹配 powershell/pwsh ... -Command "内容" 或 powershell/pwsh ... -Command '内容'
        # 也处理 -Command {脚本块} 的情况
        m = re.match(
            r"^(?:powershell|pwsh)(?:\.exe)?"       # powershell 或 pwsh
            r"(?:\s+-\w+)*"                          # 可选参数如 -NoProfile
            r"\s+-Command\s+"                        # -Command
            r"(?:"
            r'"((?:[^"\\]|\\.)*)"|'                  # "双引号内容"
            r"'((?:[^'\\]|\\.)*)'|"                  # '单引号内容'
            r"\{(.*)\}|"                             # {脚本块}
            r"(.+)"                                  # 无引号直接跟内容
            r")\s*$",
            command.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return None
        # 返回第一个非 None 的捕获组
        return next((g for g in m.groups() if g is not None), None)

    def _wrap_for_powershell(self, command: str) -> str:
        """
        将命令包装为 PowerShell 命令（使用 -EncodedCommand 避免转义问题）。

        策略：
        1. 如果命令已是 powershell/pwsh 调用 → 提取内部命令再编码
        2. 否则直接对整个命令编码
        """
        stripped = command.strip().lower()
        if stripped.startswith(("powershell", "pwsh")):
            # 已经是显式 PowerShell 调用，尝试提取内部命令
            inner = self._extract_ps_inner_command(command)
            if inner:
                logger.debug(f"Extracted inner PS command for encoding: {inner[:80]}...")
                return self._encode_for_powershell(inner)
            else:
                # 无法安全提取（可能是 powershell script.ps1 等），原样返回
                logger.debug("Cannot extract inner PS command, passing through as-is")
                return command

        # 普通 cmdlet 命令，直接编码
        return self._encode_for_powershell(command)

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict | None = None,
    ) -> CommandResult:
        """
        执行命令

        Args:
            command: 要执行的命令
            cwd: 工作目录
            timeout: 超时时间（秒）
            env: 环境变量

        Returns:
            CommandResult
        """
        work_dir = cwd or self.default_cwd
        cmd_timeout = timeout or self.timeout

        # 合并环境变量
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)

        # 打包模式：将外置 Python 目录 prepend 到子进程 PATH，
        # 使 `python script.py` 自动找到正确解释器
        try:
            from ..runtime_env import IS_FROZEN, get_python_executable
            if IS_FROZEN:
                _ext_py = get_python_executable()
                if _ext_py:
                    from pathlib import Path
                    _py_dir = str(Path(_ext_py).parent)
                    cmd_env["PATH"] = _py_dir + os.pathsep + cmd_env.get("PATH", "")
        except Exception:
            pass

        # Windows PowerShell 命令处理
        original_command = command
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)
            # EncodedCommand 很长，日志只记录原始命令
            logger.info(f"Windows PowerShell encoded: {original_command[:200]}")

        logger.info(f"Executing: {command[:300]}")
        logger.debug(f"CWD: {work_dir}")

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=cmd_env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=cmd_timeout,
            )

            result = CommandResult(
                returncode=process.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )

            logger.info(f"Command completed with code: {result.returncode}")
            if result.stderr:
                logger.debug(f"Stderr: {result.stderr}")

            return result

        except asyncio.CancelledError:
            # 三路竞速 cancel/skip：立即杀掉子进程，实时中断
            logger.warning(f"Command cancelled, killing subprocess: {original_command[:200]}")
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            raise  # 重新抛出，让上层三路竞速逻辑处理

        except TimeoutError:
            logger.error(f"Command timed out after {cmd_timeout}s")
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {cmd_timeout} seconds",
            )
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr=str(e),
            )

    async def run_interactive(
        self,
        command: str,
        cwd: str | None = None,
    ) -> AsyncIterator[str]:
        """交互式执行命令，实时输出"""
        work_dir = cwd or self.default_cwd

        cmd_env = os.environ.copy()
        try:
            from ..runtime_env import IS_FROZEN, get_python_executable
            if IS_FROZEN:
                _ext_py = get_python_executable()
                if _ext_py:
                    from pathlib import Path
                    _py_dir = str(Path(_ext_py).parent)
                    cmd_env["PATH"] = _py_dir + os.pathsep + cmd_env.get("PATH", "")
        except Exception:
            pass

        # Windows PowerShell 命令处理
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)

        logger.info(f"Executing interactively: {command[:300]}")

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work_dir,
            env=cmd_env,
        )

        if process.stdout:
            async for line in process.stdout:
                yield line.decode("utf-8", errors="replace")

        await process.wait()

    async def check_command_exists(self, command: str) -> bool:
        """检查命令是否存在"""
        check_cmd = f"where {command}" if os.name == "nt" else f"which {command}"

        result = await self.run(check_cmd)
        return result.success

    async def pip_install(self, package: str) -> CommandResult:
        """使用 pip 安装包（PyInstaller 兼容：使用 runtime_env 获取正确的 Python 解释器）"""
        from openakita.runtime_env import IS_FROZEN, get_python_executable
        py = get_python_executable()
        if py:
            return await self.run(f'"{py}" -m pip install {package}')
        if IS_FROZEN:
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr="未找到可用的 Python 解释器，无法执行 pip install。"
                       "请前往「设置中心 → Python 环境」使用「一键修复」。",
            )
        return await self.run(f"pip install {package}")

    async def npm_install(self, package: str, global_: bool = False) -> CommandResult:
        """使用 npm 安装包"""
        flag = "-g " if global_ else ""
        return await self.run(f"npm install {flag}{package}")

    async def git_clone(self, url: str, path: str | None = None) -> CommandResult:
        """克隆 Git 仓库"""
        cmd = f"git clone {url}"
        if path:
            cmd += f" {path}"
        return await self.run(cmd)

    async def run_powershell(self, command: str) -> CommandResult:
        """
        专门执行 PowerShell 命令（跨平台）。

        使用 -EncodedCommand 传递命令，彻底避免转义问题。

        Args:
            command: PowerShell 命令

        Returns:
            CommandResult
        """
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        if self._is_windows:
            return await self.run(
                f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"
            )
        else:
            # Linux/Mac 上使用 pwsh（PowerShell Core，需单独安装）
            if not shutil.which("pwsh"):
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=(
                        "PowerShell Core (pwsh) is not installed on this system.\n"
                        "Install it from: https://github.com/PowerShell/PowerShell\n"
                        "Or use a regular shell command instead."
                    ),
                )
            return await self.run(
                f"pwsh -NoProfile -NonInteractive -EncodedCommand {encoded}"
            )
