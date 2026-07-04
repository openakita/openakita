"""Compress + sanitize helpers extracted from ``core.context_manager``.

P-RC-4 P4.13. The leaf-level helpers extracted here:

* :func:`pre_request_cleanup` -- thin wrapper over
  :func:`openakita.core.microcompact.microcompact`, used by
  :class:`ContextManager` BEFORE the LLM call to drop stale tool
  results, large-result previews, and old thinking blocks.
* :func:`sanitize_tool_pairs` -- ensures every ``tool_use`` has a
  matching ``tool_result`` (and vice-versa) after a compression /
  truncation pass; orphans would otherwise cause the Anthropic
  ``tool_use_id`` validation to reject the next request.

The deep compression methods (``compress_if_needed``,
``_compress_across_boundary``, ``_llm_compress_text``,
``_summarize_messages_chunked``, ...) remain in the legacy module
for now; they will move to ``runtime.context`` in P-RC-7 as part of
the legacy ``core/`` tree deletion.
"""

from __future__ import annotations


def pre_request_cleanup(messages: list[dict]) -> list[dict]:
    """Lightweight pre-LLM-call cleanup (microcompact).

    Drops expired tool results, replaces oversized tool results with
    a preview + sidecar marker, and prunes stale thinking blocks.
    Used immediately before ``compress_if_needed`` so the heavy
    compression path sees a smaller working set.
    """
    from openakita.core.microcompact import microcompact

    return microcompact(messages)


def sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    """Ensure ``tool_use`` <-> ``tool_result`` symmetry.

    A compression / truncation pass may drop a ``tool_use`` block
    while keeping its ``tool_result`` (or vice-versa). The Anthropic
    API rejects such asymmetric histories with a
    ``tool_use_id`` validation error. This helper walks the message
    list and removes any orphan ``tool_use`` or ``tool_result`` so
    the rewritten history remains contract-valid.
    """
    # Collect every tool_use id present in assistant messages.
    tool_use_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and isinstance(item.get("id"), str):
                tool_use_ids.add(item["id"])

    # Collect every tool_use_id referenced by tool_result blocks.
    tool_result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_result":
                tu_id = item.get("tool_use_id")
                if isinstance(tu_id, str):
                    tool_result_ids.add(tu_id)

    paired = tool_use_ids & tool_result_ids

    # Rebuild the message list, filtering orphan blocks; messages
    # whose content becomes empty after filtering are themselves
    # dropped.
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content: list = []
        for item in content:
            if not isinstance(item, dict):
                new_content.append(item)
                continue
            block_type = item.get("type")
            if block_type == "tool_use":
                if item.get("id") in paired:
                    new_content.append(item)
                continue
            if block_type == "tool_result":
                if item.get("tool_use_id") in paired:
                    new_content.append(item)
                continue
            new_content.append(item)
        if new_content:
            new_msg = dict(msg)
            new_msg["content"] = new_content
            result.append(new_msg)
    return result


__all__ = ["pre_request_cleanup", "sanitize_tool_pairs"]
