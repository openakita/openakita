"""
文件系统处理器

处理文件系统相关的系统技能：
- run_shell: 执行 Shell 命令（持久会话 + 后台进程支持）
- write_file: 写入文件
- read_file: 读取文件
- edit_file: 精确字符串替换编辑
- list_directory: 列出目录
- grep: 内容搜索
- glob: 文件名模式搜索
- delete_file: 删除文件
"""

import logging
import re
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_terminal_managers: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_terminal_mgr_strong_refs: dict[int, Any] = {}


def _get_terminal_manager(agent: "Agent") -> Any:
    """Get or create a TerminalSessionManager for this agent instance.

    Uses agent object id as key. A strong reference is stored alongside the agent
    so the manager lives as long as the agent does. When the agent is GC'd,
    clean up on next access.
    """
    from ..terminal import TerminalSessionManager
    agent_id = id(agent)
    mgr = _terminal_mgr_strong_refs.get(agent_id)
    if mgr is not None:
        return mgr
    cwd = getattr(agent, "default_cwd", None) or str(Path.cwd())
    mgr = TerminalSessionManager(default_cwd=cwd)
    _terminal_mgr_strong_refs[agent_id] = mgr
    try:
        weakref.finalize(agent, _terminal_mgr_strong_refs.pop, agent_id, None)
    except TypeError:
        pass
    return mgr


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
        "edit_file",
        "list_directory",
        "grep",
        "glob",
        "delete_file",
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
        elif tool_name == "edit_file":
            return await self._edit_file(params)
        elif tool_name == "list_directory":
            return await self._list_directory(params)
        elif tool_name == "grep":
            return await self._grep(params)
        elif tool_name == "glob":
            return await self._glob(params)
        elif tool_name == "delete_file":
            return await self._delete_file(params)
        else:
            return f"❌ Unknown filesystem tool: {tool_name}"

    @staticmethod
    def _fix_windows_python_c(command: str) -> str:
        """Windows 多行 python -c 修复。

        Windows cmd.exe 无法正确处理 python -c "..." 中的换行符，
        会导致 Python 只执行第一行（通常是 import），stdout 为空。
        检测到多行 python -c 时，自动写入临时 .py 文件后执行。
        """
        import tempfile

        stripped = command.strip()

        # 匹配 python -c "..." 或 python -c '...' 或 python - <<'EOF'
        # 只处理包含换行的情况
        m = re.match(
            r'^python(?:3)?(?:\.exe)?\s+-c\s+["\'](.+)["\']$',
            stripped,
            re.DOTALL,
        )
        if not m:
            # 也匹配 heredoc 形式：python - <<'PY' ... PY
            m2 = re.match(
                r"^python(?:3)?(?:\.exe)?\s+-\s*<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1$",
                stripped,
                re.DOTALL,
            )
            if m2:
                code = m2.group(2)
            else:
                return command
        else:
            code = m.group(1)

        # 只有多行才需要修复
        if "\n" not in code:
            return command

        # 写入临时文件 (delete=False requires manual cleanup, not context manager)
        tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            suffix=".py",
            prefix="oa_shell_",
            dir=tempfile.gettempdir(),
            delete=False,
            encoding="utf-8",
        )
        tmp.write(code)
        tmp.close()

        logger.info(
            "[Windows fix] Multiline python -c → temp file: %s", tmp.name
        )
        return f'python "{tmp.name}"'

    # run_shell 成功输出最大行数
    SHELL_MAX_LINES = 200

    async def _run_shell(self, params: dict) -> str:
        """Execute shell command with persistent session + background support."""
        command = params.get("command", "")
        if not command:
            return "❌ run_shell 缺少必要参数 'command'。"

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
                    continue

        import platform
        if platform.system() == "Windows":
            command = self._fix_windows_python_c(command)

        working_directory = params.get("working_directory") or params.get("cwd")

        block_timeout_ms = params.get("block_timeout_ms")
        if block_timeout_ms is None:
            timeout_s = params.get("timeout", 60)
            # 确保 timeout_s 是整数类型（防止外部传入字符串导致 TypeError）
            try:
                timeout_s = int(timeout_s)
            except (ValueError, TypeError):
                timeout_s = 60
            timeout_s = max(10, min(timeout_s, 600))
            block_timeout_ms = timeout_s * 1000

        session_id = params.get("session_id", 1)

        terminal_mgr = _get_terminal_manager(self.agent)
        result = await terminal_mgr.execute(
            command,
            session_id=session_id,
            block_timeout_ms=block_timeout_ms,
            working_directory=working_directory,
        )

        from ...logging import get_session_log_buffer
        log_buffer = get_session_log_buffer()

        if result.backgrounded:
            log_buffer.add_log(
                level="INFO",
                module="shell",
                message=f"$ {command}\n[backgrounded, pid: {result.pid}]",
            )
            return result.stdout

        if result.success:
            log_buffer.add_log(
                level="INFO",
                module="shell",
                message=f"$ {command}\n[exit: 0]\n{result.stdout}"
                + (f"\n[stderr]: {result.stderr}" if result.stderr else ""),
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[警告]:\n{result.stderr}"

            full_text = f"命令执行成功 (exit code: 0):\n{output}"
            return self._truncate_shell_output(full_text)
        else:
            log_buffer.add_log(
                level="ERROR",
                module="shell",
                message=f"$ {command}\n[exit: {result.returncode}]\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )

            def _tail(text: str, max_chars: int = 4000, max_lines: int = 120) -> str:
                if not text:
                    return ""
                lines = text.splitlines()
                if len(lines) > max_lines:
                    lines = lines[-max_lines:]
                    text = "\n".join(lines)
                    text = f"...(已截断，仅保留最后 {max_lines} 行)\n{text}"
                if len(text) > max_chars:
                    text = text[-max_chars:]
                    text = f"...(已截断，仅保留最后 {max_chars} 字符)\n{text}"
                return text

            output_parts = [f"命令执行失败 (exit code: {result.returncode})"]

            if result.returncode == 9009:
                cmd_lower = command.strip().lower()
                if cmd_lower.startswith(("python", "python3")):
                    output_parts.append(
                        "⚠️ Python 不在系统 PATH 中（Windows 9009 = 命令未找到）。\n"
                        "请先安装 Python：run_shell 执行 'winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements'\n"
                        "安装完成后系统将自动检测，无需重启。不要再重试 python/python3 命令。"
                    )
                else:
                    first_word = command.strip().split()[0] if command.strip() else command
                    output_parts.append(
                        f"⚠️ '{first_word}' 不在系统 PATH 中（Windows 9009 = 命令未找到）。\n"
                        "请检查该程序是否已安装，或使用完整路径。"
                    )

            if result.stdout:
                output_parts.append(f"[stdout-tail]:\n{_tail(result.stdout)}")
            if result.stderr:
                output_parts.append(f"[stderr-tail]:\n{_tail(result.stderr)}")
            if not result.stdout and not result.stderr and result.returncode != 9009:
                output_parts.append("(无输出，可能命令不存在或语法错误)")

            full_error = "\n".join(output_parts)
            truncated_result = self._truncate_shell_output(full_error)
            truncated_result += (
                "\n提示: 如果不确定原因，可以调用 get_session_logs 查看详细日志，或尝试其他命令。"
            )
            return truncated_result

    def _truncate_shell_output(self, text: str) -> str:
        """截断 shell 输出，大输出保存到溢出文件并附分页提示。"""
        lines = text.split("\n")
        if len(lines) <= self.SHELL_MAX_LINES:
            return text

        total_lines = len(lines)
        from ...core.tool_executor import save_overflow
        overflow_path = save_overflow("run_shell", text)
        truncated = "\n".join(lines[: self.SHELL_MAX_LINES])
        truncated += (
            f"\n\n[OUTPUT_TRUNCATED] 命令输出共 {total_lines} 行，"
            f"已显示前 {self.SHELL_MAX_LINES} 行。\n"
            f"完整输出已保存到: {overflow_path}\n"
            f'使用 read_file(path="{overflow_path}", offset={self.SHELL_MAX_LINES + 1}) '
            f"查看后续内容。"
        )
        return truncated

    @staticmethod
    def _check_unc(path: str | None) -> str | None:
        """Block UNC paths to prevent NTLM credential leaks."""
        if path and path.startswith("\\\\"):
            return (
                f"Blocked: UNC path detected ({path}). "
                "UNC paths can trigger automatic NTLM authentication and leak "
                "credentials. Use a local path or mapped drive letter instead."
            )
        return None

    async def _write_file(self, params: dict) -> str:
        """写入文件"""
        path = params.get("path")
        unc_err = self._check_unc(path)
        if unc_err:
            return f"❌ {unc_err}"
        content = params.get("content")
        if not path:
            content_len = len(str(content)) if content else 0
            if content_len > 5000:
                return (
                    f"❌ write_file 缺少必要参数 'path'（content 长度 {content_len} 字符，"
                    "疑似因内容过长导致 JSON 参数被截断）。\n"
                    "请缩短内容后重试：\n"
                    "1. 将大文件拆分为多次写入（每次 < 8000 字符）\n"
                    "2. 或用 run_shell 执行 Python 脚本生成大文件"
                )
            return "❌ write_file 缺少必要参数 'path'。请提供文件路径和内容后重试。"
        if content is None:
            return "❌ write_file 缺少必要参数 'content'。请提供文件内容后重试。"
        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = (
                    "❌ 自检自动修复护栏：禁止写入该路径（仅允许修复 tools/skills/mcps/channels 相关目录）。"
                    f"\n目标: {target}"
                )
                logger.warning(msg)
                return msg
        await self.agent.file_tool.write(path, content)
        result = f"文件已写入: {path}"

        from ...core.im_context import get_im_session
        if not get_im_session():
            result += (
                "\n\n💡 当前为 Desktop 模式，用户无法直接访问服务器文件。"
                "请将文件的关键内容直接包含在回复中，"
                "或调用 deliver_artifacts(artifacts=[{type: 'file', path: '"
                + str(path) + "'}]) 使文件在前端可下载。"
            )
        return result

    # read_file 默认最大行数（参考 Claude Code 的 2000 行，我们用 300 更保守）
    READ_FILE_DEFAULT_LIMIT = 300

    async def _read_file(self, params: dict) -> str:
        """读取文件（支持 offset/limit 分页）"""
        path = params.get("path", "")
        if not path:
            return "❌ read_file 缺少必要参数 'path'。"
        unc_err = self._check_unc(path)
        if unc_err:
            return f"❌ {unc_err}"

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"❌ 自检自动修复护栏：禁止读取该路径。\n目标: {target}"
                logger.warning(msg)
                return msg

        content = await self.agent.file_tool.read(path)

        offset = params.get("offset", 1)  # 起始行号（1-based），默认第 1 行
        limit = params.get("limit", self.READ_FILE_DEFAULT_LIMIT)

        # 确保 offset/limit 合法
        try:
            offset = max(1, int(offset))
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            offset, limit = 1, self.READ_FILE_DEFAULT_LIMIT

        lines = content.split("\n")
        total_lines = len(lines)

        # 如果文件在 limit 范围内且从头读取，直接返回全部
        if total_lines <= limit and offset <= 1:
            return f"文件内容 ({total_lines} 行):\n{content}"

        # 分页截取
        start = offset - 1  # 转为 0-based
        end = min(start + limit, total_lines)

        if start >= total_lines:
            return (
                f"⚠️ offset={offset} 超出文件范围（文件共 {total_lines} 行）。\n"
                f'使用 read_file(path="{path}", offset=1, limit={limit}) 从头开始读取。'
            )

        shown = "\n".join(lines[start:end])
        result = f"文件内容 (第 {start+1}-{end} 行，共 {total_lines} 行):\n{shown}"

        # 如果还有更多内容，附加分页提示
        if end < total_lines:
            remaining = total_lines - end
            result += (
                f"\n\n[OUTPUT_TRUNCATED] 文件共 {total_lines} 行，"
                f"当前显示第 {start+1}-{end} 行，剩余 {remaining} 行。\n"
                f'使用 read_file(path="{path}", offset={end+1}, limit={limit}) '
                f"查看后续内容。"
            )

        return result

    # list_directory 默认最大条目数
    LIST_DIR_DEFAULT_MAX = 200

    async def _edit_file(self, params: dict) -> str:
        """精确字符串替换编辑"""
        path = params.get("path", "")
        old_string = params.get("old_string")
        new_string = params.get("new_string")

        if not path:
            return "❌ edit_file 缺少必要参数 'path'。"
        if old_string is None:
            return "❌ edit_file 缺少必要参数 'old_string'。"
        if new_string is None:
            return "❌ edit_file 缺少必要参数 'new_string'。"
        if old_string == new_string:
            return "❌ old_string 和 new_string 相同，无需替换。"

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = (
                    "❌ 自检自动修复护栏：禁止编辑该路径。"
                    f"\n目标: {target}"
                )
                logger.warning(msg)
                return msg

        replace_all = params.get("replace_all", False)

        try:
            result = await self.agent.file_tool.edit(
                path, old_string, new_string, replace_all=replace_all,
            )
            replaced = result["replaced"]
            if replace_all and replaced > 1:
                return f"文件已编辑: {path}（替换了 {replaced} 处匹配）"
            return f"文件已编辑: {path}"
        except FileNotFoundError:
            return f"❌ 文件不存在: {path}"
        except ValueError as e:
            return f"❌ edit_file 失败: {e}"

    async def _list_directory(self, params: dict) -> str:
        """列出目录（支持 pattern/recursive/max_items）"""
        path = params.get("path", "")
        if not path:
            return "❌ list_directory 缺少必要参数 'path'。"

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"❌ 自检自动修复护栏：禁止列出该目录。\n目标: {target}"
                logger.warning(msg)
                return msg

        pattern = params.get("pattern", "*")
        recursive = params.get("recursive", False)
        files = await self.agent.file_tool.list_dir(
            path, pattern=pattern, recursive=recursive,
        )

        max_items = params.get("max_items", self.LIST_DIR_DEFAULT_MAX)
        try:
            max_items = max(1, int(max_items))
        except (TypeError, ValueError):
            max_items = self.LIST_DIR_DEFAULT_MAX

        total = len(files)
        if total <= max_items:
            return f"目录内容 ({total} 条):\n" + "\n".join(files)

        shown = files[:max_items]
        result = f"目录内容 (显示前 {max_items} 条，共 {total} 条):\n" + "\n".join(shown)
        result += (
            f"\n\n[OUTPUT_TRUNCATED] 目录共 {total} 条目，已显示前 {max_items} 条。\n"
            f"如需查看更多，请使用 list_directory(path=\"{path}\", max_items={total}) "
            f"或缩小查询范围。"
        )
        return result

    # grep 最大结果条目数
    GREP_MAX_RESULTS = 200

    async def _grep(self, params: dict) -> str:
        """内容搜索"""
        pattern = params.get("pattern", "")
        if not pattern:
            return "❌ grep 缺少必要参数 'pattern'。"

        path = params.get("path", ".")
        include = params.get("include")
        context_lines = params.get("context_lines", 0)
        max_results = params.get("max_results", 50)
        case_insensitive = params.get("case_insensitive", False)

        try:
            context_lines = max(0, int(context_lines))
        except (TypeError, ValueError):
            context_lines = 0
        try:
            max_results = max(1, min(int(max_results), self.GREP_MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = 50

        try:
            results = await self.agent.file_tool.grep(
                pattern, path,
                include=include,
                context_lines=context_lines,
                max_results=max_results,
                case_insensitive=case_insensitive,
            )
        except FileNotFoundError as e:
            return f"❌ {e}"
        except ValueError as e:
            return f"❌ 正则表达式错误: {e}"

        if not results:
            return f"未找到匹配 '{pattern}' 的内容。"

        lines: list[str] = []
        for m in results:
            if context_lines > 0 and "context_before" in m:
                for ctx_line in m["context_before"]:
                    lines.append(f"{m['file']}-{ctx_line}")
            lines.append(f"{m['file']}:{m['line']}:{m['text']}")
            if context_lines > 0 and "context_after" in m:
                for ctx_line in m["context_after"]:
                    lines.append(f"{m['file']}-{ctx_line}")
                lines.append("")

        total = len(results)
        header = f"找到 {total} 条匹配"
        if total >= max_results:
            header += f"（已达上限 {max_results}，可能还有更多）"
        header += ":\n"

        output = header + "\n".join(lines)

        if len(output.split("\n")) > self.SHELL_MAX_LINES:
            from ...core.tool_executor import save_overflow
            overflow_path = save_overflow("grep", output)
            truncated = "\n".join(output.split("\n")[:self.SHELL_MAX_LINES])
            truncated += (
                f"\n\n[OUTPUT_TRUNCATED] 完整结果已保存到: {overflow_path}\n"
                f'使用 read_file(path="{overflow_path}", offset={self.SHELL_MAX_LINES + 1}) '
                f"查看后续内容。"
            )
            return truncated

        return output

    async def _glob(self, params: dict) -> str:
        """文件名模式搜索"""
        pattern = params.get("pattern", "")
        if not pattern:
            return "❌ glob 缺少必要参数 'pattern'。"

        path = params.get("path", ".")

        # 不以 **/ 开头的 pattern 自动加 **/ 前缀，使其递归搜索
        if not pattern.startswith("**/"):
            pattern = f"**/{pattern}"

        dir_path = self.agent.file_tool._resolve_path(path)
        if not dir_path.is_dir():
            return f"❌ 目录不存在: {path}"

        from ..file import DEFAULT_IGNORE_DIRS

        results: list[tuple[str, float]] = []
        glob_pattern = pattern[3:] if pattern.startswith("**/") else pattern
        for p in dir_path.rglob(glob_pattern):
            if not p.is_file():
                continue
            parts = p.relative_to(dir_path).parts
            if any(part in DEFAULT_IGNORE_DIRS for part in parts):
                continue
            if any(
                part.startswith(".") and part not in (".github", ".vscode", ".cursor")
                for part in parts[:-1]
            ):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0
            results.append((str(p.relative_to(dir_path)), mtime))

        # 按修改时间降序排序
        results.sort(key=lambda x: x[1], reverse=True)

        if not results:
            return f"未找到匹配 '{pattern}' 的文件。"

        total = len(results)
        max_show = self.LIST_DIR_DEFAULT_MAX
        file_list = [r[0] for r in results[:max_show]]
        output = f"找到 {total} 个文件（按修改时间排序）:\n" + "\n".join(file_list)

        if total > max_show:
            output += (
                f"\n\n[OUTPUT_TRUNCATED] 共 {total} 个文件，已显示前 {max_show} 个。"
            )

        return output

    async def _delete_file(self, params: dict) -> str:
        """删除文件或空目录"""
        path = params.get("path", "")
        if not path:
            return "❌ delete_file 缺少必要参数 'path'。"

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = f"❌ 自检自动修复护栏：禁止删除该路径。\n目标: {target}"
                logger.warning(msg)
                return msg

        file_path = self.agent.file_tool._resolve_path(path)

        if not file_path.exists():
            return f"❌ 路径不存在: {path}"

        if file_path.is_dir():
            try:
                children = list(file_path.iterdir())
            except PermissionError:
                return f"❌ 没有权限访问目录: {path}"
            if children:
                return (
                    f"❌ 目录非空 ({len(children)} 个项目)，不允许直接删除。"
                    f"请确认是否确实需要删除此目录及其所有内容。"
                )

        success = await self.agent.file_tool.delete(path)
        if success:
            kind = "目录" if file_path.is_dir() else "文件"
            return f"{kind}已删除: {path}"
        return f"❌ 删除失败: {path}"


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
