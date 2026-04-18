"""
Unified slash command registry.

Central definition of all slash commands shared by CLI and Desktop.
Each command entry declares metadata (name, label, description, scope)
so that both surfaces can discover and render them consistently.

The actual *action* of each command is environment-specific (CLI prints
to console, Desktop dispatches React state), so only metadata lives here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Flag, auto


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
    CommandDef("help", "Help", "Show available commands"),
    CommandDef("model", "Switch Model", "Select the LLM endpoint to use", args_hint="<endpoint>"),
    CommandDef("plan", "Plan Mode", "Toggle Plan mode: plan before executing"),
    CommandDef("clear", "Clear Conversation", "Clear all messages in the current conversation"),
    CommandDef("skill", "Use Skill", "Invoke an installed skill", args_hint="<skill>"),
    CommandDef("persona", "Switch Persona", "Switch the agent's persona preset", args_hint="<persona-id>"),
    CommandDef("agent", "Switch Agent", "Switch between multiple agents", args_hint="<agent-name>"),
    CommandDef("agents", "Agent List", "Show available agents"),
    CommandDef(
        "org",
        "Organization Mode",
        "Switch to organization orchestration mode",
        args_hint="<org-name|off>",
        scope=CommandScope.DESKTOP,
    ),
    CommandDef("thinking", "Deep Thinking", "Set thinking mode", args_hint="on|off|auto"),
    CommandDef("thinking_depth", "Thinking Depth", "Set thinking depth", args_hint="low|medium|high"),
    CommandDef("status", "Agent Status", "Show agent running status", scope=CommandScope.CLI),
    CommandDef("selfcheck", "Self-Check", "Run system self-check", scope=CommandScope.CLI),
    CommandDef("memory", "Memory Info", "View agent memory", scope=CommandScope.CLI),
    CommandDef("skills", "Skill List", "View installed skills", scope=CommandScope.CLI),
    CommandDef("channels", "IM Channels", "View IM channel status", scope=CommandScope.CLI),
    CommandDef("sessions", "Session List", "View CLI session history", scope=CommandScope.CLI),
    CommandDef(
        "session", "Switch Session", "Switch to a specific CLI session", args_hint="<#>", scope=CommandScope.CLI
    ),
    CommandDef("exit", "Exit", "Exit OpenAkita", aliases=("quit",), scope=CommandScope.CLI),
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
