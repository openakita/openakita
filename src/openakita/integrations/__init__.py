"""
统一 API 集成框架
提供标准化的 API 调用接口、认证管理、错误处理和监控
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class APIError(Exception):
    """API 调用异常"""
    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


class RateLimitError(APIError):
    """API 限流异常"""
    def __init__(self, message: str, retry_after: int | None = None, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class AuthenticationError(APIError):
    """认证失败异常"""
    pass


class BaseAPIAdapter(ABC):
    """API 适配器基类"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化 API 适配器

        Args:
            config: API 配置信息，包含认证凭据等
        """
        self.config = config
        self.name = self.__class__.__name__
        self._client = None

    @abstractmethod
    async def authenticate(self) -> bool:
        """执行认证，返回是否成功"""
        pass

    @abstractmethod
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """
        调用 API

        Args:
            endpoint: API 端点
            method: HTTP 方法
            **kwargs: 请求参数

        Returns:
            API 响应数据
        """
        pass

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            await self.authenticate()
            return True
        except Exception as e:
            logger.error(f"{self.name} 健康检查失败：{e}")
            return False

    def _log_request(self, endpoint: str, method: str, params: dict):
        """记录请求日志"""
        logger.debug(f"[{self.name}] {method} {endpoint} - Params: {json.dumps(params, ensure_ascii=False)}")

    def _log_response(self, endpoint: str, status: int, duration: float):
        """记录响应日志"""
        logger.debug(f"[{self.name}] {endpoint} - Status: {status}, Duration: {duration:.2f}ms")

    def _handle_error(self, status_code: int, response: dict) -> APIError:
        """处理错误响应"""
        if status_code == 429:
            retry_after = response.get('retry_after') or response.get('headers', {}).get('Retry-After')
            return RateLimitError("API 限流", retry_after=retry_after, status_code=status_code, response=response)
        elif status_code in [401, 403]:
            return AuthenticationError(f"认证失败：{status_code}", status_code=status_code, response=response)
        else:
            return APIError(f"API 调用失败：{status_code}", status_code=status_code, response=response)


class APIGateway:
    """API 网关 - 统一管理所有 API 适配器"""

    def __init__(self):
        self.adapters: dict[str, BaseAPIAdapter] = {}
        self._metrics = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'avg_response_time': 0.0
        }

    def register(self, name: str, adapter: BaseAPIAdapter):
        """注册 API 适配器"""
        self.adapters[name] = adapter
        logger.info(f"已注册 API 适配器：{name}")

    def get(self, name: str) -> BaseAPIAdapter | None:
        """获取 API 适配器"""
        return self.adapters.get(name)

    async def call(self, api_name: str, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """
        通过网关调用 API

        Args:
            api_name: API 名称
            endpoint: API 端点
            method: HTTP 方法
            **kwargs: 请求参数

        Returns:
            API 响应数据
        """
        adapter = self.get(api_name)
        if not adapter:
            raise APIError(f"未找到 API 适配器：{api_name}")

        start_time = datetime.now()
        self._metrics['total_calls'] += 1

        try:
            adapter._log_request(endpoint, method, kwargs)
            result = await adapter.call(endpoint, method, **kwargs)

            duration = (datetime.now() - start_time).total_seconds() * 1000
            adapter._log_response(endpoint, 200, duration)

            self._metrics['successful_calls'] += 1
            self._update_avg_response_time(duration)

            return result
        except APIError as e:
            self._metrics['failed_calls'] += 1
            logger.error(f"[{api_name}] API 调用失败：{e.message}")
            raise
        except Exception as e:
            self._metrics['failed_calls'] += 1
            logger.error(f"[{api_name}] 未知错误：{e}")
            raise APIError(str(e))

    def _update_avg_response_time(self, duration: float):
        """更新平均响应时间"""
        total = self._metrics['successful_calls'] + self._metrics['failed_calls']
        if total > 0:
            self._metrics['avg_response_time'] = (
                (self._metrics['avg_response_time'] * (total - 1) + duration) / total
            )

    def get_metrics(self) -> dict[str, Any]:
        """获取网关指标"""
        return self._metrics.copy()


# 全局网关实例
gateway = APIGateway()
