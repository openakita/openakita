"""
Terminal Session Manager — persistent shell sessions with background process support.

Inspired by Cursor's "terminal as file" abstraction:
- Each terminal session persists across multiple run_shell calls
- Working directory and environment variables carry over
- Long-running commands auto-background after block_timeout_ms
- Output streams to data/terminals/{id}.txt for async monitoring
"""

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ShellResult:
    """Result from a terminal command execution."""

    returncode: int
    stdout: str
    stderr: str
    backgrounded: bool = False
    terminal_file: str | None = None
    pid: int | None = None

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout + (f"\n{self.stderr}" if self.stderr else "")


@dataclass
class TerminalSession:
    """A persistent terminal session that maintains state across commands."""

    id: int
    cwd: str
    env: dict = field(default_factory=dict)
    last_command: str | None = None
    last_exit_code: int | None = None
    _bg_process: asyncio.subprocess.Process | None = field(
        default=None, repr=False
    )
    _bg_task: asyncio.Task | None = field(default=None, repr=False)
    _started_at: float | None = field(default=None, repr=False)

    def _get_terminal_dir(self) -> Path:
        from ..config import settings
        terminal_dir = Path(settings.openakita_home) / "data" / "terminals"
        terminal_dir.mkdir(parents=True, exist_ok=True)
        return terminal_dir

    @property
    def output_file(self) -> Path:
        return self._get_terminal_dir() / f"{self.id}.txt"

    def _write_header(self, pid: int, command: str) -> None:
        self._started_at = time.time()
        header = (
            f"---\n"
            f"pid: {pid}\n"
            f"cwd: {self.cwd}\n"
            f"last_command: {command}\n"
            f"running_for_ms: 0\n"
            f"---\n"
            f"$ {command}\n\n"
        )
        self.output_file.write_text(header, encoding="utf-8")

    def _update_running_time(self) -> None:
        if not self.output_file.exists() or self._started_at is None:
            return
        elapsed_ms = int((time.time() - self._started_at) * 1000)
        try:
            content = self.output_file.read_text(encoding="utf-8")
            import re
            content = re.sub(
                r"running_for_ms: \d+",
                f"running_for_ms: {elapsed_ms}",
                content,
                count=1,
            )
            self.output_file.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _write_footer(self, exit_code: int) -> None:
        elapsed_ms = (
            int((time.time() - self._started_at) * 1000) if self._started_at else 0
        )
        footer = (
            f"\n---\n"
            f"exit_code: {exit_code}\n"
            f"elapsed_ms: {elapsed_ms}\n"
            f"---\n"
        )
        try:
            with open(self.output_file, "a", encoding="utf-8") as f:
                f.write(footer)
        except Exception:
            pass

    def _append_output(self, text: str) -> None:
        try:
            with open(self.output_file, "a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _decode_output(self, data: bytes) -> str:
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            if sys.platform == "win32":
                try:
                    import ctypes
                    oem_cp = ctypes.windll.kernel32.GetOEMCP()
                    return data.decode(f"cp{oem_cp}", errors="replace")
                except Exception:
                    pass
            return data.decode("utf-8", errors="replace")

    async def execute(
        self,
        command: str,
        block_timeout_ms: int = 30000,
        working_directory: str | None = None,
    ) -> ShellResult:
        """Execute a command in this terminal session.

        If the command completes within block_timeout_ms, returns the result directly.
        Otherwise, the command continues in the background and output streams to
        the terminal file at data/terminals/{id}.txt.

        Uses streaming I/O (never communicate()) to avoid data loss on timeout.
        """
        if working_directory:
            self.cwd = str(Path(working_directory).resolve())

        cmd_env = os.environ.copy()
        cmd_env.update(self.env)

        try:
            from ..runtime_env import IS_FROZEN, get_python_executable
            if IS_FROZEN:
                _ext_py = get_python_executable()
                if _ext_py:
                    _py_dir = str(Path(_ext_py).parent)
                    cmd_env["PATH"] = _py_dir + os.pathsep + cmd_env.get("PATH", "")
        except Exception:
            pass

        actual_command = self._prepare_command(command)
        self.last_command = command

        logger.info(f"Terminal {self.id}: executing '{command[:200]}'")

        process = await asyncio.create_subprocess_shell(
            actual_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=cmd_env,
        )

        pid = process.pid
        self._bg_process = process
        self._write_header(pid, command)

        def _make_bg_result(reason: str) -> ShellResult:
            return ShellResult(
                returncode=-1,
                stdout=(
                    f"{reason}\n"
                    f"Output streaming to: {self.output_file}\n"
                    f"Monitor with: read_file(path=\"{self.output_file}\")\n"
                    f"The terminal file header has pid and running_for_ms "
                    f"(updated every 5s).\n"
                    f"When finished, a footer with exit_code and elapsed_ms "
                    f"will appear.\n"
                    f"Poll with exponential backoff to check progress.\n"
                    f"Kill if needed: run_shell(command=\"kill {pid}\")"
                ),
                stderr="",
                backgrounded=True,
                terminal_file=str(self.output_file),
                pid=pid,
            )

        # Stream output into both in-memory buffer and terminal file.
        # Use asyncio.shield so the collector task survives a wait_for timeout
        # — this avoids data loss that would occur with communicate() + cancel.
        collected_stdout: list[str] = []
        collected_stderr: list[str] = []

        async def _collect_output() -> None:
            """Read stdout/stderr streams, appending to buffer + terminal file."""
            update_interval = 5.0
            last_update = time.time()

            try:
                if process.stdout:
                    async for line_bytes in process.stdout:
                        line = self._decode_output(line_bytes)
                        collected_stdout.append(line)
                        self._append_output(line)

                        now = time.time()
                        if now - last_update >= update_interval:
                            self._update_running_time()
                            last_update = now

                await process.wait()

                if process.stderr:
                    stderr_bytes = await process.stderr.read()
                    stderr = self._decode_output(stderr_bytes)
                    if stderr:
                        collected_stderr.append(stderr)
                        self._append_output(f"\n[stderr]:\n{stderr}")

                exit_code = process.returncode or 0
                self.last_exit_code = exit_code
                self._write_footer(exit_code)

            except Exception as e:
                logger.error(f"Terminal {self.id}: stream error: {e}")
                self._append_output(f"\n[ERROR]: {e}\n")
                self._write_footer(-1)
            finally:
                self._bg_process = None

        if block_timeout_ms == 0:
            self._bg_task = asyncio.create_task(_collect_output())
            return _make_bg_result(f"Command started in background (pid: {pid}).")

        collector_task = asyncio.create_task(_collect_output())

        try:
            # shield() prevents the collector task from being cancelled on timeout
            await asyncio.wait_for(
                asyncio.shield(collector_task),
                timeout=block_timeout_ms / 1000.0,
            )

            # Process completed within timeout
            stdout = "".join(collected_stdout)
            stderr = "".join(collected_stderr)
            exit_code = process.returncode or 0
            self.last_exit_code = exit_code
            self._bg_process = None

            return ShellResult(
                returncode=exit_code,
                stdout=stdout,
                stderr=stderr,
                pid=pid,
            )

        except TimeoutError:
            # Timeout — collector task continues running (protected by shield)
            self._bg_task = collector_task
            return _make_bg_result(
                f"Command did not complete within {block_timeout_ms}ms, "
                f"moved to background (pid: {pid})."
            )

        except asyncio.CancelledError:
            collector_task.cancel()
            if process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            raise

    _cached_shell_tool: Any = None

    def _prepare_command(self, command: str) -> str:
        """Prepare command for execution (Windows encoding, etc.)."""
        if TerminalSession._cached_shell_tool is None:
            from .shell import ShellTool
            tool = ShellTool.__new__(ShellTool)
            tool._is_windows = sys.platform == "win32"
            tool._oem_encoding = None
            TerminalSession._cached_shell_tool = tool

        tool = TerminalSession._cached_shell_tool
        if tool._is_windows and tool._needs_powershell(command):
            return tool._wrap_for_powershell(command)
        elif tool._is_windows:
            return f"chcp 65001 >nul && {command}"
        return command


class TerminalSessionManager:
    """Manages multiple persistent terminal sessions."""

    def __init__(self, default_cwd: str | None = None):
        self.sessions: dict[int, TerminalSession] = {}
        self.default_cwd = default_cwd or os.getcwd()
        self._next_id = 1

    def get_or_create(self, session_id: int = 1) -> TerminalSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = TerminalSession(
                id=session_id,
                cwd=self.default_cwd,
            )
            if session_id >= self._next_id:
                self._next_id = session_id + 1
        return self.sessions[session_id]

    def list_sessions(self) -> list[dict]:
        result = []
        for sid, session in self.sessions.items():
            result.append({
                "id": sid,
                "cwd": session.cwd,
                "last_command": session.last_command,
                "last_exit_code": session.last_exit_code,
                "has_background_process": session._bg_process is not None,
            })
        return result

    async def execute(
        self,
        command: str,
        session_id: int = 1,
        block_timeout_ms: int = 30000,
        working_directory: str | None = None,
    ) -> ShellResult:
        session = self.get_or_create(session_id)
        return await session.execute(
            command,
            block_timeout_ms=block_timeout_ms,
            working_directory=working_directory,
        )
