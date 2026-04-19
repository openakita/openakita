"""
Subdirectory context progressive injection.

When a tool accesses a subdirectory, automatically discovers context files
such as AGENTS.md / .cursorrules in that directory and appends their content
to the tool's return value.

Complements the root-level AGENTS.md in the system prompt:
- Root AGENTS.md -> system prompt (global rules)
- Subdirectory AGENTS.md -> tool result injection (local rules, loaded on demand)

This keeps the system prompt concise while providing directory-level context to the LLM.
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CONTEXT_FILES = ("AGENTS.md", ".cursorrules", "CLAUDE.md")
_MAX_CHARS = 4000
_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, str | None]] = {}


def discover_subdir_context(directory: str | Path) -> str | None:
    """
    Check if a directory has context files and return their content.

    Results are cached for up to 5 minutes per directory.

    Returns formatted context string or None.
    """
    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        return None

    cache_key = str(dir_path)
    if cache_key in _cache:
        ts, cached_result = _cache[cache_key]
        if time.monotonic() - ts < _CACHE_TTL_SECONDS:
            return cached_result
        del _cache[cache_key]

    parts = []
    for filename in _CONTEXT_FILES:
        filepath = dir_path / filename
        if filepath.is_file():
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                if content.strip():
                    truncated = content[:_MAX_CHARS]
                    if len(content) > _MAX_CHARS:
                        truncated += f"\n... (truncated, {len(content)} total chars)"
                    parts.append(f"[{filename} from {dir_path.name}/]\n{truncated}")
                    logger.debug("Discovered subdir context: %s/%s", dir_path, filename)
            except OSError:
                pass

    result = "\n\n".join(parts) if parts else None
    _cache[cache_key] = (time.monotonic(), result)
    return result


def inject_subdir_context(tool_result: str, accessed_path: str | Path) -> str:
    """
    If the accessed path's directory has context files, append them to tool result.

    Args:
        tool_result: Original tool return value
        accessed_path: File or directory path the tool accessed

    Returns:
        Possibly augmented tool result
    """
    path = Path(accessed_path)
    directory = path if path.is_dir() else path.parent

    context = discover_subdir_context(directory)
    if context:
        return f"{tool_result}\n\n---\n📋 Directory context:\n{context}"

    return tool_result


def clear_cache() -> None:
    """Clear the context cache (useful after file changes)."""
    _cache.clear()
