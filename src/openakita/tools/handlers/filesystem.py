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
- move_file: 移动或重命名文件/目录
- delete_file: 删除文件

# ApprovalClass checklist (新增 / 修改工具时必读)
# 1. 在本文件 Handler 类的 TOOLS 列表加新工具名
# 2. 在同 Handler 类的 TOOL_CLASSES 字典加 ApprovalClass 显式声明
#    （或在 agent.py:_init_handlers 的 register() 调用里加 tool_classes={...}）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""

import logging
import re
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...core.policy_v2 import ApprovalClass
from ..path_safety import resolve_within_root

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_terminal_managers: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_terminal_mgr_strong_refs: dict[int, Any] = {}
_TRUNCATED_PREVIEW_MARKERS = (
    "[OUTPUT_TRUNCATED]",
    "[已截断",
    "[部分工具结果已截断",
    "[PAGE_HAS_MORE]",
)


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
        mgr.execution_env_spec = getattr(agent, "_execution_env_spec", None)
        for session in getattr(mgr, "sessions", {}).values():
            session.execution_env_spec = mgr.execution_env_spec
        return mgr
    cwd = getattr(agent, "default_cwd", None) or str(Path.cwd())
    mgr = TerminalSessionManager(
        default_cwd=cwd,
        execution_env_spec=getattr(agent, "_execution_env_spec", None),
    )
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
        "move_file",
        "delete_file",
    ]

    # C7：v2 PolicyEngine 显式分类（避免启发式回退到 UNKNOWN）。
    # 跨盘 / 工作区外路径的升级（MUTATING_SCOPED → MUTATING_GLOBAL）由
    # classifier._refine_with_params 处理；此处声明 base class 即可。
    TOOL_CLASSES = {
        "run_shell": ApprovalClass.EXEC_CAPABLE,
        "write_file": ApprovalClass.MUTATING_SCOPED,
        "read_file": ApprovalClass.READONLY_SCOPED,
        "edit_file": ApprovalClass.MUTATING_SCOPED,
        "list_directory": ApprovalClass.READONLY_GLOBAL,
        "grep": ApprovalClass.READONLY_SEARCH,
        "glob": ApprovalClass.READONLY_SEARCH,
        "move_file": ApprovalClass.MUTATING_SCOPED,
        "delete_file": ApprovalClass.DESTRUCTIVE,
    }

    def __init__(self, agent: "Agent"):
        """
        初始化处理器

        Args:
            agent: Agent 实例，用于访问 shell_tool 和 file_tool
        """
        self.agent = agent
        self._read_file_cache: dict[tuple[str, int, int], str] = {}

    def _get_fix_policy(self) -> dict | None:
        """
        获取自检自动修复策略（可选）

        当 SelfChecker 创建的修复 Agent 注入 _selfcheck_fix_policy 时启用。
        """
        policy = getattr(self.agent, "_selfcheck_fix_policy", None)
        if isinstance(policy, dict) and policy.get("enabled"):
            return policy
        return None

    @staticmethod
    def _looks_like_truncated_tool_preview(content: str) -> bool:
        """Detect tool preview/pagination markers that should not be written as file content."""
        if not isinstance(content, str) or not content:
            return False
        return any(marker in content for marker in _TRUNCATED_PREVIEW_MARKERS)

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

    def _allowed_roots(self) -> list[str]:
        roots = []
        roots.extend(getattr(self.agent, "allowed_roots", []) or [])
        roots.append(getattr(self.agent, "default_cwd", None) or str(Path.cwd()))
        try:
            roots.append(str(settings.data_dir))
        except Exception:
            pass
        return [str(r) for r in roots if r]

    def _guard_path_boundary(self, raw_path: str, *, op: str) -> str | None:
        result = resolve_within_root(raw_path, self._allowed_roots())
        if result.ok:
            return None
        try:
            from ...core.audit_logger import get_audit_logger

            get_audit_logger().log_event(
                "path_denial",
                {
                    "operation": op,
                    "reason": result.reason,
                    "path_ref": result.safe_ref,
                },
            )
        except Exception:
            pass
        return f"❌ 路径安全边界拒绝 {op}: {result.reason} ({result.safe_ref})"

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
        elif tool_name == "move_file":
            return await self._move_file(params)
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

        logger.info("[Windows fix] Multiline python -c → temp file: %s", tmp.name)
        return f'python "{tmp.name}"'

    # run_shell 成功输出最大行数
    SHELL_MAX_LINES = 200

    _EXIT_CODE_SEMANTICS: dict[str, dict[int, str]] = {
        "grep": {1: "无匹配结果（非错误）"},
        "egrep": {1: "无匹配结果（非错误）"},
        "fgrep": {1: "无匹配结果（非错误）"},
        "rg": {1: "无匹配结果（非错误）"},
        "diff": {1: "文件存在差异（非错误）"},
        "test": {1: "条件不成立（非错误）"},
        "find": {1: "部分路径无法访问（非错误）"},
        "cmp": {1: "文件不同（非错误）"},
        "where": {1: "未找到命令（非错误）"},
    }

    @classmethod
    def _format_run_shell_missing_command(cls, params: dict) -> str:
        """缺 'command' 参数时返回引导式错误，识别常见误传字段。

        - 列出实际收到的键，方便 LLM 发现自己传错；
        - 若误传 script/cmd/shell/bash/code，特别提示重命名为 'command'。
        """
        try:
            keys = list(params.keys()) if isinstance(params, dict) else []
        except Exception:
            keys = []

        wrong_alias = None
        if isinstance(params, dict):
            for alias in cls._RUN_SHELL_ALIAS_KEYS:
                if alias in params and params.get(alias):
                    wrong_alias = alias
                    break

        lines = [
            "❌ run_shell 缺少必要参数 'command'。",
            'Usage: run_shell(command="ls -la", working_directory=None, timeout=60)',
            f"You passed keys: {keys}",
        ]
        if wrong_alias is not None:
            lines.append(
                f"检测到你传了 '{wrong_alias}'，请改名为 'command' 后重试，参数值原样保留即可。"
            )
        else:
            lines.append(
                "常见误传字段：script / cmd / shell / bash / code → 都应使用 'command'。"
            )
        return "\n".join(lines)

    @classmethod
    def _interpret_exit_code(cls, command: str, exit_code: int) -> str | None:
        """Return a human-readable meaning if the exit code is a known
        non-error for the given command, or ``None`` otherwise."""
        stripped = command.strip()
        if not stripped:
            return None
        # Extract the first command segment, handling pipes / && / ;
        first_segment = (
            stripped.split("|")[0].strip().split("&&")[0].strip().split(";")[0].strip()
        )
        # Split into tokens; skip leading env-var assignments (VAR=val)
        tokens = first_segment.split()
        while tokens and "=" in tokens[0]:
            tokens = tokens[1:]
        if not tokens:
            return None
        cmd_name = Path(tokens[0]).stem
        meanings = cls._EXIT_CODE_SEMANTICS.get(cmd_name, {})
        return meanings.get(exit_code)

    # 常见的 LLM 误传字段名 -> 都应改写为 'command'
    _RUN_SHELL_ALIAS_KEYS = ("script", "cmd", "shell", "bash", "code")

    async def _run_shell(self, params: dict) -> str:
        """Execute shell command with persistent session + background support."""
        command = params.get("command", "")
        if not command:
            return self._format_run_shell_missing_command(params)

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
            timeout_s = params.get("timeout")
            if timeout_s is None:
                try:
                    block_timeout_ms = int(settings.run_shell_default_block_timeout_ms)
                except (TypeError, ValueError):
                    block_timeout_ms = 30000
            else:
                # 兼容旧 timeout（秒）参数。显式传入时才从秒换算为阻塞等待时间。
                try:
                    timeout_s = max(0, int(timeout_s))
                except (ValueError, TypeError):
                    timeout_s = 30
                block_timeout_ms = timeout_s * 1000
                try:
                    max_block_ms = int(settings.run_shell_max_block_timeout_ms)
                except (TypeError, ValueError):
                    max_block_ms = 1800000
                if max_block_ms > 0:
                    block_timeout_ms = min(block_timeout_ms, max_block_ms)
        else:
            try:
                block_timeout_ms = max(0, int(block_timeout_ms))
            except (ValueError, TypeError):
                block_timeout_ms = int(settings.run_shell_default_block_timeout_ms)

        session_id = params.get("session_id", 1)

        terminal_mgr = _get_terminal_manager(self.agent)
        previous_spec = getattr(terminal_mgr, "execution_env_spec", None)
        requested_scope = str(params.get("env_scope") or "").strip().lower()
        if requested_scope == "scratch":
            try:
                from ...runtime_envs import resolve_scratch_env

                terminal_mgr.execution_env_spec = resolve_scratch_env(
                    session_id=f"{getattr(self.agent, '_agent_profile_id', 'default')}:{session_id}"
                )
                for session in getattr(terminal_mgr, "sessions", {}).values():
                    session.execution_env_spec = terminal_mgr.execution_env_spec
            except Exception as exc:
                logger.warning("Failed to resolve scratch env for run_shell: %s", exc)
        elif requested_scope == "shared":
            terminal_mgr.execution_env_spec = None
            for session in getattr(terminal_mgr, "sessions", {}).values():
                session.execution_env_spec = None

        try:
            result = await terminal_mgr.execute(
                command,
                session_id=session_id,
                block_timeout_ms=block_timeout_ms,
                working_directory=working_directory,
            )
        finally:
            if requested_scope in {"scratch", "shared"}:
                terminal_mgr.execution_env_spec = previous_spec
                for session in getattr(terminal_mgr, "sessions", {}).values():
                    session.execution_env_spec = previous_spec

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
            # Check for known non-error exit codes before treating as failure
            exit_meaning = self._interpret_exit_code(command, result.returncode)
            if exit_meaning:
                log_buffer.add_log(
                    level="INFO",
                    module="shell",
                    message=f"$ {command}\n[exit: {result.returncode}, {exit_meaning}]\n{result.stdout}",
                )
                output = result.stdout or ""
                if result.stderr:
                    output += f"\n[信息]:\n{result.stderr}"
                full_text = (
                    f"命令执行完成 (exit code: {result.returncode}, {exit_meaning}):\n{output}"
                )
                return self._truncate_shell_output(full_text)

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
                        "⚠️ 当前 Shell 没有找到 Python 命令（Windows 9009 = 命令未找到）。\n"
                        "可以先尝试 `py -3 --version`，或使用已安装 Python 的完整路径。"
                        "如果确实未安装，再通过官网、Microsoft Store 或 winget 安装。"
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
            if not result.stdout and not result.stderr:
                truncated_result += "\n提示: 该命令没有输出；可换用更具体的诊断命令确认原因。"
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
        # 规范 path 名是 "path"；但 LLM 经常写成 filename/filepath/file_path。
        # 这里做一次保守兜底——只当权威的 path 缺失时才回退到别名，
        # 并且和 runtime._record_file_output 使用同一组别名，确保写盘成功后
        # 附件登记链路也能识别到同一个文件。schema 仍只声明 "path" 为主键
        # （见 tools/definitions/filesystem.py），tool description 会明确要求。
        path = (
            params.get("path")
            or params.get("filepath")
            or params.get("file_path")
            or params.get("filename")
        )
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
                    "2. 或用平台命令工具执行 Python 脚本生成大文件"
                    "（Windows 用 run_powershell，其他环境用 run_shell）"
                )
            return "❌ write_file 缺少必要参数 'path'。请提供文件路径和内容后重试。"
        if content is None:
            return "❌ write_file 缺少必要参数 'content'。请提供文件内容后重试。"
        guard = self._guard_path_boundary(path, op="write")
        if guard:
            return guard
        if self._looks_like_truncated_tool_preview(content):
            return (
                "❌ write_file 检测到内容里包含工具分页/截断预览标记，已拒绝写入，"
                "避免把不完整的预览内容覆盖到真实文件。\n"
                "请先用 read_file 的 offset/limit 继续读取缺失页，或使用 edit_file "
                "只修改目标片段；确认拿到完整内容后再写入。"
            )
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
        try:
            file_path = self.agent.file_tool._resolve_path(path)
            size = file_path.stat().st_size
            result = f"文件已写入: {path} ({size} bytes)"
        except OSError:
            result = f"文件已写入: {path}"

        from ...core.im_context import get_im_session

        if not get_im_session():
            # plan / ask 模式下 deliver_artifacts 是被 mode-guard 拦截的工具，
            # 这里再主动诱导只会让模型撞墙报"该工具在当前模式不可用"，
            # 用户体验和审计日志都很差。改成模式自适应：仅在 agent 模式
            # 才提示 deliver_artifacts，其它模式只引导内联展示文件内容。
            try:
                _exec_mode = getattr(
                    getattr(self.agent, "tool_executor", None), "_current_mode", "agent"
                )
            except Exception:
                _exec_mode = "agent"
            if _exec_mode == "agent":
                result += (
                    "\n\n💡 当前为 Desktop 模式，用户无法直接访问服务器文件。"
                    "请将文件的关键内容直接包含在回复中，"
                    "或调用 deliver_artifacts(artifacts=[{type: 'file', path: '"
                    + str(path)
                    + "'}]) 使文件在前端可下载。"
                )
            else:
                result += (
                    "\n\n💡 当前为 Desktop 模式且非 agent 执行模式，"
                    "请将文件的关键内容直接包含在回复中（如方案大纲/checklist），"
                    "供用户审阅。本模式下不提供文件下载工具。"
                )
        return result

    # read_file 默认最大行数。运行时可通过 READ_FILE_DEFAULT_LIMIT 调整。
    READ_FILE_DEFAULT_LIMIT = 2000

    async def _read_file(self, params: dict) -> str:
        """读取文件（支持 offset/limit 分页）"""
        path = params.get("path", "")
        if not path:
            return "❌ read_file 缺少必要参数 'path'。"
        unc_err = self._check_unc(path)
        if unc_err:
            return f"❌ {unc_err}"
        guard = self._guard_path_boundary(path, op="read")
        if guard:
            return guard

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
        limit = params.get("limit", getattr(settings, "read_file_default_limit", self.READ_FILE_DEFAULT_LIMIT))

        # 确保 offset/limit 合法
        try:
            offset = max(1, int(offset))
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            offset = 1
            limit = int(getattr(settings, "read_file_default_limit", self.READ_FILE_DEFAULT_LIMIT))

        cache_key = (str(self._resolve_to_abs(path)), offset, limit)
        cached = self._read_file_cache.get(cache_key)
        if cached is not None:
            return "♻️ 复用本轮 read_file 缓存结果：\n" + cached

        lines = content.split("\n")
        total_lines = len(lines)

        # 如果文件在 limit 范围内且从头读取，直接返回全部
        if total_lines <= limit and offset <= 1:
            result = f"文件内容 ({total_lines} 行):\n{content}"
            self._remember_read_file_cache(cache_key, result)
            return result

        # 分页截取
        start = offset - 1  # 转为 0-based
        end = min(start + limit, total_lines)

        if start >= total_lines:
            return (
                f"⚠️ offset={offset} 超出文件范围（文件共 {total_lines} 行）。\n"
                f'使用 read_file(path="{path}", offset=1, limit={limit}) 从头开始读取。'
            )

        shown = "\n".join(lines[start:end])
        result = f"文件内容 (第 {start + 1}-{end} 行，共 {total_lines} 行):\n{shown}"

        # 如果还有更多内容，附加分页提示
        if end < total_lines:
            remaining = total_lines - end
            result += (
                f"\n\n[PAGE_HAS_MORE] 这是分页读取结果，原文件未截断。"
                f"文件共 {total_lines} 行，当前仅显示第 {start + 1}-{end} 行，"
                f"剩余 {remaining} 行。\n"
                f'使用 read_file(path="{path}", offset={end + 1}, limit={limit}) '
                f"查看后续内容。"
            )

        self._remember_read_file_cache(cache_key, result)
        return result

    def _remember_read_file_cache(self, key: tuple[str, int, int], result: str) -> None:
        self._read_file_cache[key] = result
        if len(self._read_file_cache) > 64:
            oldest = next(iter(self._read_file_cache))
            self._read_file_cache.pop(oldest, None)

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
        guard = self._guard_path_boundary(path, op="edit")
        if guard:
            return guard

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = f"❌ 自检自动修复护栏：禁止编辑该路径。\n目标: {target}"
                logger.warning(msg)
                return msg

        replace_all = params.get("replace_all", False)

        try:
            result = await self.agent.file_tool.edit(
                path,
                old_string,
                new_string,
                replace_all=replace_all,
            )
            replaced = result["replaced"]
            try:
                file_path = self.agent.file_tool._resolve_path(path)
                size = file_path.stat().st_size
                size_info = f" ({size} bytes)"
            except OSError:
                size_info = ""
            if replace_all and replaced > 1:
                return f"文件已编辑: {path}（替换了 {replaced} 处匹配）{size_info}"
            return f"文件已编辑: {path}{size_info}"
        except FileNotFoundError:
            return f"❌ 文件不存在: {path}"
        except ValueError as e:
            return f"❌ edit_file 失败: {e}"

    async def _list_directory(self, params: dict) -> str:
        """列出目录（支持 pattern/recursive/max_items）"""
        path = params.get("path", "")
        if not path:
            return "❌ list_directory 缺少必要参数 'path'。"
        guard = self._guard_path_boundary(path, op="list")
        if guard:
            return guard

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
            path,
            pattern=pattern,
            recursive=recursive,
        )

        max_items = params.get("max_items", self.LIST_DIR_DEFAULT_MAX)
        try:
            max_items = max(1, int(max_items))
        except (TypeError, ValueError):
            max_items = self.LIST_DIR_DEFAULT_MAX

        total = len(files)
        if total <= max_items:
            result = f"目录内容 ({total} 条):\n" + "\n".join(files)
        else:
            shown = files[:max_items]
            result = f"目录内容 (显示前 {max_items} 条，共 {total} 条):\n" + "\n".join(shown)
            result += (
                f"\n\n[OUTPUT_TRUNCATED] 目录共 {total} 条目，已显示前 {max_items} 条。\n"
                f'如需查看更多，请使用 list_directory(path="{path}", max_items={total}) '
                f"或缩小查询范围。"
            )

        result = self._append_traversal_note(result)

        from ...utils.subdir_context import inject_subdir_context

        return inject_subdir_context(result, path)

    # grep 最大结果条目数
    GREP_MAX_RESULTS = 200

    async def _grep(self, params: dict) -> str:
        """内容搜索"""
        import asyncio

        pattern = params.get("pattern", "")
        if not pattern:
            return "❌ grep 缺少必要参数 'pattern'。"

        path = params.get("path", ".")
        guard = self._guard_path_boundary(path, op="grep")
        if guard:
            return guard
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
            from ...config import settings

            grep_timeout = max(
                5,
                min(int(getattr(settings, "grep_timeout_sec", 30) or 30), 600),
            )
        except Exception:
            grep_timeout = 30

        try:
            results = await asyncio.wait_for(
                self.agent.file_tool.grep(
                    pattern,
                    path,
                    include=include,
                    context_lines=context_lines,
                    max_results=max_results,
                    case_insensitive=case_insensitive,
                ),
                timeout=grep_timeout,
            )
        except FileNotFoundError as e:
            return f"❌ {e}"
        except ValueError as e:
            msg = str(e)
            if msg.startswith("grep refused"):
                return (
                    f"❌ grep 被拒绝执行: {msg}\n"
                    f"提示: 请缩小 path 到具体的项目子目录（如 src/、docs/），"
                    f"避免扫描运行时数据目录或整个用户主目录。"
                )
            return f"❌ 正则表达式错误: {e}"
        except TimeoutError:
            return (
                f"❌ grep 超时（>{grep_timeout}s）。"
                f"建议：1) 用更精确的 path 缩小范围；2) 用 include 限定文件类型"
                f'（如 include="*.py"）；3) 用更具体的 pattern。（可配置 grep_timeout_sec）'
            )

        if not results:
            return self._append_traversal_note(f"未找到匹配 '{pattern}' 的内容。")

        scan_summary = ""
        match_results = []
        for r in results:
            if "_scan_summary" in r:
                scan_summary = r["_scan_summary"]
            else:
                match_results.append(r)

        if not match_results and scan_summary:
            return self._append_traversal_note(
                f"未找到匹配 '{pattern}' 的内容。\n[grep] {scan_summary}"
            )

        lines: list[str] = []
        for m in match_results:
            if context_lines > 0 and "context_before" in m:
                for ctx_line in m["context_before"]:
                    lines.append(f"{m['file']}-{ctx_line}")
            lines.append(f"{m['file']}:{m['line']}:{m['text']}")
            if context_lines > 0 and "context_after" in m:
                for ctx_line in m["context_after"]:
                    lines.append(f"{m['file']}-{ctx_line}")
                lines.append("")
        if scan_summary:
            lines.append(f"\n[grep] {scan_summary}")

        total = len(match_results)
        header = f"找到 {total} 条匹配"
        if total >= max_results:
            header += f"（已达上限 {max_results}，可能还有更多）"
        header += ":\n"

        output = header + "\n".join(lines)

        if len(output.split("\n")) > self.SHELL_MAX_LINES:
            from ...core.tool_executor import save_overflow

            overflow_path = save_overflow("grep", output)
            truncated = "\n".join(output.split("\n")[: self.SHELL_MAX_LINES])
            truncated += (
                f"\n\n[OUTPUT_TRUNCATED] 完整结果已保存到: {overflow_path}\n"
                f'使用 read_file(path="{overflow_path}", offset={self.SHELL_MAX_LINES + 1}) '
                f"查看后续内容。"
            )
            return truncated

        return self._append_traversal_note(output)

    async def _glob(self, params: dict) -> str:
        """文件名模式搜索"""
        pattern = params.get("pattern", "")
        if not pattern:
            return "❌ glob 缺少必要参数 'pattern'。"

        path = params.get("path", ".")
        guard = self._guard_path_boundary(path, op="glob")
        if guard:
            return guard

        # 不以 **/ 开头的 pattern 自动加 **/ 前缀，使其递归搜索
        if not pattern.startswith("**/"):
            pattern = f"**/{pattern}"

        dir_path = self.agent.file_tool._resolve_path(path)
        if not dir_path.is_dir():
            return f"❌ 目录不存在: {path}"

        results: list[tuple[str, float]] = []
        glob_pattern = pattern[3:] if pattern.startswith("**/") else pattern
        for p in self.agent.file_tool._iter_matching_paths(
            dir_path,
            glob_pattern,
            recursive=True,
        ):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0
            results.append((str(p.relative_to(dir_path)), mtime))

        # 按修改时间降序排序
        results.sort(key=lambda x: x[1], reverse=True)

        if not results:
            return self._append_traversal_note(f"未找到匹配 '{pattern}' 的文件。")

        total = len(results)
        max_show = self.LIST_DIR_DEFAULT_MAX
        file_list = [r[0] for r in results[:max_show]]
        output = f"找到 {total} 个文件（按修改时间排序）:\n" + "\n".join(file_list)

        if total > max_show:
            output += f"\n\n[OUTPUT_TRUNCATED] 共 {total} 个文件，已显示前 {max_show} 个。"

        return self._append_traversal_note(output)

    def _append_traversal_note(self, output: str) -> str:
        skipped = getattr(self.agent.file_tool, "last_traversal_skipped", 0)
        if not skipped:
            return output
        return (
            f"{output}\n\n[提示] 已跳过 {skipped} 个不可访问或临时变化的目录，"
            "其余结果已正常返回。"
        )

    async def _move_file(self, params: dict) -> str:
        """移动或重命名文件/目录，并验证磁盘状态。"""
        src = (
            params.get("src")
            or params.get("source")
            or params.get("source_path")
            or params.get("from")
            or ""
        )
        dst = (
            params.get("dst")
            or params.get("destination")
            or params.get("target_path")
            or params.get("to")
            or ""
        )
        if not src or not dst:
            return "❌ move_file 缺少必要参数 'src' 和 'dst'。"
        if "\x00" in src or "\x00" in dst:
            return "❌ move_file 路径包含无效空字符，请去掉不可见字符后重试。"
        for raw in (src, dst):
            guard = self._guard_path_boundary(raw, op="move")
            if guard:
                return guard

        policy = self._get_fix_policy()
        if policy:
            write_roots = policy.get("write_roots") or []
            for raw in (src, dst):
                target = self._resolve_to_abs(raw)
                if not self._is_under_any_root(target, write_roots):
                    msg = f"❌ 自检自动修复护栏：禁止移动该路径。\n目标: {target}"
                    logger.warning(msg)
                    return msg

        src_path = self.agent.file_tool._resolve_path(src)
        dst_path = self.agent.file_tool._resolve_path(dst)

        if not src_path.exists():
            return f"❌ 源路径不存在: {src}"

        final_dst_path = dst_path / src_path.name if dst_path.exists() and dst_path.is_dir() else dst_path
        kind = "目录" if src_path.is_dir() else "文件"
        success = await self.agent.file_tool.move(src, dst)
        if not success:
            return f"❌ 移动失败: {src} -> {dst}"
        if src_path.exists():
            return f"⚠️ 移动操作返回成功但源路径仍存在: {src}"
        if not final_dst_path.exists():
            return f"⚠️ 移动操作返回成功但目标路径不存在: {final_dst_path}"
        return f"{kind}已移动: {src} -> {final_dst_path}"

    async def _delete_file(self, params: dict) -> str:
        """删除文件或空目录"""
        path = params.get("path", "")
        if not path:
            return "❌ delete_file 缺少必要参数 'path'。"
        guard = self._guard_path_boundary(path, op="delete")
        if guard:
            return guard

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

        is_dir = file_path.is_dir()
        success = await self.agent.file_tool.delete(path)
        if success:
            if file_path.exists():
                return f"⚠️ 删除操作返回成功但路径仍存在: {path}"
            kind = "目录" if is_dir else "文件"
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

