"""
8. 数据库 API - PostgreSQL
支持连接、查询、插入、更新、删除等操作
"""

from typing import List, Dict, Any, Optional, Tuple
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus

try:
    import psycopg2
    from psycopg2 import sql, extras
except ImportError:
    psycopg2 = None


class PostgreSQLAdapter(BaseAPIAdapter):
    """PostgreSQL 数据库适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - host: 数据库主机
        - port: 端口 (默认 5432)
        - database: 数据库名
        - user: 用户名
        - password: 密码
        - sslmode: SSL 模式 (可选)
        """
        super().__init__(config)
        self.conn = None
        self.cur = None
    
    def connect(self) -> bool:
        try:
            if psycopg2 is None:
                print("错误：请先安装 psycopg2 库 (pip install psycopg2-binary)")
                return False
            
            self.conn = psycopg2.connect(
                host=self.config.get('host', 'localhost'),
                port=self.config.get('port', 5432),
                database=self.config['database'],
                user=self.config['user'],
                password=self.config['password'],
                sslmode=self.config.get('sslmode', 'prefer')
            )
            self.cur = self.conn.cursor()
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()
        self.cur = None
        self.conn = None
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "query":
            return self.query(params)
        elif action == "insert":
            return self.insert(params)
        elif action == "update":
            return self.update(params)
        elif action == "delete":
            return self.delete(params)
        elif action == "execute_raw":
            return self.execute_raw(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def query(self, params: dict) -> APIResponse:
        """
        执行查询
        
        参数:
        - sql: SQL 查询语句
        - params: 查询参数 (可选，用于防止 SQL 注入)
        - fetch: 获取方式 (one/many/all，默认 all)
        """
        try:
            self.cur.execute(params['sql'], params.get('params'))
            
            fetch_mode = params.get('fetch', 'all')
            if fetch_mode == 'one':
                data = self.cur.fetchone()
            elif fetch_mode == 'many':
                data = self.cur.fetchmany(params.get('size', 10))
            else:
                data = self.cur.fetchall()
            
            # 转换为字典列表
            if data:
                columns = [desc[0] for desc in self.cur.description]
                if fetch_mode == 'one':
                    data = dict(zip(columns, data))
                else:
                    data = [dict(zip(columns, row)) for row in data]
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'rows': data, 'count': self.cur.rowcount},
                status_code=200
            )
        except Exception as e:
            self.conn.rollback()
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def insert(self, params: dict) -> APIResponse:
        """
        插入数据
        
        参数:
        - table: 表名
        - data: 数据字典
        - returning: 返回字段 (可选，如 'id')
        """
        try:
            table = params['table']
            data = params['data']
            
            columns = list(data.keys())
            values = list(data.values())
            placeholders = ','.join(['%s'] * len(columns))
            columns_sql = ','.join(columns)
            
            sql_str = f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})"
            
            if params.get('returning'):
                sql_str += f" RETURNING {params['returning']}"
            
            self.cur.execute(sql_str, values)
            self.conn.commit()
            
            result = {'rowcount': self.cur.rowcount}
            if params.get('returning'):
                result['returning'] = self.cur.fetchone()[0]
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data=result,
                status_code=200
            )
        except Exception as e:
            self.conn.rollback()
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def update(self, params: dict) -> APIResponse:
        """
        更新数据
        
        参数:
        - table: 表名
        - data: 更新数据字典
        - where: WHERE 条件 (如 "id = %s")
        - where_params: WHERE 参数
        """
        try:
            table = params['table']
            data = params['data']
            
            set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
            values = list(data.values())
            
            sql_str = f"UPDATE {table} SET {set_clause}"
            
            if params.get('where'):
                sql_str += f" WHERE {params['where']}"
                values.extend(params.get('where_params', []))
            
            self.cur.execute(sql_str, values)
            self.conn.commit()
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'rowcount': self.cur.rowcount},
                status_code=200
            )
        except Exception as e:
            self.conn.rollback()
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def delete(self, params: dict) -> APIResponse:
        """
        删除数据
        
        参数:
        - table: 表名
        - where: WHERE 条件
        - where_params: WHERE 参数
        """
        try:
            table = params['table']
            
            sql_str = f"DELETE FROM {table}"
            values = []
            
            if params.get('where'):
                sql_str += f" WHERE {params['where']}"
                values = params.get('where_params', [])
            
            self.cur.execute(sql_str, values)
            self.conn.commit()
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'rowcount': self.cur.rowcount},
                status_code=200
            )
        except Exception as e:
            self.conn.rollback()
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def execute_raw(self, params: dict) -> APIResponse:
        """
        执行原始 SQL
        
        参数:
        - sql: SQL 语句
        - params: 参数列表
        - commit: 是否提交 (默认 True)
        """
        try:
            self.cur.execute(params['sql'], params.get('params', []))
            
            if params.get('commit', True):
                self.conn.commit()
            
            # 尝试获取结果
            try:
                data = self.cur.fetchall()
                columns = [desc[0] for desc in self.cur.description]
                data = [dict(zip(columns, row)) for row in data]
            except:
                data = None
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'rows': data, 'rowcount': self.cur.rowcount},
                status_code=200
            )
        except Exception as e:
            self.conn.rollback()
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


# ============ 使用示例 ============
if __name__ == "__main__":
    config = {
        'host': 'localhost',
        'port': 5432,
        'database': 'test_db',
        'user': 'postgres',
        'password': 'your_password'
    }
    
    db = PostgreSQLAdapter(config)
    
    if db.connect():
        print("✅ PostgreSQL 连接成功")
        
        # 查询示例
        response = db.execute('query', {
            'sql': 'SELECT * FROM users WHERE status = %s',
            'params': ['active'],
            'fetch': 'all'
        })
        
        if response.is_success():
            print(f"✅ 查询成功：{len(response.data['rows'])} 条记录")
        else:
            print(f"❌ 查询失败：{response.error}")
        
        db.disconnect()
    else:
        print("❌ PostgreSQL 连接失败")
