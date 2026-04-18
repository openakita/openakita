"""
Standard SSE (Server-Sent Events) parser

Compliant with the SSE spec (https://html.spec.whatwg.org/multipage/server-sent-events.html):
- Supports multi-line data field concatenation
- Supports event type field
- Supports [DONE] termination signal
- Tolerant: JSONDecodeError logs a warning instead of crashing
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


async def parse_sse_stream(response) -> AsyncIterator[dict]:
    """Parse an SSE event stream from an httpx response.

    Args:
        response: httpx.Response (must have .aiter_lines() available)

    Yields:
        Parsed JSON event dicts
    """
    data_parts: list[str] = []
    event_type: str | None = None

    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].strip())
        elif line.startswith(":"):
            # SSE comment, ignore (often used as keepalive)
            continue
        elif line == "":
            # Empty line = end of event
            if not data_parts:
                continue
            full_data = "\n".join(data_parts)
            data_parts = []
            current_event_type = event_type
            event_type = None

            if full_data == "[DONE]":
                return

            try:
                parsed = json.loads(full_data)
            except json.JSONDecodeError:
                logger.warning(
                    "SSE JSON parse error (event_type=%s): %s",
                    current_event_type,
                    full_data[:300],
                )
                continue

            if current_event_type:
                parsed["_sse_event_type"] = current_event_type

            yield parsed

    # Handle unterminated final event (no trailing blank line)
    if data_parts:
        full_data = "\n".join(data_parts)
        if full_data != "[DONE]":
            try:
                yield json.loads(full_data)
            except json.JSONDecodeError:
                logger.warning("SSE final chunk parse error: %s", full_data[:300])
