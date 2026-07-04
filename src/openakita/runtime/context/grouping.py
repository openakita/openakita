"""Group flat messages into tool-call interaction units.

Extracted from :func:`openakita.core.context_manager.ContextManager.group_messages`
in P-RC-4 (P4.13). The legacy version was a 60-LOC staticmethod that
walked the message list and combined an ``assistant`` message with
``tool_use`` blocks together with its following ``tool_result``
messages into a single "group". Summariser and truncator passes use
the grouping to avoid splitting a tool call from its result -- an
LLM contract violation.

Pure function: no side effects, no I/O, no dependency on the legacy
class.
"""

from __future__ import annotations


def group_messages(messages: list[dict]) -> list[list[dict]]:
    """Partition ``messages`` into tool-interaction groups.

    Rules (byte-faithful with the legacy implementation):

    * an ``assistant`` message carrying any ``tool_use`` blocks
      starts a group; subsequent ``user`` messages whose content is
      a list of ``tool_result`` blocks (only) are appended to it;
      ``tool`` role messages (legacy OpenAI shape) are also
      appended;
    * any other message stands alone as a single-element group.
    """
    if not messages:
        return []

    groups: list[list[dict]] = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content", "")

        has_tool_calls = False
        if role == "assistant" and isinstance(content, list):
            has_tool_calls = any(
                isinstance(item, dict) and item.get("type") == "tool_use"
                for item in content
            )

        if has_tool_calls:
            group = [msg]
            i += 1
            while i < len(messages):
                next_msg = messages[i]
                next_role = next_msg.get("role", "")
                next_content = next_msg.get("content", "")

                if next_role == "user" and isinstance(next_content, list):
                    all_tool_results = all(
                        isinstance(item, dict)
                        and item.get("type") == "tool_result"
                        for item in next_content
                        if isinstance(item, dict)
                    )
                    if all_tool_results and next_content:
                        group.append(next_msg)
                        i += 1
                        continue

                if next_role == "tool":
                    group.append(next_msg)
                    i += 1
                    continue

                break

            groups.append(group)
        else:
            groups.append([msg])
            i += 1

    return groups


__all__ = ["group_messages"]
