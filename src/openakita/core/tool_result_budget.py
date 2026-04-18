"""
Tool result budget: truncation and overflow persistence for large results

Modeled after Claude Code's tool result management:
- Limit individual tool result size by character count
- Save oversized results to files and return a reference path
- Intelligently truncate structured results (JSON)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULT_CHARS = 30_000
OVERFLOW_DIR = Path("data/tool-overflow")


def truncate_tool_result(
    result: str,
    *,
    max_chars: int = DEFAULT_MAX_RESULT_CHARS,
    tool_name: str = "",
    session_id: str = "",
) -> str:
    """Truncate oversized tool results.

    If the result exceeds max_chars:
    1. Persist to data/tool-overflow/
    2. Return a truncated version with a file reference

    Args:
        result: Raw result string
        max_chars: Maximum character count
        tool_name: Tool name (used in logs and filenames)
        session_id: Session ID

    Returns:
        Potentially truncated result string
    """
    if len(result) <= max_chars:
        return result

    logger.info(
        "Tool result exceeds budget: %s (%d chars > %d limit)",
        tool_name,
        len(result),
        max_chars,
    )

    # Try saving to file
    file_ref = ""
    if session_id:
        file_ref = _save_overflow(result, tool_name, session_id)

    truncated = result[:max_chars]

    # For JSON results, try to preserve structure
    if result.lstrip().startswith(("{", "[")):
        truncated = _smart_json_truncate(result, max_chars)

    suffix = f"\n\n[Truncated: {len(result):,} chars total, showing first {max_chars:,}]"
    if file_ref:
        suffix += f"\n[Full result saved to: {file_ref}]"

    return truncated + suffix


def _smart_json_truncate(result: str, max_chars: int) -> str:
    """Intelligent JSON truncation.

    For JSON arrays, truncate the number of elements.
    For JSON objects, truncate the values.
    """
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return result[:max_chars]

    if isinstance(data, list) and len(data) > 10:
        # Keep first N items that fit in budget
        items = []
        current_len = 2  # for []
        for item in data:
            item_str = json.dumps(item, ensure_ascii=False, default=str)
            if current_len + len(item_str) + 2 > max_chars:
                break
            items.append(item)
            current_len += len(item_str) + 2

        return json.dumps(items, indent=2, ensure_ascii=False, default=str)

    return result[:max_chars]


def _save_overflow(result: str, tool_name: str, session_id: str) -> str:
    """Save overflow result to a file."""
    try:
        overflow_dir = OVERFLOW_DIR / session_id
        overflow_dir.mkdir(parents=True, exist_ok=True)

        import time

        filename = f"{tool_name}_{int(time.time())}.txt"
        file_path = overflow_dir / filename
        file_path.write_text(result, encoding="utf-8")

        logger.debug("Overflow result saved to %s", file_path)
        return str(file_path)
    except Exception as e:
        logger.warning("Failed to save overflow result: %s", e)
        return ""
