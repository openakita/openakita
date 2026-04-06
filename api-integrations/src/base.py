"""
基础客户端类
提供所有 API 客户端的通用功能：HTTP 请求、错误处理、重试、日志等
"""
import httpx
import structlog
from typing import Any, Dict, Optional, TypeVar, Generic
from abc import ABC, abstractmethod
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime
import json

logger = structlog.get_logger()

T = TypeVar('T')


class APIError(Exception):
    """API 调用异常基类"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Any] = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


class AuthenticationError(APIError):
    """认证失败"""
    pass


class RateLimitError(APIError):
    """速率限制"""
    def __init__(self, message: str, retry_after: Optional[int] = None, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class BaseAPIClient(ABC):
    """
    API 客户端基类
    提供通用的 HTTP 请求、认证、错误处理、重试机制
    """
    
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3
    ):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
    
    async def get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端（单例）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=self._get_default_headers()
            )
        return self._client
    
    def _get_default_headers(self) -> Dict[str, str]:
        """获取默认请求头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OpenAkita-API-Client/1.0"
        }
        if self.api_key:
            headers["Authorization"] = self._get_auth_header()
        return headers
    
    def _get_auth_header(self) -> str:
        """获取认证头（子类可重写）"""
        return f"Bearer {self.api_key}"
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError))
    )
    async def request(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送 HTTP 请求
        
        Args:
            method: HTTP 方法 (GET/POST/PUT/DELETE)
            endpoint: API 端点
            headers: 额外请求头
            params: 查询参数
            json_data: JSON 请求体
            
        Returns:
            API 响应数据
            
        Raises:
            APIError: API 调用失败
            AuthenticationError: 认证失败
            RateLimitError: 速率限制
        """
        client = await self.get_client()
        
        try:
            logger.info(
                "api_request",
                method=method,
                endpoint=endpoint,
                params=params
            )
            
            response = await client.request(
                method=method,
                url=endpoint,
                headers=headers,
                params=params,
                json=json_data,
                **kwargs
            )
            
            # 处理响应
            return await self._handle_response(response)
            
        except httpx.TimeoutException as e:
            logger.error("api_timeout", endpoint=endpoint, timeout=self.timeout)
            raise APIError(f"请求超时：{endpoint}", status_code=408)
        except httpx.NetworkError as e:
            logger.error("api_network_error", endpoint=endpoint, error=str(e))
            raise APIError(f"网络错误：{str(e)}")
        except Exception as e:
            logger.error("api_error", endpoint=endpoint, error=str(e))
            raise
    
    async def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """处理 HTTP 响应"""
        status_code = response.status_code
        
        # 记录响应日志
        logger.info(
            "api_response",
            status_code=status_code,
            endpoint=str(response.url)
        )
        
        # 成功响应
        if 200 <= status_code < 300:
            try:
                return response.json() if response.content else {}
            except json.JSONDecodeError:
                return {"raw": response.text}
        
        # 认证失败
        if status_code == 401:
            raise AuthenticationError(
                "认证失败，请检查 API 密钥",
                status_code=status_code,
                response=response.text
            )
        
        # 权限不足
        if status_code == 403:
            raise APIError(
                "权限不足",
                status_code=status_code,
                response=response.text
            )
        
        # 资源未找到
        if status_code == 404:
            raise APIError(
                "资源未找到",
                status_code=status_code,
                response=response.text
            )
        
        # 速率限制
        if status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimitError(
                f"请求过于频繁，请在 {retry_after} 秒后重试",
                retry_after=retry_after,
                status_code=status_code,
                response=response.text
            )
        
        # 服务器错误
        if status_code >= 500:
            raise APIError(
                f"服务器错误：{status_code}",
                status_code=status_code,
                response=response.text
            )
        
        # 其他错误
        error_data = response.json() if response.content else {}
        error_message = error_data.get("error", {}).get("message", response.text)
        raise APIError(
            f"API 错误：{error_message}",
            status_code=status_code,
            response=response.text
        )
    
    async def get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """GET 请求"""
        return await self.request("GET", endpoint, **kwargs)
    
    async def post(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """POST 请求"""
        return await self.request("POST", endpoint, **kwargs)
    
    async def put(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """PUT 请求"""
        return await self.request("PUT", endpoint, **kwargs)
    
    async def patch(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """PATCH 请求"""
        return await self.request("PATCH", endpoint, **kwargs)
    
    async def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """DELETE 请求"""
        return await self.request("DELETE", endpoint, **kwargs)
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
