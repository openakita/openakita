"""
Qdrant 向量数据库集成示例
用于 MVP AI 语义搜索、向量检索等场景
"""
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from typing import List, Dict, Optional
import numpy as np


class QdrantVectorClient:
    """
    Qdrant 向量数据库客户端
    
    使用场景:
    - AI 语义搜索
    - 相似内容推荐
    - 向量检索
    - 知识库检索
    """
    
    def __init__(self, url: str = "http://localhost:6333", 
                 api_key: Optional[str] = None,
                 prefer_grpc: bool = False):
        """
        初始化 Qdrant 客户端
        
        Args:
            url: Qdrant 服务地址
            api_key: API Key（可选）
            prefer_grpc: 是否优先使用 gRPC
        """
        self.client = QdrantClient(
            url=url,
            api_key=api_key,
            prefer_grpc=prefer_grpc
        )
    
    def create_collection(self, collection_name: str, 
                         vector_size: int = 768,
                         distance: str = "Cosine") -> Dict:
        """
        创建集合
        
        Args:
            collection_name: 集合名称
            vector_size: 向量维度
            distance: 距离度量方式（Cosine, Euclid, Dot）
        
        Returns:
            创建结果
        """
        try:
            distance_map = {
                "Cosine": Distance.COSINE,
                "Euclid": Distance.EUCLID,
                "Dot": Distance.DOT
            }
            
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=distance_map.get(distance, Distance.COSINE)
                )
            )
            
            return {
                "success": True,
                "collection_name": collection_name,
                "vector_size": vector_size,
                "distance": distance
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def upsert_points(self, collection_name: str, 
                      points: List[Dict]) -> Dict:
        """
        插入/更新向量点
        
        Args:
            collection_name: 集合名称
            points: 向量点列表，每项包含：
                - id: 点 ID
                - vector: 向量数组
                - payload: 元数据（可选）
        
        Returns:
            插入结果
        """
        try:
            point_structs = []
            for point in points:
                point_structs.append(PointStruct(
                    id=point["id"],
                    vector=point["vector"],
                    payload=point.get("payload", {})
                ))
            
            result = self.client.upsert(
                collection_name=collection_name,
                points=point_structs
            )
            
            return {
                "success": True,
                "inserted_count": len(points),
                "status": result.status
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def search_vectors(self, collection_name: str, 
                       query_vector: List[float],
                       limit: int = 10,
                       score_threshold: Optional[float] = None,
                       filter_dict: Optional[Dict] = None) -> Dict:
        """
        向量搜索
        
        Args:
            collection_name: 集合名称
            query_vector: 查询向量
            limit: 返回结果数量
            score_threshold: 分数阈值
            filter_dict: 过滤条件
        
        Returns:
            搜索结果
        """
        try:
            # 构建过滤条件
            query_filter = None
            if filter_dict:
                conditions = []
                for key, value in filter_dict.items():
                    conditions.append(FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    ))
                query_filter = Filter(must=conditions)
            
            # 执行搜索
            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold
            )
            
            # 格式化结果
            search_results = []
            for hit in results:
                search_results.append({
                    "id": hit.id,
                    "score": hit.score,
                    "payload": hit.payload,
                    "vector": hit.vector
                })
            
            return {
                "success": True,
                "count": len(search_results),
                "results": search_results
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete_points(self, collection_name: str, 
                      point_ids: List[int]) -> Dict:
        """
        删除向量点
        
        Args:
            collection_name: 集合名称
            point_ids: 要删除的点 ID 列表
        
        Returns:
            删除结果
        """
        try:
            result = self.client.delete(
                collection_name=collection_name,
                points_selector=point_ids
            )
            
            return {
                "success": True,
                "deleted_count": len(point_ids),
                "status": result.status
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_collection_info(self, collection_name: str) -> Dict:
        """
        获取集合信息
        
        Args:
            collection_name: 集合名称
        
        Returns:
            集合信息
        """
        try:
            info = self.client.get_collection(collection_name)
            return {
                "success": True,
                "info": {
                    "vector_count": info.vectors_count,
                    "point_count": info.points_count,
                    "status": info.status,
                    "config": {
                        "vector_size": info.config.params.vector_size,
                        "distance": info.config.params.distance
                    }
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete_collection(self, collection_name: str) -> Dict:
        """删除集合"""
        try:
            self.client.delete_collection(collection_name)
            return {"success": True, "collection_name": collection_name}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============== 使用示例 ==============
if __name__ == "__main__":
    print("=== Qdrant 向量数据库示例 ===")
    
    # 初始化客户端
    client = QdrantVectorClient(url="http://localhost:6333")
    
    # 示例 1: 创建集合
    print("\n1. 创建集合")
    result = client.create_collection(
        collection_name="mvp_documents",
        vector_size=768,
        distance="Cosine"
    )
    print(f"结果：{result}")
    
    # 示例 2: 插入向量
    print("\n2. 插入向量")
    points = [
        {
            "id": 1,
            "vector": np.random.rand(768).tolist(),
            "payload": {"text": "文档 1", "category": "tech"}
        },
        {
            "id": 2,
            "vector": np.random.rand(768).tolist(),
            "payload": {"text": "文档 2", "category": "business"}
        }
    ]
    result = client.upsert_points("mvp_documents", points)
    print(f"结果：{result}")
    
    # 示例 3: 向量搜索
    print("\n3. 向量搜索")
    query_vector = np.random.rand(768).tolist()
    result = client.search_vectors(
        collection_name="mvp_documents",
        query_vector=query_vector,
        limit=5
    )
    print(f"结果：{result}")
    
    # 示例 4: 获取集合信息
    print("\n4. 获取集合信息")
    result = client.get_collection_info("mvp_documents")
    print(f"结果：{result}")
