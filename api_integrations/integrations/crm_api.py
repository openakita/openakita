"""
CRM API 集成 - HubSpot
"""
from typing import List, Dict, Any, Optional
from .base_client import BaseAPIClient, APIError


class HubSpotClient(BaseAPIClient):
    """HubSpot CRM API 客户端"""
    
    def __init__(self, api_key: str):
        super().__init__(
            base_url="https://api.hubapi.com",
            api_key=api_key
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def create_contact(
        self,
        email: str,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建联系人"""
        properties = {"email": email}
        if firstname:
            properties["firstname"] = firstname
        if lastname:
            properties["lastname"] = lastname
        if phone:
            properties["phone"] = phone
        if company:
            properties["company"] = company
        
        return await self.post("/crm/v3/objects/contacts", json={"properties": properties})
    
    async def get_contact(self, contact_id: str) -> Dict[str, Any]:
        """获取联系人详情"""
        return await self.get(f"/crm/v3/objects/contacts/{contact_id}")
    
    async def update_contact(
        self,
        contact_id: str,
        properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新联系人"""
        return await self.patch(f"/crm/v3/objects/contacts/{contact_id}", json={"properties": properties})
    
    async def search_contacts(self, query: str) -> List[Dict[str, Any]]:
        """搜索联系人"""
        response = await self.post("/crm/v3/objects/contacts/search", json={
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "CONTAINS_TOKEN",
                    "value": query
                }]
            }]
        })
        return response.get("results", [])
    
    async def create_deal(
        self,
        deal_name: str,
        amount: Optional[float] = None,
        stage: str = "appointmentscheduled",
        close_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建交易"""
        properties = {
            "dealname": deal_name,
            "dealstage": stage
        }
        if amount:
            properties["amount"] = str(amount)
        if close_date:
            properties["closedate"] = close_date
        
        return await self.post("/crm/v3/objects/deals", json={"properties": properties})
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get("/crm/v3/objects/contacts?limit=1")
            return True
        except APIError:
            return False


# 使用示例
async def example_hubspot():
    """HubSpot 使用示例"""
    from config import APIConfig
    
    async with HubSpotClient(APIConfig.HUBSPOT_API_KEY) as client:
        # 创建联系人
        contact = await client.create_contact(
            email="customer@example.com",
            firstname="张",
            lastname="三",
            phone="13800138000",
            company="示例公司"
        )
        
        # 创建交易
        deal = await client.create_deal(
            deal_name="MVP 项目",
            amount=100000,
            stage="qualifiedtobuy",
            close_date="2026-06-01T00:00:00Z"
        )
