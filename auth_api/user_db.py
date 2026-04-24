"""
用户数据模型和存储层
使用 SQLite 存储（生产环境可替换为 PostgreSQL/MySQL）
"""
from datetime import datetime
from typing import Optional, Dict
import sqlite3
import threading
from contextlib import contextmanager


class UserDB:
    """用户数据库操作类"""
    
    def __init__(self, db_path: str = "users.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取线程安全的数据库连接"""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection
    
    @contextmanager
    def get_cursor(self):
        """获取游标上下文管理器"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
    
    def _init_db(self):
        """初始化数据库表"""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)
            
            # 创建刷新令牌存储表（用于 rotating 机制）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    jti TEXT UNIQUE NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_revoked BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_username ON users(username)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email ON users(email)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jti ON refresh_tokens(jti)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON refresh_tokens(user_id)")
    
    def create_user(self, username: str, email: str, password_hash: str) -> Optional[Dict]:
        """创建新用户"""
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                    (username, email, password_hash)
                )
                cursor.execute("SELECT * FROM users WHERE id = LAST_INSERT_ROWID()")
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.IntegrityError:
            return None
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """通过用户名获取用户"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """通过邮箱获取用户"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def store_refresh_token(self, user_id: int, jti: str, token_hash: str, expires_at: datetime):
        """存储刷新令牌"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO refresh_tokens (user_id, jti, token_hash, expires_at) VALUES (?, ?, ?, ?)",
                (user_id, jti, token_hash, expires_at)
            )
    
    def get_refresh_token(self, jti: str) -> Optional[Dict]:
        """通过 JTI 获取刷新令牌"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM refresh_tokens WHERE jti = ? AND is_revoked = FALSE",
                (jti,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def revoke_refresh_token(self, jti: str):
        """撤销刷新令牌（rotating 机制）"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE refresh_tokens SET is_revoked = TRUE WHERE jti = ?",
                (jti,)
            )
    
    def cleanup_expired_tokens(self):
        """清理过期的刷新令牌"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM refresh_tokens WHERE expires_at < CURRENT_TIMESTAMP OR is_revoked = TRUE"
            )


# 全局数据库实例
user_db = UserDB()

