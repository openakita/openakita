"""
服务商注册表基类

定义所有服务商注册表必须实现的接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

_shared_registry_client: httpx.AsyncClient | None = None


def get_registry_client() -> httpx.AsyncClient:
    """获取 registry 共享 httpx 客户端（连接池复用，避免每次请求新建/销毁）。"""
    global _shared_registry_client
    if _shared_registry_client is None or _shared_registry_client.is_closed:
        _shared_registry_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(
                max_connections=30,
                max_keepalive_connections=10,
            ),
        )
    return _shared_registry_client


@dataclass
class ProviderInfo:
    """服务商信息"""

    name: str  # 显示名称
    slug: str  # 标识符 (anthropic, dashscope, ...)
    api_type: str  # "anthropic" | "openai"
    default_base_url: str  # 默认 API 地址
    api_key_env_suggestion: str  # 建议的环境变量名
    supports_model_list: bool  # 是否支持模型列表 API
    supports_capability_api: bool  # API 是否返回能力信息
    requires_api_key: bool = True  # 是否需要 API Key（本地服务如 Ollama 为 False）
    is_local: bool = False  # 是否为本地服务商
    coding_plan_base_url: str | None = None  # Coding Plan 专用 API 地址（为 None 则不支持）
    coding_plan_api_type: str | None = (
        None  # Coding Plan 模式下的协议类型（为 None 则与 api_type 相同）
    )
    note: str | None = None  # 前端 i18n key — 服务商提示信息（如"NVIDIA 免费模型限制输出"）


@dataclass
class ModelInfo:
    """模型信息"""

    id: str  # 模型 ID (qwen-max, claude-3-opus, ...)
    name: str  # 显示名称
    capabilities: dict = field(default_factory=dict)  # {"text": True, "vision": True, ...}
    context_window: int | None = None  # 上下文窗口
    max_output_tokens: int | None = None
    pricing: dict | None = None  # 定价信息
    thinking_only: bool = False  # 是否仅支持思考模式


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
