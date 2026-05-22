"""Conversation concurrency telemetry counters (plan: v1.28, S1.9).

轻量级内存计数器，跟踪 v1.27.14 引入的 5 条新代码路径在生产中的命中率：

- ``preempt`` — INTERRUPT/STEER 抢占触发次数
- ``queue``   — QUEUE 等待触发次数
- ``settled_timeout`` — 等 settled 超时的次数
- ``abandon`` — 因超时把老 task 标 abandoned 的次数
- ``takeover`` — lifecycle.start 返回 took_over 的次数（HTTP 层埋点）

不引入 prometheus_client 等外部依赖；admin 通过
``GET /api/diagnostics/conversation_metrics`` 拿快照。grafana 大盘可
按 channel/policy 维度聚合（label 用 dict key 表示）。

并发安全性：dict.get / += 在 CPython 单 worker 进程内对单 key 的
``int += int`` 是原子的（GIL 保护）。多 worker 部署需要外存
（v1.29 范围），本模块不处理。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _CounterTable:
    # key: (counter_name, frozenset of (label_key, label_value) pairs)
    counts: dict[tuple[str, frozenset[tuple[str, str]]], int] = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, name: str, *, labels: dict[str, str] | None = None, by: int = 1) -> None:
        label_key = frozenset((k, str(v)) for k, v in (labels or {}).items())
        with self._lock:
            self.counts[(name, label_key)] += by

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            items = list(self.counts.items())
        out: list[dict[str, object]] = []
        for (name, label_key), value in items:
            out.append({
                "name": name,
                "labels": dict(label_key),
                "value": value,
            })
        return out

    def reset(self) -> None:
        """Test-only: clear all counters."""
        with self._lock:
            self.counts.clear()


_table = _CounterTable()


# ── Public counter API ────────────────────────────────────────────────


def inc_preempt(policy: str, channel: str | None = None) -> None:
    """INTERRUPT/STEER 抢占触发（旧 task 被 cancel）。"""
    _table.inc("preempt", labels={"policy": policy, "channel": channel or "unknown"})


def inc_queue(channel: str | None = None) -> None:
    """QUEUE 等待触发（新 task 等老 task settle）。"""
    _table.inc("queue", labels={"channel": channel or "unknown"})


def inc_settled_timeout(policy: str, channel: str | None = None) -> None:
    """wait_until_settled 超时（老 task 没在 preempt_settle_timeout_ms 内 settle）。

    与 :func:`inc_abandon` 通常成对出现——超时后我们就把老 task 标 abandoned。
    分两个 counter 是因为未来可能引入"超时但不 abandon"的 fallback。
    """
    _table.inc(
        "settled_timeout",
        labels={"policy": policy, "channel": channel or "unknown"},
    )


def inc_abandon(policy: str, channel: str | None = None) -> None:
    """老 task 被标 abandoned（preempt 超时后的 fallback）。"""
    _table.inc("abandon", labels={"policy": policy, "channel": channel or "unknown"})


def inc_takeover(channel: str | None = None) -> None:
    """HTTP 层 lifecycle.start 返回 took_over（INTERRUPT/STEER 成功获取锁，旧 BusyInfo 被替换）。

    在 ``api/routes/chat.py`` 或其他 HTTP 入口埋点；不在 agent 层。
    """
    _table.inc("takeover", labels={"channel": channel or "unknown"})


def inc_illegal_reasoning_entry(source: str = "unknown") -> None:
    """S5 引入的 ``IllegalReasoningEntry`` 命中计数。

    Pre-S5 暂未使用；保留以便 S5 在删除 9 处 force-write 时直接接入。
    """
    _table.inc("illegal_reasoning_entry", labels={"source": source})


def inc_queue_extended(channel: str | None = None, reason: str = "block_in_flight") -> None:
    """v1.28.2 FOLLOW-UP-S4-A: QUEUE wait 第一次 timeout 但老 task 仍有 block
    工具在跑，延长一轮等待。

    触发条件：``_preempt_or_queue_prev_task`` 在 QUEUE 分支收到 TimeoutError 时，
    重新检查 ``_prev_task.get_in_flight_tools()``，若仍有 ``"block"`` 类工具
    （write_file / run_shell / browser_click / …）则再延长
    ``preempt_block_tool_extension_ms`` 毫秒等待，而不是立即 cancel。

    监控用途：

    * 命中频次低 → 大多数 block 工具能在第一次 timeout 内完成，机制运行良好。
    * 命中频次高 → 用户的 block 工具实际耗时超过 ``preempt_settle_timeout_ms``；
      可以考虑直接提高这个 timeout，或排查工具实现是否有阻塞 bug。
    * ``reason`` 标签：``block_in_flight``（已标 block 工具）vs ``unknown_tool``
      （未标注的工具走默认 block）—— 与 ``inc_interrupt_downgrade`` 共享语义。
    """
    _table.inc(
        "queue_extended",
        labels={"channel": channel or "unknown", "reason": reason},
    )


def inc_interrupt_downgrade(channel: str | None = None, reason: str = "block_in_flight") -> None:
    """v1.28 S4: INTERRUPT 策略到达 agent 层但被自动降级为 QUEUE。

    触发条件：``_preempt_or_queue_prev_task`` 检查 in_flight_tools 时发现
    至少一个 "block" 类工具（write_file / run_shell / browser_click / …）
    正在执行——立即 cancel 会留下半成品副作用，所以降级走 QUEUE 流程。

    监控用途：

    * ``downgrade_rate = inc_interrupt_downgrade / inc_preempt``
      —— 评估 INTERRUPT 策略实际可用度；持续 > 30% 说明用户多在写工具
      执行期间按抢占，需要前端给更清晰的 "loading" 反馈。
    * ``reason`` 标签后续可扩展为 ``unknown_tool``（未标注工具走默认
      block 降级）和 ``block_in_flight``（标注为 block 的工具在跑），
      区分"我们漏标了"vs"用户姿势正常但确实在做重操作"。
    """
    _table.inc(
        "interrupt_downgrade",
        labels={"channel": channel or "unknown", "reason": reason},
    )


def snapshot() -> list[dict[str, object]]:
    """返回所有 counter 当前值的快照。"""
    return _table.snapshot()


def reset_for_tests() -> None:
    """仅供测试使用：清空所有 counter。"""
    _table.reset()


__all__ = [
    "inc_preempt",
    "inc_queue",
    "inc_settled_timeout",
    "inc_abandon",
    "inc_takeover",
    "inc_illegal_reasoning_entry",
    "inc_interrupt_downgrade",
    "inc_queue_extended",
    "snapshot",
    "reset_for_tests",
]
