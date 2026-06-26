"""Causal reasoning-chain timeline projection for the chat UI.

The desktop client assembles a faithful, causally-ordered reasoning chain from
the live SSE stream (``ChainGroup.entries[]`` in
``apps/setup-center/src/types.ts``: thinking / narration text / tool_start with
args / tool_end with result / context compression, in arrival order). That rich
structure used to exist *only* in the browser; the server persisted just a
lossy ``chain_summary`` (per-iteration thinking preview + a flat tool list), so
reloading a conversation on another window / device — or after localStorage was
evicted — rebuilt a degraded chain (no narration, no tool arguments, no
text/tool interleaving).

``ChainTimelineBuilder`` mirrors the browser's assembly on the server by
observing the *same* event stream the API already forwards, and emits a bounded
``chain_timeline`` that the history endpoint returns and the client restores
verbatim. This narrows the gap between the live stream and the persisted
history (the two pipelines now build the same structure from the same events)
without touching the reasoning engine or the LLM transcript.

Design constraints:
  - Structure-faithful, content-bounded. Each entry's text / args / result is
    capped, and the number of groups / entries per group is capped, so a
    tool-heavy turn cannot bloat ``sessions.json``. Old messages additionally
    drop ``chain_timeline`` via ``Session._HEAVY_METADATA_KEYS``.
  - Pure observer. ``observe()`` never raises on unexpected payloads and never
    influences what is streamed to the client.
  - Entry shapes match the frontend ``ChainEntry`` union (camelCase keys:
    ``toolId`` / ``beforeTokens`` / ``afterTokens``) so the client can restore
    them with minimal coercion.
"""

from __future__ import annotations

import json
from typing import Any

# Per-entry content caps (characters).
_THINKING_CAP = 1500
_TEXT_CAP = 2000
_ARGS_CAP = 500
_RESULT_CAP = 800
_CONFIG_HINT_TEXT_CAP = 500
_CONFIG_HINT_ACTIONS_CAP = 5
# Structural caps to keep the persisted blob small on tool-heavy turns.
_MAX_GROUPS = 60
_MAX_ENTRIES_PER_GROUP = 80
# Hard ceiling on the total character payload of one message's timeline. The
# per-entry / per-group caps alone allow a pathological worst case (tens of MB)
# on a very tool-heavy turn; this budget is what actually bounds sessions.json
# growth. Once exceeded, further entries are dropped and ``truncated`` is set.
_MAX_TOTAL_CHARS = 48_000


def _bound_args(args: Any) -> dict:
    """Return a size-bounded copy of a tool's args dict for persistence.

    Keeps the dict shape (the frontend renders ``tool_start.args`` as an
    object); if the serialized form is too large, collapse to a truncated
    preview marker instead of inlining a huge payload.
    """
    if not isinstance(args, dict):
        return {}
    try:
        dumped = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        return {}
    if len(dumped) <= _ARGS_CAP:
        return args
    return {"_preview": dumped[:_ARGS_CAP], "_truncated": True}


def _bound_config_hint(event: dict) -> dict:
    """Return the persisted ConfigHintPayload shape, size-bounded."""
    hint: dict[str, Any] = {
        "scope": str(event.get("scope", ""))[:_CONFIG_HINT_TEXT_CAP],
        "error_code": str(event.get("error_code", "unknown"))[:_CONFIG_HINT_TEXT_CAP],
        "title": str(event.get("title", ""))[:_CONFIG_HINT_TEXT_CAP],
    }
    message = event.get("message")
    if message is not None:
        hint["message"] = str(message)[:_CONFIG_HINT_TEXT_CAP]

    actions = event.get("actions")
    if isinstance(actions, list):
        bounded_actions: list[dict] = []
        for action in actions[:_CONFIG_HINT_ACTIONS_CAP]:
            if not isinstance(action, dict):
                continue
            bounded_action: dict[str, Any] = {}
            for key, value in action.items():
                if not isinstance(key, str):
                    continue
                if isinstance(value, str):
                    bounded_action[key] = value[:_CONFIG_HINT_TEXT_CAP]
                elif isinstance(value, (int, float, bool)) or value is None:
                    bounded_action[key] = value
            if bounded_action:
                bounded_actions.append(bounded_action)
        if bounded_actions:
            hint["actions"] = bounded_actions

    return hint


class ChainTimelineBuilder:
    """Accumulate an ordered, bounded reasoning-chain timeline from SSE events."""

    def __init__(self) -> None:
        self._groups: list[dict] = []
        self._current: dict | None = None
        self._pending_compressed: dict | None = None
        self._total_chars = 0
        self.truncated = False

    def _charge(self, n: int) -> bool:
        """Reserve ``n`` characters against the total budget; False if over."""
        if self._total_chars + n > _MAX_TOTAL_CHARS:
            self.truncated = True
            return False
        self._total_chars += n
        return True

    # ── group / entry helpers ──

    def _push_group(self, group: dict) -> None:
        self._groups.append(group)
        if len(self._groups) > _MAX_GROUPS:
            self._groups.pop(0)
            self.truncated = True
        self._current = group

    def _ensure_group(self) -> dict:
        # Events can arrive before the first ``iteration_start`` (e.g. a
        # restored-plan ``todo_created`` or a pre-loop ``chain_text``); attach
        # them to a synthetic iteration-0 group rather than dropping them.
        if self._current is None:
            group = {"iteration": 0, "entries": []}
            if self._pending_compressed is not None:
                group["entries"].append(self._pending_compressed)
                self._pending_compressed = None
            self._push_group(group)
        assert self._current is not None
        return self._current

    @staticmethod
    def _entry_chars(entry: dict) -> int:
        n = (
            len(entry.get("content", ""))
            + len(entry.get("result", ""))
            + len(entry.get("description", ""))
        )
        args = entry.get("args")
        if isinstance(args, dict) and args:
            try:
                n += len(json.dumps(args, ensure_ascii=False, default=str))
            except Exception:
                pass
        return n

    def _append_entry(self, entry: dict) -> None:
        group = self._ensure_group()
        entries = group["entries"]
        if len(entries) >= _MAX_ENTRIES_PER_GROUP:
            self.truncated = True
            return
        if not self._charge(self._entry_chars(entry)):
            return
        entries.append(entry)

    def _append_thinking(self, content: str) -> None:
        if not content:
            return
        group = self._ensure_group()
        entries = group["entries"]
        # Coalesce consecutive thinking deltas into one growing entry (mirrors
        # the frontend), capped to avoid unbounded growth.
        if entries and entries[-1].get("kind") == "thinking":
            cur = entries[-1].get("content", "")
            if len(cur) >= _THINKING_CAP:
                return
            addition = (cur + content)[:_THINKING_CAP][len(cur) :]
            if addition and self._charge(len(addition)):
                entries[-1]["content"] = cur + addition
            return
        self._append_entry({"kind": "thinking", "content": content[:_THINKING_CAP]})

    def _mark_tool_done(self, tool_id: str, status: str) -> None:
        if not tool_id or self._current is None:
            return
        for entry in reversed(self._current["entries"]):
            if entry.get("kind") == "tool_start" and entry.get("toolId") == tool_id:
                entry["status"] = status
                break

    # ── public API ──

    def observe(self, event: dict) -> None:
        """Fold one raw agent event into the timeline. Never raises."""
        if not isinstance(event, dict):
            return
        try:
            etype = event.get("type", "")
            if etype == "iteration_start":
                group: dict = {
                    "iteration": event.get("iteration", len(self._groups) + 1),
                    "entries": [],
                }
                if self._pending_compressed is not None:
                    group["entries"].append(self._pending_compressed)
                    self._pending_compressed = None
                self._push_group(group)
            elif etype == "context_compressed":
                self._pending_compressed = {
                    "kind": "compressed",
                    "beforeTokens": int(event.get("before_tokens", 0) or 0),
                    "afterTokens": int(event.get("after_tokens", 0) or 0),
                }
            elif etype == "thinking_delta":
                self._append_thinking(str(event.get("content", "")))
            elif etype == "thinking_end":
                if self._current is not None:
                    dur = event.get("duration_ms")
                    if isinstance(dur, (int, float)) and dur:
                        self._current["durationMs"] = int(dur)
            elif etype == "chain_text":
                content = str(event.get("content", ""))
                if content:
                    self._append_entry({"kind": "text", "content": content[:_TEXT_CAP]})
            elif etype == "tool_call_start":
                self._append_entry(
                    {
                        "kind": "tool_start",
                        "toolId": str(event.get("id", "")),
                        "tool": event.get("tool") or event.get("name") or "",
                        "args": _bound_args(event.get("args")),
                        "description": str(event.get("friendly_message", "")),
                        "status": "running",
                    }
                )
            elif etype == "tool_call_end":
                status = "error" if event.get("is_error") else "done"
                tool_id = str(event.get("id", ""))
                self._mark_tool_done(tool_id, status)
                self._append_entry(
                    {
                        "kind": "tool_end",
                        "toolId": tool_id,
                        "tool": event.get("tool") or "",
                        "result": str(event.get("result", ""))[:_RESULT_CAP],
                        "status": status,
                    }
                )
            elif etype == "config_hint":
                self._append_entry(
                    {
                        "kind": "config_hint",
                        "toolId": str(event.get("tool_use_id") or event.get("id") or ""),
                        "hint": _bound_config_hint(event),
                    }
                )
        except Exception:
            # A malformed event must never break streaming or persistence.
            pass

    def build(self) -> list[dict] | None:
        """Return the ordered timeline (groups with at least one entry), or None."""
        out = [g for g in self._groups if g.get("entries")]
        return out or None
