"""
Shell 工具 - 执行系统命令
增强版：支持 Windows PowerShell 命令自动转换
"""

import asyncio
import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import AsyncIterator, Optional

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
    
    # PowerShell 命令模式（需要特殊处理）
    POWERSHELL_PATTERNS = [
        r'Get-EventLog',
        r'Get-ScheduledTask',
        r'ConvertFrom-Csv',
        r'ConvertTo-Csv',
        r'Select-Object',
        r'Where-Object',
        r'ForEach-Object',
        r'Import-Module',
        r'Get-Process',
        r'Get-Service',
        r'Get-ChildItem',
        r'Set-ExecutionPolicy',
    ]
    
    def __init__(
        self,
        default_cwd: Optional[str] = None,
        timeout: int = 60,
        shell: bool = True,
    ):
        self.default_cwd = default_cwd or os.getcwd()
        self.timeout = timeout
        self.shell = shell
        self._is_windows = platform.system() == "Windows"
    
    def _needs_powershell(self, command: str) -> bool:
        """检查命令是否需要 PowerShell 执行"""
        if not self._is_windows:
            return False
        
        # 检查是否包含 PowerShell 特有命令
        for pattern in self.POWERSHELL_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False
    
    def _wrap_for_powershell(self, command: str) -> str:
        """将命令包装为 PowerShell 命令"""
        # 如果命令已经是 PowerShell 语法，直接返回
        if command.strip().startswith('powershell'):
            return command
        
        # 移除已有的 -Command 包装
        command = re.sub(r'^powershell\s+-Command\s+', '', command, flags=re.IGNORECASE)
        command = re.sub(r'^pwsh\s+-Command\s+', '', command, flags=re.IGNORECASE)
        
        # 使用 PowerShell 执行
        return f'powershell -NoProfile -Command "{command}"'
    
    async def run(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[dict] = None,
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
        
        # Windows PowerShell 命令处理
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)
            logger.info(f"Windows PowerShell wrapped: {command[:80]}...")
        
        logger.info(f"Executing: {command[:100]}...")
        logger.debug(f"CWD: {work_dir}")
        
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
                logger.debug(f"Stderr: {result.stderr[:200]}")
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"Command timed out after {cmd_timeout}s")
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
        cwd: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """交互式执行命令，实时输出"""
        work_dir = cwd or self.default_cwd
        
        # Windows PowerShell 命令处理
        if self._is_windows and self._needs_powershell(command):
            command = self._wrap_for_powershell(command)
        
        logger.info(f"Executing interactively: {command[:100]}...")
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work_dir,
        )
        
        if process.stdout:
            async for line in process.stdout:
                yield line.decode("utf-8", errors="replace")
        
        await process.wait()
    
    async def check_command_exists(self, command: str) -> bool:
        """检查命令是否存在"""
        if os.name == "nt":
            check_cmd = f"where {command}"
        else:
            check_cmd = f"which {command}"
        
        result = await self.run(check_cmd)
        return result.success
    
    async def pip_install(self, package: str) -> CommandResult:
        """使用 pip 安装包"""
        return await self.run(f"pip install {package}")
    
    async def npm_install(self, package: str, global_: bool = False) -> CommandResult:
        """使用 npm 安装包"""
        flag = "-g " if global_ else ""
        return await self.run(f"npm install {flag}{package}")
    
    async def git_clone(self, url: str, path: Optional[str] = None) -> CommandResult:
        """克隆 Git 仓库"""
        cmd = f"git clone {url}"
        if path:
            cmd += f" {path}"
        return await self.run(cmd)
    
    async def run_powershell(self, command: str) -> CommandResult:
        """
        专门执行 PowerShell 命令（跨平台）
        
        Args:
            command: PowerShell 命令
        
        Returns:
            CommandResult
        """
        if self._is_windows:
            # Windows 上直接使用 powershell
            return await self.run(f'powershell -NoProfile -Command "{command}"')
        else:
            # Linux/Mac 上使用 pwsh
            return await self.run(f'pwsh -NoProfile -Command "{command}"')
