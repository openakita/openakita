# Qdrant 向量数据库 API 集成示例
# 适用于 MVP 语义搜索、推荐系统、RAG 应用

import os
from typing import List, Dict, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import Distance, VectorParams, PointStruct


class QdrantClientWrapper:
    """Qdrant 向量数据库客户端封装"""
    
    def __init__(self):
        self.url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = os.getenv("QDRANT_API_KEY", None)
        self.client = QdrantClient(url=self.url, api_key=self.api_key)
    
    def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1536,
        distance: str = "Cosine"
    ) -> dict:
        """
        创建集合
        
        Args:
            collection_name: 集合名称
            vector_size: 向量维度
            distance: 距离度量（Cosine, Euclid, Dot）
        
        Returns:
            创建结果
        """
        try:
            # 检查集合是否已存在
            collections = self.client.get_collections().collections
            if any(c.name == collection_name for c in collections):
                return {"success": False, "error": "Collection already exists"}
            
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance[distance.upper()]
                )
            )
            return {"success": True, "collection_name": collection_name}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def upsert_points(
        self,
        collection_name: str,
        points: List[Dict],
        batch_size: int = 100
    ) -> dict:
        """
        插入/更新向量点
        
        Args:
            collection_name: 集合名称
            points: 点列表 [{"id": 1, "vector": [...], "payload": {...}}]
            batch_size: 批次大小
        
        Returns:
            插入结果
        """
        try:
            points_struct = [
                PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point.get("payload", {})
                )
                for point in points
            ]
            
            operation_info = self.client.upsert(
                collection_name=collection_name,
                points=points_struct,
                wait=True
            )
            return {
                "success": True,
                "status": operation_info.status,
                "count": len(points)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 10,
        filter_dict: Dict = None
    ) -> dict:
        """
        向量搜索
        
        Args:
            collection_name: 集合名称
            query_vector: 查询向量
            limit: 返回数量
            filter_dict: 过滤条件
        
        Returns:
            搜索结果
        """
        try:
            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                query_filter=models.Filter(**filter_dict) if filter_dict else None
            )
            
            hits = [
                {
                    "id": hit.id,
                    "score": hit.score,
                    "payload": hit.payload
                }
                for hit in results
            ]
            return {"success": True, "hits": hits, "count": len(hits)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete_points(
        self,
        collection_name: str,
        points_ids: List[int]
    ) -> dict:
        """
        删除向量点
        
        Args:
            collection_name: 集合名称
            points_ids: 点 ID 列表
        
        Returns:
            删除结果
        """
        try:
            operation_info = self.client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=points_ids)
            )
            return {
                "success": True,
                "status": operation_info.status,
                "deleted_count": len(points_ids)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_point(
        self,
        collection_name: str,
        point_id: int
    ) -> dict:
        """
        获取单个点
        
        Args:
            collection_name: 集合名称
            point_id: 点 ID
        
        Returns:
            点信息
        """
        try:
            point = self.client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=True,
                with_vectors=False
            )
            if point:
                return {"success": True, "point": point[0]}
            return {"success": False, "error": "Point not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def scroll(
        self,
        collection_name: str,
        limit: int = 10,
        offset: int = None,
        filter_dict: Dict = None
    ) -> dict:
        """
        滚动查询（分页）
        
        Args:
            collection_name: 集合名称
            limit: 每页数量
            offset: 偏移量
            filter_dict: 过滤条件
        
        Returns:
            查询结果
        """
        try:
            results, next_offset = self.client.scroll(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                query_filter=models.Filter(**filter_dict) if filter_dict else None,
                with_payload=True,
                with_vectors=False
            )
            
            points = [
                {
                    "id": point.id,
                    "payload": point.payload,
                    "vector": point.vector if point.vector else None
                }
                for point in results
            ]
            return {
                "success": True,
                "points": points,
                "next_offset": next_offset,
                "count": len(points)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete_collection(self, collection_name: str) -> dict:
        """
        删除集合
        
        Args:
            collection_name: 集合名称
        
        Returns:
            删除结果
        """
        try:
            self.client.delete_collection(collection_name=collection_name)
            return {"success": True, "collection_name": collection_name}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def collection_info(self, collection_name: str) -> dict:
        """
        获取集合信息
        
        Args:
            collection_name: 集合名称
        
        Returns:
            集合信息
        """
        try:
            info = self.client.get_collection(collection_name=collection_name)
            return {
                "success": True,
                "info": {
                    "vector_count": info.vectors_count,
                    "point_count": info.points_count,
                    "status": info.status,
                    "vectors_count": info.vectors_count
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# 使用示例
if __name__ == "__main__":
    import random
    
    client = QdrantClientWrapper()
    
    # 1. 创建集合
    result = client.create_collection(
        collection_name="documents",
        vector_size=1536,
        distance="Cosine"
    )
    print(f"创建集合：{result}")
    
    # 2. 插入向量点
    if result["success"]:
        points = [
            {
                "id": i,
                "vector": [random.random() for _ in range(1536)],
                "payload": {"text": f"Document {i}", "category": "tech"}
            }
            for i in range(10)
        ]
        result = client.upsert_points("documents", points)
        print(f"插入结果：{result}")
    
    # 3. 向量搜索
    query_vector = [random.random() for _ in range(1536)]
    result = client.search(
        collection_name="documents",
        query_vector=query_vector,
        limit=5
    )
    print(f"搜索结果：{result}")
    
    # 4. 获取集合信息
    result = client.collection_info("documents")
    print(f"集合信息：{result}")
