"""
服务商注册表基类

定义所有服务商注册表必须实现的接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProviderInfo:
    """服务商信息"""
    name: str                     # 显示名称
    slug: str                     # 标识符 (anthropic, dashscope, ...)
    api_type: str                 # "anthropic" | "openai"
    default_base_url: str         # 默认 API 地址
    api_key_env_suggestion: str   # 建议的环境变量名
    supports_model_list: bool     # 是否支持模型列表 API
    supports_capability_api: bool  # API 是否返回能力信息


@dataclass
class ModelInfo:
    """模型信息"""
    id: str                       # 模型 ID (qwen-max, claude-3-opus, ...)
    name: str                     # 显示名称
    capabilities: dict = field(default_factory=dict)  # {"text": True, "vision": True, ...}
    context_window: Optional[int] = None  # 上下文窗口
    max_output_tokens: Optional[int] = None
    pricing: Optional[dict] = None  # 定价信息
    thinking_only: bool = False   # 是否仅支持思考模式


class ProviderRegistry(ABC):
    """服务商注册表基类"""
    
    info: ProviderInfo
    
    @abstractmethod
    async def list_models(self, api_key: str) -> list[ModelInfo]:
        """
        获取可用模型列表
        
        Args:
            api_key: API Key
            
        Returns:
            模型列表
        """
        pass
    
    def get_model_capabilities(self, model_id: str) -> dict:
        """
        获取模型能力
        
        优先级: API 返回 > 预置能力表 > 默认值
        """
        from ..capabilities import infer_capabilities
        return infer_capabilities(model_id, provider_slug=self.info.slug)
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} slug={self.info.slug}>"
