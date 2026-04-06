"""
CRM 系统 API 集成
支持：销售易/纷享销客
"""
import httpx
from typing import List, Dict, Any, Optional
import logging

from ..core.base import BaseAPIIntegration, APIConfig, APIResponse
from ..core.exceptions import AuthenticationError, ValidationError, ServiceUnavailableError, NotFoundError
from ..core.config import config

logger = logging.getLogger(__name__)


class CRMAPIConfig(APIConfig):
    """CRM API 配置"""
    provider: str = "xiaoshouyi"  # xiaoshouyi, fxiaoke
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None


class CRMAPI(BaseAPIIntegration):
    """CRM 系统 API"""
    
    def __init__(self, config: CRMAPIConfig):
        super().__init__(config)
        self.config: CRMAPIConfig = config
        self.client: Optional[httpx.AsyncClient] = None
        self.access_token: Optional[str] = None
    
    async def initialize(self) -> None:
        """初始化"""
        self._validate_config()
        self.client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout
        )
        
        await self._authenticate()
    
    async def close(self) -> None:
        """关闭连接"""
        if self.client:
            await self.client.aclose()
    
    def get_required_fields(self) -> list:
        """获取必需配置字段"""
        if self.config.provider == "xiaoshouyi":
            return ['base_url', 'client_id', 'client_secret']
        elif self.config.provider == "fxiaoke":
            return ['base_url', 'username', 'password']
        return ['api_key', 'base_url']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行 CRM 操作
        
        Actions:
            - create_lead: 创建线索
                参数：name, phone, company, source
            - query_lead: 查询线索
                参数：lead_id 或 filters
            - update_lead: 更新线索
                参数：lead_id, data
            - create_customer: 创建客户
                参数：name, phone, email, company
            - query_customer: 查询客户
                参数：customer_id 或 filters
            - create_opportunity: 创建商机
                参数：name, customer_id, amount, stage
            - query_opportunity: 查询商机
                参数：opportunity_id 或 filters
        
        Returns:
            APIResponse
        """
        try:
            actions = {
                "create_lead": self._create_lead,
                "query_lead": self._query_lead,
                "update_lead": self._update_lead,
                "create_customer": self._create_customer,
                "query_customer": self._query_customer,
                "create_opportunity": self._create_opportunity,
                "query_opportunity": self._query_opportunity,
            }
            
            if action not in actions:
                raise ValidationError(f"不支持的操作：{action}")
            
            return await actions[action](**kwargs)
        except Exception as e:
            logger.error(f"CRM 操作失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
    
    async def _authenticate(self) -> None:
        """认证获取 access_token"""
        if self.config.provider == "xiaoshouyi":
            await self._auth_xiaoshouyi()
        elif self.config.provider == "fxiaoke":
            await self._auth_fxiaoke()
    
    async def _auth_xiaoshouyi(self) -> None:
        """销售易认证"""
        url = f"{self.config.base_url}/services/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret
        }
        
        response = await self.client.post(url, data=payload)
        data = response.json()
        
        if 'access_token' not in data:
            raise AuthenticationError(f"销售易认证失败：{data.get('error_description', 'Unknown error')}")
        
        self.access_token = data['access_token']
        self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
    
    async def _auth_fxiaoke(self) -> None:
        """纷享销客认证"""
        url = f"{self.config.base_url}/openapi/login"
        payload = {
            "username": self.config.username,
            "password": self.config.password
        }
        
        response = await self.client.post(url, json=payload)
        data = response.json()
        
        if data.get('code') != 200:
            raise AuthenticationError(f"纷享销客认证失败：{data.get('msg')}")
        
        self.access_token = data['data']['access_token']
        self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
    
    async def _create_lead(
        self,
        name: str,
        phone: str,
        company: Optional[str] = None,
        source: str = "web",
        **kwargs
    ) -> APIResponse:
        """创建线索"""
        if self.config.provider == "xiaoshouyi":
            url = "/services/data/v50.0/sobjects/Lead"
            payload = {
                "LastName": name,
                "Phone": phone,
                "Company": company or "Unknown",
                "LeadSource": source,
                **kwargs
            }
            
            response = await self.client.post(url, json=payload)
            data = response.json()
            
            if response.status_code >= 400:
                raise ServiceUnavailableError(f"销售易创建线索失败：{data}")
            
            return APIResponse(
                success=True,
                data={"lead_id": data.get('id')},
                status_code=201
            )
        
        # TODO: 纷享销客实现
        raise ValidationError(f"暂不支持 {self.config.provider} 的创建线索操作")
    
    async def _query_lead(self, lead_id: Optional[str] = None, filters: Optional[Dict] = None) -> APIResponse:
        """查询线索"""
        if self.config.provider == "xiaoshouyi":
            if lead_id:
                url = f"/services/data/v50.0/sobjects/Lead/{lead_id}"
            else:
                url = "/services/data/v50.0/query/"
                # 构建 SOQL 查询
                soql = "SELECT Id, LastName, Phone, Company FROM Lead"
                if filters:
                    conditions = []
                    for k, v in filters.items():
                        conditions.append(f"{k} = '{v}'")
                    if conditions:
                        soql += " WHERE " + " AND ".join(conditions)
                params = {"q": soql}
            
            response = await self.client.get(url, params=params if not lead_id else None)
            data = response.json()
            
            return APIResponse(
                success=True,
                data=data,
                status_code=200
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的查询线索操作")
    
    async def _update_lead(self, lead_id: str, data: Dict[str, Any]) -> APIResponse:
        """更新线索"""
        if self.config.provider == "xiaoshouyi":
            url = f"/services/data/v50.0/sobjects/Lead/{lead_id}"
            
            response = await self.client.patch(url, json=data)
            
            if response.status_code >= 400:
                raise ServiceUnavailableError(f"销售易更新线索失败")
            
            return APIResponse(
                success=True,
                data={"lead_id": lead_id},
                status_code=200
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的更新线索操作")
    
    async def _create_customer(self, name: str, phone: str, email: Optional[str] = None, **kwargs) -> APIResponse:
        """创建客户"""
        # 简化实现：实际需根据 CRM 提供商调整
        return await self._create_lead(name=name, phone=phone, **kwargs)
    
    async def _query_customer(self, customer_id: Optional[str] = None, filters: Optional[Dict] = None) -> APIResponse:
        """查询客户"""
        return await self._query_lead(lead_id=customer_id, filters=filters)
    
    async def _create_opportunity(
        self,
        name: str,
        amount: float,
        stage: str = "prospecting",
        customer_id: Optional[str] = None,
        **kwargs
    ) -> APIResponse:
        """创建商机"""
        if self.config.provider == "xiaoshouyi":
            url = "/services/data/v50.0/sobjects/Opportunity"
            payload = {
                "Name": name,
                "Amount": amount,
                "StageName": stage,
                "AccountId": customer_id,
                **kwargs
            }
            
            response = await self.client.post(url, json=payload)
            data = response.json()
            
            if response.status_code >= 400:
                raise ServiceUnavailableError(f"销售易创建商机失败：{data}")
            
            return APIResponse(
                success=True,
                data={"opportunity_id": data.get('id')},
                status_code=201
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的创建商机操作")
    
    async def _query_opportunity(self, opportunity_id: Optional[str] = None, filters: Optional[Dict] = None) -> APIResponse:
        """查询商机"""
        if self.config.provider == "xiaoshouyi":
            if opportunity_id:
                url = f"/services/data/v50.0/sobjects/Opportunity/{opportunity_id}"
            else:
                url = "/services/data/v50.0/query/"
                soql = "SELECT Id, Name, Amount, StageName FROM Opportunity"
                if filters:
                    conditions = []
                    for k, v in filters.items():
                        conditions.append(f"{k} = '{v}'")
                    if conditions:
                        soql += " WHERE " + " AND ".join(conditions)
                params = {"q": soql}
            
            response = await self.client.get(url, params=params if not opportunity_id else None)
            data = response.json()
            
            return APIResponse(
                success=True,
                data=data,
                status_code=200
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的查询商机操作")


# 工厂函数
def create_crm_api(provider: str = "xiaoshouyi") -> CRMAPI:
    """创建 CRM API 实例"""
    config_dict = {"provider": provider}
    
    if provider == "xiaoshouyi":
        config_dict.update({
            "base_url": config.get("XIAOSHOUYI_BASE_URL"),
            "client_id": config.get("XIAOSHOUYI_CLIENT_ID"),
            "client_secret": config.get("XIAOSHOUYI_CLIENT_SECRET"),
        })
    elif provider == "fxiaoke":
        config_dict.update({
            "base_url": config.get("FXIAOKE_BASE_URL"),
            "username": config.get("FXIAOKE_USERNAME"),
            "password": config.get("FXIAOKE_PASSWORD"),
        })
    
    return CRMAPI(CRMAPIConfig(**config_dict))
