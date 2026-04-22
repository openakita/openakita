"""Prompt asset loader for ``contrib/data/prompts/*``.

Why a loader (P3.1-P3.5 from
``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``):

The 5 prompt assets shipped under
``openakita_plugin_sdk/contrib/data/prompts/`` are the **single source of
truth** for the prompts that drive OpenAkita's agent loops, structure
proposals, reviewers, and checkpoint protocol.  Multiple plugins and the
host all consume them — the loader exists so callers do *not* hand-craft
relative file paths (which break the moment the SDK is installed as a
wheel).

Usage::

    from openakita_plugin_sdk.contrib import (
        load_prompt, list_prompts, render_prompt,
    )

    sys_prompt = load_prompt("agent_loop_system")            # str
    finishers = load_prompt("agent_loop_finishers")           # dict[str, str]
    proposal = render_prompt(
        "structure_proposal",
        VIDEO_SUMMARY_PLACEHOLDER="...",
        MAIN_CHARACTER_PLACEHOLDER="Batman",
        ...
    )
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

# Single source of truth — the 5 P3 assets.  Adding a sixth prompt MUST
# go through this list so missing-file vs. typo-name failures stay clear.
_PROMPT_NAMES: tuple[str, ...] = (
    "structure_proposal",
    "agent_loop_system",
    "agent_loop_finishers",
    "reviewer_protocol",
    "checkpoint_protocol",
)

# Filename mapping — most are .txt, the markdown protocols keep .md so an
# editor opens them with the right syntax highlighting.
_PROMPT_FILES: dict[str, str] = {
    "structure_proposal": "structure_proposal.txt",
    "agent_loop_system": "agent_loop_system.txt",
    "agent_loop_finishers": "agent_loop_finishers.txt",
    "reviewer_protocol": "reviewer_protocol.md",
    "checkpoint_protocol": "checkpoint_protocol.md",
}


class PromptNotFound(KeyError):
    """Raised when a caller asks for a prompt name that is not registered.

    Inherits from ``KeyError`` so callers can catch with either type, but
    also keeps a dedicated class name for log filtering.
    """


def list_prompts() -> tuple[str, ...]:
    """Return the registered prompt names — useful for tests and tooling."""
    return _PROMPT_NAMES


@lru_cache(maxsize=len(_PROMPT_NAMES))
def _read_raw(name: str) -> str:
    """Read a prompt file from packaged data, lru-cached for the process.

    Uses ``importlib.resources`` so the prompt files keep working when the
    SDK is installed as a wheel/zipapp — never assumes the source tree is
    on disk.
    """
    if name not in _PROMPT_FILES:
        raise PromptNotFound(
            f"Unknown prompt {name!r}.  Registered: {sorted(_PROMPT_NAMES)}"
        )
    filename = _PROMPT_FILES[name]
    pkg = resources.files("openakita_plugin_sdk.contrib.data.prompts")
    return (pkg / filename).read_text(encoding="utf-8")


def _strip_header_comments(text: str) -> str:
    """Drop the leading ``# Asset: ...`` provenance comment block.

    Each prompt file begins with a comment header documenting source and
    placeholder conventions — useful for humans, distracting for the LLM.

    The markdown protocol files (``# Reviewer — Meta Skill``) intentionally
    open with ``# Title`` *as actual content* right after a blank-line gap.
    To handle both shapes uniformly we:

    1. Confirm the file starts with ``# Asset:`` (otherwise return verbatim).
    2. Strip every leading line until the first blank line — the blank line
       is the documented end-of-header marker in every P3 asset.
    3. Strip that single blank separator and return the rest verbatim, so
       the markdown ``# Title`` body line survives intact.
    """
    lines = text.splitlines()
    if not lines:
        return text
    if not lines[0].startswith("# Asset:"):
        return text

    idx = 0
    while idx < len(lines) and lines[idx].strip() != "":
        idx += 1
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    return "\n".join(lines[idx:])


def _parse_sections(text: str) -> dict[str, str]:
    """Parse a multi-section prompt file (used by ``agent_loop_finishers``).

    Sections are introduced by a line ``[name]`` and separated by a line
    containing only ``---``.  Returns ``{name: text}`` with each section's
    content trimmed.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    buffer: list[str] = []

    def _flush() -> None:
        if current_name is not None:
            sections[current_name] = "\n".join(buffer).strip()

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "---":
            _flush()
            current_name = None
            buffer = []
            continue
        if (
            stripped.startswith("[")
            and stripped.endswith("]")
            and current_name is None
        ):
            current_name = stripped[1:-1].strip()
            buffer = []
            continue
        if current_name is not None:
            buffer.append(line)
    _flush()
    return sections


def load_prompt(name: str) -> str | dict[str, str]:
    """Load a prompt asset by registered name.

    Returns:
        * ``str`` for single-section prompts (``structure_proposal``,
          ``agent_loop_system``, ``reviewer_protocol``,
          ``checkpoint_protocol``).
        * ``dict[str, str]`` for multi-section prompts
          (``agent_loop_finishers`` → ``{"finish": ..., "use_tool": ...}``).

    Raises:
        PromptNotFound: ``name`` is not registered.
    """
    raw = _read_raw(name)
    body = _strip_header_comments(raw)
    if name == "agent_loop_finishers":
        sections = _parse_sections(body)
        if not sections:
            raise PromptNotFound(
                f"Prompt {name!r} parsed to zero sections — file format broken"
            )
        return sections
    return body


def render_prompt(name: str, /, **placeholders: Any) -> str:
    """Load ``name`` and substitute ``KEY=value`` placeholders verbatim.

    Uses literal ``str.replace`` (not ``str.format``) because CutClaw's
    placeholder convention uses bare tokens like
    ``MAIN_CHARACTER_PLACEHOLDER`` which would explode ``{}``-style
    formatters on stray braces in the prompt body (e.g. the JSON example
    block in ``structure_proposal``).

    Only single-section prompts are supported — multi-section ones must
    be loaded with :func:`load_prompt` and rendered per section.

    Example::

        text = render_prompt(
            "structure_proposal",
            MAIN_CHARACTER_PLACEHOLDER="Batman",
            INSTRUCTION_PLACEHOLDER="visceral combat highlight",
        )
    """
    base = load_prompt(name)
    if not isinstance(base, str):
        raise ValueError(
            f"render_prompt: {name!r} is multi-section; use load_prompt()"
            " and render each section explicitly."
        )
    out = base
    for key, value in placeholders.items():
        out = out.replace(key, str(value))
    return out


__all__ = [
    "PromptNotFound",
    "list_prompts",
    "load_prompt",
    "render_prompt",
]
