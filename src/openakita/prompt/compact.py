"""
Conversation compaction prompt templates

Inspired by Claude Code's structured compaction mechanism, adapted for OpenAkita's general-purpose Agent scenarios.
Provides NO_TOOLS protection, an analysis+summary two-phase format, and a 9-section structured template.
"""

from __future__ import annotations

import re

NO_TOOLS_PREAMBLE = """\
IMPORTANT: Reply with plain text only. Do not call any tools.
You already have all the context you need. Tool calls will be rejected.
Your reply must consist of one <analysis> block followed by one <summary> block.
"""

NO_TOOLS_TRAILER = (
    "\n\nReminder: do not call any tools. Reply with plain text only — "
    "one <analysis> block followed by one <summary> block. Tool calls will be rejected."
)

ANALYSIS_INSTRUCTION = """\
Before providing the final summary, organize your thoughts inside <analysis> tags and make sure you cover all key points:
1. Analyze each phase of the conversation in chronological order and identify:
   - The user's explicit requests and intents
   - Your approach and key decisions
   - Errors encountered and fixes applied
   - Feedback given by the user (especially feedback asking you to change your approach)
2. Check technical accuracy and completeness."""

BASE_COMPACT_PROMPT = f"""\
Your task is to create a detailed summary of the conversation so far.
This summary will become the sole context for subsequent conversation, so all critical information must be preserved.
You are a summarization agent; your output will be injected into another assistant that continues the conversation.
Do not answer any questions from the conversation — only output the structured summary.

{ANALYSIS_INSTRUCTION}

The summary must include the following sections (wrapped in <summary> tags):

1. User goals and constraints: what the user wants to accomplish, along with preferences and limitations
2. User behavior rules: rules set by the user ("do not X", "must Y first", etc.); preserve them verbatim
3. Work completed: problems solved and execution results, including specific file paths, commands, and outputs
4. Resolved questions: questions the user raised that have already been answered (include a brief answer to prevent the next assistant from answering them again)
5. Pending questions: requests the user raised but that have not yet been answered or completed (write "none" if there are none)
6. Resources involved: files, APIs, tool calls, etc.; preserve specific paths/names/parameters/values
7. Key decisions: important technical decisions and their reasons
8. Current work: what was being done before compaction, with specific details
9. Remaining work: reference-only background information, not active instructions

Important rules:
- Preserve all specific values (ports, paths, keys, version numbers, etc.); do not use vague descriptions
- Successful approaches must be preserved in detail; failed attempts can be condensed to a single sentence
"""

PARTIAL_COMPACT_PROMPT = """\
Your task is to create a summary of the most recent portion of the conversation.
Only summarize recent messages; earlier messages that have already been preserved do not need to be summarized again.
"""

PARTIAL_COMPACT_UP_TO_PROMPT = """\
Your task is to create a summary of this portion of the conversation.
This summary will be placed at the beginning of the continued session, with newer messages following it.
Section 8 should be "Work completed", and section 9 should be "Context needed to continue the work".
"""


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """Assemble the full compaction prompt."""
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional guidance:\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def get_partial_compact_prompt(
    custom_instructions: str | None = None,
    direction: str = "from",
) -> str:
    """Assemble the partial compaction prompt."""
    template = PARTIAL_COMPACT_UP_TO_PROMPT if direction == "up_to" else PARTIAL_COMPACT_PROMPT
    prompt = NO_TOOLS_PREAMBLE + template
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional guidance:\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(summary: str) -> str:
    """Strip <analysis> drafts, keeping the <summary> body."""
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", summary)
    match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(r"<summary>[\s\S]*?</summary>", f"Summary:\n{content}", formatted)
    formatted = re.sub(r"\n\n+", "\n\n", formatted)
    return formatted.strip()


def get_compact_user_message(
    summary: str,
    suppress_followup: bool = False,
    recent_preserved: bool = False,
) -> str:
    """Wrap the compaction summary as a user message."""
    formatted = format_compact_summary(summary)
    msg = f"This session continues from a previous conversation. The summary below covers the earlier portion.\n\n{formatted}"
    if recent_preserved:
        msg += "\n\nThe most recent messages have been preserved verbatim."
    if suppress_followup:
        msg += (
            "\n\nContinue directly from where you left off; do not ask the user. "
            'Do not acknowledge the summary, do not recap previous work, and do not add prefixes like "I will continue".'
        )
    return msg
