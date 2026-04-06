"""
Salesforce API 客户端
支持客户管理、销售机会、联系人管理、自定义对象等功能
文档：https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/
"""
from typing import List, Optional, Dict, Any
from .base import BaseAPIClient, APIError, AuthenticationError
import structlog

logger = structlog.get_logger()


class SalesforceClient(BaseAPIClient):
    """Salesforce API 客户端"""
    
    def __init__(self, instance_url: str, access_token: str, api_version: str = "v59.0"):
        """
        初始化 Salesforce 客户端
        
        Args:
            instance_url: Salesforce 实例 URL (如 https://your-instance.salesforce.com)
            access_token: OAuth 访问令牌
            api_version: API 版本
        """
        super().__init__(
            base_url=f"{instance_url.rstrip('/')}/services/data/{api_version}",
            api_key=access_token,
            timeout=30
        )
        self.instance_url = instance_url
        self.access_token = access_token
        self.api_version = api_version
    
    def _get_auth_header(self) -> str:
        """Salesforce 使用 Bearer Token 认证"""
        return f"Bearer {self.access_token}"
    
    async def test_auth(self) -> bool:
        """测试认证是否有效"""
        try:
            response = await self.get("/limits")
            return "DailyApiRequests" in response
        except APIError:
            return False
    
    async def get_sobject_describe(self, sobject_name: str) -> Dict[str, Any]:
        """获取对象元数据"""
        response = await self.get(f"/sobjects/{sobject_name}/describe")
        return response
    
    async def create_record(
        self,
        sobject_name: str,
        fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        创建记录
        
        Args:
            sobject_name: 对象名称 (如 Account, Contact, Opportunity)
            fields: 字段数据
            
        Returns:
            创建结果 (包含 id)
        """
        response = await self.post(f"/sobjects/{sobject_name}", json_data=fields)
        
        logger.info("salesforce_record_created", sobject=sobject_name, record_id=response.get("id"))
        return response
    
    async def get_record(self, sobject_name: str, record_id: str) -> Dict[str, Any]:
        """获取记录详情"""
        response = await self.get(f"/sobjects/{sobject_name}/{record_id}")
        return response
    
    async def update_record(
        self,
        sobject_name: str,
        record_id: str,
        fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新记录"""
        response = await self.patch(f"/sobjects/{sobject_name}/{record_id}", json_data=fields)
        
        logger.info("salesforce_record_updated", sobject=sobject_name, record_id=record_id)
        return response
    
    async def delete_record(self, sobject_name: str, record_id: str) -> Dict[str, Any]:
        """删除记录"""
        response = await self.delete(f"/sobjects/{sobject_name}/{record_id}")
        
        logger.info("salesforce_record_deleted", sobject=sobject_name, record_id=record_id)
        return response
    
    async def query(self, soql: str) -> Dict[str, Any]:
        """
        执行 SOQL 查询
        
        Args:
            soql: SOQL 查询语句
            
        Returns:
            查询结果
        """
        response = await self.get("/query", params={"q": soql})
        
        logger.info("salesforce_query_executed", records_count=len(response.get("records", [])))
        return response
    
    async def query_more(self, next_records_url: str) -> Dict[str, Any]:
        """获取分页的更多结果"""
        # next_records_url 是完整 URL，需要提取路径
        path = next_records_url.split(self.instance_url)[1]
        response = await self.get(path)
        return response
    
    async def create_account(
        self,
        name: str,
        type: Optional[str] = None,
        industry: Optional[str] = None,
        phone: Optional[str] = None,
        website: Optional[str] = None,
        billing_street: Optional[str] = None,
        billing_city: Optional[str] = None,
        billing_state: Optional[str] = None,
        billing_postal_code: Optional[str] = None,
        billing_country: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建客户账户
        
        Args:
            name: 账户名称
            type: 类型
            industry: 行业
            phone: 电话
            website: 网站
            billing_street: 账单街道
            billing_city: 账单城市
            billing_state: 账单州/省
            billing_postal_code: 账单邮编
            billing_country: 账单国家
            description: 描述
            
        Returns:
            创建结果
        """
        fields = {
            "Name": name
        }
        
        if type:
            fields["Type"] = type
        if industry:
            fields["Industry"] = industry
        if phone:
            fields["Phone"] = phone
        if website:
            fields["Website"] = website
        if billing_street:
            fields["BillingStreet"] = billing_street
        if billing_city:
            fields["BillingCity"] = billing_city
        if billing_state:
            fields["BillingState"] = billing_state
        if billing_postal_code:
            fields["BillingPostalCode"] = billing_postal_code
        if billing_country:
            fields["BillingCountry"] = billing_country
        if description:
            fields["Description"] = description
        
        return await self.create_record("Account", fields)
    
    async def get_account(self, account_id: str) -> Dict[str, Any]:
        """获取账户详情"""
        return await self.get_record("Account", account_id)
    
    async def update_account(self, account_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """更新账户"""
        return await self.update_record("Account", account_id, fields)
    
    async def create_contact(
        self,
        account_id: str,
        first_name: str,
        last_name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        title: Optional[str] = None,
        department: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建联系人
        
        Args:
            account_id: 关联的账户 ID
            first_name: 名
            last_name: 姓
            email: 邮箱
            phone: 电话
            title: 职位
            department: 部门
            
        Returns:
            创建结果
        """
        fields = {
            "AccountId": account_id,
            "FirstName": first_name,
            "LastName": last_name
        }
        
        if email:
            fields["Email"] = email
        if phone:
            fields["Phone"] = phone
        if title:
            fields["Title"] = title
        if department:
            fields["Department"] = department
        
        return await self.create_record("Contact", fields)
    
    async def get_contact(self, contact_id: str) -> Dict[str, Any]:
        """获取联系人详情"""
        return await self.get_record("Contact", contact_id)
    
    async def create_opportunity(
        self,
        account_id: str,
        name: str,
        amount: float,
        close_date: str,
        stage_name: str = "Prospecting",
        probability: Optional[int] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建销售机会
        
        Args:
            account_id: 关联的账户 ID
            name: 机会名称
            amount: 金额
            close_date: 预计关闭日期 (YYYY-MM-DD)
            stage_name: 阶段名称
            probability: 成功概率 (%)
            description: 描述
            
        Returns:
            创建结果
        """
        fields = {
            "AccountId": account_id,
            "Name": name,
            "Amount": amount,
            "CloseDate": close_date,
            "StageName": stage_name
        }
        
        if probability:
            fields["Probability"] = probability
        if description:
            fields["Description"] = description
        
        return await self.create_record("Opportunity", fields)
    
    async def get_opportunity(self, opportunity_id: str) -> Dict[str, Any]:
        """获取销售机会详情"""
        return await self.get_record("Opportunity", opportunity_id)
    
    async def update_opportunity_stage(
        self,
        opportunity_id: str,
        stage_name: str,
        probability: Optional[int] = None
    ) -> Dict[str, Any]:
        """更新销售机会阶段"""
        fields = {"StageName": stage_name}
        if probability:
            fields["Probability"] = probability
        return await self.update_record("Opportunity", opportunity_id, fields)
    
    async def search(self, search_string: str, sobject_name: Optional[str] = None) -> Dict[str, Any]:
        """
        SOSL 搜索
        
        Args:
            search_string: 搜索字符串
            sobject_name: 限定对象类型（可选）
            
        Returns:
            搜索结果
        """
        if sobject_name:
            soql = f"FIND '{search_string}' IN ALL FIELDS RETURNING {sobject_name}"
        else:
            soql = f"FIND '{search_string}' IN ALL FIELDS"
        
        response = await self.get("/search", params={"q": soql})
        return response
    
    async def get_recent_records(
        self,
        sobject_name: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """获取最近的记录"""
        response = await self.get(f"/sobjects/{sobject_name}/recent?limit={limit}")
        return response


# 使用示例
async def example_usage():
    """Salesforce API 使用示例"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    instance_url = os.getenv("SALESFORCE_INSTANCE_URL")
    access_token = os.getenv("SALESFORCE_ACCESS_TOKEN")
    
    if not instance_url or not access_token:
        print("❌ 请设置 SALESFORCE_INSTANCE_URL 和 SALESFORCE_ACCESS_TOKEN 环境变量")
        return
    
    async with SalesforceClient(instance_url, access_token) as client:
        # 测试认证
        is_valid = await client.test_auth()
        print(f"✅ 认证有效：{is_valid}")
        
        # 创建账户
        account = await client.create_account(
            name="测试公司",
            type="Customer",
            industry="Technology",
            phone="123-456-7890",
            website="https://example.com"
        )
        print(f"🏢 创建账户：{account.get('id')}")
        
        # 创建联系人
        contact = await client.create_contact(
            account_id=account.get("id"),
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            title="CTO"
        )
        print(f"👤 创建联系人：{contact.get('id')}")
        
        # 创建销售机会
        opportunity = await client.create_opportunity(
            account_id=account.get("id"),
            name="测试机会",
            amount=10000,
            close_date="2026-06-30",
            stage_name="Prospecting"
        )
        print(f"💼 创建销售机会：{opportunity.get('id')}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
