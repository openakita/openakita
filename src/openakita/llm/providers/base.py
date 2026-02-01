"""
LLM Provider 基类

定义所有 Provider 必须实现的接口。
"""

import time
from abc import ABC, abstractmethod
from typing import Optional, AsyncIterator

from ..types import LLMRequest, LLMResponse, EndpointConfig

# 冷静期时长（秒）
COOLDOWN_SECONDS = 180  # 3 分钟


class LLMProvider(ABC):
    """LLM Provider 基类"""
    
    def __init__(self, config: EndpointConfig):
        self.config = config
        self._healthy = True
        self._last_error: Optional[str] = None
        self._cooldown_until: float = 0  # 冷静期结束时间戳
    
    @property
    def name(self) -> str:
        """Provider 名称"""
        return self.config.name
    
    @property
    def model(self) -> str:
        """模型名称"""
        return self.config.model
    
    @property
    def is_healthy(self) -> bool:
        """是否健康
        
        检查：
        1. 是否被标记为不健康
        2. 是否在冷静期内
        """
        # 冷静期结束后自动恢复健康
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            self._healthy = True
            self._cooldown_until = 0
            self._last_error = None
        
        return self._healthy
    
    @property
    def last_error(self) -> Optional[str]:
        """最后一次错误"""
        return self._last_error
    
    @property
    def cooldown_remaining(self) -> int:
        """冷静期剩余秒数"""
        if self._cooldown_until <= 0:
            return 0
        remaining = self._cooldown_until - time.time()
        return max(0, int(remaining))
    
    def mark_unhealthy(self, error: str):
        """标记为不健康，进入 3 分钟冷静期"""
        self._healthy = False
        self._last_error = error
        self._cooldown_until = time.time() + COOLDOWN_SECONDS
    
    def mark_healthy(self):
        """标记为健康，清除冷静期"""
        self._healthy = True
        self._last_error = None
        self._cooldown_until = 0
    
    @abstractmethod
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """
        发送聊天请求
        
        Args:
            request: 统一请求格式
            
        Returns:
            统一响应格式
        """
        pass
    
    @abstractmethod
    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """
        流式聊天请求
        
        Args:
            request: 统一请求格式
            
        Yields:
            流式事件
        """
        pass
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        默认实现：发送一个简单请求测试连接
        """
        try:
            from ..types import Message
            request = LLMRequest(
                messages=[Message(role="user", content="Hi")],
                max_tokens=10,
            )
            await self.chat(request)
            self.mark_healthy()
            return True
        except Exception as e:
            self.mark_unhealthy(str(e))
            return False
    
    @property
    def supports_tools(self) -> bool:
        """是否支持工具调用"""
        return self.config.has_capability("tools")
    
    @property
    def supports_vision(self) -> bool:
        """是否支持图片"""
        return self.config.has_capability("vision")
    
    @property
    def supports_video(self) -> bool:
        """是否支持视频"""
        return self.config.has_capability("video")
    
    @property
    def supports_thinking(self) -> bool:
        """是否支持思考模式"""
        return self.config.has_capability("thinking")
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} model={self.model}>"
