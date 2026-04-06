"""
数据库 API - PostgreSQL + pgvector
支持向量存储和相似性搜索
"""

from typing import Dict, Any, List, Optional
import logging
from .base import BaseAPI, APIResponse, APIMode
import time

logger = logging.getLogger(__name__)


class DatabaseAPI(BaseAPI):
    """PostgreSQL + pgvector 数据库 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
        self.mock_vectors = []
        self.mock_data = {
            "documents": [
                {"id": 1, "content": "产品文档 V1.0", "embedding": [0.1] * 1536, "created_at": "2024-01-15"},
                {"id": 2, "content": "用户手册", "embedding": [0.2] * 1536, "created_at": "2024-02-20"},
                {"id": 3, "content": "API 参考指南", "embedding": [0.3] * 1536, "created_at": "2024-03-10"},
            ]
        }
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟数据库操作"""
        action = kwargs.get('action', 'query')
        
        try:
            if action == 'insert':
                data = kwargs.get('data', {})
                new_id = len(self.mock_data['documents']) + 1
                new_record = {
                    'id': new_id,
                    **data,
                    'created_at': time.strftime('%Y-%m-%d')
                }
                self.mock_data['documents'].append(new_record)
                return APIResponse(success=True, data={'id': new_id}, status_code=201)
            
            elif action == 'query':
                sql = kwargs.get('sql', 'SELECT * FROM documents LIMIT 10')
                return APIResponse(
                    success=True,
                    data={
                        'rows': self.mock_data['documents'][:10],
                        'count': len(self.mock_data['documents'])
                    }
                )
            
            elif action == 'vector_search':
                query_vector = kwargs.get('query_vector', [])
                top_k = kwargs.get('top_k', 5)
                # 模拟向量相似度搜索（随机返回）
                import random
                results = random.sample(self.mock_data['documents'], min(top_k, len(self.mock_data['documents'])))
                for r in results:
                    r['similarity'] = round(random.uniform(0.7, 0.99), 4)
                return APIResponse(
                    success=True,
                    data={'results': results, 'total': len(results)}
                )
            
            elif action == 'execute':
                sql = kwargs.get('sql', '')
                logger.info(f"[MOCK] 执行 SQL: {sql}")
                return APIResponse(success=True, data={'rows_affected': 0})
            
            else:
                return APIResponse(success=False, data=None, error=f"未知操作：{action}", status_code=400)
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用 - 使用 psycopg2"""
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            
            action = kwargs.get('action', 'query')
            db_url = self._config.get('DATABASE_URL')
            
            conn = psycopg2.connect(db_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            if action == 'insert':
                table = kwargs.get('table', 'documents')
                data = kwargs.get('data', {})
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))
                sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id"
                cur.execute(sql, list(data.values()))
                result = cur.fetchone()
                conn.commit()
                cur.close()
                conn.close()
                return APIResponse(success=True, data=dict(result))
            
            elif action == 'query':
                sql = kwargs.get('sql', 'SELECT * FROM documents LIMIT 10')
                cur.execute(sql)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                return APIResponse(success=True, data={'rows': [dict(r) for r in rows], 'count': len(rows)})
            
            elif action == 'vector_search':
                query_vector = kwargs.get('query_vector', [])
                top_k = kwargs.get('top_k', 5)
                vector_str = '[' + ','.join(map(str, query_vector)) + ']'
                sql = f"""
                    SELECT id, content, 1 - (embedding <=> %s::vector) as similarity
                    FROM documents
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """
                cur.execute(sql, (vector_str, vector_str, top_k))
                rows = cur.fetchall()
                cur.close()
                conn.close()
                return APIResponse(success=True, data={'results': [dict(r) for r in rows], 'total': len(rows)})
            
            else:
                cur.close()
                conn.close()
                return APIResponse(success=False, data=None, error=f"不支持的操作：{action}", status_code=400)
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def insert(self, table: str, data: Dict[str, Any]) -> APIResponse:
        """插入数据"""
        return self.call(action='insert', table=table, data=data)
    
    def query(self, sql: str) -> APIResponse:
        """执行查询"""
        return self.call(action='query', sql=sql)
    
    def vector_search(self, query_vector: List[float], top_k: int = 5) -> APIResponse:
        """向量相似性搜索"""
        return self.call(action='vector_search', query_vector=query_vector, top_k=top_k)


def test_database_api():
    """数据库 API 测试"""
    print("=" * 50)
    print("数据库 API 测试")
    print("=" * 50)
    
    api = DatabaseAPI(mode=APIMode.MOCK)
    
    print("\n[测试 1] 查询数据")
    result = api.query("SELECT * FROM documents")
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"记录数：{result.data.get('count', 0)}")
    
    print("\n[测试 2] 插入数据")
    result = api.insert('documents', {
        'content': '测试文档',
        'embedding': [0.5] * 1536
    })
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    
    print("\n[测试 3] 向量搜索")
    result = api.vector_search([0.4] * 1536, top_k=3)
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"匹配结果数：{result.data.get('total', 0)}")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_database_api()
