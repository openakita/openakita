# src/openakita/agents/cli_providers/gemini.py
"""Gemini CLI provider adapter.

Gemini CLI's non-interactive mode emits a single JSON object on stdout:
    gemini --output-format json --prompt "<msg>"
No stream-of-events shape — the adapter buffers bytes until EOF and parses
once. Resume: `--resume <id>`. Write mode: `--yolo` to auto-accept tool calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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

SESSION_ROOT: Path = Path.home() / ".gemini" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.GEMINI


def _resolve_binary() -> str | None:
    return which_command("gemini")


class GeminiAdapter:
    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="gemini",
                binary="gemini",
                install_hint="npm install -g @google/gemini-cli",
            )
        argv = [binary, "--output-format", "json", "--prompt", request.message]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            argv.append("--yolo")
        if request.resume_id:
            argv += ["--resume", request.resume_id]
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        return build_cli_env()

    async def run(self, request, argv, env, *, on_spawn):
        buf = bytearray()
        proc_ref: dict[str, asyncio.subprocess.Process] = {}

        def track(proc):
            proc_ref["p"] = proc
            on_spawn(proc)

        try:
            async for line in stream_cli_subprocess(
                argv, env, request.cwd, request.cancelled, on_spawn=track,
            ):
                buf.extend(line)
        except asyncio.CancelledError:
            return _cancelled_result(None)

        if request.cancelled.is_set():
            return _cancelled_result(None)

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

        try:
            obj = json.loads(bytes(buf))
        except (json.JSONDecodeError, UnicodeDecodeError):
            obj = {}

        session_id = obj.get("session_id")
        response = obj.get("response") or {}
        final_text = str(response.get("text", ""))
        tool_calls = response.get("tool_calls") or []
        tools_used = [str(tc.get("name", "")) for tc in tool_calls if isinstance(tc, dict)]
        usage = obj.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        errored = bool(obj.get("error")) or exit_code != 0
        error_message = str(obj.get("error")) if obj.get("error") else None

        if errored:
            err_type = classify_cli_error(
                exit_code=exit_code, stderr=stderr_text, exception=None,
            )
            return ProviderRunResult(
                final_text=final_text,
                tools_used=tools_used,
                artifacts=[],
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                exit_reason=ExitReason.ERROR,
                errored=True,
                error_message=error_message or f"{err_type.value}: {stderr_text[:200]}",
            )

        return ProviderRunResult(
            final_text=final_text,
            tools_used=tools_used,
            artifacts=[],
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    async def cleanup(self) -> None:
        return None


def _cancelled_result(_ignored) -> ProviderRunResult:
    return ProviderRunResult(
        final_text="",
        tools_used=[],
        artifacts=[],
        session_id=None,
        input_tokens=0,
        output_tokens=0,
        exit_reason=ExitReason.CANCELLED,
        errored=False,
        error_message=None,
    )


PROVIDER: GeminiAdapter = GeminiAdapter()
