"""lark-cli-tool: wraps the official lark-cli as LLM-callable tools.

Provides three tools:
  - lark_setup  : one-click install + configure + authenticate (or individual steps)
  - lark_run    : execute any lark-cli command with structured output
  - lark_schema : inspect API method parameters and response structure

IMPORTANT: This plugin uses @larksuite/cli (the official Lark Open Platform CLI),
NOT @byted-apaas/cli (which is the Feishu low-code platform CLI).
The npm package is: @larksuite/cli
The binary name is: lark-cli
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from typing import Any

from openakita.plugins.api import PluginAPI, PluginBase

_MAX_OUTPUT_CHARS = 30_000
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_DEVICE_CODE_PATTERN = re.compile(r"device.code[:\s]+([A-Za-z0-9_-]+)", re.IGNORECASE)
_SHELL_META = re.compile(r"[;&|`$(){}!\n\r]")

# npm package name — must be @larksuite/cli, NOT @byted-apaas/cli
_NPM_PACKAGE = "@larksuite/cli"
_SKILLS_REPO = "larksuite/cli"


TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lark_setup",
            "description": (
                "Install, configure, and authenticate the official Lark/Feishu CLI "
                "(npm package: @larksuite/cli) for accessing the Feishu Open Platform.\n\n"
                "IMPORTANT: This is @larksuite/cli, NOT @byted-apaas/cli. Do NOT confuse them.\n\n"
                "Actions:\n"
                '  "quickstart" — ONE-CLICK full setup: auto-installs lark-cli if needed, '
                "configures app credentials, and starts OAuth login. Returns browser URLs "
                "for the user to complete — no tenant ID or manual input needed. "
                "USE THIS for first-time setup.\n\n"
                '  "check"      — check if lark-cli is installed and show auth status\n'
                '  "install"    — install @larksuite/cli and agent skills via npm\n'
                '  "configure"  — set up app credentials (returns browser URL for user)\n'
                '  "login"      — start OAuth login (returns browser URL for user)\n'
                '  "login_poll" — resume login polling with a device code\n'
                '  "logout"     — sign out and clear stored credentials\n'
                '  "status"     — show current login status and granted scopes\n\n'
                "For first-time users, just use action='quickstart'. It handles everything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "quickstart",
                            "check",
                            "install",
                            "configure",
                            "login",
                            "login_poll",
                            "logout",
                            "status",
                        ],
                        "description": (
                            "Setup action. Use 'quickstart' for first-time one-click setup."
                        ),
                    },
                    "domain": {
                        "type": "string",
                        "description": (
                            'Comma-separated domains for login scope filtering, e.g. "calendar,task". '
                            "Only used with action=login."
                        ),
                    },
                    "device_code": {
                        "type": "string",
                        "description": (
                            "Device code for resuming login polling. "
                            "Only used with action=login_poll."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lark_run",
            "description": (
                "Execute any lark-cli command and return structured output.\n\n"
                "The command string should NOT include the 'lark-cli' prefix.\n"
                "Output defaults to JSON format for easy parsing.\n\n"
                "Business domains and example commands:\n"
                "  Calendar : calendar +agenda | calendar +event-create --title '...' --start '...' --end '...'\n"
                "  Messenger: im +messages-send --chat-id 'oc_xxx' --text 'Hello' | im +messages-list --chat-id 'oc_xxx'\n"
                "  Docs     : docs +create --title '...' --markdown '...' | docs +read --document-id 'xxx'\n"
                "  Drive    : drive +upload --file './report.pdf' | drive +search --query 'quarterly report'\n"
                "  Sheets   : sheets +read --spreadsheet-id 'xxx' --range 'A1:D10' | sheets +write ...\n"
                "  Base     : base +records-list --app-token 'xxx' --table-id 'xxx'\n"
                "  Tasks    : task +create --title 'Review PR' --due '2026-04-01'\n"
                "  Wiki     : wiki +search --query 'onboarding'\n"
                "  Contact  : contact +search --query 'zhang@example.com'\n"
                "  Mail     : mail +list | mail +send --to 'user@example.com' --subject 'Hi' --body '...'\n"
                "  Meetings : vc +list\n\n"
                "Three command layers:\n"
                "  Shortcuts   : calendar +agenda (human & AI friendly, + prefix)\n"
                "  API commands: calendar calendars list (1:1 with platform endpoints)\n"
                "  Raw API     : api GET /open-apis/calendar/v4/calendars\n\n"
                "Tips:\n"
                "  --dry-run   : preview request without executing (safe for write ops)\n"
                "  --page-all  : auto-paginate through all pages\n"
                "  --as bot    : execute as bot identity instead of user"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The lark-cli command to execute, without the 'lark-cli' prefix. "
                            "Example: 'calendar +agenda' or "
                            "'im +messages-send --chat-id oc_xxx --text Hello'"
                        ),
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "pretty", "table", "ndjson", "csv"],
                        "description": "Output format. Defaults to 'json'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (5-300). Defaults to 30.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lark_schema",
            "description": (
                "Inspect a lark-cli API method's parameters, request body, response structure, "
                "supported identities, and required scopes.\n\n"
                "Call without arguments to list all available domains and methods.\n"
                "Call with a method name to get detailed schema information.\n\n"
                "Examples:\n"
                '  method=""                              → list all domains\n'
                '  method="calendar.events.instance_view" → show event view schema\n'
                '  method="im.messages.create"            → show send-message schema'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": (
                            "API method name, e.g. 'calendar.events.instance_view'. "
                            "Leave empty to list available domains."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]


def _sanitize_arg(arg: str) -> str:
    return _SHELL_META.sub("", arg)


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text)} chars total)"


def _extract_urls(text: str) -> list[str]:
    return _URL_PATTERN.findall(text)


def _extract_device_code(text: str) -> str | None:
    m = _DEVICE_CODE_PATTERN.search(text)
    return m.group(1) if m else None


def _find_executable(name: str) -> str | None:
    """Find an executable on PATH, handling Windows .cmd/.ps1 wrappers."""
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        for suffix in (".cmd", ".ps1", ".exe"):
            found = shutil.which(name + suffix)
            if found:
                return found
    return None


class _LarkCLIRunner:
    """Manages subprocess execution of lark-cli commands."""

    def __init__(self, cli_path: str = "lark-cli", npx_path: str = "npx"):
        self.cli_path = cli_path
        self.npx_path = npx_path

    def find_cli(self) -> str | None:
        return _find_executable(self.cli_path)

    def find_npm(self) -> str | None:
        return _find_executable("npm")

    def find_npx(self) -> str | None:
        return _find_executable(self.npx_path)

    def find_node(self) -> str | None:
        return _find_executable("node")

    async def _exec(
        self,
        cmd: list[str],
        *,
        timeout: int = 30,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        env = {**os.environ, **(env_extra or {})}
        env.setdefault("LANG", "en_US.UTF-8")
        env["PYTHONIOENCODING"] = "utf-8"
        if sys.platform == "win32":
            env.setdefault("CHCP", "65001")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": f"Executable not found: {cmd[0]}"}
        except OSError as exc:
            return {"success": False, "error": f"Failed to execute: {exc}"}

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode or 0

        result: dict[str, Any] = {"success": exit_code == 0, "exit_code": exit_code}

        if stdout:
            try:
                result["data"] = json.loads(stdout)
            except (json.JSONDecodeError, ValueError):
                result["output"] = _truncate(stdout)

        combined = stdout + "\n" + stderr
        urls = _extract_urls(combined)
        if urls:
            result["urls"] = urls
        device_code = _extract_device_code(combined)
        if device_code:
            result["device_code"] = device_code

        if stderr and exit_code != 0:
            result["error"] = _truncate(stderr)
        elif stderr:
            result["stderr"] = _truncate(stderr)

        return result

    async def run(
        self,
        args: list[str],
        *,
        timeout: int = 30,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        cli = self.find_cli()
        if cli is None:
            return {
                "success": False,
                "error": (
                    "lark-cli is not installed. "
                    "Use lark_setup(action='quickstart') for automatic installation, "
                    "or manually run: npm install -g @larksuite/cli"
                ),
            }
        return await self._exec([cli, *args], timeout=timeout, env_extra=env_extra)

    async def run_npm(
        self, args: list[str], *, timeout: int = 120
    ) -> dict[str, Any]:
        npm = self.find_npm()
        if npm is None:
            return {
                "success": False,
                "error": (
                    "npm is not found. Please install Node.js first: "
                    "https://nodejs.org/"
                ),
            }
        return await self._exec([npm, *args], timeout=timeout)

    async def run_npx(
        self, args: list[str], *, timeout: int = 120
    ) -> dict[str, Any]:
        npx = self.find_npx()
        if npx is None:
            return {
                "success": False,
                "error": (
                    "npx is not found. Please install Node.js first: "
                    "https://nodejs.org/"
                ),
            }
        return await self._exec([npx, *args], timeout=timeout)


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        config = api.get_config() or {}
        cli_path = config.get("lark_cli_path", "lark-cli")
        npx_path = config.get("npx_path", "npx")
        default_timeout = int(config.get("default_timeout", 30))
        default_format = config.get("default_format", "json")

        runner = _LarkCLIRunner(cli_path=cli_path, npx_path=npx_path)

        async def handler(tool_name: str, arguments: dict) -> str:
            try:
                if tool_name == "lark_setup":
                    result = await _handle_setup(runner, arguments, default_timeout)
                elif tool_name == "lark_run":
                    result = await _handle_run(
                        runner, arguments, default_timeout, default_format
                    )
                elif tool_name == "lark_schema":
                    result = await _handle_schema(runner, arguments, default_timeout)
                else:
                    result = {"success": False, "error": f"Unknown tool: {tool_name}"}
            except Exception as exc:
                api.log_error(f"lark-cli tool error: {exc}", exc)
                result = {"success": False, "error": str(exc)}

            return json.dumps(result, ensure_ascii=False, indent=2)

        api.register_tools(TOOL_DEFINITIONS, handler)
        api.log(f"lark-cli-tool loaded (cli={cli_path})")

    def on_unload(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Setup handler
# ---------------------------------------------------------------------------

async def _handle_setup(
    runner: _LarkCLIRunner, args: dict, default_timeout: int
) -> dict[str, Any]:
    action = args.get("action", "quickstart")

    if action == "quickstart":
        return await _quickstart(runner, args, default_timeout)

    if action == "check":
        return await _check(runner)

    if action == "install":
        return await _install(runner)

    if action == "configure":
        return await _configure(runner, default_timeout)

    if action == "login":
        return await _login(runner, args, default_timeout)

    if action == "login_poll":
        device_code = args.get("device_code", "")
        if not device_code:
            return {"success": False, "error": "device_code is required for login_poll"}
        return await runner.run(
            ["auth", "login", "--device-code", _sanitize_arg(device_code)],
            timeout=default_timeout,
        )

    if action == "logout":
        return await runner.run(["auth", "logout"], timeout=15)

    if action == "status":
        return await runner.run(["auth", "status"], timeout=15)

    return {"success": False, "error": f"Unknown setup action: {action}"}


async def _quickstart(
    runner: _LarkCLIRunner, args: dict, default_timeout: int
) -> dict[str, Any]:
    """One-click setup: install → configure → login, with clear user instructions."""
    steps: list[dict[str, Any]] = []
    user_actions: list[str] = []

    # --- Step 1: Check prerequisites ---
    node = runner.find_node()
    if node is None:
        return {
            "success": False,
            "error": "Node.js is not installed.",
            "user_action_required": (
                "请先安装 Node.js：https://nodejs.org/\n"
                "安装完成后再次运行此命令。"
            ),
        }

    # --- Step 2: Install lark-cli if not present ---
    cli = runner.find_cli()
    if cli is None:
        install_result = await _install(runner)
        steps.append({"step": "install", **install_result})
        if not install_result.get("success"):
            return {
                "success": False,
                "steps": steps,
                "error": "Failed to install lark-cli. See steps for details.",
            }
        cli = runner.find_cli()
        if cli is None:
            return {
                "success": False,
                "steps": steps,
                "error": (
                    "lark-cli was installed but cannot be found on PATH. "
                    "You may need to restart your terminal or add npm global bin to PATH."
                ),
                "hint": "Try running: npm install -g @larksuite/cli",
            }
    else:
        steps.append({"step": "install", "skipped": True, "cli_path": cli})

    # --- Step 3: Check if already authenticated ---
    status_result = await runner.run(["auth", "status"], timeout=15)
    if status_result.get("success") and _is_logged_in(status_result):
        steps.append({"step": "auth_check", "already_logged_in": True, **status_result})
        return {
            "success": True,
            "message": "lark-cli 已安装且已登录，无需额外配置。可以直接使用 lark_run 执行命令。",
            "steps": steps,
        }

    # --- Step 4: Configure app credentials ---
    config_result = await _configure(runner, default_timeout)
    steps.append({"step": "configure", **config_result})

    config_urls = config_result.get("urls", [])
    if config_urls:
        user_actions.append(
            "【第 1 步】请在浏览器中打开以下链接，完成飞书应用创建：\n"
            + config_urls[0]
            + "\n（按照页面指引操作即可，无需手动填写租户 ID 等信息）"
        )

    # --- Step 5: Start OAuth login ---
    login_result = await _login(runner, args, default_timeout)
    steps.append({"step": "login", **login_result})

    login_urls = login_result.get("urls", [])
    if login_urls:
        step_num = 2 if config_urls else 1
        user_actions.append(
            f"【第 {step_num} 步】请在浏览器中打开以下链接，完成 OAuth 授权：\n"
            + login_urls[0]
            + "\n（登录您的飞书账号并授权即可）"
        )

    device_code = login_result.get("device_code")
    if device_code:
        user_actions.append(
            f"授权完成后，我会自动使用 device_code ({device_code}) 完成登录验证。"
        )

    if user_actions:
        return {
            "success": True,
            "setup_in_progress": True,
            "user_action_required": "\n\n".join(user_actions),
            "device_code": device_code,
            "steps": steps,
            "next_step": (
                "用户完成浏览器授权后，"
                + (
                    f"调用 lark_setup(action='login_poll', device_code='{device_code}') 完成验证。"
                    if device_code
                    else "调用 lark_setup(action='status') 检查登录状态。"
                )
            ),
        }

    return {
        "success": True,
        "steps": steps,
        "message": "Setup completed. Use lark_setup(action='status') to verify.",
    }


async def _check(runner: _LarkCLIRunner) -> dict[str, Any]:
    node = runner.find_node()
    npm = runner.find_npm()
    cli = runner.find_cli()

    result: dict[str, Any] = {
        "success": True,
        "prerequisites": {
            "node": {"installed": node is not None, "path": node},
            "npm": {"installed": npm is not None, "path": npm},
        },
        "lark_cli": {
            "installed": cli is not None,
            "path": cli,
        },
    }

    if cli:
        status = await runner.run(["auth", "status"], timeout=15)
        result["auth"] = status
        result["logged_in"] = _is_logged_in(status)
    else:
        if node is None:
            result["hint"] = (
                "需要先安装 Node.js (https://nodejs.org/)，然后运行 "
                "lark_setup(action='quickstart') 一键配置。"
            )
        else:
            result["hint"] = (
                "lark-cli 未安装。运行 lark_setup(action='quickstart') 自动安装并配置。"
            )

    return result


async def _install(runner: _LarkCLIRunner) -> dict[str, Any]:
    npm = runner.find_npm()
    if npm is None:
        return {
            "success": False,
            "error": (
                "npm not found. Install Node.js first: https://nodejs.org/"
            ),
        }

    install_result = await runner.run_npm(
        ["install", "-g", _NPM_PACKAGE], timeout=120
    )
    if not install_result.get("success"):
        return {
            "success": False,
            "error": f"Failed to install {_NPM_PACKAGE}",
            "details": install_result,
        }

    npx = runner.find_npx()
    skills_result: dict[str, Any] = {"skipped": True}
    if npx:
        skills_result = await runner.run_npx(
            ["skills", "add", _SKILLS_REPO, "-y", "-g"], timeout=120
        )

    cli = runner.find_cli()
    return {
        "success": cli is not None,
        "cli_path": cli,
        "install": install_result,
        "skills": skills_result,
    }


async def _configure(
    runner: _LarkCLIRunner, default_timeout: int
) -> dict[str, Any]:
    result = await runner.run(
        ["config", "init", "--new"], timeout=max(default_timeout, 60)
    )

    if result.get("urls"):
        result["user_action_required"] = (
            "请在浏览器中打开以下链接完成应用配置（无需手动输入租户 ID）：\n"
            + result["urls"][0]
        )
    return result


async def _login(
    runner: _LarkCLIRunner, args: dict, default_timeout: int
) -> dict[str, Any]:
    cmd = ["auth", "login", "--recommend", "--no-wait"]
    domain = args.get("domain")
    if domain:
        cmd = ["auth", "login", "--domain", _sanitize_arg(domain), "--no-wait"]

    result = await runner.run(cmd, timeout=default_timeout)

    if result.get("urls"):
        result["user_action_required"] = (
            "请在浏览器中打开以下链接完成 OAuth 授权登录：\n"
            + result["urls"][0]
        )

    return result


def _is_logged_in(status_result: dict[str, Any]) -> bool:
    """Heuristic to detect if auth status indicates a logged-in state."""
    if not status_result.get("success"):
        return False
    output = status_result.get("output", "")
    data = status_result.get("data", {})
    if isinstance(data, dict):
        if data.get("logged_in") or data.get("user") or data.get("email"):
            return True
    if "logged in" in output.lower() or "user:" in output.lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Run handler
# ---------------------------------------------------------------------------

async def _handle_run(
    runner: _LarkCLIRunner,
    args: dict,
    default_timeout: int,
    default_format: str,
) -> dict[str, Any]:
    command_str = args.get("command", "").strip()
    if not command_str:
        return {"success": False, "error": "command is required"}

    parts = _split_command(command_str)
    if not parts:
        return {"success": False, "error": "Empty command after parsing"}

    fmt = args.get("format", default_format)
    timeout = min(max(int(args.get("timeout", default_timeout)), 5), 300)

    if "--format" not in parts:
        parts.extend(["--format", fmt])

    return await runner.run(parts, timeout=timeout)


# ---------------------------------------------------------------------------
# Schema handler
# ---------------------------------------------------------------------------

async def _handle_schema(
    runner: _LarkCLIRunner, args: dict, default_timeout: int
) -> dict[str, Any]:
    method = args.get("method", "").strip()
    cmd = ["schema"]
    if method:
        cmd.append(_sanitize_arg(method))
    return await runner.run(cmd, timeout=default_timeout)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _split_command(command: str) -> list[str]:
    """Split a command string into tokens, respecting quotes.

    Does NOT invoke the shell — only lark-cli is called directly.
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for ch in command:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            else:
                current.append(ch)
        elif ch in ("'", '"'):
            in_quote = ch
        elif ch in (" ", "\t"):
            if current:
                tokens.append("".join(current))
                current = []
        else:
            if _SHELL_META.match(ch):
                continue
            current.append(ch)

    if current:
        tokens.append("".join(current))

    return tokens
