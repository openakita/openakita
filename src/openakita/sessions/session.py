"""
Session object definitions.

A Session represents an independent conversation context, including:
- source channel info
- conversation history
- session variables
- configuration overrides
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Session state."""

    ACTIVE = "active"  # active
    IDLE = "idle"  # idle (no activity but not expired)
    EXPIRED = "expired"  # expired
    CLOSED = "closed"  # closed


@dataclass
class SessionConfig:
    """
    Session configuration.

    Overrides the global config to provide per-session customization.
    """

    max_history: int = 100  # max number of history messages
    timeout_minutes: int = 30  # timeout (minutes)
    language: str = "zh"  # language
    model: str | None = None  # override the default model
    custom_prompt: str | None = None  # custom system prompt
    auto_summarize: bool = True  # whether to auto-summarize long conversations

    def merge_with_defaults(self, defaults: "SessionConfig") -> "SessionConfig":
        """Merge configs; self takes precedence."""
        return SessionConfig(
            max_history=self.max_history or defaults.max_history,
            timeout_minutes=self.timeout_minutes or defaults.timeout_minutes,
            language=self.language or defaults.language,
            model=self.model or defaults.model,
            custom_prompt=self.custom_prompt or defaults.custom_prompt,
            auto_summarize=self.auto_summarize
            if self.auto_summarize is not None
            else defaults.auto_summarize,
        )


@dataclass
class SessionContext:
    """
    Session context.

    Stores session-level state and data.
    """

    messages: list[dict] = field(default_factory=list)  # conversation history
    variables: dict[str, Any] = field(default_factory=dict)  # session variables
    current_task: str | None = None  # current task ID
    memory_scope: str | None = None  # memory scope ID
    summary: str | None = None  # conversation summary (for long-conversation compaction)
    topic_boundaries: list[int] = field(default_factory=list)  # message indices of topic boundaries
    current_topic_start: int = 0  # start message index of the current topic
    agent_profile_id: str = "default"
    agent_switch_history: list[dict] = field(default_factory=list)
    handoff_events: list[dict] = field(default_factory=list)  # agent_handoff events for SSE
    # Active agents in this session (multi-agent collaboration)
    active_agents: list[str] = field(default_factory=list)
    # Delegation chain for the current request
    delegation_chain: list[dict] = field(default_factory=list)
    # Sub-agent work records — persisted traces of delegated tasks
    sub_agent_records: list[dict] = field(default_factory=list)
    _msg_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    _DEDUP_TIME_WINDOW_SECONDS = 30

    def add_message(self, role: str, content: str, **metadata) -> bool:
        """Add a message (with deduplication: consecutive duplicates + duplicates within a time window).

        Returns:
            True if the message was actually added, False if deduped.
        """
        with self._msg_lock:
            if self.messages:
                last = self.messages[-1]
                if last.get("role") == role and last.get("content") == content:
                    return False

            now = datetime.now()
            for msg in reversed(self.messages[-8:]):
                if msg.get("role") != role:
                    continue
                msg_content = msg.get("content", "") or ""
                if msg_content != content:
                    continue
                ts_str = msg.get("timestamp", "")
                if ts_str:
                    try:
                        msg_time = datetime.fromisoformat(ts_str)
                        if (now - msg_time).total_seconds() < self._DEDUP_TIME_WINDOW_SECONDS:
                            return False
                    except (ValueError, TypeError):
                        pass

            self.messages.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": now.isoformat(),
                    **metadata,
                }
            )
            return True

    def mark_topic_boundary(self) -> None:
        """Mark a topic boundary at the current message position.

        Afterwards, get_current_topic_messages() can be used to retrieve only the current topic's messages.
        """
        boundary_idx = len(self.messages)
        self.topic_boundaries.append(boundary_idx)
        self.current_topic_start = boundary_idx

    def get_current_topic_messages(self) -> list[dict]:
        """Get the messages in the current topic (starting from the last boundary)."""
        if self.current_topic_start >= len(self.messages):
            return []
        return self.messages[self.current_topic_start :]

    def get_pre_topic_messages(self) -> list[dict]:
        """Get messages before the current topic boundary."""
        return self.messages[: self.current_topic_start]

    def get_messages(self, limit: int | None = None) -> list[dict]:
        """Get message history."""
        if limit is not None:
            try:
                return self.messages[-int(limit) :]
            except (ValueError, TypeError):
                pass
        return self.messages

    def set_variable(self, key: str, value: Any) -> None:
        """Set a session variable."""
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        """Get a session variable."""
        return self.variables.get(key, default)

    def clear_messages(self) -> None:
        """Clear message history."""
        with self._msg_lock:
            self.messages = []
            self.topic_boundaries = []
            self.current_topic_start = 0
            self.variables["_context_reset_at"] = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """Serialize."""
        return {
            "messages": self.messages,
            "variables": self.variables,
            "current_task": self.current_task,
            "memory_scope": self.memory_scope,
            "summary": self.summary,
            "topic_boundaries": self.topic_boundaries,
            "current_topic_start": self.current_topic_start,
            "agent_profile_id": self.agent_profile_id,
            "agent_switch_history": self.agent_switch_history,
            "handoff_events": self.handoff_events,
            "active_agents": self.active_agents,
            "delegation_chain": self.delegation_chain,
            "sub_agent_records": self.sub_agent_records,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionContext":
        """Deserialize."""
        return cls(
            messages=data.get("messages", []),
            variables=data.get("variables", {}),
            current_task=data.get("current_task"),
            memory_scope=data.get("memory_scope"),
            summary=data.get("summary"),
            topic_boundaries=data.get("topic_boundaries", []),
            current_topic_start=data.get("current_topic_start", 0),
            agent_profile_id=data.get("agent_profile_id", "default"),
            agent_switch_history=data.get("agent_switch_history", []),
            handoff_events=data.get("handoff_events", []),
            active_agents=data.get("active_agents", []),
            delegation_chain=data.get("delegation_chain", []),
            sub_agent_records=data.get("sub_agent_records", []),
        )


@dataclass
class Session:
    """
    Session object.

    Represents an independent conversation context, associated with:
    - source channel (telegram/feishu/...)
    - chat ID (private/group/topic)
    - user ID
    """

    id: str
    channel: str  # source channel
    chat_id: str  # chat ID (group/private)
    user_id: str  # user ID
    thread_id: str | None = None  # topic/thread ID (e.g. Feishu threads)
    chat_type: str = "private"  # "group" | "private"
    display_name: str = ""  # user nickname (for UI display)
    chat_name: str = ""  # chat/group name (group name, channel name, etc.)

    # State
    state: SessionState = SessionState.ACTIVE
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    # Context
    context: SessionContext = field(default_factory=SessionContext)

    # Config (can override globals)
    config: SessionConfig = field(default_factory=SessionConfig)

    # Metadata
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        channel: str,
        chat_id: str,
        user_id: str,
        thread_id: str | None = None,
        config: SessionConfig | None = None,
        chat_type: str = "private",
        display_name: str = "",
        chat_name: str = "",
    ) -> "Session":
        """Create a new session."""
        session_id = (
            f"{channel}_{chat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        return cls(
            id=session_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            thread_id=thread_id,
            chat_type=chat_type,
            display_name=display_name,
            chat_name=chat_name,
            config=config or SessionConfig(),
        )

    def touch(self) -> None:
        """Update the last-active time."""
        self.last_active = datetime.now()
        if self.state == SessionState.IDLE:
            self.state = SessionState.ACTIVE

    def is_expired(self, timeout_minutes: int | None = None) -> bool:
        """Mark as expired only after an extended period of inactivity (30-day cold archive)."""
        timeout = timeout_minutes or (60 * 24 * 30)  # 30 days
        elapsed = (datetime.now() - self.last_active).total_seconds() / 60
        return elapsed > timeout

    def mark_expired(self) -> None:
        """Mark as expired."""
        self.state = SessionState.EXPIRED

    def mark_idle(self) -> None:
        """Mark as idle."""
        self.state = SessionState.IDLE

    def close(self) -> None:
        """Close the session."""
        self.state = SessionState.CLOSED

    # ==================== Metadata management ====================

    def set_metadata(self, key: str, value: Any) -> None:
        """Set metadata."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata."""
        return self.metadata.get(key, default)

    # ==================== Task management ====================

    def set_task(self, task_id: str, description: str) -> None:
        """
        Set the current task.

        Args:
            task_id: task ID
            description: task description
        """
        self.context.current_task = task_id
        self.context.set_variable("task_description", description)
        self.context.set_variable("task_status", "in_progress")
        self.context.set_variable("task_started_at", datetime.now().isoformat())
        self.touch()
        logger.debug(f"Session {self.id}: set task {task_id}")

    def complete_task(self, success: bool = True, result: str = "") -> None:
        """
        Complete the current task.

        Args:
            success: whether it succeeded
            result: result description
        """
        self.context.set_variable("task_status", "completed" if success else "failed")
        self.context.set_variable("task_result", result)
        self.context.set_variable("task_completed_at", datetime.now().isoformat())

        task_id = self.context.current_task
        self.context.current_task = None

        self.touch()
        logger.debug(
            f"Session {self.id}: completed task {task_id} ({'success' if success else 'failed'})"
        )

    def get_task_status(self) -> dict:
        """
        Get the current task status.

        Returns:
            Task status dict
        """
        return {
            "task_id": self.context.current_task,
            "description": self.context.get_variable("task_description"),
            "status": self.context.get_variable("task_status"),
            "started_at": self.context.get_variable("task_started_at"),
            "completed_at": self.context.get_variable("task_completed_at"),
            "result": self.context.get_variable("task_result"),
        }

    def has_active_task(self) -> bool:
        """Whether there is an active task."""
        return self.context.current_task is not None

    @property
    def session_key(self) -> str:
        """Unique session identifier."""
        key = f"{self.channel}:{self.chat_id}:{self.user_id}"
        if self.thread_id:
            key += f":{self.thread_id}"
        return key

    def add_message(self, role: str, content: str, **metadata) -> bool:
        """Add a message and update last-active time. Returns True if added, False if skipped by dedup."""
        added = self.context.add_message(role, content, **metadata)
        self.touch()
        if added and len(self.context.messages) > self.config.max_history:
            self._truncate_history()
        return added

    _RULE_SIGNAL_WORDS = (
        "don't",
        "must",
        "forbid",
        "each time",
        "rule",
        "never",
        "essential",
        "always",
        "always",
        "never",
        "must",
        "rule",
    )

    def _truncate_history(self) -> None:
        """Truncate history messages, keeping 75%, and insert a brief summary of the dropped portion at the head.

        Prioritizes preserving user-defined behavioral rule messages.
        """
        with self.context._msg_lock:
            keep_count = int(self.config.max_history * 3 / 4)
            messages = self.context.messages
            dropped = messages[:-keep_count]
            kept = messages[-keep_count:]

            self._mark_dropped_for_extraction(dropped)

            max_summary_len = 300
            max_rules_len = 500
            keywords: list[str] = []
            rule_snippets: list[str] = []
            rules_len = 0

            for msg in dropped:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not isinstance(content, str) or not content:
                    continue

                from openakita.core.tool_executor import smart_truncate

                is_rule = any(w in content for w in self._RULE_SIGNAL_WORDS)
                if is_rule and rules_len < max_rules_len:
                    snippet, _ = smart_truncate(
                        content.replace("\n", " ").strip(),
                        300,
                        save_full=False,
                        label="rule_hist",
                    )
                    rule_snippets.append(snippet)
                    rules_len += len(snippet)
                else:
                    preview, _ = smart_truncate(
                        content.replace("\n", " ").strip(),
                        150,
                        save_full=False,
                        label="msg_hist",
                    )
                    keywords.append(preview)

            header_parts: list[str] = []
            if rule_snippets:
                header_parts.append("[User rules (must follow)]\n" + "\n".join(rule_snippets))
            if keywords:
                header = "[Historical context, not the current task]\n"
                body = ""
                for kw in keywords:
                    candidate = (body + "\n" + kw).strip() if body else kw
                    if len(header) + len(candidate) > max_summary_len:
                        break
                    body = candidate
                if body:
                    header_parts.append(header + body)

            if header_parts:
                kept.insert(0, {"role": "system", "content": "\n\n".join(header_parts)})

            self.context.messages = kept
            logger.debug(
                f"Session {self.id}: truncated history — "
                f"dropped {len(dropped)}, kept {len(kept)} messages, "
                f"preserved {len(rule_snippets)} rule snippets"
            )

    def _mark_dropped_for_extraction(self, dropped: list[dict]) -> None:
        """v2: flag truncated messages as needing extraction.

        Notifies the memory system via metadata["_memory_manager"] or a callback mechanism.
        If the memory system is unavailable, silently skip (does not affect the truncation flow).
        """
        memory_manager = self.metadata.get("_memory_manager")
        if memory_manager is None:
            return
        store = getattr(memory_manager, "store", None)
        if store is None:
            return
        try:
            for i, msg in enumerate(dropped):
                content = msg.get("content", "")
                if not content or not isinstance(content, str) or len(content) < 10:
                    continue
                store.enqueue_extraction(
                    session_id=self.id,
                    turn_index=i,
                    content=content,
                    tool_calls=msg.get("tool_calls"),
                    tool_results=msg.get("tool_results"),
                )
        except Exception as e:
            logger.warning(f"Failed to enqueue dropped messages for extraction: {e}")

    def to_dict(self) -> dict:
        """Serialize."""
        # Filter out private metadata starting with _ (e.g. _gateway, _session_key runtime data)
        serializable_metadata = {
            k: v
            for k, v in self.metadata.items()
            if not k.startswith("_") and self._is_json_serializable(v)
        }

        return {
            "id": self.id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "chat_type": self.chat_type,
            "display_name": self.display_name,
            "chat_name": self.chat_name,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "context": self.context.to_dict(),
            "config": {
                "max_history": self.config.max_history,
                "timeout_minutes": self.config.timeout_minutes,
                "language": self.config.language,
                "model": self.config.model,
                "custom_prompt": self.config.custom_prompt,
                "auto_summarize": self.config.auto_summarize,
            },
            "metadata": serializable_metadata,
        }

    def _is_json_serializable(self, value: Any) -> bool:
        """Check whether the value is JSON-serializable."""
        import json

        try:
            json.dumps(value)
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Deserialize."""
        config_data = data.get("config", {})
        return cls(
            id=data["id"],
            channel=data["channel"],
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            thread_id=data.get("thread_id"),
            chat_type=data.get("chat_type", "private"),
            display_name=data.get("display_name", ""),
            chat_name=data.get("chat_name", ""),
            state=SessionState(data.get("state", "active")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active=datetime.fromisoformat(data["last_active"]),
            context=SessionContext.from_dict(data.get("context") or {}),
            config=SessionConfig(
                max_history=config_data.get("max_history", 100),
                timeout_minutes=config_data.get("timeout_minutes", 30),
                language=config_data.get("language", "zh"),
                model=config_data.get("model"),
                custom_prompt=config_data.get("custom_prompt"),
                auto_summarize=config_data.get("auto_summarize", True),
            ),
            metadata=data.get("metadata", {}),
        )
