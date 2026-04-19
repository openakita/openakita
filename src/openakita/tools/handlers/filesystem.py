"""
Filesystem handler

Handles filesystem-related system skills:
- run_shell: Execute shell commands (persistent session + background process support)
- write_file: Write a file
- read_file: Read a file
- edit_file: Exact string-replacement editing
- list_directory: List a directory
- grep: Content search
- glob: Filename pattern search
- delete_file: Delete a file
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
    Filesystem handler

    Handles all filesystem-related tool calls.
    """

    # Tools handled by this handler
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
        Initialize the handler.

        Args:
            agent: Agent instance, used to access shell_tool and file_tool
        """
        self.agent = agent

    def _get_fix_policy(self) -> dict | None:
        """
        Get the self-check auto-fix policy (optional).

        Enabled when the fix Agent created by SelfChecker injects _selfcheck_fix_policy.
        """
        policy = getattr(self.agent, "_selfcheck_fix_policy", None)
        if isinstance(policy, dict) and policy.get("enabled"):
            return policy
        return None

    def _resolve_to_abs(self, raw: str) -> Path:
        p = Path(raw)
        if p.is_absolute():
            return p.resolve()
        # FileTool uses cwd as base_path; stay consistent here
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
        Handle a tool call.

        Args:
            tool_name: Tool name
            params: Parameter dictionary

        Returns:
            Execution result string
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
        """Fix multiline python -c on Windows.

        Windows cmd.exe cannot correctly handle newlines inside python -c "...",
        causing Python to only execute the first line (typically an import) and leaving
        stdout empty. When a multiline python -c is detected, write it to a temporary
        .py file and execute that instead.
        """
        import tempfile

        stripped = command.strip()

        # Match python -c "..." or python -c '...' or python - <<'EOF'
        # Only handle cases that contain newlines
        m = re.match(
            r'^python(?:3)?(?:\.exe)?\s+-c\s+["\'](.+)["\']$',
            stripped,
            re.DOTALL,
        )
        if not m:
            # Also match heredoc form: python - <<'PY' ... PY
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

        # Only multiline code needs fixing
        if "\n" not in code:
            return command

        # Write to a temporary file (delete=False requires manual cleanup, not context manager)
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

    # Maximum number of lines returned on successful run_shell output
    SHELL_MAX_LINES = 200

    _EXIT_CODE_SEMANTICS: dict[str, dict[int, str]] = {
        "grep": {1: "no matches found (not an error)"},
        "egrep": {1: "no matches found (not an error)"},
        "fgrep": {1: "no matches found (not an error)"},
        "rg": {1: "no matches found (not an error)"},
        "diff": {1: "files differ (not an error)"},
        "test": {1: "condition is false (not an error)"},
        "find": {1: "some paths inaccessible (not an error)"},
        "cmp": {1: "files differ (not an error)"},
        "where": {1: "command not found (not an error)"},
    }

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

    async def _run_shell(self, params: dict) -> str:
        """Execute shell command with persistent session + background support."""
        command = params.get("command", "")
        if not command:
            return "run_shell is missing required parameter 'command'."

        policy = self._get_fix_policy()
        if policy:
            deny_patterns = policy.get("deny_shell_patterns") or []
            for pat in deny_patterns:
                try:
                    if re.search(pat, command, flags=re.IGNORECASE):
                        msg = (
                            "Self-check auto-fix guardrail: commands touching system/Windows layer are not allowed."
                            f"\nCommand: {command}"
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
            # Ensure timeout_s is an int (avoid TypeError if a string is passed in)
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
                output += f"\n[warning]:\n{result.stderr}"

            full_text = f"Command succeeded (exit code: 0):\n{output}"
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
                    output += f"\n[info]:\n{result.stderr}"
                full_text = (
                    f"Command finished (exit code: {result.returncode}, {exit_meaning}):\n{output}"
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
                    text = f"...(truncated, only the last {max_lines} lines retained)\n{text}"
                if len(text) > max_chars:
                    text = text[-max_chars:]
                    text = f"...(truncated, only the last {max_chars} characters retained)\n{text}"
                return text

            output_parts = [f"Command failed (exit code: {result.returncode})"]

            if result.returncode == 9009:
                cmd_lower = command.strip().lower()
                if cmd_lower.startswith(("python", "python3")):
                    output_parts.append(
                        "Python is not on the system PATH (Windows 9009 = command not found).\n"
                        "Please install Python first: run_shell 'winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements'\n"
                        "The system will detect it automatically once installed; no restart needed. Do not retry python/python3 commands."
                    )
                else:
                    first_word = command.strip().split()[0] if command.strip() else command
                    output_parts.append(
                        f"'{first_word}' is not on the system PATH (Windows 9009 = command not found).\n"
                        "Check whether the program is installed, or use its full path."
                    )

            if result.stdout:
                output_parts.append(f"[stdout-tail]:\n{_tail(result.stdout)}")
            if result.stderr:
                output_parts.append(f"[stderr-tail]:\n{_tail(result.stderr)}")
            if not result.stdout and not result.stderr and result.returncode != 9009:
                output_parts.append("(no output; the command may not exist or have a syntax error)")

            full_error = "\n".join(output_parts)
            truncated_result = self._truncate_shell_output(full_error)
            truncated_result += (
                "\nTip: if the cause is unclear, call get_session_logs for detailed logs, or try a different command."
            )
            return truncated_result

    def _truncate_shell_output(self, text: str) -> str:
        """Truncate shell output; large outputs are saved to an overflow file with a pagination hint."""
        lines = text.split("\n")
        if len(lines) <= self.SHELL_MAX_LINES:
            return text

        total_lines = len(lines)
        from ...core.tool_executor import save_overflow

        overflow_path = save_overflow("run_shell", text)
        truncated = "\n".join(lines[: self.SHELL_MAX_LINES])
        truncated += (
            f"\n\n[OUTPUT_TRUNCATED] Command output has {total_lines} lines; "
            f"showing the first {self.SHELL_MAX_LINES}.\n"
            f"Full output saved to: {overflow_path}\n"
            f'Use read_file(path="{overflow_path}", offset={self.SHELL_MAX_LINES + 1}) '
            f"to view the rest."
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
        """Write to a file."""
        # The canonical path key is "path", but LLMs often use filename/filepath/file_path.
        # Do a conservative fallback here — only fall back to aliases when the canonical
        # path is missing, using the same alias set as runtime._record_file_output so that
        # once the write succeeds, the attachment-registration path also sees the same file.
        # The schema still declares "path" as the sole primary key (see
        # tools/definitions/filesystem.py); the tool description will make this explicit.
        path = (
            params.get("path")
            or params.get("filepath")
            or params.get("file_path")
            or params.get("filename")
        )
        unc_err = self._check_unc(path)
        if unc_err:
            return unc_err
        content = params.get("content")
        if not path:
            content_len = len(str(content)) if content else 0
            if content_len > 5000:
                return (
                    f"write_file is missing required parameter 'path' (content length {content_len} chars; "
                    "the JSON argument may have been truncated because it was too long).\n"
                    "Please shorten the content and retry:\n"
                    "1. Split a large file into multiple writes (< 8000 chars each)\n"
                    "2. Or use run_shell to run a Python script that generates the large file"
                )
            return "write_file is missing required parameter 'path'. Please provide the file path and content, then retry."
        if content is None:
            return "write_file is missing required parameter 'content'. Please provide the file content, then retry."
        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = (
                    "Self-check auto-fix guardrail: writing to this path is not allowed (only tools/skills/mcps/channels directories are allowed for fixes)."
                    f"\nTarget: {target}"
                )
                logger.warning(msg)
                return msg
        await self.agent.file_tool.write(path, content)
        try:
            file_path = self.agent.file_tool._resolve_path(path)
            size = file_path.stat().st_size
            result = f"File written: {path} ({size} bytes)"
        except OSError:
            result = f"File written: {path}"

        from ...core.im_context import get_im_session

        if not get_im_session():
            result += (
                "\n\nNote: currently in Desktop mode; the user cannot directly access server files. "
                "Include the key contents of the file directly in your reply, "
                "or call deliver_artifacts(artifacts=[{type: 'file', path: '"
                + str(path)
                + "'}]) to make the file downloadable in the frontend."
            )
        return result

    # read_file default max lines (Claude Code uses 2000; we use 300 as a more conservative default)
    READ_FILE_DEFAULT_LIMIT = 300

    async def _read_file(self, params: dict) -> str:
        """Read a file (supports offset/limit pagination)."""
        path = params.get("path", "")
        if not path:
            return "read_file is missing required parameter 'path'."
        unc_err = self._check_unc(path)
        if unc_err:
            return unc_err

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"Self-check auto-fix guardrail: reading this path is not allowed.\nTarget: {target}"
                logger.warning(msg)
                return msg

        content = await self.agent.file_tool.read(path)

        offset = params.get("offset", 1)  # Starting line number (1-based); defaults to line 1
        limit = params.get("limit", self.READ_FILE_DEFAULT_LIMIT)

        # Ensure offset/limit are valid
        try:
            offset = max(1, int(offset))
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            offset, limit = 1, self.READ_FILE_DEFAULT_LIMIT

        lines = content.split("\n")
        total_lines = len(lines)

        # If the file fits within limit and we are reading from the start, return all of it
        if total_lines <= limit and offset <= 1:
            return f"File content ({total_lines} lines):\n{content}"

        # Paginate
        start = offset - 1  # Convert to 0-based
        end = min(start + limit, total_lines)

        if start >= total_lines:
            return (
                f"offset={offset} is out of range (file has {total_lines} lines).\n"
                f'Use read_file(path="{path}", offset=1, limit={limit}) to read from the start.'
            )

        shown = "\n".join(lines[start:end])
        result = f"File content (lines {start + 1}-{end} of {total_lines}):\n{shown}"

        # If there's more content, add a pagination hint
        if end < total_lines:
            remaining = total_lines - end
            result += (
                f"\n\n[OUTPUT_TRUNCATED] File has {total_lines} lines; "
                f"currently showing lines {start + 1}-{end}, {remaining} more remaining.\n"
                f'Use read_file(path="{path}", offset={end + 1}, limit={limit}) '
                f"to view the rest."
            )

        return result

    # Default max entries for list_directory
    LIST_DIR_DEFAULT_MAX = 200

    async def _edit_file(self, params: dict) -> str:
        """Exact string-replacement editing."""
        path = params.get("path", "")
        old_string = params.get("old_string")
        new_string = params.get("new_string")

        if not path:
            return "edit_file is missing required parameter 'path'."
        if old_string is None:
            return "edit_file is missing required parameter 'old_string'."
        if new_string is None:
            return "edit_file is missing required parameter 'new_string'."
        if old_string == new_string:
            return "old_string and new_string are identical; no replacement needed."

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = f"Self-check auto-fix guardrail: editing this path is not allowed.\nTarget: {target}"
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
                return f"File edited: {path} ({replaced} matches replaced){size_info}"
            return f"File edited: {path}{size_info}"
        except FileNotFoundError:
            return f"File not found: {path}"
        except ValueError as e:
            return f"edit_file failed: {e}"

    async def _list_directory(self, params: dict) -> str:
        """List a directory (supports pattern/recursive/max_items)."""
        path = params.get("path", "")
        if not path:
            return "list_directory is missing required parameter 'path'."

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            read_roots = policy.get("read_roots") or []
            if not self._is_under_any_root(target, read_roots):
                msg = f"Self-check auto-fix guardrail: listing this directory is not allowed.\nTarget: {target}"
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
            result = f"Directory contents ({total} entries):\n" + "\n".join(files)
        else:
            shown = files[:max_items]
            result = f"Directory contents (showing first {max_items} of {total}):\n" + "\n".join(shown)
            result += (
                f"\n\n[OUTPUT_TRUNCATED] Directory has {total} entries; showing the first {max_items}.\n"
                f'For more, use list_directory(path="{path}", max_items={total}) '
                f"or narrow the query."
            )

        from ...utils.subdir_context import inject_subdir_context

        return inject_subdir_context(result, path)

    # Max number of grep result entries
    GREP_MAX_RESULTS = 200

    async def _grep(self, params: dict) -> str:
        """Content search."""
        pattern = params.get("pattern", "")
        if not pattern:
            return "grep is missing required parameter 'pattern'."

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
                pattern,
                path,
                include=include,
                context_lines=context_lines,
                max_results=max_results,
                case_insensitive=case_insensitive,
            )
        except FileNotFoundError as e:
            return f"{e}"
        except ValueError as e:
            return f"Regex error: {e}"

        if not results:
            return f"No content matching '{pattern}' was found."

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
        header = f"Found {total} match(es)"
        if total >= max_results:
            header += f" (limit {max_results} reached; there may be more)"
        header += ":\n"

        output = header + "\n".join(lines)

        if len(output.split("\n")) > self.SHELL_MAX_LINES:
            from ...core.tool_executor import save_overflow

            overflow_path = save_overflow("grep", output)
            truncated = "\n".join(output.split("\n")[: self.SHELL_MAX_LINES])
            truncated += (
                f"\n\n[OUTPUT_TRUNCATED] Full results saved to: {overflow_path}\n"
                f'Use read_file(path="{overflow_path}", offset={self.SHELL_MAX_LINES + 1}) '
                f"to view the rest."
            )
            return truncated

        return output

    async def _glob(self, params: dict) -> str:
        """Filename pattern search."""
        pattern = params.get("pattern", "")
        if not pattern:
            return "glob is missing required parameter 'pattern'."

        path = params.get("path", ".")

        # Patterns not starting with **/ get **/ prepended so the search is recursive
        if not pattern.startswith("**/"):
            pattern = f"**/{pattern}"

        dir_path = self.agent.file_tool._resolve_path(path)
        if not dir_path.is_dir():
            return f"Directory not found: {path}"

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

        # Sort by modification time descending
        results.sort(key=lambda x: x[1], reverse=True)

        if not results:
            return f"No files matching '{pattern}' were found."

        total = len(results)
        max_show = self.LIST_DIR_DEFAULT_MAX
        file_list = [r[0] for r in results[:max_show]]
        output = f"Found {total} file(s) (sorted by modification time):\n" + "\n".join(file_list)

        if total > max_show:
            output += f"\n\n[OUTPUT_TRUNCATED] {total} files in total; showing the first {max_show}."

        return output

    async def _delete_file(self, params: dict) -> str:
        """Delete a file or an empty directory."""
        path = params.get("path", "")
        if not path:
            return "delete_file is missing required parameter 'path'."

        policy = self._get_fix_policy()
        if policy:
            target = self._resolve_to_abs(path)
            write_roots = policy.get("write_roots") or []
            if not self._is_under_any_root(target, write_roots):
                msg = f"Self-check auto-fix guardrail: deleting this path is not allowed.\nTarget: {target}"
                logger.warning(msg)
                return msg

        file_path = self.agent.file_tool._resolve_path(path)

        if not file_path.exists():
            return f"Path not found: {path}"

        if file_path.is_dir():
            try:
                children = list(file_path.iterdir())
            except PermissionError:
                return f"No permission to access directory: {path}"
            if children:
                return (
                    f"Directory is not empty ({len(children)} items); direct deletion is not allowed. "
                    f"Please confirm whether you really want to delete this directory and all of its contents."
                )

        is_dir = file_path.is_dir()
        success = await self.agent.file_tool.delete(path)
        if success:
            if file_path.exists():
                return f"Delete operation reported success but the path still exists: {path}"
            kind = "Directory" if is_dir else "File"
            return f"{kind} deleted: {path}"
        return f"Delete failed: {path}"


def create_handler(agent: "Agent"):
    """
    Create a filesystem handler.

    Args:
        agent: Agent instance

    Returns:
        The handler's handle method
    """
    handler = FilesystemHandler(agent)
    return handler.handle
