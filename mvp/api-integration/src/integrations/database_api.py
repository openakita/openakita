"""
数据库 API 集成
支持：PostgreSQL + pgvector 向量数据库
"""
import asyncpg
from typing import List, Dict, Any, Optional, Tuple
import logging
import json

from ..core.base import BaseAPIIntegration, APIConfig, APIResponse
from ..core.exceptions import AuthenticationError, ValidationError, ServiceUnavailableError
from ..core.config import config

logger = logging.getLogger(__name__)


class DatabaseAPIConfig(APIConfig):
    """数据库 API 配置"""
    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    user: str = "postgres"
    password: Optional[str] = None
    pool_size: int = 5
    enable_vector: bool = True  # 是否启用 pgvector


class DatabaseAPI(BaseAPIIntegration):
    """数据库 API"""
    
    def __init__(self, config: DatabaseAPIConfig):
        super().__init__(config)
        self.config: DatabaseAPIConfig = config
        self.pool: Optional[asyncpg.Pool] = None
    
    async def initialize(self) -> None:
        """初始化连接池"""
        self._validate_config()
        
        try:
            self.pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password,
                min_size=2,
                max_size=self.config.pool_size,
                command_timeout=self.config.timeout
            )
            
            # 初始化 pgvector 扩展
            if self.config.enable_vector:
                await self._init_vector_extension()
                
        except Exception as e:
            logger.error(f"数据库初始化失败：{e}")
            raise ServiceUnavailableError(f"数据库连接失败：{str(e)}")
    
    async def close(self) -> None:
        """关闭连接池"""
        if self.pool:
            await self.pool.close()
    
    def get_required_fields(self) -> list:
        """获取必需配置字段"""
        return ['host', 'database', 'user', 'password']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行数据库操作
        
        Actions:
            - query: 执行查询
                参数：sql, params
            - execute: 执行插入/更新/删除
                参数：sql, params
            - vector_search: 向量相似度搜索
                参数：table, column, query_vector, top_k
            - vector_insert: 插入向量数据
                参数：table, data, vector
            - create_table: 创建表
                参数：table_name, schema
        
        Returns:
            APIResponse
        """
        try:
            actions = {
                "query": self._query,
                "execute": self._execute,
                "vector_search": self._vector_search,
                "vector_insert": self._vector_insert,
                "create_table": self._create_table,
            }
            
            if action not in actions:
                raise ValidationError(f"不支持的操作：{action}")
            
            return await actions[action](**kwargs)
        except Exception as e:
            logger.error(f"数据库操作失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=500
            )
    
    async def _init_vector_extension(self) -> None:
        """初始化 pgvector 扩展"""
        async with self.pool.acquire() as conn:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
            logger.info("pgvector 扩展已初始化")
    
    async def _query(self, sql: str, params: Optional[Tuple] = None, fetch_all: bool = True) -> APIResponse:
        """执行查询"""
        async with self.pool.acquire() as conn:
            if fetch_all:
                rows = await conn.fetch(sql, *(params or []))
                data = [dict(row) for row in rows]
            else:
                row = await conn.fetchrow(sql, *(params or []))
                data = dict(row) if row else None
            
            return APIResponse(
                success=True,
                data=data,
                status_code=200
            )
    
    async def _execute(self, sql: str, params: Optional[Tuple] = None) -> APIResponse:
        """执行插入/更新/删除"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(sql, *(params or []))
            
            # 解析结果 (如 "INSERT 0 1")
            parts = result.split()
            rows_affected = int(parts[-1]) if parts[-1].isdigit() else 0
            
            return APIResponse(
                success=True,
                data={"rows_affected": rows_affected, "command": parts[0]},
                status_code=200
            )
    
    async def _vector_search(
        self,
        table: str,
        vector_column: str,
        query_vector: List[float],
        top_k: int = 5,
        filter_conditions: Optional[str] = None,
        return_columns: str = "*"
    ) -> APIResponse:
        """向量相似度搜索"""
        # 构建查询
        vector_str = f"[{','.join(map(str, query_vector))}]"
        
        sql = f"""
            SELECT {return_columns}, 
                   {vector_column} <-> $1::vector AS similarity
            FROM {table}
        """
        
        params = [vector_str]
        
        if filter_conditions:
            sql += f" WHERE {filter_conditions}"
        
        sql += f" ORDER BY {vector_column} <-> $1::vector LIMIT {top_k}"
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, vector_str)
            data = [dict(row) for row in rows]
            
            return APIResponse(
                success=True,
                data={"results": data, "count": len(data)},
                status_code=200
            )
    
    async def _vector_insert(
        self,
        table: str,
        data: Dict[str, Any],
        vector: Optional[List[float]] = None,
        vector_column: str = "embedding"
    ) -> APIResponse:
        """插入向量数据"""
        if vector:
            data[vector_column] = f"[{','.join(map(str, vector))}]"
        
        columns = list(data.keys())
        values = list(data.values())
        placeholders = [f"${i+1}" for i in range(len(values))]
        
        # 转换 vector 为 pgvector 格式
        for i, val in enumerate(values):
            if isinstance(val, list) and all(isinstance(x, float) for x in val):
                values[i] = f"[{','.join(map(str, val))}]"
        
        sql = f"""
            INSERT INTO {table} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            RETURNING id
        """
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)
            
            return APIResponse(
                success=True,
                data={"id": row['id'] if row else None},
                status_code=201
            )
    
    async def _create_table(
        self,
        table_name: str,
        schema: Dict[str, str],
        vector_columns: Optional[List[str]] = None,
        if_not_exists: bool = True
    ) -> APIResponse:
        """创建表"""
        columns = []
        
        for col_name, col_type in schema.items():
            columns.append(f"{col_name} {col_type}")
        
        # 添加 vector 列
        if vector_columns:
            for col in vector_columns:
                if col not in schema:
                    columns.append(f"{col} vector")
        
        exists_clause = "IF NOT EXISTS " if if_not_exists else ""
        sql = f"CREATE TABLE {exists_clause}{table_name} ({', '.join(columns)})"
        
        async with self.pool.acquire() as conn:
            await conn.execute(sql)
            
            return APIResponse(
                success=True,
                data={"table_name": table_name},
                status_code=200
            )


# 工厂函数
def create_database_api(
    host: Optional[str] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    port: int = 5432,
    enable_vector: bool = True
) -> DatabaseAPI:
    """创建数据库 API 实例"""
    return DatabaseAPI(DatabaseAPIConfig(
        host=host or config.get("DB_HOST", "localhost"),
        port=port or config.get("DB_PORT", 5432),
        database=database or config.get("DB_NAME", "postgres"),
        user=user or config.get("DB_USER", "postgres"),
        password=password or config.get("DB_PASSWORD"),
        enable_vector=enable_vector
    ))
