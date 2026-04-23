# src/openakita/agents/cli_providers/copilot.py
"""GitHub Copilot CLI provider adapter.

Copilot CLI is text-stream-only; there is no JSON output mode. Tool events
arrive as lines like `[tool: read_file]` interleaved with plain assistant
text. Session id appears in a marker line `[session: <id>]`. ANSI escape
sequences are stripped per-line before parsing.

Message delivery: Copilot expects the prompt on stdin (`--input-file -`).
Our helper writes the message to stdin after spawn. In tests that rely on
fake binaries (sh -c), set env COPILOT_SUPPRESS_STDIN=1 to skip the write.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers._common import (
    binary_not_found_error,
    build_cli_env,
    stream_cli_subprocess,
)
from openakita.agents.cli_runner import (
    CliRunRequest,
    ExitReason,
    ProviderRunResult,
)
from openakita.agents.profile import CliPermissionMode
from openakita.tools.errors import classify_cli_error
from openakita.utils.path_helper import which_command

logger = logging.getLogger(__name__)

SESSION_ROOT: Path = Path.home() / ".copilot" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.COPILOT

ANSI_RE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
TOOL_MARKER_RE = re.compile(r"^\[tool:\s*([^\]]+)\]\s*$")
SESSION_MARKER_RE = re.compile(r"^\[session:\s*([^\]]+)\]\s*$")


def _resolve_binary() -> str | None:
    return which_command("copilot")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub(b"", text.encode("utf-8", "replace")).decode("utf-8", "replace")


@dataclass
class _TurnAccumulator:
    session_id: str | None = None
    text_parts: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    def apply(self, line: str) -> None:
        m = TOOL_MARKER_RE.match(line)
        if m:
            self.tools_used.append(m.group(1).strip())
            return
        m = SESSION_MARKER_RE.match(line)
        if m:
            self.session_id = m.group(1).strip()
            return
        if line:
            self.text_parts.append(line)


class CopilotAdapter:
    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="copilot",
                binary="copilot",
                install_hint="npm install -g @github/copilot-cli",
            )
        argv = [binary, "--no-color", "--input-file", "-"]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--yes")
        if request.resume_id:
            argv += ["--session", request.resume_id]
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        return build_cli_env()

    async def run(self, request, argv, env, *, on_spawn):
        acc = _TurnAccumulator()
        proc_ref: dict[str, asyncio.subprocess.Process] = {}

        def track(proc):
            proc_ref["p"] = proc
            on_spawn(proc)
            if env.get("COPILOT_SUPPRESS_STDIN"):
                return
            # Write the prompt to stdin and close so the CLI's read loop
            # progresses. Errors on stdin close are non-fatal — if Copilot
            # exited early (e.g. auth error) stdout will convey that.
            try:
                if proc.stdin is not None:
                    proc.stdin.write(request.message.encode("utf-8") + b"\n")
                    proc.stdin.close()
            except Exception as exc:
                logger.debug("copilot: stdin write failed: %s", exc)

        try:
            async for line in stream_cli_subprocess(
                argv, env, request.cwd, request.cancelled, on_spawn=track,
            ):
                stripped = _strip_ansi(line.decode("utf-8", "replace")).rstrip("\n").strip()
                if stripped:
                    acc.apply(stripped)
        except asyncio.CancelledError:
            return _cancelled_result(acc)

        if request.cancelled.is_set():
            return _cancelled_result(acc)

        proc = proc_ref.get("p")
        exit_code = 0
        stderr_text = ""
        if proc is not None:
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                exit_code = -1
            if proc.stderr is not None:
                try:
                    stderr_text = (await proc.stderr.read()).decode("utf-8", "replace")
                except Exception:
                    logger.debug("stderr read failed", exc_info=True)

        if exit_code != 0:
            err_type = classify_cli_error(
                exit_code=exit_code, stderr=stderr_text, exception=None,
            )
            return ProviderRunResult(
                final_text=" ".join(acc.text_parts),
                tools_used=list(acc.tools_used),
                artifacts=[],
                session_id=acc.session_id,
                input_tokens=0,
                output_tokens=0,
                exit_reason=ExitReason.ERROR,
                errored=True,
                error_message=f"{err_type.value}: {stderr_text[:200]}",
            )

        return ProviderRunResult(
            final_text=" ".join(acc.text_parts),
            tools_used=list(acc.tools_used),
            artifacts=[],
            session_id=acc.session_id,
            input_tokens=0,
            output_tokens=0,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    async def cleanup(self) -> None:
        return None


def _cancelled_result(acc: _TurnAccumulator) -> ProviderRunResult:
    return ProviderRunResult(
        final_text=" ".join(acc.text_parts),
        tools_used=list(acc.tools_used),
        artifacts=[],
        session_id=acc.session_id,
        input_tokens=0,
        output_tokens=0,
        exit_reason=ExitReason.CANCELLED,
        errored=False,
        error_message=None,
    )


PROVIDER: CopilotAdapter = CopilotAdapter()
