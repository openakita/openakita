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
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_KILL_WAIT_TIMEOUT = 5  # seconds to wait for process.wait() after kill

# ---------------------------------------------------------------------------
# Git Bash 自动定位（参考 CC BashTool findGitBashPath）
# ---------------------------------------------------------------------------
_git_bash_cache: str | None | bool = False  # False = not yet searched


def find_git_bash_path() -> str | None:
    """在 Windows 上自动定位 Git Bash 可执行文件。

    搜索顺序（参考 CC）:
    1. OPENAKITA_GIT_BASH_PATH 环境变量
    2. 常见安装路径
    3. 系统 PATH
    """
    global _git_bash_cache
    if _git_bash_cache is not False:
        return _git_bash_cache  # type: ignore[return-value]

    if sys.platform != "win32":
        _git_bash_cache = None
        return None

    env_path = os.environ.get("OPENAKITA_GIT_BASH_PATH")
    if env_path and os.path.isfile(env_path):
        _git_bash_cache = env_path
        logger.info(f"[GitBash] Found via env: {env_path}")
        return env_path

    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Git\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            _git_bash_cache = candidate
            logger.info(f"[GitBash] Found at: {candidate}")
            return candidate

    which_bash = shutil.which("bash")
    if which_bash:
        _git_bash_cache = which_bash
        logger.info(f"[GitBash] Found in PATH: {which_bash}")
        return which_bash

    _git_bash_cache = None
    logger.debug("[GitBash] Not found on this system")
    return None


# ---------------------------------------------------------------------------
# UNC 路径安全检查（参考 CC isUNCPath — 防 NTLM 认证泄漏）
# ---------------------------------------------------------------------------
_UNC_RE = re.compile(r"^\\\\[^\\]")


def is_unc_path(path: str) -> bool:
    """检查是否为 UNC 路径（\\\\server\\share 形式）。

    UNC 路径可能触发 Windows 自动 NTLM 认证，导致凭证泄漏。
    """
    return bool(_UNC_RE.match(path))


def check_unc_safety(command: str) -> str | None:
    """检查命令中是否包含 UNC 路径，返回警告信息或 None。"""
    tokens = command.split()
    for token in tokens:
        if is_unc_path(token):
            return (
                f"Blocked: UNC path detected ({token}). "
                "UNC paths can trigger automatic NTLM authentication "
                "and leak credentials. Use mapped drive letters instead."
            )
    return None


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
        self._is_windows = sys.platform == "win32"
        self._oem_encoding: str | None = None

    # ------------------------------------------------------------------
    # 进程清理（Windows 安全杀死进程树）
    # ------------------------------------------------------------------

    async def _kill_process_tree(
        self, process: asyncio.subprocess.Process
    ) -> None:
        """杀死进程及其所有子进程，然后带超时地等待退出。

        Windows 上 process.kill() 仅杀死直接子进程，孙进程（如 node 启动的
        服务）会继续运行并持有 stdout/stderr 管道，导致 process.wait() 永久
        阻塞。改用 taskkill /T /F 杀死整个进程树。
        """
        pid = process.pid
        if pid is None:
            return

        if self._is_windows:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=_KILL_WAIT_TIMEOUT,
                )
            except Exception as e:
                logger.debug(f"taskkill failed for PID {pid}: {e}")
                try:
                    process.kill()
                except Exception:
                    pass
        else:
            try:
                process.kill()
            except Exception:
                pass

        # 带超时等待，防止无限阻塞
        try:
            await asyncio.wait_for(process.wait(), timeout=_KILL_WAIT_TIMEOUT)
        except (TimeoutError, Exception):
            logger.warning(f"Process {pid} did not exit within {_KILL_WAIT_TIMEOUT}s after kill")

    # ------------------------------------------------------------------
    # Windows 编码处理
    # ------------------------------------------------------------------

    def _get_oem_encoding(self) -> str:
        """获取 Windows OEM 代码页编码名（如 cp936），用于解码回退"""
        if self._oem_encoding is not None:
            return self._oem_encoding
        try:
            import ctypes
            oem_cp = ctypes.windll.kernel32.GetOEMCP()
            self._oem_encoding = f"cp{oem_cp}"
        except Exception:
            self._oem_encoding = "gbk"
        return self._oem_encoding

    def _decode_output(self, data: bytes) -> str:
        """智能解码子进程输出：优先 UTF-8，回退到系统 OEM 代码页。

        cmd.exe 默认以 OEM 代码页 (中文 Windows = GBK/CP936) 输出，
        即使用 chcp 65001 也可能有极少数程序不遵守。
        此方法先尝试 UTF-8 严格解码，失败后以系统代码页兜底。
        """
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            if self._is_windows:
                encoding = self._get_oem_encoding()
                try:
                    return data.decode(encoding, errors="replace")
                except (UnicodeDecodeError, LookupError):
                    pass
            return data.decode("utf-8", errors="replace")

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
        输出强制使用 UTF-8 编码，避免中文乱码。
        """
        utf8_preamble = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        )
        full_command = utf8_preamble + command
        encoded = base64.b64encode(full_command.encode("utf-16-le")).decode("ascii")
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

        # UNC 路径安全检查
        unc_warning = check_unc_safety(command)
        if unc_warning:
            return CommandResult(returncode=-1, stdout="", stderr=unc_warning)

        if work_dir and is_unc_path(work_dir):
            return CommandResult(
                returncode=-1, stdout="",
                stderr=f"Blocked: UNC working directory ({work_dir}). "
                       "Use a local path or mapped drive letter.",
            )

        # 合并环境变量
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)

        # macOS GUI 应用 PATH 增强：Finder/Dock 启动的 .app 只继承
        # /usr/bin:/bin:/usr/sbin:/sbin，不含 Homebrew/NVM 等路径。
        # 复用 path_helper 已缓存的 login shell PATH，使 run_shell
        # 能找到 brew/node/npm/python3 等用户已安装的工具。
        try:
            from ..utils.path_helper import resolve_macos_login_shell_path
            _shell_path = resolve_macos_login_shell_path()
            if _shell_path:
                cmd_env["PATH"] = _shell_path
        except Exception:
            pass

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

        # Windows 命令编码处理
        original_command = command
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)
            logger.info(f"Windows PowerShell encoded: {original_command[:200]}")
        elif self._is_windows:
            # 强制 cmd.exe 使用 UTF-8 代码页，解决中文路径/文件名乱码
            command = f"chcp 65001 >nul && {command}"

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
                stdout=self._decode_output(stdout),
                stderr=self._decode_output(stderr),
            )

            logger.info(f"Command completed with code: {result.returncode}")
            if result.stderr:
                logger.debug(f"Stderr: {result.stderr}")

            return result

        except asyncio.CancelledError:
            # 三路竞速 cancel/skip：立即杀掉子进程，实时中断
            logger.warning(f"Command cancelled, killing subprocess: {original_command[:200]}")
            if process and process.returncode is None:
                await self._kill_process_tree(process)
            raise  # 重新抛出，让上层三路竞速逻辑处理

        except TimeoutError:
            logger.error(f"Command timed out after {cmd_timeout}s")
            if process and process.returncode is None:
                await self._kill_process_tree(process)
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
            from ..utils.path_helper import resolve_macos_login_shell_path
            _shell_path = resolve_macos_login_shell_path()
            if _shell_path:
                cmd_env["PATH"] = _shell_path
        except Exception:
            pass
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

        # Windows 命令编码处理
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)
        elif self._is_windows:
            command = f"chcp 65001 >nul && {command}"

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
                yield self._decode_output(line)

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

        使用 _encode_for_powershell 统一处理 UTF-8 编码 + Base64。

        Args:
            command: PowerShell 命令

        Returns:
            CommandResult
        """
        # 使用统一的编码方法（含 UTF-8 preamble）
        encoded_cmd = self._encode_for_powershell(command)
        if self._is_windows:
            # 直接调用 run()；命令已编码，run() 中 _needs_powershell 会匹配
            # 到 "powershell" 前缀但 _wrap_for_powershell 会因为无法提取
            # inner command 而原样返回，所以不会双重编码
            return await self.run(encoded_cmd)
        else:
            if not shutil.which("pwsh"):
                return CommandResult(
                    returncode=1,
                    stdout="",
                    stderr=(
                        "PowerShell Core (pwsh) is not installed on this system.\n"
                        "Install it from: https://github.com/PowerShell/PowerShell\n"
                        "Or use a regular shell command instead."
                    ),
                )
            # 替换 powershell 为 pwsh
            return await self.run(encoded_cmd.replace("powershell ", "pwsh ", 1))
