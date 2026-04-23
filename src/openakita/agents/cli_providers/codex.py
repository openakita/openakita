# src/openakita/agents/cli_providers/codex.py
"""Codex CLI provider adapter.

The adapter deliberately preserves the user's real Codex home. Subscription
login state lives under `$CODEX_HOME` (normally `~/.codex/auth.json`), so
per-run temp homes would hide valid login state from systemd-launched
OpenAkita.

Session history lives at `~/.codex/sessions/<session-id>.jsonl`. The
`SESSION_ROOT` constant is consumed by `api/routes/sessions.py` for the
external-CLI listing endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Callable
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
from openakita.tools.mcp_catalog import MCPCatalog
from openakita.utils.path_helper import which_command

logger = logging.getLogger(__name__)

SESSION_ROOT: Path = Path.home() / ".codex" / "sessions"
CLI_PROVIDER_ID: CliProviderId = CliProviderId.CODEX
_ABS_PATH_RE = re.compile(r"(?<![\w:])(/[^\s`'\"<>|]+)")
_QUOTED_ABS_PATH_RE = re.compile(r"[`'\"](/[^`'\"]+)[`'\"]")
_PROJECT_MARKERS = (
    ".git",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "pnpm-workspace.yaml",
    "Cargo.toml",
    "go.mod",
)


def _resolve_binary() -> str | None:
    return which_command("codex")


def _prompt_with_extra(request: CliRunRequest) -> str:
    if not request.system_prompt_extra:
        return request.message
    return f"{request.system_prompt_extra}\n\n{request.message}"


def _mcp_section_name(server_id: str) -> str:
    return server_id.replace("-", "_")


def _mcp_config_overrides(server_ids: tuple[str, ...]) -> list[str]:
    if not server_ids:
        return []

    try:
        catalog = MCPCatalog()
    except Exception as exc:
        logger.warning("codex mcp config unavailable: %s", exc)
        return []

    overrides: list[str] = []
    for server_id in server_ids:
        info = catalog.get_server(server_id)
        if info is None or not info.command:
            logger.warning("codex mcp config: no catalog entry for %r", server_id)
            continue

        section = _mcp_section_name(server_id)
        prefix = f"mcp_servers.{section}"
        overrides.extend(["-c", f"{prefix}.command={json.dumps(info.command)}"])
        if info.args:
            overrides.extend(["-c", f"{prefix}.args={json.dumps(list(info.args))}"])
        if info.env:
            env_value = (
                "{ "
                + ", ".join(f"{k} = {json.dumps(v)}" for k, v in dict(info.env).items())
                + " }"
            )
            overrides.extend(["-c", f"{prefix}.env={env_value}"])
    return overrides


def _explicit_absolute_paths(text: str) -> list[Path]:
    candidates: list[str] = []
    candidates.extend(match.group(1) for match in _QUOTED_ABS_PATH_RE.finditer(text))
    candidates.extend(match.group(1) for match in _ABS_PATH_RE.finditer(text))

    paths: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        cleaned = raw.rstrip(".,:;)])}")
        if not cleaned or cleaned.startswith("//"):
            continue
        path = Path(cleaned).expanduser()
        if not path.is_absolute():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _nearest_project_root(path: Path) -> Path | None:
    start = path if path.exists() and path.is_dir() else path.parent
    for current in (start, *start.parents):
        if current == current.parent:
            break
        if any((current / marker).exists() for marker in _PROJECT_MARKERS):
            return current
    return None


def _extra_writable_roots(request: CliRunRequest) -> list[Path]:
    cwd = request.cwd.resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    roots: list[Path] = []
    seen = {str(cwd)}
    text = f"{request.system_prompt_extra}\n{request.message}"

    for path in _explicit_absolute_paths(text):
        target = path if path.exists() and path.is_dir() else path.parent
        try:
            resolved_target = target.resolve(strict=False)
        except OSError:
            continue
        if _is_relative_to(resolved_target, cwd):
            continue

        project_root = _nearest_project_root(resolved_target)
        if project_root is not None:
            root = project_root.resolve()
        elif _is_relative_to(resolved_target, tmp_root):
            root = tmp_root
        else:
            continue

        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots


@dataclass(frozen=True)
class _StreamEvent:
    kind: str                         # "init" | "assistant_text" | "assistant_thinking" | "tool_use" | "result" | "error"
    session_id: str | None = None
    text: str = ""
    tool_name: str | None = None
    call_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: str | None = None


def _string_parts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            for key in ("text", "message", "content", "summary"):
                parts.extend(_string_parts(item.get(key)))
        return parts
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "message", "content", "summary"):
            parts.extend(_string_parts(value.get(key)))
        return parts
    return []


def _payload_from_line(line: bytes) -> dict | None:
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    payload = obj.get("payload")
    if isinstance(payload, dict):
        return payload
    return obj


def _parse_message_content(obj: dict) -> list[_StreamEvent]:
    if obj.get("role") not in (None, "assistant"):
        return []
    return [
        _StreamEvent(kind="assistant_text", text=text)
        for text in _string_parts(obj.get("content") or obj.get("message"))
        if text
    ]


def _parse_usage(obj: dict) -> _StreamEvent:
    usage = obj.get("usage") or {}
    if not usage and isinstance(obj.get("info"), dict):
        info = obj["info"]
        usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
    return _StreamEvent(
        kind="result",
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def _parse_stream_line(line: bytes) -> list[_StreamEvent]:
    obj = _payload_from_line(line)
    if obj is None:
        return []

    etype = obj.get("type") or obj.get("event")
    if etype in ("session_start", "init"):
        return [_StreamEvent(kind="init", session_id=obj.get("session_id") or obj.get("id"))]
    if etype == "task_started":
        return [_StreamEvent(kind="init", session_id=obj.get("turn_id"))]

    if etype in ("assistant_delta", "message_delta"):
        return [_StreamEvent(
            kind="assistant_text",
            text=str(obj.get("text", "") or obj.get("delta", "")),
        )]
    if etype == "agent_message":
        return [
            _StreamEvent(kind="assistant_text", text=text)
            for text in _string_parts(obj.get("message"))
            if text
        ]
    if etype == "message":
        return _parse_message_content(obj)
    if etype == "reasoning":
        return [
            _StreamEvent(kind="assistant_thinking", text=text)
            for text in _string_parts(obj.get("summary") or obj.get("content"))
            if text
        ]

    if etype in ("tool_call", "tool_use"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name=str(obj.get("name", "")),
            call_id=obj.get("call_id"),
        )]
    if etype in ("function_call", "custom_tool_call"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name=str(obj.get("name", "") or etype),
            call_id=obj.get("call_id"),
        )]
    if etype == "exec_command_end":
        return [_StreamEvent(
            kind="tool_use",
            tool_name="exec_command",
            call_id=obj.get("call_id"),
        )]
    if etype == "patch_apply_end":
        return [_StreamEvent(
            kind="tool_use",
            tool_name="apply_patch",
            call_id=obj.get("call_id"),
        )]
    if etype == "view_image_tool_call":
        return [_StreamEvent(
            kind="tool_use",
            tool_name="view_image",
            call_id=obj.get("call_id"),
        )]

    if etype in ("turn_end", "result"):
        if obj.get("error") or obj.get("is_error"):
            return [_StreamEvent(
                kind="error",
                error_message=str(obj.get("error") or obj.get("result") or "unknown"),
            )]
        return [_parse_usage(obj)]
    if etype == "token_count":
        return [_parse_usage(obj)]
    if etype == "task_complete":
        events = [
            _StreamEvent(kind="assistant_text", text=text)
            for text in _string_parts(obj.get("last_agent_message"))
            if text
        ]
        events.append(_StreamEvent(kind="result"))
        return events
    if etype == "turn_aborted":
        return [_StreamEvent(
            kind="error",
            error_message=str(obj.get("reason") or "turn aborted"),
        )]
    return []


@dataclass
class _TurnAccumulator:
    session_id: str | None = None
    text_parts: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tool_call_ids: set[str] = field(default_factory=set)
    input_tokens: int = 0
    output_tokens: int = 0
    errored: bool = False
    error_message: str | None = None

    def apply(self, ev: _StreamEvent) -> bool:
        if ev.kind == "init":
            self.session_id = ev.session_id
            return True
        elif ev.kind == "assistant_text":
            if ev.text and self.text_parts and self.text_parts[-1] == ev.text:
                return False
            self.text_parts.append(ev.text)
            return True
        elif ev.kind == "assistant_thinking":
            return True
        elif ev.kind == "tool_use" and ev.tool_name:
            if ev.call_id:
                if ev.call_id in self.tool_call_ids:
                    return False
                self.tool_call_ids.add(ev.call_id)
            self.tools_used.append(ev.tool_name)
            return True
        elif ev.kind == "result":
            if ev.input_tokens:
                self.input_tokens = ev.input_tokens
            if ev.output_tokens:
                self.output_tokens = ev.output_tokens
            return True
        elif ev.kind == "error":
            self.errored = True
            self.error_message = ev.error_message
            return True
        return False


class CodexAdapter:
    """Codex CLI adapter. Stateless across calls — per-turn state lives in run()."""

    def build_argv(self, request: CliRunRequest) -> list[str]:
        binary = _resolve_binary()
        if binary is None:
            raise binary_not_found_error(
                tool_name="codex",
                binary="codex",
                install_hint="npm install -g @openai/codex",
            )
        options = ["--json", *_mcp_config_overrides(request.mcp_servers)]
        if not request.resume_id:
            options += ["--cd", str(request.cwd)]
        if request.profile.cli_permission_mode == CliPermissionMode.PLAN and not request.resume_id:
            options += ["--sandbox", "read-only"]
        if request.profile.cli_permission_mode == CliPermissionMode.WRITE:
            options += ["--full-auto", "--skip-git-repo-check"]
            if not request.resume_id:
                for root in _extra_writable_roots(request):
                    options += ["--add-dir", str(root)]

        argv = [binary, "exec"]
        if request.resume_id:
            argv += ["resume", *options, request.resume_id]
        else:
            argv += options
        argv.append(_prompt_with_extra(request))
        return argv

    def build_env(self, request: CliRunRequest) -> dict[str, str]:
        return build_cli_env(request.profile)

    async def run(
        self,
        request: CliRunRequest,
        argv: list[str],
        env: dict[str, str],
        *,
        on_spawn: Callable[[asyncio.subprocess.Process], None],
    ) -> ProviderRunResult:
        acc = _TurnAccumulator()
        proc_ref: dict[str, asyncio.subprocess.Process] = {}
        progress_cancelled = False

        def track(proc: asyncio.subprocess.Process) -> None:
            proc_ref["p"] = proc
            on_spawn(proc)

        async def emit_progress(ev: _StreamEvent) -> None:
            nonlocal progress_cancelled
            cb = request.on_progress
            if cb is None:
                return
            try:
                if ev.kind == "assistant_text" and ev.text:
                    await cb("assistant_text", text=ev.text)
                    return
                if ev.kind == "assistant_thinking" and ev.text:
                    await cb("assistant_thinking", text=ev.text)
                    return
                if ev.kind == "tool_use" and ev.tool_name:
                    await cb("tool_use", tool_name=ev.tool_name)
            except asyncio.CancelledError:
                progress_cancelled = True
                raise
            except Exception:
                logger.debug("progress callback failed", exc_info=True)

        stderr_buffer: list[bytes] = []

        try:
            async for line in stream_cli_subprocess(
                argv,
                env,
                request.cwd,
                request.cancelled,
                on_spawn=track,
                on_stderr=stderr_buffer.append,
            ):
                for ev in _parse_stream_line(line):
                    if acc.apply(ev):
                        await emit_progress(ev)
        except asyncio.CancelledError:
            if progress_cancelled:
                raise
            return _cancelled_result(acc)

        if request.cancelled.is_set():
            return _cancelled_result(acc)

        proc = proc_ref.get("p")
        exit_code = 0
        if proc is not None:
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                exit_code = -1
        stderr_text = b"".join(stderr_buffer).decode("utf-8", "replace")

        if acc.errored or exit_code != 0:
            err_type = classify_cli_error(
                exit_code=exit_code, stderr=stderr_text, exception=None,
            )
            return ProviderRunResult(
                final_text="".join(acc.text_parts),
                tools_used=list(acc.tools_used),
                artifacts=[],
                session_id=acc.session_id,
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                exit_reason=ExitReason.ERROR,
                errored=True,
                error_message=acc.error_message or f"{err_type.value}: {stderr_text[:200]}",
            )

        return ProviderRunResult(
            final_text="".join(acc.text_parts),
            tools_used=list(acc.tools_used),
            artifacts=[],
            session_id=acc.session_id,
            input_tokens=acc.input_tokens,
            output_tokens=acc.output_tokens,
            exit_reason=ExitReason.COMPLETED,
            errored=False,
            error_message=None,
        )

    async def cleanup(self) -> None:
        return None


def _cancelled_result(acc: _TurnAccumulator) -> ProviderRunResult:
    return ProviderRunResult(
        final_text="".join(acc.text_parts),
        tools_used=list(acc.tools_used),
        artifacts=[],
        session_id=acc.session_id,
        input_tokens=acc.input_tokens,
        output_tokens=acc.output_tokens,
        exit_reason=ExitReason.CANCELLED,
        errored=False,
        error_message=None,
    )


PROVIDER: CodexAdapter = CodexAdapter()
