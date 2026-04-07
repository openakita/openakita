"""
CRM API 适配器
支持 Salesforce 和纷享销客
"""

from typing import Any

import aiohttp

from . import APIError, AuthenticationError, BaseAPIAdapter


class SalesforceAdapter(BaseAPIAdapter):
    """Salesforce CRM 适配器"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.username = config.get('username')
        self.password = config.get('password')
        self.security_token = config.get('security_token')
        self.consumer_key = config.get('consumer_key')
        self.consumer_secret = config.get('consumer_secret')
        self.instance_url = None
        self.access_token = None
        self._session = None

    async def authenticate(self) -> bool:
        if not all([self.username, self.password, self.consumer_key, self.consumer_secret]):
            raise AuthenticationError("缺少 Salesforce 认证信息")

        try:
            async with aiohttp.ClientSession() as session, session.post(
                'https://login.salesforce.com/services/oauth2/token',
                data={
                    'grant_type': 'password',
                    'client_id': self.consumer_key,
                    'client_secret': self.consumer_secret,
                    'username': self.username,
                    'password': f"{self.password}{self.security_token}"
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                result = await response.json()
                if response.status == 200:
                    self.access_token = result['access_token']
                    self.instance_url = result['instance_url']
                    return True
                else:
                    raise AuthenticationError(f"Salesforce 认证失败：{result}")
        except Exception as e:
            raise APIError(f"Salesforce 认证失败：{str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        async with self._session.request(
            method,
            f"{self.instance_url}{endpoint}",
            headers=headers,
            json=kwargs.get('json'),
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            if response.status >= 400:
                raise self._handle_error(response.status, result)
            return result

    async def query(self, soql: str) -> dict[str, Any]:
        return await self.call(f"/services/data/v58.0/query?q={soql}")

    async def create_record(self, sobject: str, data: dict) -> dict[str, Any]:
        return await self.call(f"/services/data/v58.0/sobjects/{sobject}", method="POST", json=data)

    async def close(self):
        if self._session:
            await self._session.close()


class FXiaokeAdapter(BaseAPIAdapter):
    """纷享销客 CRM 适配器"""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.app_key = config.get('app_key')
        self.app_secret = config.get('app_secret')
        self.access_token = None
        self._session = None

    async def authenticate(self) -> bool:
        if not self.app_key or not self.app_secret:
            raise AuthenticationError("缺少纷享销客认证信息")

        try:
            async with aiohttp.ClientSession() as session, session.post(
                'https://open.fxiaoke.com/webapp/oauth2/token',
                json={
                    'appKey': self.app_key,
                    'appSecret': self.app_secret,
                    'grantType': 'client'
                }
            ) as response:
                result = await response.json()
                if response.status == 200 and result.get('errorCode') == 0:
                    self.access_token = result['data']['accessToken']
                    return True
                else:
                    raise AuthenticationError(f"纷享销客认证失败：{result}")
        except Exception as e:
            raise APIError(f"纷享销客认证失败：{str(e)}")

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        if not self._session:
            self._session = aiohttp.ClientSession()

        await self.authenticate()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        async with self._session.request(
            method,
            f"https://open.fxiaoke.com{endpoint}",
            headers=headers,
            json=kwargs.get('json'),
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            if response.status >= 400 or result.get('errorCode', 0) != 0:
                raise self._handle_error(response.status, result)
            return result

    async def create_customer(self, data: dict) -> dict[str, Any]:
        return await self.call("/api/crm/customer", method="POST", json=data)

    async def close(self):
        if self._session:
            await self._session.close()


def create_crm_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {'salesforce': SalesforceAdapter, 'fxiaoke': FXiaokeAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的 CRM 提供商：{provider}")
    return providers[provider](config)
