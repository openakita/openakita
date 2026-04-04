"""
OrgBlackboard — 三级共享记忆系统

组织级（黑板）、部门级、节点私有三层记忆，
支持读写、容量管理、自动淘汰。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .models import MemoryScope, MemoryType, OrgMemoryEntry

logger = logging.getLogger(__name__)


def _safe_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


MAX_ORG_MEMORIES = 200
MAX_DEPT_MEMORIES = 100
MAX_NODE_MEMORIES = 50


class OrgBlackboard:
    """Three-tier shared memory for an organization."""

    def __init__(self, org_dir: Path, org_id: str) -> None:
        self._org_dir = org_dir
        self._org_id = org_id
        self._memory_dir = org_dir / "memory"
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    def clear(self) -> None:
        """Remove all blackboard/memory data (used during org reset)."""
        import shutil
        logger.warning(
            f"[Blackboard] Clearing ALL memory for org {self._org_id}"
        )
        if self._memory_dir.exists():
            shutil.rmtree(self._memory_dir, ignore_errors=True)
            self._memory_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_org(self, limit: int = 20, tag: str | None = None) -> list[OrgMemoryEntry]:
        return self._read_scope(
            self._memory_dir / "blackboard.jsonl", limit=limit, tag=tag
        )

    def read_department(
        self, dept_name: str, limit: int = 20, tag: str | None = None
    ) -> list[OrgMemoryEntry]:
        p = self._memory_dir / "departments" / f"{dept_name}.jsonl"
        return self._read_scope(p, limit=limit, tag=tag)

    def read_node(
        self, node_id: str, limit: int = 20, tag: str | None = None
    ) -> list[OrgMemoryEntry]:
        p = self._memory_dir / "nodes" / f"{node_id}.jsonl"
        return self._read_scope(p, limit=limit, tag=tag)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_org(
        self,
        content: str,
        source_node: str,
        memory_type: MemoryType = MemoryType.FACT,
        tags: list[str] | None = None,
        importance: float = 0.5,
        source_message_id: str | None = None,
    ) -> OrgMemoryEntry | None:
        bb_path = self._memory_dir / "blackboard.jsonl"
        if self._is_duplicate(bb_path, content):
            logger.debug(f"[Blackboard] Skipping duplicate org entry: {content[:50]}")
            return None

        entry = OrgMemoryEntry(
            org_id=self._org_id,
            scope=MemoryScope.ORG,
            scope_owner=self._org_id,
            memory_type=memory_type,
            content=content,
            source_node=source_node,
            source_message_id=source_message_id,
            tags=tags or [],
            importance=importance,
        )
        self._append(bb_path, entry, MAX_ORG_MEMORIES)
        logger.info(
            f"[Blackboard] write_org by={source_node} type={memory_type.value} "
            f"importance={importance} content={content[:80]!r}"
        )
        return entry

    def write_department(
        self,
        dept_name: str,
        content: str,
        source_node: str,
        memory_type: MemoryType = MemoryType.FACT,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> OrgMemoryEntry | None:
        p = self._memory_dir / "departments" / f"{dept_name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        if self._is_duplicate(p, content):
            logger.debug(f"[Blackboard] Skipping duplicate dept entry: {content[:50]}")
            return None

        entry = OrgMemoryEntry(
            org_id=self._org_id,
            scope=MemoryScope.DEPARTMENT,
            scope_owner=dept_name,
            memory_type=memory_type,
            content=content,
            source_node=source_node,
            tags=tags or [],
            importance=importance,
        )
        self._append(p, entry, MAX_DEPT_MEMORIES)
        logger.info(
            f"[Blackboard] write_dept dept={dept_name} by={source_node} "
            f"content={content[:80]!r}"
        )
        return entry

    def write_node(
        self,
        node_id: str,
        content: str,
        memory_type: MemoryType = MemoryType.FACT,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> OrgMemoryEntry:
        entry = OrgMemoryEntry(
            org_id=self._org_id,
            scope=MemoryScope.NODE,
            scope_owner=node_id,
            memory_type=memory_type,
            content=content,
            source_node=node_id,
            tags=tags or [],
            importance=importance,
        )
        p = self._memory_dir / "nodes" / f"{node_id}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        self._append(p, entry, MAX_NODE_MEMORIES)
        logger.info(
            f"[Blackboard] write_node node={node_id} content={content[:80]!r}"
        )
        return entry

    # ------------------------------------------------------------------
    # Summaries for prompt injection
    # ------------------------------------------------------------------

    def get_org_summary(self, max_entries: int = 10) -> str:
        entries = self.read_org(limit=max_entries)
        if not entries:
            return "(暂无组织级记忆)"
        lines = []
        for e in entries:
            tag_str = f" [{', '.join(e.tags)}]" if e.tags else ""
            lines.append(f"- [{e.memory_type.value}] {e.content}{tag_str}")
        return "\n".join(lines)

    def get_dept_summary(self, dept_name: str, max_entries: int = 5) -> str:
        entries = self.read_department(dept_name, limit=max_entries)
        if not entries:
            return f"({dept_name} 暂无部门级记忆)"
        lines = []
        for e in entries:
            lines.append(f"- [{e.memory_type.value}] {e.content}")
        return "\n".join(lines)

    def get_node_summary(self, node_id: str, max_entries: int = 5) -> str:
        entries = self.read_node(node_id, limit=max_entries)
        if not entries:
            return "(暂无私有记忆)"
        lines = []
        for e in entries:
            lines.append(f"- {e.content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        scope: MemoryScope | None = None,
        scope_owner: str | None = None,
        memory_type: MemoryType | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[OrgMemoryEntry]:
        """Query across all memory scopes with optional filters."""
        all_entries: list[OrgMemoryEntry] = []

        if scope is None or scope == MemoryScope.ORG:
            all_entries.extend(self.read_org(limit=200))
        if scope is None or scope == MemoryScope.DEPARTMENT:
            dept_dir = self._memory_dir / "departments"
            if dept_dir.exists():
                for f in dept_dir.glob("*.jsonl"):
                    if scope_owner and f.stem != scope_owner:
                        continue
                    all_entries.extend(self._read_scope(f, limit=200))
        if scope is None or scope == MemoryScope.NODE:
            node_dir = self._memory_dir / "nodes"
            if node_dir.exists():
                for f in node_dir.glob("*.jsonl"):
                    if scope_owner and f.stem != scope_owner:
                        continue
                    all_entries.extend(self._read_scope(f, limit=200))

        if memory_type:
            all_entries = [e for e in all_entries if e.memory_type == memory_type]
        if tag:
            all_entries = [e for e in all_entries if tag in e.tags]

        all_entries.sort(key=lambda e: e.created_at, reverse=True)
        return all_entries[:limit]

    def delete_entry(self, memory_id: str) -> bool:
        """Delete a memory entry by ID from any scope file."""
        for fpath in self._all_memory_files():
            if not fpath.is_file():
                continue
            lines = fpath.read_text(encoding="utf-8").strip().split("\n")
            new_lines = []
            found = False
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == memory_id:
                        found = True
                        continue
                except Exception:
                    pass
                new_lines.append(line)
            if found:
                fpath.write_text("\n".join(new_lines) + "\n" if new_lines else "", encoding="utf-8")
                return True
        return False

    def _all_memory_files(self) -> list[Path]:
        """Return all jsonl memory files across all scopes."""
        files = []
        bb = self._memory_dir / "blackboard.jsonl"
        if bb.exists():
            files.append(bb)
        for sub in ("departments", "nodes"):
            d = self._memory_dir / sub
            if d.exists():
                files.extend(d.glob("*.jsonl"))
        return files

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_expired(self, entry: OrgMemoryEntry) -> bool:
        if not entry.ttl_hours:
            return False
        try:
            created = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
            expiry = created + timedelta(hours=entry.ttl_hours)
            return datetime.now(created.tzinfo) > expiry
        except (ValueError, TypeError):
            return False

    def _read_scope(
        self, path: Path, limit: int = 20, tag: str | None = None
    ) -> list[OrgMemoryEntry]:
        if not path.is_file():
            return []
        entries: list[OrgMemoryEntry] = []
        try:
            for line in path.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                e = OrgMemoryEntry.from_dict(json.loads(line))
                if tag and tag not in e.tags:
                    continue
                if self._is_expired(e):
                    continue
                entries.append(e)
        except Exception as exc:
            logger.warning(f"Failed to read memory {path}: {exc}")
        entries.sort(key=lambda e: _safe_float(e.importance), reverse=True)
        return entries[:int(limit)]

    @staticmethod
    def _is_duplicate(path: Path, content: str, prefix_len: int = 100) -> bool:
        """Check if an entry with the same content prefix already exists."""
        if not path.is_file():
            return False
        prefix = content[:prefix_len].strip()
        if not prefix:
            return False
        try:
            for line in path.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    existing = json.loads(line).get("content", "")
                    if existing[:prefix_len].strip() == prefix:
                        return True
                except (json.JSONDecodeError, KeyError):
                    continue
        except Exception:
            pass
        return False

    def _append(self, path: Path, entry: OrgMemoryEntry, max_entries: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        self._evict_if_needed(path, max_entries)

    def _evict_if_needed(self, path: Path, max_entries: int) -> None:
        """Remove expired and least important entries if over capacity."""
        if not path.is_file():
            return
        lines = [ln for ln in path.read_text(encoding="utf-8").strip().split("\n") if ln.strip()]

        live_entries: list[tuple[float, str]] = []
        expired_count = 0
        for line in lines:
            try:
                d = json.loads(line)
                entry = OrgMemoryEntry.from_dict(d)
                if self._is_expired(entry):
                    expired_count += 1
                    continue
                live_entries.append((_safe_float(entry.importance), line))
            except Exception:
                continue

        if expired_count > 0:
            logger.info(
                f"[Blackboard] Removed {expired_count} expired entries from {path.name}"
            )

        if len(live_entries) <= max_entries and expired_count == 0:
            return

        live_entries.sort(key=lambda x: x[0], reverse=True)
        kept = live_entries[:max_entries]
        evicted_count = len(live_entries) - len(kept)
        if evicted_count > 0:
            logger.warning(
                f"[Blackboard] Evicted {evicted_count} low-importance entries "
                f"from {path.name} (capacity={max_entries})"
            )
        path.write_text(
            "\n".join(line for _, line in kept) + "\n",
            encoding="utf-8",
        )
