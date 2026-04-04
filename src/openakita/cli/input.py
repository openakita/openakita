"""
CLI input system powered by prompt_toolkit.

Replaces rich.prompt.Prompt.ask with a full-featured input:
- Persistent file history (~/.openakita/cli_history)
- Slash command completion
- Multi-line editing (Alt+Enter to send, Enter for newline in multi-line mode)
- Ctrl+C clears current line; double Ctrl+C or /exit to quit
- Auto syntax highlight for / commands
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys


class SlashCommandCompleter(Completer):
    """Completes slash commands with descriptions."""

    def __init__(self, commands: Sequence[tuple[str, str]] = ()):
        """commands: sequence of (name, description) tuples, e.g. ("/help", "显示帮助")"""
        self._commands = list(commands)

    def set_commands(self, commands: Sequence[tuple[str, str]]) -> None:
        self._commands = list(commands)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return

        for name, desc in self._commands:
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=name,
                    display_meta=desc,
                )


def _build_key_bindings() -> KeyBindings:
    """Custom key bindings for the CLI input.

    - Enter sends the input (single-line default)
    - Alt+Enter / Shift+Enter inserts a newline
    - Ctrl+C clears the buffer (or raises KeyboardInterrupt if empty)
    """
    kb = KeyBindings()

    @kb.add(Keys.Escape, Keys.Enter)
    def _alt_enter(event):
        event.current_buffer.insert_text("\n")

    return kb


def create_cli_session(
    commands: Sequence[tuple[str, str]] = (),
) -> tuple[PromptSession, SlashCommandCompleter]:
    """Create a configured PromptSession for CLI interactive mode.

    Returns (session, completer) so the completer can be updated later.
    """
    history_dir = Path.home() / ".openakita"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / "cli_history"

    completer = SlashCommandCompleter(commands)
    kb = _build_key_bindings()

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        completer=completer,
        complete_while_typing=True,
        key_bindings=kb,
        enable_history_search=True,
        mouse_support=False,
        multiline=False,
    )

    return session, completer


async def prompt_input(session: PromptSession, prompt_text: str = "You> ") -> str:
    """Async wrapper around prompt_toolkit's prompt.

    Returns the user input string. Raises EOFError on Ctrl+D, KeyboardInterrupt on double Ctrl+C.
    """
    return await session.prompt_async(
        HTML(f"<b><style fg='dodgerblue'>{prompt_text}</style></b>"),
    )
