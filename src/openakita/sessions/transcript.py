"""
JSONL session persistence

Modeled after Claude Code's sessionStorage.ts:
- Each message is appended as a single JSON line
- Loading supports starting from a compact boundary
- Sub-agents have independent transcript files
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRANSCRIPT_DIR = Path("data/transcripts")


def get_transcript_path(session_id: str) -> Path:
    """Return the transcript file path."""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    return TRANSCRIPT_DIR / f"{session_id}.jsonl"


def append_entry(session_id: str, entry: dict) -> None:
    """Append an entry to the transcript.

    Atomicity: each append writes a complete JSON line followed by a newline.
    """
    path = get_transcript_path(session_id)
    entry_with_ts = {
        "_ts": datetime.now().isoformat(),
        **entry,
    }
    line = json.dumps(entry_with_ts, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_message(
    session_id: str,
    role: str,
    content: Any,
    *,
    message_id: str = "",
    metadata: dict | None = None,
) -> None:
    """Append a message to the transcript."""
    entry: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": content if isinstance(content, str) else _serialize_content(content),
    }
    if message_id:
        entry["message_id"] = message_id
    if metadata:
        entry["metadata"] = metadata
    append_entry(session_id, entry)


def append_tool_result(
    session_id: str,
    tool_use_id: str,
    tool_name: str,
    result: str,
    is_error: bool = False,
) -> None:
    """Append a tool result to the transcript."""
    append_entry(
        session_id,
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "content": result[:5000] if len(result) > 5000 else result,
            "is_error": is_error,
        },
    )


def append_compact_boundary(session_id: str, summary: str = "") -> None:
    """Append a compaction boundary marker.

    When loading, reading can start after this marker, skipping the already-compacted prefix.
    """
    append_entry(
        session_id,
        {
            "type": "compact_boundary",
            "summary": summary,
        },
    )


def load_transcript(
    session_id: str,
    *,
    from_compact_boundary: bool = False,
) -> list[dict]:
    """Load the transcript.

    Args:
        session_id: Session ID
        from_compact_boundary: If True, start reading from the last compact_boundary

    Returns:
        List of message/event records
    """
    path = get_transcript_path(session_id)
    if not path.exists():
        return []

    entries: list[dict] = []
    last_boundary_idx = -1

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
                if entry.get("type") == "compact_boundary":
                    last_boundary_idx = len(entries) - 1
            except json.JSONDecodeError:
                logger.warning("Skipped malformed transcript line %d in %s", i, session_id)

    if from_compact_boundary and last_boundary_idx >= 0:
        return entries[last_boundary_idx + 1 :]

    return entries


def transcript_exists(session_id: str) -> bool:
    """Check whether a transcript exists."""
    return get_transcript_path(session_id).exists()


def _serialize_content(content: Any) -> Any:
    """Serialize message content."""
    if isinstance(content, list):
        result = []
        for block in content:
            if hasattr(block, "to_dict"):
                result.append(block.to_dict())
            elif isinstance(block, dict):
                result.append(block)
            else:
                result.append(str(block))
        return result
    return str(content)
