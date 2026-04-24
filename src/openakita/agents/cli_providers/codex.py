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
import os
import re
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
_UNKNOWN_EVENT_LOG_LIMIT = 20
_unknown_event_log_count = 0
_NO_SANDBOX_VALUES = {"danger-full-access", "danger_full_access", "disabled", "false", "none", "off"}


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


def _codex_home_from_profile(profile: object | None) -> Path:
    cli_env = getattr(profile, "cli_env", None) or {}
    raw = cli_env.get("CODEX_HOME") if isinstance(cli_env, dict) else None
    if raw:
        expanded = os.path.expandvars(os.path.expanduser(str(raw)))
        return Path(expanded)
    return Path.home() / ".codex"


def _value_disables_sandbox(value: object) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _NO_SANDBOX_VALUES
    if isinstance(value, dict):
        if value.get("enabled") is False:
            return True
        mode = value.get("mode") or value.get("sandbox_mode")
        return _value_disables_sandbox(mode)
    return False


def _codex_config_disables_sandbox(profile: object | None) -> bool:
    path = _codex_home_from_profile(profile) / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.debug("codex config unavailable or invalid at %s: %s", path, exc)
        return False

    return (
        _value_disables_sandbox(data.get("sandbox_mode"))
        or _value_disables_sandbox(data.get("sandbox"))
    )


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


def _json_obj_from_line(line: bytes) -> dict | None:
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _payload_from_obj(obj: dict) -> dict:
    """Return the semantic event payload for wrapper-style Codex JSONL rows."""
    payload = obj.get("payload")
    if isinstance(payload, dict):
        return payload

    msg = obj.get("msg")
    if isinstance(msg, dict):
        return msg

    event = obj.get("event")
    if isinstance(event, dict):
        return event

    item = obj.get("item")
    if isinstance(item, dict) and str(obj.get("type", "")).startswith("item."):
        merged = dict(item)
        merged["_outer_type"] = obj.get("type")
        return merged

    return obj


def _payload_from_line(line: bytes) -> dict | None:
    obj = _json_obj_from_line(line)
    if obj is None:
        return None
    return _payload_from_obj(obj)


def _first_text(*values: object) -> str:
    for value in values:
        parts = _string_parts(value)
        if parts:
            return "".join(parts)
    return ""


def _event_id(obj: dict) -> str | None:
    raw = obj.get("call_id") or obj.get("id") or obj.get("item_id")
    return str(raw) if raw else None


def _log_unknown_event(obj: dict) -> None:
    global _unknown_event_log_count
    if _unknown_event_log_count >= _UNKNOWN_EVENT_LOG_LIMIT:
        return
    _unknown_event_log_count += 1
    payload = obj.get("payload")
    item = obj.get("item")
    shape: dict[str, Any] = {
        "type": obj.get("type") or obj.get("event"),
        "keys": sorted(str(k) for k in obj)[:20],
    }
    if isinstance(payload, dict):
        shape["payload_type"] = payload.get("type") or payload.get("event")
        shape["payload_keys"] = sorted(str(k) for k in payload)[:20]
    if isinstance(item, dict):
        shape["item_type"] = item.get("type") or item.get("kind")
        shape["item_keys"] = sorted(str(k) for k in item)[:20]
    logger.debug("ignored codex json event shape: %s", shape)


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
    if not usage and isinstance(obj.get("response"), dict):
        usage = obj["response"].get("usage") or {}
    if not usage and isinstance(obj.get("info"), dict):
        info = obj["info"]
        usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
    return _StreamEvent(
        kind="result",
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def _parse_item_event(item: dict) -> list[_StreamEvent]:
    itype = item.get("type") or item.get("kind")
    if itype == "agent_message":
        text = _first_text(item.get("message"), item.get("text"), item.get("content"))
        return [_StreamEvent(kind="assistant_text", text=text)] if text else []
    if itype in ("message", "assistant_message", "output_message"):
        return _parse_message_content(item)
    if itype in ("output_text", "text"):
        text = _first_text(item.get("text"), item.get("content"), item.get("message"))
        return [_StreamEvent(kind="assistant_text", text=text)] if text else []
    if itype in ("reasoning", "reasoning_summary", "thinking"):
        text = _first_text(item.get("summary"), item.get("content"), item.get("text"))
        return [_StreamEvent(kind="assistant_thinking", text=text)] if text else []
    if itype in (
        "function_call",
        "custom_tool_call",
        "tool_call",
        "local_shell_call",
        "mcp_tool_call",
        "command_execution",
        "file_change",
    ):
        name = str(item.get("name") or item.get("tool_name") or "")
        if not name and itype == "local_shell_call":
            name = "exec_command"
        if not name and itype == "command_execution":
            name = "exec_command"
        if not name and itype == "file_change":
            name = "file_change"
        if not name and itype == "mcp_tool_call":
            name = "mcp_tool_call"
        return [_StreamEvent(kind="tool_use", tool_name=name, call_id=_event_id(item))] if name else []
    return []


def _status_events(text: str, *, session_id: str | None = None) -> list[_StreamEvent]:
    events: list[_StreamEvent] = []
    if session_id:
        events.append(_StreamEvent(kind="init", session_id=session_id))
    if text:
        events.append(_StreamEvent(kind="assistant_thinking", text=text))
    return events


def _parse_payload(obj: dict) -> list[_StreamEvent]:
    etype = obj.get("type") or obj.get("event") or obj.get("_outer_type")
    if etype in ("session_start", "init", "thread.started", "session.created"):
        return _status_events(
            "Codex session started.",
            session_id=obj.get("session_id") or obj.get("thread_id") or obj.get("id"),
        )
    if etype in ("task_started", "turn_started", "turn.started", "response.created"):
        return _status_events(
            "Codex started working.",
            session_id=obj.get("turn_id") or obj.get("session_id") or obj.get("id"),
        )

    if etype in ("assistant_delta", "message_delta"):
        return [_StreamEvent(
            kind="assistant_text",
            text=str(obj.get("text", "") or obj.get("delta", "")),
        )]
    if etype in ("agent_message_delta", "response.output_text.delta"):
        return [_StreamEvent(
            kind="assistant_text",
            text=str(obj.get("delta", "") or obj.get("text", "")),
        )]
    if etype == "agent_message":
        direct_text = _first_text(obj.get("message"), obj.get("text"), obj.get("content"))
        if direct_text:
            return [_StreamEvent(kind="assistant_text", text=direct_text)]
        return [
            _StreamEvent(kind="assistant_text", text=text)
            for text in _string_parts(obj.get("message"))
            if text
        ]
    if etype == "message":
        return _parse_message_content(obj)
    if etype in ("reasoning", "agent_reasoning_delta", "response.reasoning_text.delta"):
        return [
            _StreamEvent(kind="assistant_thinking", text=text)
            for text in _string_parts(
                obj.get("summary") or obj.get("content") or obj.get("delta") or obj.get("text")
            )
            if text
        ]
    if etype in ("response.reasoning_summary_text.delta", "reasoning_summary_delta"):
        text = str(obj.get("delta", "") or obj.get("text", ""))
        return [_StreamEvent(kind="assistant_thinking", text=text)] if text else []

    if etype in ("tool_call", "tool_use", "function_call", "custom_tool_call"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name=str(obj.get("name", "") or obj.get("tool_name", "") or etype),
            call_id=_event_id(obj),
        )]
    if etype in ("response.output_item.added", "response.output_item.done"):
        item = obj.get("item")
        if isinstance(item, dict):
            return _parse_item_event(item)
        return []
    if etype in ("command_execution", "file_change", "local_shell_call", "mcp_tool_call"):
        return _parse_item_event(obj)
    if str(etype or "").startswith("item."):
        return _parse_item_event(obj)
    if etype in ("exec_command_begin", "exec_command_end"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name="exec_command",
            call_id=_event_id(obj),
        )]
    if etype in ("patch_apply_begin", "patch_apply_end"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name="apply_patch",
            call_id=_event_id(obj),
        )]
    if etype in ("mcp_tool_call_begin", "mcp_tool_call_end"):
        return [_StreamEvent(
            kind="tool_use",
            tool_name=str(obj.get("name") or obj.get("tool_name") or "mcp_tool_call"),
            call_id=_event_id(obj),
        )]
    if etype == "view_image_tool_call":
        return [_StreamEvent(
            kind="tool_use",
            tool_name="view_image",
            call_id=_event_id(obj),
        )]

    if etype in ("turn_end", "turn.completed", "result", "response.completed"):
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
    if etype in ("error", "response.failed"):
        return [_StreamEvent(
            kind="error",
            error_message=str(
                obj.get("message")
                or obj.get("error")
                or obj.get("reason")
                or "codex error"
            ),
        )]
    return []


def _parse_stream_line(line: bytes) -> list[_StreamEvent]:
    raw = _json_obj_from_line(line)
    if raw is None:
        return []

    payload = _payload_from_obj(raw)
    events = _parse_payload(payload)
    if events:
        return events

    if payload is not raw:
        events = _parse_payload(raw)
        if events:
            return events

    _log_unknown_event(raw)
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
            if _codex_config_disables_sandbox(request.profile):
                logger.info(
                    "Codex config disables sandbox; using noninteractive no-sandbox write mode"
                )
                options += ["--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"]
            else:
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

        final_text = "".join(acc.text_parts)
        # Empty turn detection: catch CLI processes that exit successfully but do nothing.
        # Previously only checked in WRITE mode; now applies to all modes to prevent
        # "exits code 0, 0 tools, no output" being treated as success.
        if not final_text.strip() and not acc.tools_used:
            return ProviderRunResult(
                final_text=final_text,
                tools_used=[],
                artifacts=[],
                session_id=acc.session_id,
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                exit_reason=ExitReason.ERROR,
                errored=True,
                error_message=(
                    "empty_turn: Codex exited successfully but produced no assistant "
                    "output and no tool events; no write was confirmed"
                ),
            )

        return ProviderRunResult(
            final_text=final_text,
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
