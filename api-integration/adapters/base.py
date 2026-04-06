"""
API 适配器统一接口规范
定义所有 API 适配器的基类和通用接口
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass
from enum import Enum


class APIStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class APIResponse:
    """统一 API 响应结构"""
    status: APIStatus
    data: Optional[Any] = None
    error: Optional[str] = None
    status_code: int = 200
    
    def is_success(self) -> bool:
        return self.status == APIStatus.SUCCESS


class BaseAPIAdapter(ABC):
    """API 适配器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialized = False
    
    @abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        pass
    
    @abstractmethod
    def execute(self, action: str, params: Dict[str, Any]) -> APIResponse:
        """执行 API 调用"""
        pass
    
    def health_check(self) -> bool:
        """健康检查"""
        try:
            return self.connect()
        except Exception:
            return False
