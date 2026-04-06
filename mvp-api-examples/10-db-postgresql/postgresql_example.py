# PostgreSQL 数据库连接 API 集成示例
# 适用于 MVP 数据库操作

import os
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor


class PostgreSQLClient:
    """PostgreSQL 数据库客户端封装"""
    
    def __init__(self, connection_string: Optional[str] = None, max_connections: int = 10):
        """
        初始化数据库连接池
        
        Args:
            connection_string: 数据库连接字符串
            max_connections: 最大连接数
        """
        if connection_string is None:
            connection_string = os.getenv(
                "DATABASE_URL",
                "postgresql://user:password@localhost:5432/mvp_db"
            )
        
        self.connection_string = connection_string
        self.connection_pool = pool.SimpleConnectionPool(
            1, max_connections,
            dsn=connection_string
        )
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接（上下文管理器）"""
        conn = self.connection_pool.getconn()
        try:
            yield conn
        finally:
            self.connection_pool.putconn(conn)
    
    @contextmanager
    def get_cursor(self, commit: bool = False):
        """获取数据库游标（上下文管理器）"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """
        执行查询语句
        
        Args:
            query: SQL 查询语句
            params: 查询参数
        
        Returns:
            查询结果列表
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def execute_insert(self, table: str, data: Dict[str, Any]) -> int:
        """
        执行插入操作
        
        Args:
            table: 表名
            data: 要插入的数据字典
        
        Returns:
            插入的行 ID
        """
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ", ".join(["%s"] * len(columns))
        
        query = sql.SQL("INSERT INTO {table} ({fields}) VALUES ({values}) RETURNING id").format(
            table=sql.Identifier(table),
            fields=sql.SQL(", ").join(map(sql.Identifier, columns)),
            values=sql.SQL(placeholders)
        )
        
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(query, values)
            return cursor.fetchone()["id"]
    
    def execute_update(self, table: str, data: Dict[str, Any], where: str, where_params: tuple) -> int:
        """
        执行更新操作
        
        Args:
            table: 表名
            data: 要更新的数据字典
            where: WHERE 条件
            where_params: WHERE 条件参数
        
        Returns:
            更新的行数
        """
        set_clause = ", ".join([f"{col} = %s" for col in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"
        
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(query, tuple(data.values()) + where_params)
            return cursor.rowcount
    
    def execute_delete(self, table: str, where: str, where_params: tuple) -> int:
        """
        执行删除操作
        
        Args:
            table: 表名
            where: WHERE 条件
            where_params: WHERE 条件参数
        
        Returns:
            删除的行数
        """
        query = f"DELETE FROM {table} WHERE {where}"
        
        with self.get_cursor(commit=True) as cursor:
            cursor.execute(query, where_params)
            return cursor.rowcount
    
    def create_table(self, table_name: str, columns: Dict[str, str]) -> bool:
        """
        创建表
        
        Args:
            table_name: 表名
            columns: 列定义字典 {列名: 类型}
        
        Returns:
            是否成功
        """
        column_defs = ", ".join([f"{col} {dtype}" for col, dtype in columns.items()])
        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({column_defs})"
        
        try:
            with self.get_cursor(commit=True) as cursor:
                cursor.execute(query)
            return True
        except Exception as e:
            print(f"创建表失败：{e}")
            return False
    
    def close(self):
        """关闭连接池"""
        if self.connection_pool:
            self.connection_pool.closeall()


# 使用示例
if __name__ == "__main__":
    # 初始化客户端
    db = PostgreSQLClient()
    
    # 1. 创建示例表
    db.create_table("users", {
        "id": "SERIAL PRIMARY KEY",
        "email": "VARCHAR(255) UNIQUE NOT NULL",
        "password_hash": "VARCHAR(255) NOT NULL",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    })
    
    # 2. 插入数据
    user_id = db.execute_insert(
        "users",
        {
            "email": "test@example.com",
            "password_hash": "hashed_password_123"
        }
    )
    print(f"插入用户 ID: {user_id}")
    
    # 3. 查询数据
    users = db.execute_query("SELECT * FROM users WHERE email = %s", ("test@example.com",))
    print(f"查询结果：{users}")
    
    # 4. 更新数据
    rows_updated = db.execute_update(
        "users",
        {"password_hash": "new_hashed_password"},
        "email = %s",
        ("test@example.com",)
    )
    print(f"更新行数：{rows_updated}")
    
    # 5. 删除数据
    rows_deleted = db.execute_delete(
        "users",
        "email = %s",
        ("test@example.com",)
    )
    print(f"删除行数：{rows_deleted}")
    
    # 关闭连接
    db.close()
