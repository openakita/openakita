"""
Group chat response strategy

Five modes:
- always:       respond to all group messages
- mention_only: respond only when @mentioned (default)
- smart:        forward messages to the Agent and let the AI decide whether to reply
- allowlist:    respond only in whitelisted groups (requires GroupPolicyConfig)
- disabled:     disable group chat responses entirely
"""

import logging
import time
from collections import defaultdict
from enum import StrEnum

logger = logging.getLogger(__name__)


class GroupResponseMode(StrEnum):
    ALWAYS = "always"
    MENTION_ONLY = "mention_only"
    SMART = "smart"
    ALLOWLIST = "allowlist"
    DISABLED = "disabled"


class SmartModeThrottle:
    """Smart mode rate limiter / batcher

    Accumulates non-@ group messages and, once batch_size is reached (or
    batch_timeout expires), sends them to the LLM in a single request to
    drastically reduce LLM call count.
    """

    def __init__(
        self,
        max_per_minute: int = 5,
        batch_size: int = 3,
        cooldown_after_reply: int = 60,
        batch_timeout: float = 10.0,
    ):
        self.max_per_minute = max_per_minute
        self.batch_size = batch_size
        self.cooldown_after_reply = cooldown_after_reply
        self.batch_timeout = batch_timeout

        self._counter: dict[str, list[float]] = defaultdict(list)
        self._last_reply_time: dict[str, float] = {}
        self._buffer: dict[str, list[dict]] = defaultdict(list)

    def should_process(self, chat_id: str) -> bool:
        """Check whether a smart-mode message can be processed for this group (rate limit)."""
        now = time.monotonic()

        # Periodically clean up inactive chat entries (every 100 calls)
        self._call_count = getattr(self, "_call_count", 0) + 1
        if self._call_count % 100 == 0:
            self._cleanup_stale_chats(now)

        # Cooldown check
        last_reply = self._last_reply_time.get(chat_id, 0)
        if now - last_reply < self.cooldown_after_reply:
            return False

        # Rate limit check
        timestamps = self._counter[chat_id]
        cutoff = now - 60
        self._counter[chat_id] = [t for t in timestamps if t > cutoff]
        if len(self._counter[chat_id]) >= self.max_per_minute:
            return False

        return True

    def _cleanup_stale_chats(self, now: float) -> None:
        """Remove chat entries inactive for over 1 hour to prevent memory leaks."""
        stale_threshold = 3600  # 1 hour

        all_cids = set(self._counter) | set(self._last_reply_time) | set(self._buffer)
        for cid in all_cids:
            ts_list = self._counter.get(cid, [])
            last_activity = max(ts_list) if ts_list else 0
            last_activity = max(last_activity, self._last_reply_time.get(cid, 0))
            buf = self._buffer.get(cid, [])
            if buf:
                last_activity = max(last_activity, buf[-1].get("time", 0))
            if now - last_activity > stale_threshold:
                self._counter.pop(cid, None)
                self._last_reply_time.pop(cid, None)
                self._buffer.pop(cid, None)

    def record_process(self, chat_id: str) -> None:
        """Record that a message was processed."""
        self._counter[chat_id].append(time.monotonic())

    def record_reply(self, chat_id: str) -> None:
        """Record that a reply was sent to this group and start cooldown."""
        self._last_reply_time[chat_id] = time.monotonic()

    _MAX_BUFFER_SIZE = 50

    def buffer_message(self, chat_id: str, text: str, user_id: str) -> int:
        """Buffer a non-@ message and return the current buffer size."""
        buf = self._buffer[chat_id]
        if len(buf) >= self._MAX_BUFFER_SIZE:
            buf.pop(0)
        buf.append(
            {
                "text": text,
                "user_id": user_id,
                "time": time.monotonic(),
            }
        )
        return len(buf)

    def drain_buffer(self, chat_id: str) -> list[dict]:
        """Drain and return all buffered messages for this group."""
        msgs = self._buffer.pop(chat_id, [])
        return msgs

    def is_batch_ready(self, chat_id: str) -> bool:
        """Return True if the buffer is full or has timed out."""
        buf = self._buffer.get(chat_id, [])
        if not buf:
            return False
        if len(buf) >= self.batch_size:
            return True
        oldest = buf[0]["time"]
        if time.monotonic() - oldest > self.batch_timeout:
            return True
        return False
