"""
数据库 API 集成示例
支持 PostgreSQL 和 MySQL
"""

import psycopg2
import mysql.connector
from typing import List, Dict, Any, Optional
from contextlib import contextmanager


class PostgreSQLAPI:
    """PostgreSQL 数据库 API"""
    
    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接上下文管理器"""
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            yield conn
        finally:
            if conn:
                conn.close()
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """
        执行查询语句
        
        Args:
            query: SQL 查询语句
            params: 查询参数
            
        Returns:
            list: 查询结果
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    columns = [desc[0] for desc in cur.description]
                    results = [dict(zip(columns, row)) for row in cur.fetchall()]
                    print(f"✓ 查询成功，返回 {len(results)} 条记录")
                    return results
        except Exception as e:
            print(f"✗ 查询失败：{e}")
            return []
    
    def execute_update(self, query: str, params: tuple = None) -> int:
        """
        执行更新语句（INSERT/UPDATE/DELETE）
        
        Args:
            query: SQL 更新语句
            params: 语句参数
            
        Returns:
            int: 影响的行数
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    conn.commit()
                    affected = cur.rowcount
                    print(f"✓ 更新成功，影响 {affected} 行")
                    return affected
        except Exception as e:
            print(f"✗ 更新失败：{e}")
            return 0
    
    def execute_transaction(self, queries: List[tuple]) -> bool:
        """
        执行事务（多个 SQL 语句）
        
        Args:
            queries: [(query1, params1), (query2, params2), ...]
            
        Returns:
            bool: 事务是否成功
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for query, params in queries:
                        cur.execute(query, params)
                    conn.commit()
                    print(f"✓ 事务执行成功，共 {len(queries)} 条语句")
                    return True
        except Exception as e:
            print(f"✗ 事务执行失败，已回滚：{e}")
            return False


class MySQLAPI:
    """MySQL 数据库 API"""
    
    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接上下文管理器"""
        conn = None
        try:
            conn = mysql.connector.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            yield conn
        finally:
            if conn:
                conn.close()
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """执行查询语句"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(dictionary=True) as cur:
                    cur.execute(query, params)
                    results = cur.fetchall()
                    print(f"✓ 查询成功，返回 {len(results)} 条记录")
                    return results
        except Exception as e:
            print(f"✗ 查询失败：{e}")
            return []
    
    def execute_update(self, query: str, params: tuple = None) -> int:
        """执行更新语句"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    conn.commit()
                    affected = cur.rowcount
                    print(f"✓ 更新成功，影响 {affected} 行")
                    return affected
        except Exception as e:
            print(f"✗ 更新失败：{e}")
            return 0


# 使用示例
if __name__ == "__main__":
    # PostgreSQL 示例
    pg = PostgreSQLAPI(
        host="localhost",
        port=5432,
        database="myapp",
        user="postgres",
        password="password"
    )
    
    # 查询
    users = pg.execute_query("SELECT * FROM users WHERE status = %s", ("active",))
    print(f"活跃用户：{users}")
    
    # 更新
    pg.execute_update(
        "UPDATE users SET last_login = NOW() WHERE id = %s",
        (1,)
    )
    
    # 事务
    pg.execute_transaction([
        ("INSERT INTO orders (user_id, amount) VALUES (%s, %s)", (1, 100)),
        ("UPDATE users SET balance = balance - %s WHERE id = %s", (100, 1))
    ])
    
    # MySQL 示例
    mysql = MySQLAPI(
        host="localhost",
        port=3306,
        database="myapp",
        user="root",
        password="password"
    )
    
    results = mysql.execute_query("SELECT * FROM products LIMIT 10")
    print(f"产品列表：{results}")
