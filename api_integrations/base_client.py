"""
API 基础客户端类
提供统一的错误处理、重试机制和日志记录
"""
import logging
import asyncio
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod
import httpx

logger = logging.getLogger(__name__)


class APIError(Exception):
    """API 调用异常"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


class BaseAPIClient(ABC):
    """API 客户端基类"""
    
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def connect(self):
        """建立连接"""
        if not self._client:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._get_headers()
            )
    
    async def close(self):
        """关闭连接"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "OpenAkita-API-Client/1.0"
        }
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """发送 HTTP 请求"""
        if not self._client:
            await self.connect()
        
        try:
            response = await self._client.request(method, endpoint, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP 错误：{e.response.status_code} - {e.response.text}")
            raise APIError(
                message=f"API 请求失败：{e.response.status_code}",
                status_code=e.response.status_code,
                response=e.response.json() if e.response.text else None
            )
        except httpx.RequestError as e:
            logger.error(f"请求错误：{str(e)}")
            raise APIError(message=f"网络请求失败：{str(e)}")
    
    async def get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """GET 请求"""
        return await self._request("GET", endpoint, **kwargs)
    
    async def post(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """POST 请求"""
        return await self._request("POST", endpoint, **kwargs)
    
    async def put(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """PUT 请求"""
        return await self._request("PUT", endpoint, **kwargs)
    
    async def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """DELETE 请求"""
        return await self._request("DELETE", endpoint, **kwargs)
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """测试连接"""
        pass
