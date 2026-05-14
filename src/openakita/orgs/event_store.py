"""
OrgEventStore — 事件溯源 + 操作记录 + 组织报告生成

所有状态变更以追加事件流记录，便于按时间回看与状态重建。事件按天分文件
存储在 events/{YYYYMMDD}.jsonl。

⚠️ 这是 **非密码学** 操作记录：

OrgEventStore 与 ``core/policy_v2/audit_chain.ChainedJsonlWriter`` 不同——
后者带哈希链 + 校验，能检测篡改 / 漏行 / 重排；前者只是顺序 append 的
JSONL，本质上是"运营事件流"。任何对篡改检测有要求的合规场景（用户许可
决策、shell 高危执行、Policy 变更）必须走 ``ChainedJsonlWriter``，
不要落到这里。详见 ``docs/policy_v2_research.md`` C17 章节"OrgEventStore
非密码学审计说明"。

C17 Phase D 在此处加了：

- ``threading.Lock`` 保护同进程多线程 emit（之前在 query 期间被 emit
  打断会读到撕裂行）。
- ``filelock.FileLock`` 保护跨进程多 worker emit（多个 ``openakita
  serve`` 同写一个 org_dir 时不丢行）。
- ``logger.warning`` 在 write 失败时不再静默，方便排障。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import _new_id

logger = logging.getLogger(__name__)


# Optional cross-process lock; ``filelock`` is in pyproject.toml since C16,
# but a missing import falls back to "single-process safe only" rather than
# crashing the entire org subsystem.
try:
    from filelock import FileLock
    from filelock import Timeout as _FileLockTimeout
    _HAS_FILELOCK = True
except Exception:  # pragma: no cover - extremely unlikely
    _HAS_FILELOCK = False
    _FileLockTimeout = Exception  # type: ignore[assignment, misc]


class OrgEventStore:
    """Append-only event store for an organization.

    Thread-safe within a single process (``_lock``) and best-effort
    cross-process safe (``_filelock``) so that concurrent ``openakita
    serve`` workers don't trample each other.
    """

    # Acquiring a contested filelock for an append should take milliseconds;
    # if it takes longer than this, something is wedged and we'd rather log
    # a warning + drop the event than block the caller indefinitely.
    _FILELOCK_TIMEOUT_SECONDS = 2.0

    def __init__(self, org_dir: Path, org_id: str) -> None:
        self._org_dir = org_dir
        self._org_id = org_id
        self._events_dir = org_dir / "events"
        self._reports_dir = org_dir / "reports"
        self._logs_dir = org_dir / "logs"
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        # In-process write serialization.
        self._lock = threading.Lock()
        # Cross-process write serialization; resolves to a .lock sibling of
        # the events dir so it stays scoped to one org.
        self._lock_path = self._events_dir / ".write.lock"
        self._filelock = (
            FileLock(str(self._lock_path)) if _HAS_FILELOCK else None
        )

    def clear(self) -> None:
        """Remove all event files (used during org reset)."""
        import shutil
        for d in (self._events_dir, self._logs_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        event_type: str,
        actor: str,
        data: dict | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Append an immutable event to the event stream."""
        now = datetime.now(UTC)
        event = {
            "event_id": _new_id("evt_"),
            "event_type": event_type,
            "org_id": self._org_id,
            "actor": actor,
            "timestamp": now.isoformat(),
            "data": data or {},
            "metadata": metadata or {},
        }

        day_file = self._events_dir / f"{now.strftime('%Y%m%d')}.jsonl"
        line = json.dumps(event, ensure_ascii=False) + "\n"

        # C17 Phase D: serialize writes both inside this process (RLock-equivalent
        # via Lock — emit is never re-entrant) and across processes (filelock).
        # On filelock timeout we degrade to a single-process write rather than
        # dropping silently, but warn so an operator can see the contention.
        with self._lock:
            try:
                acquired_cross_process = False
                if self._filelock is not None:
                    try:
                        self._filelock.acquire(timeout=self._FILELOCK_TIMEOUT_SECONDS)
                        acquired_cross_process = True
                    except _FileLockTimeout:
                        logger.warning(
                            "[EventStore] cross-process lock timed out after %.1fs "
                            "for org=%s (writing without it); event_type=%s",
                            self._FILELOCK_TIMEOUT_SECONDS,
                            self._org_id,
                            event_type,
                        )
                try:
                    with open(day_file, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as e:
                    logger.error(f"[EventStore] Failed to write event: {e}")
                finally:
                    if acquired_cross_process and self._filelock is not None:
                        try:
                            self._filelock.release()
                        except Exception:  # pragma: no cover
                            pass
            except Exception as e:  # noqa: BLE001
                logger.error(f"[EventStore] Unexpected emit error: {e}")

        return event

    def query(
        self,
        event_type: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        chain_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict]:
        """Query events with optional filters. Returns newest events first."""
        results: list[dict] = []
        enough = False

        files = sorted(self._events_dir.glob("*.jsonl"), reverse=True)
        for f in files:
            if enough:
                break
            if since:
                day = f.stem
                if day < since.replace("-", "")[:8]:
                    break
            if until:
                day = f.stem
                if day > until.replace("-", "")[:8]:
                    continue

            try:
                lines = f.read_text(encoding="utf-8").strip().split("\n")
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    evt = json.loads(line)
                    ts = evt.get("timestamp", "")
                    if since and ts < since:
                        enough = True
                        break
                    if until and ts > until:
                        continue
                    if event_type and evt.get("event_type") != event_type:
                        continue
                    if actor and evt.get("actor") != actor:
                        continue
                    data = evt.get("data") or {}
                    if chain_id is not None and data.get("chain_id") != chain_id:
                        continue
                    if task_id is not None and data.get("task_id") != task_id:
                        continue
                    results.append(evt)
                    if len(results) >= limit:
                        return results
            except Exception as e:
                logger.warning(f"[EventStore] Failed to read {f}: {e}")

        return results

    def get_last_pending(self, node_id: str) -> dict | None:
        """Find the last pending/in-progress event for a node (for restart recovery)."""
        files = sorted(self._events_dir.glob("*.jsonl"), reverse=True)
        for f in files[:3]:
            try:
                lines = f.read_text(encoding="utf-8").strip().split("\n")
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    evt = json.loads(line)
                    if evt.get("actor") == node_id and evt.get("event_type") in (
                        "task_started", "node_activated"
                    ):
                        return evt
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def get_audit_log(
        self,
        days: int = 7,
        event_types: list[str] | None = None,
    ) -> list[dict]:
        """Get an audit trail of important events."""
        important_types = event_types or [
            "org_started", "org_stopped", "org_paused", "org_resumed",
            "user_command", "task_completed", "task_failed",
            "node_frozen", "node_unfrozen", "node_dismissed",
            "scaling_requested", "scaling_approved", "scaling_rejected",
            "approval_resolved",
            "heartbeat_decision", "standup_completed",
        ]
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        all_events = self.query(since=since, limit=1000)
        return [e for e in all_events if e.get("event_type") in important_types]

    def write_audit_log(self, days: int = 7) -> Path:
        """Generate and save a human-readable audit log file."""
        events = self.get_audit_log(days=days)
        now = datetime.now(UTC)
        log_file = self._logs_dir / f"audit_{now.strftime('%Y%m%d')}.md"

        lines = [
            "# 审计日志",
            "",
            f"**组织**: {self._org_id}",
            f"**生成时间**: {now.isoformat()}",
            f"**覆盖范围**: 最近 {days} 天",
            f"**事件数量**: {len(events)}",
            "",
            "| 时间 | 事件 | 执行者 | 详情 |",
            "|------|------|--------|------|",
        ]

        for evt in events:
            ts = evt.get("timestamp", "")[:19]
            etype = evt.get("event_type", "")
            actor = evt.get("actor", "")
            data = evt.get("data", {})
            detail = ", ".join(f"{k}={v}" for k, v in list(data.items())[:3])
            if len(detail) > 80:
                detail = detail[:80] + "..."
            lines.append(f"| {ts} | {etype} | {actor} | {detail} |")

        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_file

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_summary_report(self, days: int = 7) -> dict:
        """Generate a statistical summary of org activity."""
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        events = self.query(since=since, limit=5000)

        type_counts: Counter = Counter()
        actor_counts: Counter = Counter()
        daily_counts: Counter = Counter()
        tasks_completed = 0
        tasks_failed = 0
        messages_sent = 0
        errors = []

        for evt in events:
            etype = evt.get("event_type", "")
            type_counts[etype] += 1
            actor_counts[evt.get("actor", "unknown")] += 1
            day = evt.get("timestamp", "")[:10]
            daily_counts[day] += 1

            if etype == "task_completed":
                tasks_completed += 1
            elif etype == "task_failed":
                tasks_failed += 1
                errors.append({
                    "time": evt.get("timestamp", ""),
                    "node": evt.get("actor", ""),
                    "error": evt.get("data", {}).get("error", "")[:100],
                })
            elif etype in ("message_sent", "task_assigned"):
                messages_sent += 1

        return {
            "period_days": days,
            "total_events": len(events),
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "messages_sent": messages_sent,
            "event_type_distribution": dict(type_counts.most_common(20)),
            "node_activity": dict(actor_counts.most_common(20)),
            "daily_activity": dict(sorted(daily_counts.items())),
            "recent_errors": errors[:10],
        }

    def generate_report_markdown(self, days: int = 7) -> Path:
        """Generate and save a markdown report."""
        summary = self.generate_summary_report(days)
        now = datetime.now(UTC)
        report_path = self._reports_dir / f"report_{now.strftime('%Y%m%d')}.md"

        lines = [
            "# 组织运行报告",
            "",
            f"**组织**: {self._org_id}",
            f"**生成时间**: {now.isoformat()}",
            f"**统计周期**: 最近 {days} 天",
            "",
            "## 概览",
            f"- 总事件数: {summary['total_events']}",
            f"- 完成任务: {summary['tasks_completed']}",
            f"- 失败任务: {summary['tasks_failed']}",
            f"- 消息总量: {summary['messages_sent']}",
            "",
            "## 事件类型分布",
        ]

        for etype, count in summary["event_type_distribution"].items():
            lines.append(f"- {etype}: {count}")

        lines.append("")
        lines.append("## 节点活跃度")
        for node, count in summary["node_activity"].items():
            lines.append(f"- {node}: {count} 次操作")

        lines.append("")
        lines.append("## 每日活动")
        for day, count in summary["daily_activity"].items():
            lines.append(f"- {day}: {count} 个事件")

        if summary["recent_errors"]:
            lines.append("")
            lines.append("## 近期错误")
            for err in summary["recent_errors"]:
                lines.append(f"- [{err['time'][:19]}] {err['node']}: {err['error']}")

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path
