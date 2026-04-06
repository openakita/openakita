"""
文档 API 集成 - Notion
"""
from typing import List, Dict, Any, Optional
from .base_client import BaseAPIClient, APIError


class NotionClient(BaseAPIClient):
    """Notion API 客户端"""
    
    def __init__(self, api_key: str):
        super().__init__(
            base_url="https://api.notion.com/v1",
            api_key=api_key
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
    
    async def create_page(
        self,
        parent_database_id: str,
        title: str,
        properties: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """创建页面"""
        payload = {
            "parent": {"database_id": parent_database_id},
            "properties": {
                "Name": {
                    "title": [{"text": {"content": title}}]
                }
            }
        }
        
        if properties:
            payload["properties"].update(properties)
        
        return await self.post("/pages", json=payload)
    
    async def get_page(self, page_id: str) -> Dict[str, Any]:
        """获取页面详情"""
        return await self.get(f"/pages/{page_id}")
    
    async def update_page(
        self,
        page_id: str,
        properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新页面"""
        return await self.patch(f"/pages/{page_id}", json={"properties": properties})
    
    async def query_database(
        self,
        database_id: str,
        filter_criteria: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """查询数据库"""
        payload = {}
        if filter_criteria:
            payload["filter"] = filter_criteria
        
        response = await self.post(f"/databases/{database_id}/query", json=payload)
        return response.get("results", [])
    
    async def append_block_children(
        self,
        block_id: str,
        children: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """追加内容块"""
        return await self.patch(f"/blocks/{block_id}/children", json={"children": children})
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get("/users/me")
            return True
        except APIError:
            return False


# 使用示例
async def example_notion():
    """Notion 使用示例"""
    from config import APIConfig
    
    async with NotionClient(APIConfig.NOTION_API_KEY) as client:
        # 创建页面
        page = await client.create_page(
            parent_database_id=APIConfig.NOTION_DATABASE_ID,
            title="项目文档",
            properties={
                "Status": {"select": {"name": "进行中"}}
            }
        )
        
        # 添加内容块
        await client.append_block_children(
            block_id=page["id"],
            children=[
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": "项目概述"}}]}
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": "这是项目描述内容"}}]}
                }
            ]
        )
