"""
文件系统处理器

处理文件系统相关的系统技能：
- run_shell: 执行 Shell 命令
- write_file: 写入文件
- read_file: 读取文件
- list_directory: 列出目录
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class FilesystemHandler:
    """
    文件系统处理器

    处理所有文件系统相关的工具调用
    """

    # 该处理器处理的工具
    TOOLS = [
        "run_shell",
        "write_file",
        "read_file",
        "list_directory",
    ]

    def __init__(self, agent: "Agent"):
        """
        初始化处理器

        Args:
            agent: Agent 实例，用于访问 shell_tool 和 file_tool
        """
        self.agent = agent

    def _get_fix_policy(self) -> dict | None:
        """
        获取自检自动修复策略（可选）

        当 SelfChecker 创建的修复 Agent 注入 _selfcheck_fix_policy 时启用。
        """
        policy = getattr(self.agent, "_selfcheck_fix_policy", None)
        if isinstance(policy, dict) and policy.get("enabled"):
            return policy
        return None

    def _resolve_to_abs(self, raw: str) -> Path:
        p = Path(raw)
        if p.is_absolute():
            return p.resolve()
        # FileTool 以 cwd 为 base_path；这里保持一致
        return (Path.cwd() / p).resolve()

    def _is_under_any_root(self, target: Path, roots: list[str]) -> bool:
        for r in roots or []:
            try:
                root = Path(r).resolve()
                if target == root or target.is_relative_to(root):
                    return True
            except Exception:
                continue
        return False

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """
        处理工具调用

        Args:
            tool_name: 工具名称
            params: 参数字典

        Returns:
            执行结果字符串
        """
        if tool_name == "run_shell":
            return await self._run_shell(params)
        elif tool_name == "write_file":
            return await self._write_file(params)
        elif tool_name == "read_file":
            return await self._read_file(params)
        elif tool_name == "list_directory":
            return await self._list_directory(params)
        else:
            return f"❌ Unknown filesystem tool: {tool_name}"

    async def _run_shell(self, params: dict) -> str:
        """执行 Shell 命令"""
        command = params["command"]
        timeout = params.get("timeout", 60)
        timeout = max(10, min(timeout, 600))

        policy = self._get_fix_policy()
        if policy:
            deny_patterns = policy.get("deny_shell_patterns") or []
            for pat in deny_patterns:
                try:
                    if re.search(pat, command, flags=re.IGNORECASE):
                        msg = (
                            "❌ 自检自动修复护栏：禁止执行可能涉及系统/Windows 层面的命令。"
                            f"\n命令: {command}"
                        )
                        logger.warning(msg)
                        return msg
                except re.error:
                    # 忽略无效 regex
                    continue

        result = await self.agent.shell_tool.run(
            command,
            cwd=params.get("cwd"),
            timeout=timeout,
        )

        # 记录到日志
        from ...logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()

        if result.success:
            log_buffer.add_log(
                level="INFO",
                module="shell",
                message=f"$ {command}\n[exit: 0]\n{result.stdout}"
                + (f"\n[stderr]: {result.stderr}" if result.stderr else ""),
            )
            # 即使成功，也返回 stderr 中的警告信息
            output = result.stdout
            if result.stderr:
                output += f"\n[警告]:\n{result.stderr}"
            return f"命令执行成功 (exit code: 0):\n{output}"
        else:
            log_buffer.add_log(
                level="ERROR",
                module="shell",
                message=f"$ {command}\n[exit: {result.returncode}]\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )

            def _tail(text: str, max_chars: int = 4000, max_lines: int = 120) -> str:
                """失败时强限长：只保留尾部，避免注入过多终端日志。"""
                if not text:
                    return ""
                # 先按行取尾部
                lines = text.splitlines()
                if len(lines) > max_lines:
                    lines = lines[-max_lines:]
                    text = "\n".join(lines)
                    text = f"...(已截断，仅保留最后 {max_lines} 行)\n{text}"
                # 再按字符强裁剪
                if len(text) > max_chars:
                    text = text[-max_chars:]
                    text = f"...(已截断，仅保留最后 {max_chars} 字符)\n{text}"
                return text

            output_parts = [f"命令执行失败 (exit code: {result.returncode})"]
            if result.stdout:
                output_parts.append(f"[stdout-tail]:\n{_tail(result.stdout)}")
            if result.stderr:
                output_parts.append(f"[stderr-tail]:\n{_tail(result.stderr)}")
            if not result.stdout and not result.stderr:
                output_parts.append("(无输出，可能命令不存在或语法错误)")
            output_parts.append(
                "\n提示: 如果不确定原因，可以调用 get_session_logs 查看详细日志，或尝试其他命令。"
            )
            return "\n".join(output_parts)

    async def _write_file(self, params: dict) -> str:
        """写入文件"""
        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(params["path"])
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = (
                    "❌ 自检自动修复护栏：禁止写入该路径（仅允许修复 tools/skills/mcps/channels 相关目录）。"
                    f"\n目标: {target}"
                )
                logger.warning(msg)
                return msg
        await self.agent.file_tool.write(params["path"], params["content"])
        return f"文件已写入: {params['path']}"

    async def _read_file(self, params: dict) -> str:
        """读取文件"""
        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(params["path"])
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"❌ 自检自动修复护栏：禁止读取该路径。\n目标: {target}"
                logger.warning(msg)
                return msg
        content = await self.agent.file_tool.read(params["path"])
        return f"文件内容:\n{content}"

    async def _list_directory(self, params: dict) -> str:
        """列出目录"""
        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(params["path"])
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"❌ 自检自动修复护栏：禁止列出该目录。\n目标: {target}"
                logger.warning(msg)
                return msg
        files = await self.agent.file_tool.list_dir(params["path"])
        return "目录内容:\n" + "\n".join(files)


def create_handler(agent: "Agent"):
    """
    创建文件系统处理器

    Args:
        agent: Agent 实例

    Returns:
        处理器的 handle 方法
    """
    handler = FilesystemHandler(agent)
    return handler.handle
