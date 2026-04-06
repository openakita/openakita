"""
MVP API 集成 - 基础抽象类
提供统一的 API 接口规范、错误处理、重试机制和日志记录
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from enum import Enum
import logging
import time
from dataclasses import dataclass
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class APIMode(Enum):
    """API 运行模式"""
    MOCK = "mock"
    REAL = "real"


@dataclass
class APIResponse:
    """统一 API 响应结构"""
    success: bool
    data: Any
    error: Optional[str] = None
    status_code: int = 200
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class BaseAPI(ABC):
    """API 集成抽象基类"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        self.mode = mode
        self.retry_attempts = 3
        self.retry_backoff = 2  # 秒
        self._config = {}
        
    @abstractmethod
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用实现（子类重写）"""
        pass
    
    @abstractmethod
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock API 调用实现（子类重写）"""
        pass
    
    def call(self, **kwargs) -> APIResponse:
        """统一调用入口，包含重试逻辑"""
        last_error = None
        
        for attempt in range(1, self.retry_attempts + 1):
            try:
                logger.info(f"[{self.__class__.__name__}] 调用尝试 {attempt}/{self.retry_attempts}, 模式：{self.mode.value}")
                
                if self.mode == APIMode.MOCK:
                    response = self._call_mock(**kwargs)
                else:
                    response = self._call_real(**kwargs)
                
                if response.success:
                    logger.info(f"[{self.__class__.__name__}] 调用成功")
                    return response
                else:
                    last_error = response.error
                    logger.warning(f"[{self.__class__.__name__}] 调用失败：{response.error}")
                    
            except Exception as e:
                last_error = str(e)
                logger.error(f"[{self.__class__.__name__}] 调用异常：{e}")
            
            # 重试等待
            if attempt < self.retry_attempts:
                wait_time = self.retry_backoff * attempt
                logger.info(f"[{self.__class__.__name__}] {wait_time}秒后重试...")
                time.sleep(wait_time)
        
        # 所有重试失败
        return APIResponse(
            success=False,
            data=None,
            error=f"所有重试失败：{last_error}",
            status_code=500
        )
    
    def configure(self, **kwargs):
        """配置 API 凭据"""
        self._config.update(kwargs)
        logger.info(f"[{self.__class__.__name__}] 配置已更新")
    
    def set_mode(self, mode: APIMode):
        """切换运行模式"""
        self.mode = mode
        logger.info(f"[{self.__class__.__name__}] 模式已切换为：{mode.value}")
