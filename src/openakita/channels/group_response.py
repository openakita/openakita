"""
群聊响应策略

五种模式:
- always:       所有群消息都响应
- mention_only: 仅被@时才响应（默认）
- smart:        消息送给 Agent，由 AI 判断是否需要回复
- allowlist:    仅白名单群聊才响应（需配合 GroupPolicyConfig 使用）
- disabled:     完全禁用群聊响应
"""

import logging
import time
from collections import defaultdict
from enum import Enum

logger = logging.getLogger(__name__)


class GroupResponseMode(str, Enum):
    ALWAYS = "always"
    MENTION_ONLY = "mention_only"
    SMART = "smart"
    ALLOWLIST = "allowlist"
    DISABLED = "disabled"


class SmartModeThrottle:
    """smart 模式限流器

    批量积攒非@群消息，攒够 batch_size 条（或超时）后一次性送 LLM 判断，
    大幅减少 LLM 调用次数。
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
        """检查该群是否可以处理一条 smart 消息（频率限制）"""
        now = time.monotonic()

        # 定期清理不活跃的 chat 条目（每 100 次调用清理一次）
        self._call_count = getattr(self, "_call_count", 0) + 1
        if self._call_count % 100 == 0:
            self._cleanup_stale_chats(now)

        # 冷却期检查
        last_reply = self._last_reply_time.get(chat_id, 0)
        if now - last_reply < self.cooldown_after_reply:
            return False

        # 频率限制
        timestamps = self._counter[chat_id]
        cutoff = now - 60
        self._counter[chat_id] = [t for t in timestamps if t > cutoff]
        if len(self._counter[chat_id]) >= self.max_per_minute:
            return False

        return True

    def _cleanup_stale_chats(self, now: float) -> None:
        """清理超过 1 小时无活动的 chat 条目，防止内存泄漏。"""
        stale_threshold = 3600  # 1 小时

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
        """记录处理了一条消息"""
        self._counter[chat_id].append(time.monotonic())

    def record_reply(self, chat_id: str) -> None:
        """记录给该群发了回复，开始冷却"""
        self._last_reply_time[chat_id] = time.monotonic()

    _MAX_BUFFER_SIZE = 50

    def buffer_message(self, chat_id: str, text: str, user_id: str) -> int:
        """缓冲一条非@消息，返回当前缓冲区大小"""
        buf = self._buffer[chat_id]
        if len(buf) >= self._MAX_BUFFER_SIZE:
            buf.pop(0)
        buf.append({
            "text": text,
            "user_id": user_id,
            "time": time.monotonic(),
        })
        return len(buf)

    def drain_buffer(self, chat_id: str) -> list[dict]:
        """取出并清空该群的缓冲消息"""
        msgs = self._buffer.pop(chat_id, [])
        return msgs

    def is_batch_ready(self, chat_id: str) -> bool:
        """缓冲区是否已满或超时"""
        buf = self._buffer.get(chat_id, [])
        if not buf:
            return False
        if len(buf) >= self.batch_size:
            return True
        oldest = buf[0]["time"]
        if time.monotonic() - oldest > self.batch_timeout:
            return True
        return False
