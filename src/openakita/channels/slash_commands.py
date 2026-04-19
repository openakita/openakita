"""
Unified Slash Command Registry

Command definitions shared across CLI and IM Gateway, ensuring consistent
behavior on both sides. Each command declares its applicable scope
(cli/im/both) and required permission level.
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
        description="Show all available commands",
        category="general",
    ),
    SlashCommand(
        name="/new",
        aliases=["/新话题", "/reset"],
        description="Start a new topic, clear conversation context",
        category="conversation",
    ),
    SlashCommand(
        name="/model",
        aliases=[],
        description="Show current model and available models",
        category="model",
    ),
    SlashCommand(
        name="/switch",
        aliases=[],
        description="Temporarily switch model",
        category="model",
    ),
    SlashCommand(
        name="/restore",
        aliases=[],
        description="Restore default model",
        category="model",
    ),
    SlashCommand(
        name="/thinking",
        aliases=[],
        description="Toggle thinking mode [on|off|auto]",
        category="thinking",
    ),
    SlashCommand(
        name="/thinking_depth",
        aliases=[],
        description="Set thinking depth [low|medium|high]",
        category="thinking",
    ),
    SlashCommand(
        name="/chain",
        aliases=[],
        description="Chain-of-thought progress push toggle [on|off]",
        category="thinking",
    ),
    SlashCommand(
        name="/mode",
        aliases=["/模式"],
        description="Show current multi-agent mode description",
        category="agent",
        scope=CommandScope.IM,
    ),
    SlashCommand(
        name="/persona",
        aliases=["/人格"],
        description="Switch persona preset",
        category="persona",
    ),
    SlashCommand(
        name="/pair",
        aliases=[],
        description="DM pairing authorization management",
        category="security",
        scope=CommandScope.IM,
        admin_only=True,
    ),
    SlashCommand(
        name="/background",
        aliases=["/bg"],
        description="Run a task in the background (without blocking current conversation)",
        category="task",
        scope=CommandScope.IM,
    ),
    SlashCommand(
        name="/restart",
        aliases=[],
        description="Restart the agent service",
        category="system",
        admin_only=True,
    ),
    SlashCommand(
        name="/feishu",
        aliases=[],
        description="Feishu adapter management",
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
        "general": "General",
        "conversation": "Conversation",
        "model": "Model Management",
        "thinking": "Thinking Mode",
        "agent": "Multi-Agent",
        "persona": "Persona",
        "security": "Security",
        "task": "Tasks",
        "system": "System",
        "adapter": "Adapters",
    }

    lines = ["**Available Commands:**\n"]
    for cat, cmds in categories.items():
        label = category_labels.get(cat, cat)
        lines.append(f"**{label}:**")
        for cmd in cmds:
            aliases = ", ".join(f"`{a}`" for a in cmd.aliases)
            alias_str = f" ({aliases})" if aliases else ""
            lines.append(f"  `{cmd.name}`{alias_str} — {cmd.description}")
        lines.append("")

    return "\n".join(lines)
