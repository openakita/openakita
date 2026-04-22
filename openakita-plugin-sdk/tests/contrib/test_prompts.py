"""Tests for openakita_plugin_sdk.contrib.prompts (P3.1-P3.5).

The 5 prompt assets shipped under ``contrib/data/prompts/`` are the
single source of truth for the prompts driving CutClaw-derived agent
loops in OpenAkita.  These tests pin:

* the registered prompt names + their underlying filenames,
* header-comment stripping (so the LLM never sees provenance noise),
* multi-section parsing (``agent_loop_finishers`` returns dict),
* placeholder rendering for ``structure_proposal`` (the only prompt
  with required placeholders),
* error envelopes for unknown names + multi-section render misuse,
* importlib.resources path so the loader keeps working when the SDK
  is installed as a wheel.
"""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import (
    PromptNotFound,
    list_prompts,
    load_prompt,
    render_prompt,
)
from openakita_plugin_sdk.contrib import prompts as prompts_mod


# ── registry ──────────────────────────────────────────────────────────


def test_list_prompts_returns_all_five_p3_assets() -> None:
    """If a future commit adds a sixth asset it MUST go through the
    registry — this test will fail until the new name is added."""
    assert set(list_prompts()) == {
        "structure_proposal",
        "agent_loop_system",
        "agent_loop_finishers",
        "reviewer_protocol",
        "checkpoint_protocol",
    }


def test_unknown_prompt_raises_prompt_not_found() -> None:
    with pytest.raises(PromptNotFound, match="Unknown prompt"):
        load_prompt("does_not_exist")


def test_prompt_not_found_is_a_key_error() -> None:
    """Callers can catch with either ``KeyError`` or ``PromptNotFound``."""
    assert issubclass(PromptNotFound, KeyError)


# ── header stripping ──────────────────────────────────────────────────


def test_load_strips_provenance_header_from_text_files() -> None:
    """``# Asset:`` / ``# Source:`` lines must NEVER reach the LLM —
    they're internal documentation and would burn tokens otherwise."""
    text = load_prompt("agent_loop_system")
    assert isinstance(text, str)
    assert "# Asset:" not in text
    assert "# Source:" not in text
    # actual content survives
    assert "THINK" in text
    assert "ACT" in text
    assert "OBSERVE" in text


def test_load_preserves_markdown_title_in_protocol_files() -> None:
    """The markdown protocols open with ``# Title`` *as content* right
    after the header gap — that title MUST survive stripping (otherwise
    the agent's reviewer prompt loses its h1)."""
    text = load_prompt("reviewer_protocol")
    assert isinstance(text, str)
    assert text.lstrip().startswith("# Reviewer")
    assert "## When to Use" in text


def test_load_preserves_h1_in_checkpoint_protocol() -> None:
    text = load_prompt("checkpoint_protocol")
    assert isinstance(text, str)
    assert text.lstrip().startswith("# Checkpoint Protocol")
    assert "approval" in text.lower()


# ── multi-section parsing (P3.3) ──────────────────────────────────────


def test_finishers_returns_dict_with_finish_and_use_tool() -> None:
    """P3.3 ships *two* prompts in one file separated by ``---`` — the
    loader must split them so callers can pick by name."""
    sections = load_prompt("agent_loop_finishers")
    assert isinstance(sections, dict)
    assert set(sections.keys()) == {"finish", "use_tool"}


def test_finisher_finish_section_mentions_commit() -> None:
    sections = load_prompt("agent_loop_finishers")
    assert "commit" in sections["finish"].lower()


def test_finisher_use_tool_section_mentions_tool_calls() -> None:
    sections = load_prompt("agent_loop_finishers")
    assert "tool_calls" in sections["use_tool"]


# ── placeholder rendering (P3.1) ──────────────────────────────────────


def test_render_structure_proposal_substitutes_all_placeholders() -> None:
    """CutClaw uses bare-token placeholders (no ``{}``) so brace-free
    ``str.replace`` is correct — verify a few key ones get filled in."""
    rendered = render_prompt(
        "structure_proposal",
        VIDEO_SUMMARY_PLACEHOLDER="A corgi running on the beach.",
        MAIN_CHARACTER_PLACEHOLDER="Pippin",
        INSTRUCTION_PLACEHOLDER="zoomy beach montage",
        TOTAL_SCENE_COUNT_PLACEHOLDER="12",
        MAX_SCENE_INDEX_PLACEHOLDER="11",
        AUDIO_SUMMARY_PLACEHOLDER="upbeat ukelele",
        AUDIO_STRUCTURE_PLACEHOLDER="intro 0-5s, build 5-25s, peak 25-45s",
    )
    assert "Pippin" in rendered
    assert "corgi running on the beach" in rendered
    assert "zoomy beach montage" in rendered
    # verify NONE of the placeholder tokens leak into the rendered text
    assert "PLACEHOLDER" not in rendered


def test_render_prompt_rejects_multi_section_prompt() -> None:
    """Multi-section prompts must not be rendered with placeholders —
    otherwise the loader would happily run replace across the whole
    concatenated body and silently include section headers."""
    with pytest.raises(ValueError, match="multi-section"):
        render_prompt("agent_loop_finishers", ANY_PLACEHOLDER="x")


def test_render_prompt_with_no_placeholders_returns_raw_body() -> None:
    """Calling ``render_prompt`` on an asset that has no placeholders
    must be a no-op — used by callers that just want the body."""
    body = render_prompt("agent_loop_system")
    raw = load_prompt("agent_loop_system")
    assert body == raw


# ── importlib.resources path ──────────────────────────────────────────


def test_loader_does_not_assume_filesystem_layout() -> None:
    """The loader uses importlib.resources so the prompt files keep
    working from a wheel/zipapp install.  This test asserts the public
    API does not require ``__file__`` paths from callers."""
    assert "Path" not in load_prompt.__doc__ if load_prompt.__doc__ else True
    text = prompts_mod._read_raw("agent_loop_system")
    assert text.startswith("# Asset:")


def test_lru_cache_returns_same_string_object() -> None:
    """Re-loading a hot prompt must hit the cache — saves IO on every
    agent loop step."""
    a = prompts_mod._read_raw("agent_loop_system")
    b = prompts_mod._read_raw("agent_loop_system")
    assert a is b
