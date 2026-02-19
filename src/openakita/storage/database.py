"""
SQLite 数据库封装
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from ..config import settings
from .models import (
    Conversation,
    MemoryEntry,
    Message,
    SkillRecord,
)

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.db_full_path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """连接数据库"""
        # 确保目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        await self._init_tables()

        logger.info(f"Database connected: {self.db_path}")

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    async def _init_tables(self) -> None:
        """初始化数据表"""
        await self._connection.executescript("""
            -- 对话表
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            );

            -- 消息表
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            -- 技能记录表
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                version TEXT,
                source TEXT,
                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                use_count INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            );

            -- 记忆表
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                importance INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            );

            -- 任务记录表
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                attempts INTEGER DEFAULT 0,
                result TEXT,
                error TEXT,
                metadata TEXT DEFAULT '{}'
            );

            -- 用户偏好表
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- ========== 新增表 (v0.5.0) ==========

            -- 定时任务表
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                trigger_type TEXT NOT NULL,
                trigger_config TEXT NOT NULL,
                prompt TEXT NOT NULL,
                script_path TEXT,
                channel_id TEXT,
                chat_id TEXT,
                user_id TEXT,
                enabled INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending',
                last_run TIMESTAMP,
                next_run TIMESTAMP,
                run_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            );

            -- 任务执行日志表
            CREATE TABLE IF NOT EXISTS task_executions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                result TEXT,
                error TEXT,
                duration_seconds REAL,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
            );

            -- 用户表（跨平台统一用户）
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                avatar_url TEXT,
                preferences TEXT DEFAULT '{}',
                permissions TEXT DEFAULT '["user"]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP,
                total_messages INTEGER DEFAULT 0
            );

            -- 用户通道绑定表
            CREATE TABLE IF NOT EXISTS user_bindings (
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                channel_user_id TEXT NOT NULL,
                bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, channel),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            -- 会话表
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                state TEXT DEFAULT 'active',
                context TEXT DEFAULT '{}',
                config TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            );

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user ON scheduled_tasks(user_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run);
            CREATE INDEX IF NOT EXISTS idx_task_executions_task ON task_executions(task_id);
            CREATE INDEX IF NOT EXISTS idx_user_bindings_channel ON user_bindings(channel, channel_user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel, chat_id);

            -- ========== Token 用量追踪表 ==========

            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                endpoint_name TEXT,
                model TEXT,
                operation_type TEXT,
                operation_detail TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                context_tokens INTEGER DEFAULT 0,
                iteration INTEGER DEFAULT 0,
                channel TEXT,
                user_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(timestamp);
            CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
            CREATE INDEX IF NOT EXISTS idx_token_usage_endpoint ON token_usage(endpoint_name);
            CREATE INDEX IF NOT EXISTS idx_token_usage_op ON token_usage(operation_type);
        """)
        await self._connection.commit()

    # ===== 对话相关 =====

    async def create_conversation(self, title: str = "") -> int:
        """创建对话"""
        cursor = await self._connection.execute(
            "INSERT INTO conversations (title) VALUES (?)",
            (title,),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_conversation(self, id: int) -> Conversation | None:
        """获取对话"""
        cursor = await self._connection.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # 获取消息
        messages = await self.get_messages(id)

        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            messages=messages,
            metadata=json.loads(row["metadata"]),
        )

    async def get_messages(self, conversation_id: int) -> list[Message]:
        """获取对话消息"""
        cursor = await self._connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,),
        )
        rows = await cursor.fetchall()

        return [
            Message(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        """添加消息"""
        cursor = await self._connection.execute(
            """INSERT INTO messages (conversation_id, role, content, metadata)
               VALUES (?, ?, ?, ?)""",
            (conversation_id, role, content, json.dumps(metadata or {})),
        )

        # 更新对话的 updated_at
        await self._connection.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )

        await self._connection.commit()
        return cursor.lastrowid

    # ===== 技能相关 =====

    async def record_skill(
        self,
        name: str,
        version: str,
        source: str,
        metadata: dict | None = None,
    ) -> int:
        """记录技能安装"""
        cursor = await self._connection.execute(
            """INSERT OR REPLACE INTO skills (name, version, source, metadata)
               VALUES (?, ?, ?, ?)""",
            (name, version, source, json.dumps(metadata or {})),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_skill(self, name: str) -> SkillRecord | None:
        """获取技能记录"""
        cursor = await self._connection.execute(
            "SELECT * FROM skills WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return SkillRecord(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            source=row["source"],
            installed_at=datetime.fromisoformat(row["installed_at"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
            use_count=row["use_count"],
            metadata=json.loads(row["metadata"]),
        )

    async def update_skill_usage(self, name: str) -> None:
        """更新技能使用记录"""
        await self._connection.execute(
            """UPDATE skills
               SET last_used = CURRENT_TIMESTAMP, use_count = use_count + 1
               WHERE name = ?""",
            (name,),
        )
        await self._connection.commit()

    async def list_skills(self) -> list[SkillRecord]:
        """列出所有技能"""
        cursor = await self._connection.execute("SELECT * FROM skills ORDER BY installed_at DESC")
        rows = await cursor.fetchall()

        return [
            SkillRecord(
                id=row["id"],
                name=row["name"],
                version=row["version"],
                source=row["source"],
                installed_at=datetime.fromisoformat(row["installed_at"]),
                last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
                use_count=row["use_count"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    # ===== 记忆相关 =====

    async def add_memory(
        self,
        category: str,
        content: str,
        importance: int = 0,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> int:
        """添加记忆"""
        cursor = await self._connection.execute(
            """INSERT INTO memories (category, content, importance, tags, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                category,
                content,
                importance,
                json.dumps(tags or []),
                json.dumps(metadata or {}),
            ),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_memories(
        self,
        category: str | None = None,
        limit: int = 100,
        min_importance: int = 0,
    ) -> list[MemoryEntry]:
        """获取记忆"""
        query = "SELECT * FROM memories WHERE importance >= ?"
        params: list[Any] = [min_importance]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        return [
            MemoryEntry(
                id=row["id"],
                category=row["category"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                importance=row["importance"],
                tags=json.loads(row["tags"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def search_memories(self, query: str, limit: int = 10) -> list[MemoryEntry]:
        """搜索记忆"""
        cursor = await self._connection.execute(
            """SELECT * FROM memories
               WHERE content LIKE ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (f"%{query}%", limit),
        )
        rows = await cursor.fetchall()

        return [
            MemoryEntry(
                id=row["id"],
                category=row["category"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                importance=row["importance"],
                tags=json.loads(row["tags"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    # ===== 任务相关 =====

    async def record_task(
        self,
        task_id: str,
        description: str,
        status: str = "pending",
    ) -> int:
        """记录任务"""
        cursor = await self._connection.execute(
            """INSERT OR REPLACE INTO tasks (task_id, description, status)
               VALUES (?, ?, ?)""",
            (task_id, description, status),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def update_task(
        self,
        task_id: str,
        status: str | None = None,
        result: Any = None,
        error: str | None = None,
        attempts: int | None = None,
    ) -> None:
        """更新任务"""
        updates = []
        params = []

        if status:
            updates.append("status = ?")
            params.append(status)
            if status == "completed":
                updates.append("completed_at = CURRENT_TIMESTAMP")

        if result is not None:
            updates.append("result = ?")
            params.append(json.dumps(result))

        if error is not None:
            updates.append("error = ?")
            params.append(error)

        if attempts is not None:
            updates.append("attempts = ?")
            params.append(attempts)

        if updates:
            params.append(task_id)
            await self._connection.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
                params,
            )
            await self._connection.commit()

    # ===== 偏好相关 =====

    async def set_preference(self, key: str, value: Any) -> None:
        """设置偏好"""
        await self._connection.execute(
            """INSERT OR REPLACE INTO preferences (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (key, json.dumps(value)),
        )
        await self._connection.commit()

    async def get_preference(self, key: str, default: Any = None) -> Any:
        """获取偏好"""
        cursor = await self._connection.execute(
            "SELECT value FROM preferences WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()

        if row:
            return json.loads(row["value"])
        return default

    async def get_all_preferences(self) -> dict[str, Any]:
        """获取所有偏好"""
        cursor = await self._connection.execute("SELECT key, value FROM preferences")
        rows = await cursor.fetchall()

        return {row["key"]: json.loads(row["value"]) for row in rows}

    # ===== Token 用量统计 =====

    async def get_token_usage_summary(
        self,
        start_time: datetime,
        end_time: datetime,
        group_by: str = "endpoint_name",
        endpoint_name: str | None = None,
        operation_type: str | None = None,
    ) -> list[dict]:
        """按维度聚合 token 用量"""
        allowed = {"endpoint_name", "operation_type", "model", "session_id", "channel"}
        if group_by not in allowed:
            group_by = "endpoint_name"

        where = ["timestamp >= ?", "timestamp <= ?"]
        params: list[Any] = [start_time.isoformat(), end_time.isoformat()]
        if endpoint_name:
            where.append("endpoint_name = ?")
            params.append(endpoint_name)
        if operation_type:
            where.append("operation_type = ?")
            params.append(operation_type)

        sql = f"""
            SELECT {group_by} AS group_key,
                   SUM(input_tokens) AS total_input,
                   SUM(output_tokens) AS total_output,
                   SUM(input_tokens + output_tokens) AS total_tokens,
                   SUM(cache_creation_tokens) AS total_cache_creation,
                   SUM(cache_read_tokens) AS total_cache_read,
                   COUNT(*) AS request_count
            FROM token_usage
            WHERE {' AND '.join(where)}
            GROUP BY {group_by}
            ORDER BY total_tokens DESC
        """
        cursor = await self._connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_token_usage_timeline(
        self,
        start_time: datetime,
        end_time: datetime,
        interval: str = "hour",
        endpoint_name: str | None = None,
    ) -> list[dict]:
        """按时间线聚合 token 用量（折线图用）"""
        fmt_map = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "week": "%Y-W%W"}
        time_fmt = fmt_map.get(interval, "%Y-%m-%d %H:00")

        where = ["timestamp >= ?", "timestamp <= ?"]
        params: list[Any] = [start_time.isoformat(), end_time.isoformat()]
        if endpoint_name:
            where.append("endpoint_name = ?")
            params.append(endpoint_name)

        sql = f"""
            SELECT strftime('{time_fmt}', timestamp) AS time_bucket,
                   SUM(input_tokens) AS total_input,
                   SUM(output_tokens) AS total_output,
                   SUM(input_tokens + output_tokens) AS total_tokens,
                   COUNT(*) AS request_count
            FROM token_usage
            WHERE {' AND '.join(where)}
            GROUP BY time_bucket
            ORDER BY time_bucket
        """
        cursor = await self._connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_token_usage_sessions(
        self,
        start_time: datetime,
        end_time: datetime,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """按会话列出 token 消耗"""
        sql = """
            SELECT session_id,
                   MIN(timestamp) AS first_call,
                   MAX(timestamp) AS last_call,
                   SUM(input_tokens) AS total_input,
                   SUM(output_tokens) AS total_output,
                   SUM(input_tokens + output_tokens) AS total_tokens,
                   COUNT(*) AS request_count,
                   GROUP_CONCAT(DISTINCT operation_type) AS operation_types,
                   GROUP_CONCAT(DISTINCT endpoint_name) AS endpoints
            FROM token_usage
            WHERE timestamp >= ? AND timestamp <= ? AND session_id != ''
            GROUP BY session_id
            ORDER BY last_call DESC
            LIMIT ? OFFSET ?
        """
        cursor = await self._connection.execute(
            sql, (start_time.isoformat(), end_time.isoformat(), limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_token_usage_total(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> dict:
        """获取总计 token 用量"""
        sql = """
            SELECT COALESCE(SUM(input_tokens), 0) AS total_input,
                   COALESCE(SUM(output_tokens), 0) AS total_output,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation,
                   COALESCE(SUM(cache_read_tokens), 0) AS total_cache_read,
                   COUNT(*) AS request_count
            FROM token_usage
            WHERE timestamp >= ? AND timestamp <= ?
        """
        cursor = await self._connection.execute(
            sql, (start_time.isoformat(), end_time.isoformat())
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}
