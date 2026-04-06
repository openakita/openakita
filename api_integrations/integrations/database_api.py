"""
数据库 API 集成 - Supabase
"""
from typing import List, Dict, Any, Optional
from .base_client import BaseAPIClient, APIError


class SupabaseClient(BaseAPIClient):
    """Supabase API 客户端"""
    
    def __init__(self, url: str, api_key: str):
        super().__init__(
            base_url=url.rstrip("/"),
            api_key=api_key
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "apikey": self.api_key
        }
    
    async def insert(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """插入数据"""
        return await self.post(f"/rest/v1/{table}", json=data)
    
    async def select(
        self,
        table: str,
        columns: str = "*",
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """查询数据"""
        params = {"select": columns}
        if filters:
            params.update(filters)
        
        response = await self.get(f"/rest/v1/{table}", params=params)
        return response if isinstance(response, list) else []
    
    async def update(
        self,
        table: str,
        data: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新数据"""
        # Supabase REST API 使用查询参数过滤
        return await self.patch(f"/rest/v1/{table}", json=data, params=filters)
    
    async def delete(self, table: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """删除数据"""
        return await self.delete(f"/rest/v1/{table}", params=filters)
    
    async def rpc(self, function_name: str, args: Dict[str, Any]) -> Any:
        """调用存储过程"""
        return await self.post(f"/rest/v1/rpc/{function_name}", json=args)
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get("/rest/v1/")
            return True
        except APIError:
            return False


# 使用示例
async def example_supabase():
    """Supabase 使用示例"""
    from config import APIConfig
    
    async with SupabaseClient(APIConfig.SUPABASE_URL, APIConfig.SUPABASE_KEY) as client:
        # 插入数据
        user = await client.insert("users", {
            "name": "张三",
            "email": "zhangsan@example.com",
            "age": 28
        })
        
        # 查询数据
        users = await client.select("users", columns="*", filters={"age.gte": 18})
        
        # 更新数据
        await client.update(
            "users",
            {"age": 29},
            {"email.eq": "zhangsan@example.com"}
        )
        
        # 删除数据
        await client.delete("users", {"email.eq": "zhangsan@example.com"})
