"""Smart-truncate helper for oversized tool output.

Pure function: takes a string, returns a (possibly
truncated) string + a was-truncated flag. The truncation strategy
keeps a configurable head fraction + a tail fraction so the LLM
sees both the start (intent + first errors) and the end (final
status) of long output; the dropped middle is saved to a sidecar
file by the optional :func:`save_overflow` callback so the agent
can ``read_file`` it later if needed.
"""

from __future__ import annotations

from collections.abc import Callable

from openakita.config import settings

DEFAULT_TOOL_RESULT_MAX_CHARS = 32000
MAX_TOOL_RESULT_CHARS = DEFAULT_TOOL_RESULT_MAX_CHARS  # backward-compatible export
OVERFLOW_MARKER = "[OUTPUT_TRUNCATED]"


def get_tool_result_max_chars() -> int:
    """Return the runtime cap for tool result size.

    Reads ``settings.tool_result_max_chars`` with a floor of 1000 and
    falls back to :data:`DEFAULT_TOOL_RESULT_MAX_CHARS` on parse error.
    Implements the runtime tool-result size limit contract.
    """
    try:
        return max(
            1000,
            int(getattr(settings, "tool_result_max_chars", DEFAULT_TOOL_RESULT_MAX_CHARS)),
        )
    except (TypeError, ValueError):
        return DEFAULT_TOOL_RESULT_MAX_CHARS


def smart_truncate(
    content: str,
    limit: int,
    *,
    label: str = "content",
    save_full: bool = True,
    head_ratio: float = 0.65,
    save_overflow_fn: Callable[[str, str], str] | None = None,
) -> tuple[str, bool]:
    """Truncate ``content`` to ``limit`` chars; return ``(text, was_truncated)``.

    The truncated form is ``head + marker + tail`` where ``head`` is
    ``int(limit * head_ratio)`` chars and ``tail`` is the rest minus
    a 120-char budget for the marker. When ``content`` already fits,
    returns the original string unchanged with ``was_truncated=False``.

    The ``save_overflow_fn`` callback (defaults to the v2
    :func:`save_overflow`) is invoked when ``save_full=True``; it
    receives ``(label, content)`` and returns the sidecar file path.
    The marker mentions that path so the LLM can recover the full
    text via ``read_file``.
    """
    if not content or len(content) <= limit:
        return content, False

    head = int(limit * head_ratio)
    tail = limit - head - 120
    if tail < 0:
        tail = 0

    overflow_ref = ""
    if save_full:
        if save_overflow_fn is None:
            from .overflow import save_overflow as _default_save_overflow

            save_overflow_fn = _default_save_overflow
        path = save_overflow_fn(label, content)
        overflow_ref = f", 完整内容: {path}, 可用 read_file 查看"

    marker = f"\n[已截断, 原文{len(content)}字{overflow_ref}]\n"

    if tail > 0:
        return content[:head] + marker + content[-tail:], True
    return content[:head] + marker, True


__all__ = [
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "OVERFLOW_MARKER",
    "get_tool_result_max_chars",
    "smart_truncate",
]
