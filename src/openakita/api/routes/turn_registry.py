"""Per-turn idempotency registry (plan: v1.28, S1.6).

HTTP 层一个轻量级 in-flight 表，专门处理"同 ``turn_id`` 在短时间内被重发"
的场景：客户端网络抖动、SSE 重连、UI bug 导致同一个 turn 被发两次时，
返回 409 + ``Retry-After`` 而不是真的去开第二条流。

设计要点：

* **不持久化**：仅进程内 dict。OpenAkita v1.28 单进程为前提；多 worker
  部署（v1.29+）需换分布式 store（Redis SET NX EX）。
* **TTL 自动过期**：默认 60s；远大于任何正常 turn 的 SSE 持续时间，但
  小到不会积累内存。
* **状态机**：``in_flight`` → ``succeeded`` / ``failed``。``in_flight``
  时短路 409；终态保留 TTL 内允许客户端 idempotent 重试（同一答复）。
* **与 ConversationLifecycleManager 解耦**：lifecycle 管 conversation 级
  并发，turn registry 管 turn 级 idempotency。两者协同：先查 turn registry
  短路，再走 lifecycle.start 抢 conversation 锁。

"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

TurnStatus = Literal["in_flight", "succeeded", "failed"]


@dataclass
class TurnRecord:
    status: TurnStatus = "in_flight"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # 可选的"结果摘要"——目前不缓存完整响应（SSE 不可重播），仅用于
    # 调试日志和 admin diagnostics。
    summary: str = ""


class TurnRegistry:
    """In-process registry of in-flight + recently-finished turns."""

    DEFAULT_TTL_SECONDS = 60.0

    def __init__(self, *, ttl_seconds: float | None = None) -> None:
        self._records: dict[str, TurnRecord] = {}
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS

    async def begin(self, turn_id: str) -> tuple[TurnStatus | Literal["new"], TurnRecord | None]:
        """Try to claim ``turn_id``.

        Returns one of:

        - ``("new", None)`` — caller may proceed; record now marked
          ``in_flight``.
        - ``("in_flight", record)`` — duplicate request; caller should
          return 409 with ``Retry-After``.
        - ``("succeeded", record)`` / ``("failed", record)`` — terminal
          record still within TTL; caller may decide whether to replay
          or treat as new (current default: treat as duplicate).
        """
        if not turn_id:
            return "new", None  # callers with no turn_id never get short-circuited
        async with self._lock:
            self._expire_stale_locked()
            rec = self._records.get(turn_id)
            if rec is None:
                fresh = TurnRecord(status="in_flight", started_at=time.time())
                self._records[turn_id] = fresh
                return "new", None
            return rec.status, rec

    async def mark_succeeded(self, turn_id: str, *, summary: str = "") -> None:
        if not turn_id:
            return
        async with self._lock:
            rec = self._records.get(turn_id)
            if rec is None:
                # Race: TTL expired between begin and mark; just record terminal.
                self._records[turn_id] = TurnRecord(
                    status="succeeded",
                    started_at=time.time(),
                    finished_at=time.time(),
                    summary=summary,
                )
                return
            rec.status = "succeeded"
            rec.finished_at = time.time()
            rec.summary = summary

    async def mark_failed(self, turn_id: str, *, summary: str = "") -> None:
        if not turn_id:
            return
        async with self._lock:
            rec = self._records.get(turn_id)
            if rec is None:
                self._records[turn_id] = TurnRecord(
                    status="failed",
                    started_at=time.time(),
                    finished_at=time.time(),
                    summary=summary,
                )
                return
            rec.status = "failed"
            rec.finished_at = time.time()
            rec.summary = summary

    async def snapshot(self) -> list[dict[str, object]]:
        """Diagnostics endpoint helper."""
        async with self._lock:
            self._expire_stale_locked()
            return [
                {
                    "turn_id": tid,
                    "status": rec.status,
                    "started_at": rec.started_at,
                    "finished_at": rec.finished_at,
                    "summary": rec.summary,
                }
                for tid, rec in self._records.items()
            ]

    def reset_for_tests(self) -> None:
        """Test-only synchronous clear."""
        self._records.clear()

    # ── Internals ───────────────────────────────────────────────────

    def _expire_stale_locked(self) -> None:
        now = time.time()
        ttl = self._ttl_seconds
        stale_keys = []
        for tid, rec in self._records.items():
            ref = rec.finished_at if rec.finished_at is not None else rec.started_at
            if now - ref > ttl:
                stale_keys.append(tid)
        for tid in stale_keys:
            logger.debug("[TurnRegistry] expiring stale turn %s", tid)
            del self._records[tid]


# ── Module-level singleton ──────────────────────────────────────────


_instance: TurnRegistry | None = None


def get_turn_registry() -> TurnRegistry:
    global _instance
    if _instance is None:
        _instance = TurnRegistry()
    return _instance


__all__ = [
    "TurnRecord",
    "TurnRegistry",
    "TurnStatus",
    "get_turn_registry",
]
