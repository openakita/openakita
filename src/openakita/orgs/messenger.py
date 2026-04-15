"""
OrgMessenger — 组织内消息路由与冲突解决

负责节点间消息投递、优先级队列、超时管理、死锁检测。
每个节点拥有独立的异步消息信箱。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from .models import (
    MsgType,
    NodeStatus,
    Organization,
    OrgMessage,
)

logger = logging.getLogger(__name__)

DEFAULT_MSG_TTL = 300
TASK_MSG_TTL = 1800  # 30 min — deliverables / results must survive long orchestration rounds
DEADLOCK_CHECK_INTERVAL = 30

_TASK_MSG_TYPES = frozenset({
    "task_assign", "task_result", "task_delivered", "task_accepted", "task_rejected",
})


class NodeMailbox:
    """Async priority queue for a single node.

    Two consumption paths exist:
      1. **Handler path** – ``messenger.send()`` invokes a handler callback
         which receives the ``msg`` reference directly.  The message *also*
         sits in the queue as a phantom entry.  Call
         ``mark_handler_processed(msg.id)`` to record that this message has
         already been handled.
      2. **Drain path** – ``_drain_node_pending()`` calls ``get()`` to pop
         the next entry.  If the entry was already processed by the handler
         path it must be skipped (see ``is_handler_processed``).
    """

    def __init__(self, node_id: str, max_size: int = 100):
        self.node_id = node_id
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        self._paused = False
        self._seq = 0
        self._frozen_buffer: list = []
        self._dispatched = 0
        self._handler_processed: set[str] = set()

    async def put(self, msg: OrgMessage) -> None:
        priority = -(int(msg.priority) if msg.priority else 0)
        self._seq += 1
        item = (priority, msg.created_at, self._seq, msg)
        if self._paused:
            self._frozen_buffer.append(item)
            logger.debug(f"Mailbox {self.node_id} is paused, buffering msg {msg.id}")
            return
        await self._queue.put(item)

    async def get(self, timeout: float = 60.0) -> OrgMessage | None:
        try:
            _, _, _, msg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return msg
        except TimeoutError:
            return None

    def mark_dispatched(self) -> None:
        """Mark one pending message as dispatched (counter-based)."""
        self._dispatched += 1

    def mark_handler_processed(self, msg_id: str) -> None:
        """Record that the handler callback already processed *msg_id*.

        Increments ``_dispatched`` (so ``pending_count`` decreases by 1)
        and records the id so the drain path can skip the phantom.
        """
        self._handler_processed.add(msg_id)
        self._dispatched += 1

    def is_handler_processed(self, msg_id: str) -> bool:
        return msg_id in self._handler_processed

    def consume_phantom(self, msg_id: str) -> None:
        """Called by the drain path when it dequeues a handler-processed phantom.

        ``get()`` already decremented ``qsize`` by 1.  We must also
        decrement ``_dispatched`` by 1 so that ``pending_count``
        (= qsize − dispatched) stays unchanged.
        """
        self._handler_processed.discard(msg_id)
        if self._dispatched > 0:
            self._dispatched -= 1

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        restored = 0
        while self._frozen_buffer:
            item = self._frozen_buffer.pop(0)
            self._queue.put_nowait(item)
            restored += 1
        if restored:
            logger.debug(f"Mailbox {self.node_id} resumed, restored {restored} buffered messages")

    @property
    def frozen_buffer_count(self) -> int:
        return len(self._frozen_buffer)

    @property
    def pending_count(self) -> int:
        return max(0, self._queue.qsize() - self._dispatched)

    @property
    def total_received(self) -> int:
        """Total messages ever received (queue + dispatched)."""
        return self._queue.qsize()

    @property
    def is_paused(self) -> bool:
        return self._paused


class OrgMessenger:
    """Message routing and conflict detection for an organization."""

    def __init__(self, org: Organization, org_dir: Path) -> None:
        self._org = org
        self._org_dir = org_dir
        self._mailboxes: dict[str, NodeMailbox] = {}
        self._comm_log = org_dir / "logs" / "communications.jsonl"
        self._comm_log.parent.mkdir(parents=True, exist_ok=True)

        self._wait_graph: dict[str, set[str]] = defaultdict(set)
        self._message_handlers: dict[str, Callable] = {}
        self._on_deadlock: Callable[[list[list[str]]], Any] | None = None

        self._edge_msg_counts: dict[str, list[float]] = defaultdict(list)
        self._pending_messages: dict[str, OrgMessage] = {}

        self._task_affinity: dict[str, str] = {}

        self._deadlock_task: asyncio.Task | None = None
        self._ttl_task: asyncio.Task | None = None

        for node in org.nodes:
            self._mailboxes[node.id] = NodeMailbox(node.id)

    def clear_all(self) -> None:
        """Clear all mailboxes and pending state (used during org reset)."""
        for node_id in list(self._mailboxes):
            self._mailboxes[node_id] = NodeMailbox(node_id)
        self._pending_messages.clear()
        self._wait_graph.clear()
        self._edge_msg_counts.clear()
        self._task_affinity.clear()

    def clear_node_pending(self, node_id: str) -> int:
        """Clear all pending messages for a specific node."""
        old = self._mailboxes.get(node_id)
        cleared = old.pending_count if old else 0
        self._mailboxes[node_id] = NodeMailbox(node_id)
        return cleared

    def update_org(self, org: Organization) -> None:
        self._org = org
        for node in org.nodes:
            if node.id not in self._mailboxes:
                self._mailboxes[node.id] = NodeMailbox(node.id)

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def start_background_tasks(self) -> None:
        """Start periodic deadlock detection and TTL expiration loops."""
        if self._deadlock_task is None or self._deadlock_task.done():
            self._deadlock_task = asyncio.create_task(self._deadlock_loop())
        if self._ttl_task is None or self._ttl_task.done():
            self._ttl_task = asyncio.create_task(self._ttl_loop())

    async def stop_background_tasks(self) -> None:
        for task in (self._deadlock_task, self._ttl_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._deadlock_task = None
        self._ttl_task = None

    async def _deadlock_loop(self) -> None:
        """Periodically check for deadlocks in the wait-for graph."""
        try:
            while True:
                await asyncio.sleep(DEADLOCK_CHECK_INTERVAL)
                cycles = self.check_deadlock()
                if cycles:
                    logger.warning(f"[Messenger] Deadlock detected: {cycles}")
                    self._break_cycles(cycles)
                    if self._on_deadlock:
                        try:
                            result = self._on_deadlock(cycles)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"[Messenger] Deadlock handler error: {e}")
        except asyncio.CancelledError:
            pass

    def _break_cycles(self, cycles: list[list[str]]) -> None:
        """Break detected deadlock cycles by removing edges from the wait graph.

        Strategy: for each cycle, remove the edge from the last node back to the
        first (the "closing" edge) to break the circular wait.
        """
        for cycle in cycles:
            if len(cycle) < 2:
                continue
            breaker = cycle[-2]
            target = cycle[-1]
            if breaker in self._wait_graph:
                removed = target in self._wait_graph[breaker]
                self._wait_graph[breaker].discard(target)
                if removed:
                    logger.info(
                        f"[Messenger] Broke deadlock: removed {breaker} -> {target} "
                        f"from wait graph (cycle: {cycle})"
                    )

    async def _ttl_loop(self) -> None:
        """Expire messages that have exceeded their TTL."""
        try:
            while True:
                await asyncio.sleep(60)
                now = time.time()
                expired_ids = []
                for msg_id, msg in list(self._pending_messages.items()):
                    try:
                        from datetime import datetime
                        sent_ts = datetime.fromisoformat(msg.created_at).timestamp()
                    except Exception:
                        continue
                    default_ttl = (
                        TASK_MSG_TTL
                        if getattr(msg, "msg_type", None)
                        and msg.msg_type.value in _TASK_MSG_TYPES
                        else DEFAULT_MSG_TTL
                    )
                    ttl = msg.metadata.get("ttl", default_ttl)
                    if now - sent_ts > ttl and msg.status in ("sent", "delivered"):
                        msg.status = "expired"
                        expired_ids.append(msg_id)
                        logger.info(f"[Messenger] Message {msg_id} expired (TTL={ttl}s)")
                for mid in expired_ids:
                    self._pending_messages.pop(mid, None)
        except asyncio.CancelledError:
            pass

    def set_deadlock_handler(self, handler: Callable[[list[list[str]]], Any]) -> None:
        self._on_deadlock = handler

    def register_handler(
        self, node_id: str, handler: Callable[[OrgMessage], Coroutine]
    ) -> None:
        self._message_handlers[node_id] = handler

    def register_node(
        self, node_id: str, handler: Callable[[OrgMessage], Coroutine] | None = None
    ) -> None:
        if node_id not in self._mailboxes:
            self._mailboxes[node_id] = NodeMailbox(node_id)
        if handler is not None:
            self._message_handlers[node_id] = handler

    def unregister_node(self, node_id: str) -> None:
        self._mailboxes.pop(node_id, None)
        self._message_handlers.pop(node_id, None)
        affinities_to_remove = [
            k for k, v in self._task_affinity.items() if v == node_id
        ]
        for k in affinities_to_remove:
            self._task_affinity.pop(k, None)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(self, msg: OrgMessage) -> bool:
        """Route a message to the target node's mailbox."""
        if msg.to_node is None:
            return await self._broadcast(msg)

        chain_id = msg.metadata.get("task_chain_id")
        if chain_id:
            affinity_node = self._task_affinity.get(chain_id)
            if (
                affinity_node
                and affinity_node != msg.to_node
                and affinity_node != msg.from_node
            ):
                actual = self._org.get_node(affinity_node)
                if actual and actual.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    msg.to_node = affinity_node
                    logger.debug(f"[Messenger] Task affinity: chain {chain_id} -> {affinity_node}")

        target = self._org.get_node(msg.to_node)
        if target is None:
            avail = ", ".join(f"{n.id}({n.role_title})" for n in self._org.nodes[:20])
            logger.warning(
                f"[Messenger] Target node not found: {msg.to_node}. "
                f"Available: {avail}"
            )
            return False

        if target.status == NodeStatus.FROZEN:
            logger.info(f"[Messenger] Target node {msg.to_node} is frozen, message queued")

        if msg.edge_id is None:
            msg.edge_id = self._find_edge(msg.from_node, msg.to_node)

        if msg.edge_id and not self._check_bandwidth(msg.edge_id):
            logger.warning(f"[Messenger] Bandwidth limit exceeded on edge {msg.edge_id}")
            return False

        self._log_message(msg)

        self._pending_messages[msg.id] = msg

        mailbox = self._mailboxes.get(msg.to_node)
        if mailbox:
            await mailbox.put(msg)
            msg.status = "delivered"

        if msg.from_node != msg.to_node:
            if not self._would_create_cycle(msg.from_node, msg.to_node):
                self._wait_graph[msg.from_node].add(msg.to_node)
            else:
                logger.info(
                    f"[Messenger] Skipped wait-graph edge {msg.from_node} -> {msg.to_node} "
                    f"(would create cycle)"
                )

        if msg.to_node in self._message_handlers:
            try:
                await self._message_handlers[msg.to_node](msg)
            except Exception as e:
                logger.error(f"[Messenger] Handler error for {msg.to_node}: {e}")

        return True

    async def send_task(
        self,
        from_node: str,
        to_node: str,
        task_content: str,
        priority: int = 0,
        metadata: dict | None = None,
    ) -> OrgMessage:
        msg = OrgMessage(
            org_id=self._org.id,
            from_node=from_node,
            to_node=to_node,
            msg_type=MsgType.TASK_ASSIGN,
            content=task_content,
            priority=priority,
            metadata=metadata or {},
        )
        await self.send(msg)
        return msg

    async def send_result(
        self,
        from_node: str,
        to_node: str,
        result: str,
        reply_to: str | None = None,
        metadata: dict | None = None,
    ) -> OrgMessage:
        msg = OrgMessage(
            org_id=self._org.id,
            from_node=from_node,
            to_node=to_node,
            msg_type=MsgType.TASK_RESULT,
            content=result,
            reply_to=reply_to,
            metadata=metadata or {},
        )
        await self.send(msg)

        if from_node in self._wait_graph:
            self._wait_graph[from_node].discard(to_node)

        return msg

    async def escalate(
        self, from_node: str, content: str, priority: int = 1,
        metadata: dict | None = None,
    ) -> OrgMessage | None:
        parent = self._org.get_parent(from_node)
        if parent is None:
            logger.warning(f"[Messenger] No parent for escalation from {from_node}")
            return None
        msg = OrgMessage(
            org_id=self._org.id,
            from_node=from_node,
            to_node=parent.id,
            msg_type=MsgType.ESCALATE,
            content=content,
            priority=priority,
            metadata=metadata or {},
        )
        await self.send(msg)
        return msg

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def _broadcast(self, msg: OrgMessage) -> bool:
        """Broadcast to all nodes (or department)."""
        targets: list[str] = []
        if msg.msg_type == MsgType.DEPT_BROADCAST:
            sender = self._org.get_node(msg.from_node)
            if sender:
                targets = [
                    n.id for n in self._org.nodes
                    if n.department == sender.department and n.id != msg.from_node
                ]
        else:
            targets = [n.id for n in self._org.nodes if n.id != msg.from_node]

        trigger_handler = msg.msg_type in (MsgType.TASK_ASSIGN, MsgType.TASK_RESULT)

        for nid in targets:
            copy = OrgMessage(
                org_id=msg.org_id,
                from_node=msg.from_node,
                to_node=nid,
                msg_type=msg.msg_type,
                content=msg.content,
                priority=msg.priority,
                metadata=dict(msg.metadata) if msg.metadata else {},
            )
            mailbox = self._mailboxes.get(nid)
            if mailbox:
                await mailbox.put(copy)
            if trigger_handler and nid in self._message_handlers:
                try:
                    await self._message_handlers[nid](copy)
                except Exception as e:
                    logger.error(f"[Messenger] Broadcast handler error for {nid}: {e}")

        self._log_message(msg)
        return True

    # ------------------------------------------------------------------
    # Mailbox access
    # ------------------------------------------------------------------

    def get_mailbox(self, node_id: str) -> NodeMailbox | None:
        return self._mailboxes.get(node_id)

    def get_pending_count(self, node_id: str) -> int:
        mb = self._mailboxes.get(node_id)
        return mb.pending_count if mb else 0

    def freeze_mailbox(self, node_id: str) -> None:
        mb = self._mailboxes.get(node_id)
        if mb:
            mb.pause()

    def unfreeze_mailbox(self, node_id: str) -> None:
        mb = self._mailboxes.get(node_id)
        if mb:
            mb.resume()

    # ------------------------------------------------------------------
    # Task affinity
    # ------------------------------------------------------------------

    def bind_task_affinity(self, task_chain_id: str, node_id: str) -> None:
        """Bind a task chain to a specific node (clone), so all follow-ups go there."""
        self._task_affinity[task_chain_id] = node_id
        logger.debug(f"[Messenger] Task affinity bound: {task_chain_id} -> {node_id}")

    def get_task_affinity(self, task_chain_id: str) -> str | None:
        return self._task_affinity.get(task_chain_id)

    def release_task_affinity(self, task_chain_id: str) -> None:
        self._task_affinity.pop(task_chain_id, None)

    def get_clone_mailbox_counts(self, source_node_id: str) -> dict[str, int]:
        """Get pending message counts for all clones of a source node."""
        counts: dict[str, int] = {}
        for node in self._org.nodes:
            if node.clone_source == source_node_id or node.id == source_node_id:
                mb = self._mailboxes.get(node.id)
                counts[node.id] = mb.pending_count if mb else 0
        return counts

    # ------------------------------------------------------------------
    # Deadlock detection
    # ------------------------------------------------------------------

    def _would_create_cycle(self, from_node: str, to_node: str) -> bool:
        """Check if adding from_node -> to_node would create a cycle via BFS."""
        if to_node not in self._wait_graph:
            return False
        visited: set[str] = set()
        queue = [to_node]
        while queue:
            current = queue.pop(0)
            if current == from_node:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self._wait_graph.get(current, set()) - visited)
        return False

    def check_deadlock(self) -> list[list[str]] | None:
        """Check for cycles in the wait-for graph. Returns cycles if found."""
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def _dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self._wait_graph.get(node, set()):
                if neighbor not in visited:
                    _dfs(neighbor, path)
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])

            path.pop()
            rec_stack.discard(node)

        for node in list(self._wait_graph.keys()):
            if node not in visited:
                _dfs(node, [])

        return cycles if cycles else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def mark_processed(self, msg_id: str) -> None:
        """Mark a message as processed and clear from wait graph."""
        msg = self._pending_messages.pop(msg_id, None)
        if msg and msg.to_node:
            if msg.from_node in self._wait_graph:
                self._wait_graph[msg.from_node].discard(msg.to_node)

    def _check_bandwidth(self, edge_id: str) -> bool:
        """Check if a message can be sent on this edge without exceeding bandwidth."""
        edge = None
        for e in self._org.edges:
            if e.id == edge_id:
                edge = e
                break
        if not edge or edge.bandwidth_limit <= 0:
            return True

        now = time.time()
        window_start = now - 60.0
        timestamps = self._edge_msg_counts.get(edge_id, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= edge.bandwidth_limit:
            return False
        timestamps.append(now)
        self._edge_msg_counts[edge_id] = timestamps
        return True

    def _find_edge(self, from_id: str, to_id: str) -> str | None:
        for e in self._org.edges:
            if (e.source == from_id and e.target == to_id) or (
                e.bidirectional and e.source == to_id and e.target == from_id
            ):
                return e.id
        return None

    def _log_message(self, msg: OrgMessage) -> None:
        try:
            with open(self._comm_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[Messenger] Failed to log message: {e}")
