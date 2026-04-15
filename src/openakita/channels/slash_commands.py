"""
统一 Slash 命令注册表

跨 CLI 和 IM Gateway 的命令定义，确保两端命令行为一致。
每个命令声明适用范围（cli/im/both）和所需权限级别。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class CommandScope(StrEnum):
    CLI = "cli"
    IM = "im"
    BOTH = "both"


@dataclass
class SlashCommand:
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    scope: CommandScope = CommandScope.BOTH
    category: str = "general"
    admin_only: bool = False

    @property
    def all_triggers(self) -> list[str]:
        return [self.name] + self.aliases


COMMAND_REGISTRY: list[SlashCommand] = [
    SlashCommand(
        name="/help",
        aliases=["/帮助"],
        description="查看所有可用指令",
        category="general",
    ),
    SlashCommand(
        name="/new",
        aliases=["/新话题", "/reset"],
        description="开启新话题，清除对话上下文",
        category="conversation",
    ),
    SlashCommand(
        name="/model",
        aliases=[],
        description="查看当前模型和可用列表",
        category="model",
    ),
    SlashCommand(
        name="/switch",
        aliases=[],
        description="临时切换模型",
        category="model",
    ),
    SlashCommand(
        name="/restore",
        aliases=[],
        description="恢复默认模型",
        category="model",
    ),
    SlashCommand(
        name="/thinking",
        aliases=[],
        description="切换思考模式 [on|off|auto]",
        category="thinking",
    ),
    SlashCommand(
        name="/thinking_depth",
        aliases=[],
        description="设置思考深度 [low|medium|high]",
        category="thinking",
    ),
    SlashCommand(
        name="/chain",
        aliases=[],
        description="思维链进度推送开关 [on|off]",
        category="thinking",
    ),
    SlashCommand(
        name="/mode",
        aliases=["/模式"],
        description="查看当前多Agent模式说明",
        category="agent",
        scope=CommandScope.IM,
    ),
    SlashCommand(
        name="/persona",
        aliases=["/人格"],
        description="切换人格预设",
        category="persona",
    ),
    SlashCommand(
        name="/pair",
        aliases=[],
        description="DM 配对授权管理",
        category="security",
        scope=CommandScope.IM,
        admin_only=True,
    ),
    SlashCommand(
        name="/background",
        aliases=["/bg"],
        description="在后台执行任务（不阻塞当前对话）",
        category="task",
        scope=CommandScope.IM,
    ),
    SlashCommand(
        name="/restart",
        aliases=[],
        description="重启 Agent 服务",
        category="system",
        admin_only=True,
    ),
    SlashCommand(
        name="/feishu",
        aliases=[],
        description="飞书适配器管理",
        category="adapter",
        scope=CommandScope.IM,
    ),
]


def get_commands_for_scope(scope: str) -> list[SlashCommand]:
    """Get all commands available for a given scope (cli/im)."""
    result = []
    for cmd in COMMAND_REGISTRY:
        if cmd.scope == CommandScope.BOTH:
            result.append(cmd)
        elif cmd.scope.value == scope:
            result.append(cmd)
    return result


def is_slash_command(text: str) -> bool:
    """Check if text starts with a registered slash command."""
    text_lower = text.strip().lower()
    for cmd in COMMAND_REGISTRY:
        for trigger in cmd.all_triggers:
            if text_lower == trigger or text_lower.startswith(trigger + " "):
                return True
    return False


def format_help(scope: str = "im") -> str:
    """Generate formatted help text for a given scope."""
    commands = get_commands_for_scope(scope)
    categories: dict[str, list[SlashCommand]] = {}
    for cmd in commands:
        categories.setdefault(cmd.category, []).append(cmd)

    category_labels = {
        "general": "通用",
        "conversation": "对话管理",
        "model": "模型管理",
        "thinking": "思考模式",
        "agent": "多Agent",
        "persona": "人格",
        "security": "安全",
        "task": "任务",
        "system": "系统",
        "adapter": "适配器",
    }

    lines = ["**可用指令:**\n"]
    for cat, cmds in categories.items():
        label = category_labels.get(cat, cat)
        lines.append(f"**{label}:**")
        for cmd in cmds:
            aliases = ", ".join(f"`{a}`" for a in cmd.aliases)
            alias_str = f" ({aliases})" if aliases else ""
            lines.append(f"  `{cmd.name}`{alias_str} — {cmd.description}")
        lines.append("")

    return "\n".join(lines)
