"""SQLite-backed storage for the relational memory graph."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import Any

from .types import (
    Dimension,
    EdgeType,
    EntityRef,
    MemoryEdge,
    MemoryNode,
    NodeType,
)

logger = logging.getLogger(__name__)


class RelationalMemoryStore:
    """Manages mdrm_* tables inside the shared MemoryStorage database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._ensure_tables()

    @staticmethod
    def _active_node_where(alias: str = "") -> tuple[str, list[str]]:
        prefix = f"{alias}." if alias else ""
        return f"({prefix}valid_until IS NULL OR {prefix}valid_until >= ?)", [
            datetime.now().isoformat()
        ]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        c = self._conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS mdrm_nodes (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'event',
                occurred_at TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                entities TEXT DEFAULT '[]',
                action_verb TEXT DEFAULT '',
                action_category TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                project TEXT DEFAULT '',
                goal TEXT DEFAULT '',
                importance REAL DEFAULT 0.5,
                confidence REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                embedding BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mdrm_edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES mdrm_nodes(id) ON DELETE CASCADE,
                target_id TEXT NOT NULL REFERENCES mdrm_nodes(id) ON DELETE CASCADE,
                edge_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                weight REAL DEFAULT 0.5,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mdrm_entity_index (
                entity_name TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                node_id TEXT NOT NULL REFERENCES mdrm_nodes(id) ON DELETE CASCADE,
                role TEXT DEFAULT '',
                PRIMARY KEY (entity_name, node_id)
            );

            CREATE TABLE IF NOT EXISTS mdrm_reachable (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                hops INTEGER NOT NULL,
                min_weight REAL DEFAULT 0.0,
                PRIMARY KEY (source_id, target_id, dimension)
            );

            CREATE TABLE IF NOT EXISTS mdrm_entity_aliases (
                alias TEXT PRIMARY KEY,
                canonical TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'rule'
            );
        """)

        # v2: 多 Agent 记忆隔离预留
        try:
            c.execute("ALTER TABLE mdrm_nodes ADD COLUMN agent_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_mdrm_nodes_time ON mdrm_nodes(occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_nodes_type ON mdrm_nodes(node_type)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_nodes_project ON mdrm_nodes(project)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_nodes_session ON mdrm_nodes(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_nodes_agent ON mdrm_nodes(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_edges_source ON mdrm_edges(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_edges_target ON mdrm_edges(target_id)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_edges_dim ON mdrm_edges(dimension)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_edges_type ON mdrm_edges(edge_type)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_entity_name ON mdrm_entity_index(entity_name)",
            "CREATE INDEX IF NOT EXISTS idx_mdrm_reach_src ON mdrm_reachable(source_id, dimension)",
        ]:
            try:
                c.execute(idx_sql)
            except sqlite3.OperationalError:
                pass

        # FTS5 standalone table with pre-tokenized CJK content
        self._ensure_fts_table(c)

        self._conn.commit()

    def _ensure_fts_table(self, c: sqlite3.Cursor) -> None:
        """Create or migrate FTS5 to standalone table with pre-tokenized CJK bigrams."""
        needs_create = True
        try:
            cur = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='mdrm_nodes_fts'"
            )
            row = cur.fetchone()
            if row:
                sql = row[0] or ""
                if "node_id" in sql and "content_tokens" in sql:
                    needs_create = False
                else:
                    c.execute("DROP TABLE IF EXISTS mdrm_nodes_fts")
                    for name in ("mdrm_fts_ai", "mdrm_fts_ad", "mdrm_fts_au"):
                        c.execute(f"DROP TRIGGER IF EXISTS {name}")
                    logger.info("[RelationalStore] Migrating FTS5 to tokenized standalone table")
        except sqlite3.OperationalError:
            pass

        if not needs_create:
            return

        try:
            c.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS mdrm_nodes_fts USING fts5(
                    node_id UNINDEXED,
                    content_tokens,
                    action_tokens,
                    tokenize='unicode61'
                )
            """)
        except sqlite3.OperationalError as e:
            logger.debug(f"FTS5 table creation skipped: {e}")
            return

        try:
            cur = c.execute("SELECT id, content, action_verb FROM mdrm_nodes")
            count = 0
            for row in cur.fetchall():
                c.execute(
                    "INSERT INTO mdrm_nodes_fts (node_id, content_tokens, action_tokens) "
                    "VALUES (?, ?, ?)",
                    (
                        row[0],
                        self._tokenize_for_fts(row[1] or ""),
                        self._tokenize_for_fts(row[2] or ""),
                    ),
                )
                count += 1
            if count:
                logger.info(f"[RelationalStore] FTS5 index rebuilt: {count} nodes tokenized")
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # CJK-aware FTS5 tokenization
    # ------------------------------------------------------------------

    _CJK_RANGE_RE = re.compile(
        r"([\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+)"
    )

    @staticmethod
    def _tokenize_for_fts(text: str) -> str:
        """Pre-tokenize text for FTS5 indexing.

        CJK text is split into overlapping bigrams so that FTS5's unicode61
        tokenizer can index and match them. Non-CJK text (English etc.) passes
        through unchanged since unicode61 already handles space-delimited words.

        Examples:
            "记忆模块"       → "记忆 忆模 模块"
            "hello world"   → "hello world"
            "SQLite性能优化" → "SQLite 性能 能优 优化"
        """
        if not text:
            return ""
        result: list[str] = []
        for seg in RelationalMemoryStore._CJK_RANGE_RE.split(text):
            if not seg:
                continue
            first = ord(seg[0])
            is_cjk = (
                0x4E00 <= first <= 0x9FFF
                or 0x3400 <= first <= 0x4DBF
                or 0x3040 <= first <= 0x309F
                or 0x30A0 <= first <= 0x30FF
                or 0xAC00 <= first <= 0xD7AF
            )
            if is_cjk:
                if len(seg) == 1:
                    result.append(seg)
                else:
                    for i in range(len(seg) - 1):
                        result.append(seg[i : i + 2])
            else:
                stripped = seg.strip()
                if stripped:
                    result.append(stripped)
        return " ".join(result)

    def _sync_fts(self, node_id: str, content: str, action_verb: str) -> None:
        """Sync a single node's entry in the FTS5 index."""
        try:
            cur = self._conn.execute(
                "SELECT rowid FROM mdrm_nodes_fts WHERE node_id = ?", (node_id,)
            )
            row = cur.fetchone()
            if row:
                self._conn.execute("DELETE FROM mdrm_nodes_fts WHERE rowid = ?", (row[0],))
            self._conn.execute(
                "INSERT INTO mdrm_nodes_fts (node_id, content_tokens, action_tokens) "
                "VALUES (?, ?, ?)",
                (node_id, self._tokenize_for_fts(content), self._tokenize_for_fts(action_verb)),
            )
        except sqlite3.OperationalError:
            pass

    def _delete_fts(self, node_id: str) -> None:
        """Remove a node from the FTS5 index."""
        try:
            cur = self._conn.execute(
                "SELECT rowid FROM mdrm_nodes_fts WHERE node_id = ?", (node_id,)
            )
            row = cur.fetchone()
            if row:
                self._conn.execute("DELETE FROM mdrm_nodes_fts WHERE rowid = ?", (row[0],))
        except sqlite3.OperationalError:
            pass

    def rebuild_fts(self) -> int:
        """Rebuild the entire FTS5 index from mdrm_nodes. Returns count."""
        try:
            self._conn.execute("DELETE FROM mdrm_nodes_fts")
            cur = self._conn.execute("SELECT id, content, action_verb FROM mdrm_nodes")
            count = 0
            for row in cur.fetchall():
                self._conn.execute(
                    "INSERT INTO mdrm_nodes_fts (node_id, content_tokens, action_tokens) "
                    "VALUES (?, ?, ?)",
                    (
                        row[0],
                        self._tokenize_for_fts(row[1] or ""),
                        self._tokenize_for_fts(row[2] or ""),
                    ),
                )
                count += 1
            self._conn.commit()
            return count
        except sqlite3.OperationalError:
            return 0

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    def save_node(self, node: MemoryNode) -> None:
        now = datetime.now().isoformat()
        entities_json = json.dumps(
            [{"name": e.name, "type": e.type, "role": e.role} for e in node.entities],
            ensure_ascii=False,
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO mdrm_nodes
               (id, content, node_type, occurred_at, valid_from, valid_until,
                entities, action_verb, action_category, session_id, project, goal,
                importance, confidence, access_count, embedding, created_at, updated_at,
                agent_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                node.id,
                node.content,
                node.node_type.value,
                node.occurred_at.isoformat(),
                (node.valid_from or node.occurred_at).isoformat(),
                node.valid_until.isoformat() if node.valid_until else None,
                entities_json,
                node.action_verb,
                node.action_category,
                node.session_id,
                node.project,
                node.goal,
                node.importance,
                node.confidence,
                node.access_count,
                node.embedding,
                node.created_at.isoformat() if node.created_at else now,
                now,
                node.agent_id,
            ),
        )

        # Update entity index
        self._conn.execute("DELETE FROM mdrm_entity_index WHERE node_id = ?", (node.id,))
        for ent in node.entities:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO mdrm_entity_index
                       (entity_name, entity_type, node_id, role)
                       VALUES (?, ?, ?, ?)""",
                    (ent.name.lower(), ent.type, node.id, ent.role),
                )
            except sqlite3.IntegrityError:
                pass

        self._sync_fts(node.id, node.content, node.action_verb)
        self._conn.commit()

    def save_nodes_batch(self, nodes: list[MemoryNode]) -> None:
        if not nodes:
            return
        now = datetime.now().isoformat()
        for node in nodes:
            entities_json = json.dumps(
                [{"name": e.name, "type": e.type, "role": e.role} for e in node.entities],
                ensure_ascii=False,
            )
            self._conn.execute(
                """INSERT OR REPLACE INTO mdrm_nodes
                   (id, content, node_type, occurred_at, valid_from, valid_until,
                    entities, action_verb, action_category, session_id, project, goal,
                    importance, confidence, access_count, embedding, created_at, updated_at,
                    agent_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    node.id,
                    node.content,
                    node.node_type.value,
                    node.occurred_at.isoformat(),
                    (node.valid_from or node.occurred_at).isoformat(),
                    node.valid_until.isoformat() if node.valid_until else None,
                    entities_json,
                    node.action_verb,
                    node.action_category,
                    node.session_id,
                    node.project,
                    node.goal,
                    node.importance,
                    node.confidence,
                    node.access_count,
                    node.embedding,
                    node.created_at.isoformat() if node.created_at else now,
                    now,
                    node.agent_id,
                ),
            )
            self._conn.execute("DELETE FROM mdrm_entity_index WHERE node_id = ?", (node.id,))
            for ent in node.entities:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO mdrm_entity_index
                           (entity_name, entity_type, node_id, role) VALUES (?, ?, ?, ?)""",
                        (ent.name.lower(), ent.type, node.id, ent.role),
                    )
                except sqlite3.IntegrityError:
                    pass
            self._sync_fts(node.id, node.content, node.action_verb)
        self._conn.commit()

    def get_node(self, node_id: str) -> MemoryNode | None:
        cur = self._conn.execute("SELECT * FROM mdrm_nodes WHERE id = ?", (node_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_node(cur.description, row)

    def delete_node(self, node_id: str) -> bool:
        self._conn.execute(
            "DELETE FROM mdrm_edges WHERE source_id=? OR target_id=?", (node_id, node_id)
        )
        self._conn.execute("DELETE FROM mdrm_entity_index WHERE node_id=?", (node_id,))
        self._conn.execute(
            "DELETE FROM mdrm_reachable WHERE source_id=? OR target_id=?", (node_id, node_id)
        )
        self._delete_fts(node_id)
        cur = self._conn.execute("DELETE FROM mdrm_nodes WHERE id=?", (node_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def count_nodes(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM mdrm_nodes")
        return cur.fetchone()[0]

    def count_edges(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM mdrm_edges")
        return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------

    def save_edge(self, edge: MemoryEdge) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO mdrm_edges
               (id, source_id, target_id, edge_type, dimension, weight, metadata, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                edge.id,
                edge.source_id,
                edge.target_id,
                edge.edge_type.value,
                edge.dimension.value,
                edge.weight,
                json.dumps(edge.metadata, ensure_ascii=False),
                edge.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def save_edges_batch(self, edges: list[MemoryEdge]) -> None:
        for edge in edges:
            self._conn.execute(
                """INSERT OR REPLACE INTO mdrm_edges
                   (id, source_id, target_id, edge_type, dimension, weight, metadata, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.edge_type.value,
                    edge.dimension.value,
                    edge.weight,
                    json.dumps(edge.metadata, ensure_ascii=False),
                    edge.created_at.isoformat(),
                ),
            )
        self._conn.commit()

    def get_edges_for_node(self, node_id: str, dimension: str | None = None) -> list[MemoryEdge]:
        if dimension:
            cur = self._conn.execute(
                "SELECT * FROM mdrm_edges WHERE (source_id=? OR target_id=?) AND dimension=?",
                (node_id, node_id, dimension),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM mdrm_edges WHERE source_id=? OR target_id=?",
                (node_id, node_id),
            )
        return [self._row_to_edge(cur.description, r) for r in cur.fetchall()]

    def get_neighbors(self, node_id: str, dimension: str | None = None) -> list[str]:
        edges = self.get_edges_for_node(node_id, dimension)
        neighbors: list[str] = []
        for e in edges:
            other = e.target_id if e.source_id == node_id else e.source_id
            if other not in neighbors:
                neighbors.append(other)
        return neighbors

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_fts(self, query: str, limit: int = 20) -> list[MemoryNode]:
        """Full-text search with CJK bigram tokenization and BM25 ranking."""
        if not query or len(query.strip()) < 2:
            return []
        tokenized = self._tokenize_for_fts(query)
        safe_q = re.sub(r'[":*^~()\-+]', " ", tokenized).strip()
        if not safe_q:
            return self.search_like(query, limit)
        terms = [t for t in safe_q.split() if len(t) >= 2]
        if not terms:
            return self.search_like(query, limit)
        fts_query = " OR ".join(terms)
        try:
            cur = self._conn.execute(
                "SELECT node_id FROM mdrm_nodes_fts "
                "WHERE mdrm_nodes_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit),
            )
            ranked_ids = [r[0] for r in cur.fetchall()]
            if not ranked_ids:
                return self.search_like(query, limit)
            placeholders = ",".join("?" for _ in ranked_ids)
            active_where, active_params = self._active_node_where()
            cur2 = self._conn.execute(
                f"SELECT * FROM mdrm_nodes WHERE id IN ({placeholders}) AND {active_where}",
                ranked_ids + active_params,
            )
            desc = cur2.description
            nodes_by_id: dict[str, MemoryNode] = {}
            for r in cur2.fetchall():
                n = self._row_to_node(desc, r)
                nodes_by_id[n.id] = n
            return [nodes_by_id[nid] for nid in ranked_ids if nid in nodes_by_id]
        except sqlite3.OperationalError:
            return self.search_like(query, limit)

    def search_like(self, query: str, limit: int = 20) -> list[MemoryNode]:
        if not query or len(query.strip()) < 2:
            return []
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        active_where, active_params = self._active_node_where()
        cur = self._conn.execute(
            """SELECT * FROM mdrm_nodes
               WHERE (content LIKE ? ESCAPE '\\' OR action_verb LIKE ? ESCAPE '\\'
               OR project LIKE ? ESCAPE '\\')
               AND """
            + active_where
            + """
               ORDER BY importance DESC LIMIT ?""",
            (pattern, pattern, pattern, *active_params, limit),
        )
        return [self._row_to_node(cur.description, r) for r in cur.fetchall()]

    def search_by_entity(self, entity_name: str, limit: int = 20) -> list[MemoryNode]:
        name_lower = entity_name.lower()
        # Check alias table first
        cur = self._conn.execute(
            "SELECT canonical FROM mdrm_entity_aliases WHERE alias = ?", (name_lower,)
        )
        row = cur.fetchone()
        canonical = row[0] if row else name_lower

        active_where, active_params = self._active_node_where("n")
        cur = self._conn.execute(
            """SELECT n.* FROM mdrm_nodes n
               JOIN mdrm_entity_index ei ON n.id = ei.node_id
               WHERE ei.entity_name = ? AND """
            + active_where
            + """
               ORDER BY n.importance DESC LIMIT ?""",
            (canonical, *active_params, limit),
        )
        return [self._row_to_node(cur.description, r) for r in cur.fetchall()]

    def search_by_time_range(
        self, start: datetime, end: datetime, limit: int = 50
    ) -> list[MemoryNode]:
        active_where, active_params = self._active_node_where()
        cur = self._conn.execute(
            """SELECT * FROM mdrm_nodes
               WHERE occurred_at >= ? AND occurred_at <= ? AND """
            + active_where
            + """
               ORDER BY occurred_at DESC LIMIT ?""",
            (start.isoformat(), end.isoformat(), *active_params, limit),
        )
        return [self._row_to_node(cur.description, r) for r in cur.fetchall()]

    def get_all_nodes(self, limit: int = 2000) -> list[MemoryNode]:
        active_where, active_params = self._active_node_where()
        cur = self._conn.execute(
            f"SELECT * FROM mdrm_nodes WHERE {active_where} ORDER BY importance DESC LIMIT ?",
            (*active_params, limit),
        )
        return [self._row_to_node(cur.description, r) for r in cur.fetchall()]

    def get_all_edges(self, node_ids: set[str] | None = None) -> list[MemoryEdge]:
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            id_list = list(node_ids)
            cur = self._conn.execute(
                f"SELECT * FROM mdrm_edges WHERE source_id IN ({placeholders}) "
                f"OR target_id IN ({placeholders})",
                id_list + id_list,
            )
        else:
            cur = self._conn.execute("SELECT * FROM mdrm_edges")
        return [self._row_to_edge(cur.description, r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Reachable table (materialized 1-2 hop paths)
    # ------------------------------------------------------------------

    def rebuild_reachable(self) -> int:
        """Rebuild the materialized reachable table from edges. Returns row count."""
        c = self._conn.cursor()
        c.execute("DELETE FROM mdrm_reachable")

        # 1-hop (both directions since graph is treated as undirected)
        c.execute("""
            INSERT OR REPLACE INTO mdrm_reachable (source_id, target_id, dimension, hops, min_weight)
            SELECT source_id, target_id, dimension, 1, weight FROM mdrm_edges
        """)
        c.execute("""
            INSERT OR IGNORE INTO mdrm_reachable (source_id, target_id, dimension, hops, min_weight)
            SELECT target_id, source_id, dimension, 1, weight FROM mdrm_edges
        """)

        # 2-hop (join on 1-hop reachable for bidirectional paths)
        c.execute("""
            INSERT OR IGNORE INTO mdrm_reachable (source_id, target_id, dimension, hops, min_weight)
            SELECT r1.source_id, r2.target_id, r1.dimension, 2,
                   MIN(r1.min_weight, r2.min_weight)
            FROM mdrm_reachable r1
            JOIN mdrm_reachable r2 ON r1.target_id = r2.source_id
                                   AND r1.dimension = r2.dimension
                                   AND r1.hops = 1 AND r2.hops = 1
            WHERE r1.source_id != r2.target_id
        """)

        self._conn.commit()
        cur = c.execute("SELECT COUNT(*) FROM mdrm_reachable")
        return cur.fetchone()[0]

    def query_reachable(
        self, source_id: str, dimension: str | None = None, max_hops: int = 2
    ) -> list[dict]:
        if dimension:
            cur = self._conn.execute(
                """SELECT target_id, dimension, hops, min_weight
                   FROM mdrm_reachable
                   WHERE source_id = ? AND dimension = ? AND hops <= ?
                   ORDER BY min_weight DESC""",
                (source_id, dimension, max_hops),
            )
        else:
            cur = self._conn.execute(
                """SELECT target_id, dimension, hops, min_weight
                   FROM mdrm_reachable
                   WHERE source_id = ? AND hops <= ?
                   ORDER BY min_weight DESC""",
                (source_id, max_hops),
            )
        return [
            {"target_id": r[0], "dimension": r[1], "hops": r[2], "min_weight": r[3]}
            for r in cur.fetchall()
        ]

    # ------------------------------------------------------------------
    # Entity aliases
    # ------------------------------------------------------------------

    def get_all_entity_names(self, limit: int = 500) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT entity_name FROM mdrm_entity_index LIMIT ?", (limit,)
        )
        return [r[0] for r in cur.fetchall()]

    def add_alias(
        self, alias: str, canonical: str, confidence: float = 0.5, source: str = "rule"
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO mdrm_entity_aliases (alias, canonical, confidence, source) VALUES (?,?,?,?)",
            (alias.lower(), canonical.lower(), confidence, source),
        )
        self._conn.commit()

    def resolve_entity(self, name: str) -> str:
        cur = self._conn.execute(
            "SELECT canonical FROM mdrm_entity_aliases WHERE alias = ?", (name.lower(),)
        )
        row = cur.fetchone()
        return row[0] if row else name.lower()

    # ------------------------------------------------------------------
    # Edge weight updates
    # ------------------------------------------------------------------

    def strengthen_edge(self, edge_id: str, delta: float = 0.05) -> None:
        self._conn.execute(
            "UPDATE mdrm_edges SET weight = MIN(1.0, weight + ?) WHERE id = ?",
            (delta, edge_id),
        )
        self._conn.commit()

    def decay_edges(self, factor: float = 0.98) -> int:
        cur = self._conn.execute("UPDATE mdrm_edges SET weight = weight * ?", (factor,))
        self._conn.commit()
        return cur.rowcount

    def prune_weak_edges(self, threshold: float = 0.05) -> int:
        cur = self._conn.execute("DELETE FROM mdrm_edges WHERE weight < ?", (threshold,))
        self._conn.commit()
        return cur.rowcount

    def increment_access(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE mdrm_nodes SET access_count = access_count + 1 WHERE id = ?",
            (node_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _row_to_node(self, description: Any, row: tuple) -> MemoryNode:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row, strict=False))
        entities_raw = d.get("entities", "[]")
        if isinstance(entities_raw, str):
            try:
                entities_raw = json.loads(entities_raw)
            except Exception:
                entities_raw = []
        entities = [
            EntityRef(name=e.get("name", ""), type=e.get("type", "concept"), role=e.get("role", ""))
            for e in entities_raw
        ]

        nt = NodeType.EVENT
        try:
            nt = NodeType(d.get("node_type", "event"))
        except ValueError:
            pass

        def _parse_dt(val: str | None) -> datetime:
            if not val:
                return datetime.now()
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return datetime.now()

        return MemoryNode(
            id=d["id"],
            content=d.get("content", ""),
            node_type=nt,
            occurred_at=_parse_dt(d.get("occurred_at")),
            valid_from=_parse_dt(d.get("valid_from")),
            valid_until=_parse_dt(d["valid_until"]) if d.get("valid_until") else None,
            entities=entities,
            action_verb=d.get("action_verb", ""),
            action_category=d.get("action_category", ""),
            session_id=d.get("session_id", ""),
            project=d.get("project", ""),
            goal=d.get("goal", ""),
            importance=d.get("importance", 0.5),
            confidence=d.get("confidence", 0.5),
            access_count=d.get("access_count", 0),
            embedding=d.get("embedding"),
            created_at=_parse_dt(d.get("created_at")),
            updated_at=_parse_dt(d.get("updated_at")),
            agent_id=d.get("agent_id", ""),
        )

    def _row_to_edge(self, description: Any, row: tuple) -> MemoryEdge:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row, strict=False))

        et = EdgeType.RELATED_TO
        try:
            et = EdgeType(d.get("edge_type", "related_to"))
        except ValueError:
            pass

        dim = Dimension.ENTITY
        try:
            dim = Dimension(d.get("dimension", "entity"))
        except ValueError:
            pass

        meta = d.get("metadata", "{}")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        created = datetime.now()
        try:
            created = datetime.fromisoformat(d.get("created_at", ""))
        except Exception:
            pass

        return MemoryEdge(
            id=d["id"],
            source_id=d.get("source_id", ""),
            target_id=d.get("target_id", ""),
            edge_type=et,
            dimension=dim,
            weight=d.get("weight", 0.5),
            metadata=meta,
            created_at=created,
        )
