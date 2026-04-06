"""
API 集成基础模块 - 统一接口规范
所有 API 集成模块必须继承此基类
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel
import asyncio
import logging

logger = logging.getLogger(__name__)


class APIConfig(BaseModel):
    """API 配置模型"""
    api_key: Optional[str] = None
    secret_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0


class APIResponse(BaseModel):
    """统一 API 响应模型"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    request_id: Optional[str] = None


class BaseAPIIntegration(ABC):
    """API 集成基类"""
    
    def __init__(self, config: APIConfig):
        self.config = config
        self.session = None
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化 API 客户端"""
        pass
    
    @abstractmethod
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行 API 调用
        
        Args:
            action: 操作类型（如 'send', 'query', 'update', 'delete'）
            **kwargs: 操作参数
        
        Returns:
            APIResponse: 统一响应对象
        """
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """关闭连接"""
        pass
    
    async def _retry_request(self, func, *args, **kwargs) -> Any:
        """带重试的请求执行"""
        last_exception = None
        
        for attempt in range(self.config.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{self.config.max_retries}): {str(e)}")
                
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (2 ** attempt))
        
        raise last_exception
    
    def _validate_config(self) -> None:
        """验证配置完整性"""
        required_fields = self.get_required_fields()
        missing = [f for f in required_fields if not getattr(self.config, f, None)]
        
        if missing:
            raise ValueError(f"缺少必需配置：{', '.join(missing)}")
    
    def get_required_fields(self) -> list:
        """获取必需的配置字段（子类重写）"""
        return ['api_key']
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
