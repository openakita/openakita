"""
Unified slash command registry.

Central definition of all slash commands shared by CLI and Desktop.
Each command entry declares metadata (name, label, description, scope)
so that both surfaces can discover and render them consistently.

The actual *action* of each command is environment-specific (CLI prints
to console, Desktop dispatches React state), so only metadata lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Sequence


class CommandScope(Flag):
    """Where a command is available."""
    CLI = auto()
    DESKTOP = auto()
    ALL = CLI | DESKTOP


@dataclass(frozen=True, slots=True)
class CommandDef:
    """Metadata for a single slash command."""
    name: str
    label: str
    description: str
    scope: CommandScope = CommandScope.ALL
    args_hint: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


COMMANDS: tuple[CommandDef, ...] = (
    CommandDef("help", "帮助", "显示可用命令列表"),
    CommandDef("model", "切换模型", "选择使用的 LLM 端点", args_hint="<端点名>"),
    CommandDef("plan", "计划模式", "开启/关闭 Plan 模式，先计划再执行"),
    CommandDef("clear", "清空对话", "清除当前对话的所有消息"),
    CommandDef("skill", "使用技能", "调用已安装的技能", args_hint="<技能名>"),
    CommandDef("persona", "切换角色", "切换 Agent 的人格预设", args_hint="<角色ID>"),
    CommandDef("agent", "切换 Agent", "在多 Agent 间切换", args_hint="<Agent名>"),
    CommandDef("agents", "Agent 列表", "显示可用的 Agent 列表"),
    CommandDef("org", "组织模式", "切换到组织编排模式", args_hint="<组织名|off>",
               scope=CommandScope.DESKTOP),
    CommandDef("thinking", "深度思考", "设置思考模式", args_hint="on|off|auto"),
    CommandDef("thinking_depth", "思考程度", "设置思考程度", args_hint="low|medium|high"),
    CommandDef("status", "Agent 状态", "显示 Agent 运行状态",
               scope=CommandScope.CLI),
    CommandDef("selfcheck", "自检", "运行系统自检",
               scope=CommandScope.CLI),
    CommandDef("memory", "记忆信息", "查看 Agent 记忆",
               scope=CommandScope.CLI),
    CommandDef("skills", "技能列表", "查看已安装技能",
               scope=CommandScope.CLI),
    CommandDef("channels", "IM 通道", "查看 IM 通道状态",
               scope=CommandScope.CLI),
    CommandDef("sessions", "会话列表", "查看 CLI 历史会话",
               scope=CommandScope.CLI),
    CommandDef("session", "切换会话", "切换到指定的 CLI 会话", args_hint="<#>",
               scope=CommandScope.CLI),
    CommandDef("exit", "退出", "退出 OpenAkita", aliases=("quit",),
               scope=CommandScope.CLI),
)


def get_commands(scope: CommandScope | None = None) -> Sequence[CommandDef]:
    """Return commands filtered by scope. None returns all."""
    if scope is None:
        return COMMANDS
    return tuple(c for c in COMMANDS if scope in c.scope)


def find_command(name: str) -> CommandDef | None:
    """Look up a command by name or alias."""
    name = name.lstrip("/").lower()
    for cmd in COMMANDS:
        if cmd.name == name or name in cmd.aliases:
            return cmd
    return None
